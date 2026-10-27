#!/bin/bash
# ============================================================================
# 飞书 secret 一条龙脚本 · 退出码契约守卫
#
# 断言什么（依据 AGENTS.md §16 Y 第 9 条 + 交接单 §44.3 / §46）：
#   ① 显式 exit N 的业务失败路径 ⇒ 退出码必须真实：3 / 4 / 5 / 6
#   ② 成功路径 ⇒ 退出码 0 + 末行哨兵
#   ③ 失败路径 ⇒ 假 store 与假 db 必须零写入
#   ④ 失败路径 ⇒ 末行**不能**出现成功哨兵（否则「哨兵」这个判据本身是恒真的）
#
# ★ 为什么这个台架零生产接触（两条设计，别删）：
#   ① HOME 指向 sandbox ⇒ 脚本的默认备份目录 $HOME/.solobrave-backups 也落在 sandbox
#      ⇒ 连「故意不传 --bak-dir」的负向对照也只写 sandbox，不会往真备份目录丢副本
#   ② --db / --store-db 指向 sandbox 里的假库 ⇒ 真 store 与真生产 db 零读写
#
# ★ 期望值一律用「运行前快照」，不硬编码（A 边 §46.8 坑 ②）：
#   norow / nofile / garbage 三种形态的初始态本来就不是旧指纹
#   ⇒ 硬编码旧指纹会造出 3 例假红 ⇒ 改成「跑完 == 跑前」
#
# ★ 判文件存在一律用 os.path.isfile，**不用 sqlite3.connect**
#   （A 边 §46.8 坑 ①：connect 对不存在的路径会**创建空库** ⇒ 读一次就把初始态改掉了）
#
# 用法：
#   bash scripts/e2e_feishu_rotate_exit_codes.sh                  # 用默认被测脚本
#   bash scripts/e2e_feishu_rotate_exit_codes.sh --script <path>  # 指定被测脚本
#   bash scripts/e2e_feishu_rotate_exit_codes.sh --strict          # 被测脚本缺失 => exit 1
#   bash scripts/e2e_feishu_rotate_exit_codes.sh --app mumu       # 换一个被测 app（见下）
#
# 被测脚本是交付物、放在 repo 外 ⇒ 本机没装就没有可测对象
#   ⇒ 默认打印 SKIP 并 exit 0（--strict 则 exit 1）
#   ⚠️ SKIP 不等于 PASS：输出里会写明「跳过原因」，不要把 SKIP 当通过
# ============================================================================

# ⚠️ 注意：这里的 HOME 是**真** HOME（下面才被改掉）⇒ 默认被测脚本路径要用真 HOME 取
REAL_HOME="${HOME}"
SCRIPT="${REAL_HOME}/.solobrave-secrets/rotate_feishu_secret_and_db.sh"
STRICT=0
APP_TAG="t_default"
while [ $# -gt 0 ]; do
  case "$1" in
    --script) SCRIPT="${2:-}"; shift 2 ;;
    --app)    APP_TAG="${2:-}"; shift 2 ;;
    --strict) STRICT=1; shift ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

if [ ! -f "$SCRIPT" ]; then
  echo "⚠️ SKIP：被测脚本不存在 => $SCRIPT"
  echo "   原因：交付物放在 repo 外（~/.solobrave-secrets/），本机未安装就没有可测对象。"
  echo "   ⚠️ SKIP != PASS。"
  if [ "$STRICT" = "1" ]; then exit 1; fi
  exit 0
fi

# ★ ★ ★ 被测 app 可切换（--app）：老大轮换顺序是「先木木 → 后貂蝉」⇒ 守卫必须能测任何一个
#   app_id 与 tenant_id 都取自 ~/.openclaw/openclaw.json（不可变 ID ⇒ 定位靠 ID 不靠名字）
APP_TAG="t_default"
case "$APP_TAG" in
  t_default) APP="cli_aa420db06a78dcc5"; TEN="t_default"; NAME="FEISHU_APPSECRET_T_DEFAULT" ;;
  mumu)      APP="cli_aa51b1b62a7a9bf6"; TEN="mumu";      NAME="FEISHU_APPSECRET_MUMU" ;;
  default)   APP="cli_aafb5d7cd6389be4"; TEN="default";   NAME="FEISHU_APPSECRET_MAIN" ;;
  helen)     APP="cli_aaf9b98563b99bd7"; TEN="helen";     NAME="FEISHU_APPSECRET_HELEN" ;;
  *) echo "未知 app 标签: $APP_TAG" >&2; exit 2 ;;
esac
OLD_V="OLDfakeSECRET_${TEN}_00000AAA"
NEW_V="NEWfakeSECRET_${TEN}_00000BBB"
# ★ 长度自洽：直接按 NEW_V 截 31 字符 ⇒ 不手数（手数过一次，写成 30 实际 30 ⇒ 守卫自己 exit 9）
BAD_V="${NEW_V:0:31}"                                # 31 字符 ⇒ 触发长度闸
[ "${#OLD_V}" -eq 32 ] || { echo "fixture 常量长度 ${#OLD_V} != 32" >&2; exit 9; }
[ "${#NEW_V}" -eq 32 ] || { echo "fixture 常量长度 ${#NEW_V} != 32" >&2; exit 9; }
[ "${#BAD_V}" -eq 31 ] || { echo "fixture 常量长度 ${#BAD_V} != 31" >&2; exit 9; }

SANDBOX="$(mktemp -d /private/tmp/.sbfb-guard.XXXXXXXX)"
cleanup() {
  # ★★ 2026-10-27（B 边 §59）：这里**必须用绝对路径 /bin/rm**，不能用裸 `rm`。
  #   实测根因：本机 PATH 里的 `rm` 是 MiniMax Code 的**可恢复删除 shim**
  #   （/Users/qichen/.minimax/shims/rm → mavis-trash）⇒ 它在删除本 sandbox 时
  #   **静默失败**（rc=0 但目录还在）⇒ 而原写法带 `2>/dev/null` ⇒ **错误被吞掉**
  #   ⇒ 表现 = 守卫每次跑净增 1 个 /private/tmp/.sbfb-guard.* 残留（实测累计 49 个）
  #   ⇒ 且守卫自己的断言只查 `/private/tmp/.sbfb.*`（被测脚本的），**查不到自己的残留**。
  #   ⇒ 修法：① 用 /bin/rm 绝对路径 ② 断言扩展到 .sbfb-guard.*（见下方）
  [ -n "${SANDBOX:-}" ] && [ -d "${SANDBOX}" ] && /bin/rm -rf "${SANDBOX}" 2>/dev/null
  # ★ 清理后**实测**自己还在不在 ⇒ 这个值供下方断言用（不是抄跑前快照 ⇒ 不是恒真）
  #   ⇒ 幂等：已删掉时目录不存在 ⇒ 仍判 "yes" ⇒ exit 时 trap 再调一次也无害。
  if [ -n "${SANDBOX:-}" ] && [ -d "${SANDBOX}" ]; then
    CLEANUP_OK="no"
  else
    CLEANUP_OK="yes"
  fi
}
trap cleanup EXIT

# ★★ B 边 §80.12（2026-10-27 13:1x）：真备份目录的**指纹**（不是纯计数）
#   ⇒ 为什么不用纯计数：① 目录不存在时两边都是 0 ⇒ 相等 ⇒ 判绿 ⇒ **恒真**
#                     ② 「删一个 + 加一个」计数不变 ⇒ 也漏
#   ⇒ 指纹 = 「文件名 + 大小」排序输出 ⇒ 能抓到新增/删除/改名/size 变化
#   ⇒ 目录不存在 ⇒ 返回 __MISSING__ ⇒ 由调用方**判红**（不许静默 0=0）
real_bak_fp() {
  [ -d "$1" ] || { echo "__MISSING__"; return 0; }
  ls -l "$1" 2>/dev/null | awk 'NR>1 {print $5, $9}' | sort
}

# ★ HOME 改到 sandbox ⇒ 脚本默认备份目录随之进 sandbox
export HOME="${SANDBOX}/home"
mkdir -p "${HOME}" "${SANDBOX}/bin" "${SANDBOX}/bak"
REAL_BAK="${REAL_HOME}/.solobrave-backups"
# ★★ B 边 §80.12：改用**指纹**（原来用纯计数 ⇒ 目录不存在时 0=0 ⇒ 恒真）
REAL_BAK_BEFORE="$(real_bak_fp "${REAL_BAK}")"
SBFB_STORE="${SANDBOX}/store.db"
APP_DB="${SANDBOX}/app.db"

# ---------------------------------------------------------------- stub curl
cat > "${SANDBOX}/bin/curl" <<'STUB'
#!/bin/bash
# stub curl：只回 JSON；code 由 $STUB_CODE 决定（0 = 接受，其余 = 拒收）
echo "{\"code\":${STUB_CODE:-0},\"msg\":\"stub\"}"
STUB
chmod +x "${SANDBOX}/bin/curl"

# ------------------------------------------------------------- stub openclaw
cat > "${SANDBOX}/bin/openclaw" <<'STUB'
#!/bin/bash
# stub openclaw secrets store set NAME --scope team --kind secret --value-file PATH
name=""; vf=""; seen_set=0
while [ $# -gt 0 ]; do
  case "$1" in
    secrets|store) shift ;;
    set) seen_set=1; shift ;;
    --scope|--kind) shift 2 ;;
    --value-file) vf="$2"; shift 2 ;;
    --dry-run) shift ;;
    *) if [ "$seen_set" = "1" ] && [ -z "$name" ]; then name="$1"; fi; shift ;;
  esac
done
python3 - "$SBFB_STORE" "$name" "$vf" <<'PY'
import os, sqlite3, sys
db, name, vf = sys.argv[1], sys.argv[2], sys.argv[3]
val = open(vf).read()
con = sqlite3.connect(db)
con.execute("""CREATE TABLE IF NOT EXISTS secret_store_entries(
  scope_kind TEXT, scope_id TEXT, name TEXT, value TEXT, kind TEXT,
  created_at_ms INTEGER, updated_at_ms INTEGER, updated_by TEXT,
  deleted_at_ms INTEGER, allowed_hosts TEXT)""")
con.execute("DELETE FROM secret_store_entries WHERE name=?", (name,))
con.execute("INSERT INTO secret_store_entries VALUES('team','',?,?,'secret',0,0,'cli',NULL,NULL)", (name, val))
con.commit(); con.close()
PY
STUB
chmod +x "${SANDBOX}/bin/openclaw"
export PATH="${SANDBOX}/bin:${PATH}"
export SBFB_STORE

# ----------------------------------------------------------------- fixtures
DB_SCHEMA="CREATE TABLE IF NOT EXISTS tenant_feishu_config(
  tenant_id TEXT PRIMARY KEY, app_id TEXT, app_secret TEXT, status TEXT,
  error TEXT, bound_at TEXT, updated_at INTEGER, route_agent_id TEXT)"

# 建 db 的公共部分（注意：造完要显式删掉 -wal/-shm ⇒ 才是「WAL 且无 sidecar」的生产同款状态）
make_db() {
  python3 - "$APP_DB" "$DB_SCHEMA" "$@" <<'PY'
import sqlite3, sys
db, schema = sys.argv[1], sys.argv[2]
rows = []
for a in sys.argv[3:]:
    rows.append(a.split("|"))
con = sqlite3.connect(db)
con.execute("PRAGMA journal_mode=WAL")
con.execute(schema)
for r in rows:
    con.execute("INSERT INTO tenant_feishu_config VALUES(?,?,?,'routed','','2026-01-01',111,'agent_x')", r)
con.commit(); con.close()
PY
  # ★ SQLite 关闭时只 checkpoint、**不删** sidecar ⇒ 不手动删就不是「无 sidecar」状态
  rm -f "${APP_DB}-wal" "${APP_DB}-shm"
}

# 假 store db（带一条旧值行）⇒ 让「store 备份」这条路径也被走到
make_store() {
  rm -f "${SBFB_STORE}"
  python3 - "$SBFB_STORE" "$OLD_V" "$NAME" <<'PY'
import sqlite3, sys
db, old, name = sys.argv[1], sys.argv[2], sys.argv[3]
con = sqlite3.connect(db)
con.execute("""CREATE TABLE secret_store_entries(
  scope_kind TEXT, scope_id TEXT, name TEXT, value TEXT, kind TEXT,
  created_at_ms INTEGER, updated_at_ms INTEGER, updated_by TEXT,
  deleted_at_ms INTEGER, allowed_hosts TEXT)""")
con.execute("INSERT INTO secret_store_entries VALUES('team','',?,?,'secret',0,0,'cli',NULL,NULL)", (name, old))
con.commit(); con.close()
PY
}

# make_fixture <variant>
make_fixture() {
  local variant="$1"
  rm -f "${APP_DB}" "${APP_DB}-wal" "${APP_DB}-shm"
  make_store
  case "$variant" in
    normal)
      make_db "${TEN}|${APP}|${OLD_V}"
      ;;
    norow)
      # db 存在，但**没有**该 app_id 的行
      make_db "other_tenant|cli_some_other_app|${OLD_V}"
      ;;
    tenant_mismatch)
      # 有该 app_id 的行，但 tenant_id 不符
      make_db "wrong_tenant|${APP}|${OLD_V}"
      ;;
    dup_rows)
      # 同 app_id 命中两行（tenant_id 是主键 ⇒ 用两个不同 tenant_id）
      make_db "${TEN}|${APP}|${OLD_V}" "second_tenant|${APP}|${OLD_V}"
      ;;
    nofile)
      # db 文件不存在 ⇒ **不许凭空创建**
      ;;
    garbage)
      # db 存在但不是 sqlite 库
      printf 'this is definitely not a sqlite database\n' > "${APP_DB}"
      ;;
    garbage_store)
      # ★ store db 是垃圾文件（app db 仍是好的）⇒ 命中「备份 store 失败」分支
      #   ⇒ 这条**可判别**：app db 好 ⇒ 预检不会替它兜底 ⇒ 单独改坏该分支它就会红
      make_db "${TEN}|${APP}|${OLD_V}"
      printf 'this is definitely not a sqlite database\n' > "${SBFB_STORE}"
      ;;
    notable)
      # ★ 是**合法 sqlite 库**，但**没有 tenant_feishu_config 这张表**
      #   ⇒ 备份步骤能成功 ⇒ 命中的是**预检的读库失败分支**（except Exception）
      #   ⇒ S5e(garbage) 命中的其实是**备份阶段**失败，两者不是同一条路径
      python3 - "$APP_DB" <<'PY'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
con.execute("PRAGMA journal_mode=WAL")
con.execute("CREATE TABLE some_other_table(x TEXT)")
con.commit(); con.close()
PY
      rm -f "${APP_DB}-wal" "${APP_DB}-shm"
      ;;
    *) echo "未知 fixture 变体: $variant" >&2; exit 9 ;;
  esac
}

# 读假 store 的 secret 状态（只输出指纹，不输出明文）
# ★ 四态，与 db_state_of 同构：
#   NOFILE          = 文件不存在
#   GARBAGE:<md5>   = 文件存在但不是 sqlite 库（带内容指纹 ⇒ 内容被改也能发现）
#   NOROW           = 是 sqlite 库但没有这一条
#   <fp>            = 该条 value 的指纹
read_store_state() {
  python3 - "$SBFB_STORE" "$NAME" <<'PY'
import hashlib, os, sqlite3, sys
p, name = sys.argv[1], sys.argv[2]
if not os.path.isfile(p):
    print("NOFILE"); sys.exit(0)
try:
    con = sqlite3.connect(p)
    row = con.execute("SELECT value FROM secret_store_entries WHERE name=?", (name,)).fetchone()
    con.close()
except Exception:
    print("GARBAGE:" + hashlib.md5(open(p, "rb").read()).hexdigest()[:12]); sys.exit(0)
if row is None:
    print("NOROW"); sys.exit(0)
print(hashlib.md5(str(row[0]).strip().encode()).hexdigest()[:12])
PY
}

# ★ db 状态四态（判存在用 isfile，**不用 connect** ⇒ 不会把「不存在」变成「空库」）
#   NOFILE       = 文件不存在
#   GARBAGE:<md5> = 文件存在但不是 sqlite 库（带内容指纹 ⇒ 内容被改也能发现）
#   NOROW        = 是 sqlite 库但没有该 app_id 的行
#   <fp>         = 该 app_id 行的 app_secret 指纹
db_state_of() {
  python3 - "$APP_DB" "$APP" <<'PY'
import hashlib, os, sqlite3, sys
p, app = sys.argv[1], sys.argv[2]
if not os.path.isfile(p):
    print("NOFILE"); sys.exit(0)
try:
    con = sqlite3.connect(p)
    row = con.execute("SELECT app_secret FROM tenant_feishu_config WHERE app_id=?", (app,)).fetchone()
    con.close()
except Exception:
    print("GARBAGE:" + hashlib.md5(open(p, "rb").read()).hexdigest()[:12]); sys.exit(0)
if row is None:
    print("NOROW"); sys.exit(0)
print(hashlib.md5(str(row[0]).strip().encode()).hexdigest()[:12])
PY
}

fp_of() { python3 -c "import hashlib,sys;print(hashlib.md5(sys.argv[1].encode()).hexdigest()[:12])" "$1"; }
OLD_FP="$(fp_of "$OLD_V")"
NEW_FP="$(fp_of "$NEW_V")"

PASS=0
FAIL=0
CASE_DONE=0                # ★★ B 边 §80.12：实际执行了几个 case（用于「漏跑就判红」）
ok()  { PASS=$((PASS+1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL+1)); echo "  ❌ $1"; }

# ------------------------------------------------------------------ 场景跑
# run_case <名称> <stub_code> <输入文件> <期望退出码> <期望store变:yes|no> <期望db变:yes|no> <fixture变体>
# 期望值用「跑前快照」：store/db 都要求「跑完 == 跑前」（不变）或 == NEW_FP（变）
run_case() {
  local name="$1" stub_code="$2" infile="$3" exp_rc="$4" exp_store="$5" exp_db="$6" variant="$7"
  local rc out store_before db_before store_after db_after sentinel bad_items=""
  CASE_DONE=$((CASE_DONE+1))     # ★★ B 边 §80.12：实际执行计数（供「case 数守恒」断言）
  make_fixture "$variant"
  store_before="$(read_store_state)"
  db_before="$(db_state_of)"
  STUB_CODE="$stub_code" bash "$SCRIPT" "$NAME" \
      --db "$APP_DB" --store-db "$SBFB_STORE" --bak-dir "$SANDBOX/bak" \
      < "$infile" > "${SANDBOX}/out.txt" 2>&1
  rc=$?                       # ★ 直接取，不经过管道（管道会洗掉退出码）
  out="${SANDBOX}/out.txt"
  store_after="$(read_store_state)"
  db_after="$(db_state_of)"
  if grep -q '哨兵：全部步骤完成' "$out"; then sentinel="yes"; else sentinel="no"; fi

  [ "$rc" = "$exp_rc" ] || bad_items="${bad_items} 退出码(实得 ${rc} 期望 ${exp_rc});"
  if [ "$exp_rc" = "0" ]; then
    [ "$sentinel" = "yes" ] || bad_items="${bad_items} 成功路径缺末行哨兵;"
  else
    [ "$sentinel" = "no" ] || bad_items="${bad_items} 失败路径竟打了成功哨兵;"
  fi
  if [ "$exp_store" = "yes" ]; then
    [ "$store_after" = "$NEW_FP" ] || bad_items="${bad_items} store 指纹(实得 ${store_after} 期望 ${NEW_FP});"
  else
    [ "$store_after" = "$store_before" ] || bad_items="${bad_items} store 被改(实得 ${store_after} 跑前 ${store_before});"
  fi
  if [ "$exp_db" = "yes" ]; then
    [ "$db_after" = "$NEW_FP" ] || bad_items="${bad_items} db 指纹(实得 ${db_after} 期望 ${NEW_FP});"
  else
    [ "$db_after" = "$db_before" ] || bad_items="${bad_items} db 被改(实得 ${db_after} 跑前 ${db_before});"
  fi

  if [ -n "$bad_items" ]; then
    echo "  ❌ ${name}"
    echo "${bad_items}" | sed 's/^/       - /'
    return 1
  fi
  echo "  ✅ ${name}（退出码 ${rc}；store/db 与跑前一致或如期望；哨兵 ${sentinel}）"
  return 0
}

# ------------------------------------------------------------------ 正式用例
# ★★ B 边 §64：跑前快照 .sbfb.* 集合 ⇒ 跑后取**差集** ⇒ 只对自己这次产生的负责（并发不假红）
#   ⇒ ★★ 快照文件**必须放在 /tmp 下、且名字不以 .sbfb 开头** ——
#     否则它会被下面 `ls -d /private/tmp/.sbfb.*` **自己匹配到** ⇒ 快照里混进自己的路径 ⇒ 差集永远非空。
SBFB_SNAP="$(mktemp -t b_sbfb_before.XXXXXXXX)"
SBFB_AFTER="$(mktemp -t b_sbfb_after.XXXXXXXX)"
# ★★ 2026-10-27（B 边 §72 修，A 边 §70.2 实测抓到）：原来的 `/private/tmp/.sbfb.?????????`
#   是 **9 个 ?** ⇒ 而被测脚本模板是 `mktemp -d /private/tmp/.sbfb.XXXXXXXX` = **8 位**
#   ⇒ **命中恒为 0** ⇒ 「本次运行零残留」这条断言**恒真** ⇒ 判不出任何东西 ⇒ **假绿**。
#   ⇒ 修法三条同时满足：① **宽度无关**（不写死位数，mktemp 位数变了也不漏）
#     ② **覆盖历史命名形状**（旧命名空间 `sbfb-e2e.*` 无前导点）
#     ③ **排除守卫自己的** `.sbfb-guard.*`（否则与下面「守卫自身零残留」互相计数）
_sbfb_list() {
  {
    ls -d /private/tmp/.sbfb.* 2>/dev/null | grep -v '^/private/tmp/\.sbfb-guard\.'
    ls -d /private/tmp/sbfb-e2e.* 2>/dev/null
  } | sort -u
}
_sbfb_list > "$SBFB_SNAP"
echo "=== 飞书一条龙脚本 · 退出码契约守卫 ==="
echo "被测脚本: ${SCRIPT}"
echo "被测 app : ${APP_TAG}  (app_id=${APP}  tenant=${TEN}  entry=${NAME})"
echo ""
printf '%s' "$NEW_V"  > "${SANDBOX}/in_ok.txt"
printf '%s' "$BAD_V"  > "${SANDBOX}/in_short.txt"
: >                      "${SANDBOX}/in_empty.txt"

echo "--- 正向用例：输入层 ---"
if run_case "S1 成功：code=0 ⇒ 退出码 0 + 末行哨兵 + store/db 都写入新值" 0 "${SANDBOX}/in_ok.txt"    0 yes yes normal; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi
if run_case "S2 空输入(EOF) ⇒ 退出码 3 + 零写入"                            0 "${SANDBOX}/in_empty.txt" 3 no  no  normal; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi
if run_case "S3 长度 31 ⇒ 退出码 4 + 零写入"                                 0 "${SANDBOX}/in_short.txt" 4 no  no  normal; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi
if run_case "S4 飞书拒收(code=10014) ⇒ 退出码 5 + 零写入"                    10014 "${SANDBOX}/in_ok.txt" 5 no  no  normal; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi

echo ""
echo "--- 正向用例：db 侧预检（A 边 §46 新增 2.5 步骤 ⇒ 半写缺陷已修）---"
if run_case "S5a db 里没有该 app_id 的行 ⇒ 退出码 6 + 零写入"               0 "${SANDBOX}/in_ok.txt" 6 no no norow;           then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi
if run_case "S5b 该 app_id 的 tenant_id 不符 ⇒ 退出码 6 + 零写入"             0 "${SANDBOX}/in_ok.txt" 6 no no tenant_mismatch; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi
if run_case "S5c 同 app_id 命中两行 ⇒ 退出码 6 + 零写入"                     0 "${SANDBOX}/in_ok.txt" 6 no no dup_rows;         then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi
if run_case "S5d db 文件不存在 ⇒ 退出码 6 + 零写入"                          0 "${SANDBOX}/in_ok.txt" 6 no no nofile;           then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi
if run_case "S5e db 存在但不是 sqlite 库 ⇒ 退出码 6 + 零写入"                0 "${SANDBOX}/in_ok.txt" 6 no no garbage;          then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi
if run_case "S5f db 是合法 sqlite 但没有目标表 ⇒ 退出码 6 + 零写入"        0 "${SANDBOX}/in_ok.txt" 6 no no notable;          then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi
if run_case "S5g store db 是垃圾文件 ⇒ 退出码 6 + 零写入（备份 store 失败分支）" 0 "${SANDBOX}/in_ok.txt" 6 no no garbage_store;  then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi

echo ""
echo "--- S5d 额外断言：预检不得凭空造出 db 文件 ---"
make_fixture nofile
if [ -f "${APP_DB}" ]; then
  bad "S5d fixture 本身就有 db 文件（台架 bug）"
else
  ok "S5d fixture 初始态正确：db 文件不存在"
fi
if run_case "S5d'(复跑) db 不存在 ⇒ 跑完 db 仍不存在" 0 "${SANDBOX}/in_ok.txt" 6 no no nofile; then
  if [ -f "${APP_DB}" ]; then
    bad "★ 预检凭空创建出了 db 文件（半写型副作用）"
  else
    ok "预检零副作用：db 文件始终不存在"
    PASS=$((PASS+1))
  fi
else
  FAIL=$((FAIL+1))
fi

echo ""
echo "--- 负向对照（证明断言不是恒真：故意用错期望，必须被判失败）---"
if run_case "N1 期望退出码写成 0（实为 5）=> 必须失败" 10014 "${SANDBOX}/in_ok.txt" 0 no no normal; then
  bad "负向对照 N1 没被抓住 => 退出码断言是恒真，守卫无效"
else
  ok "负向对照 N1 被正确抓住"
fi
if run_case "N2 期望 store 被改（实为零写入）=> 必须失败" 10014 "${SANDBOX}/in_ok.txt" 5 yes no normal; then
  bad "负向对照 N2 没被抓住 => 零写入断言是恒真，守卫无效"
else
  ok "负向对照 N2 被正确抓住"
fi
if run_case "N3 期望退出码写成 0（实为 6）=> 必须失败" 0 "${SANDBOX}/in_ok.txt" 0 no no norow; then
  bad "负向对照 N3 没被抓住 => db 预检的退出码断言是恒真，守卫无效"
else
  ok "负向对照 N3 被正确抓住"
fi
if run_case "N4 期望 store 被改（实为零写入）=> 必须失败" 0 "${SANDBOX}/in_ok.txt" 6 yes no norow; then
  bad "负向对照 N4 没被抓住 => 预检的零写入断言是恒真，守卫无效"
else
  ok "负向对照 N4 被正确抓住"
fi

echo ""
echo "--- 生产隔离断言 ---"
REAL_BAK_AFTER="$(real_bak_fp "${REAL_BAK}")"
if [ "$REAL_BAK_BEFORE" = "__MISSING__" ] || [ "$REAL_BAK_AFTER" = "__MISSING__" ]; then
  # ★★ B 边 §80.12：**探针失效不许落到通过分支** —— 目录不存在 ⇒ 这条断言**无法判定**
  #   ⇒ 原来这里是 BEFORE=0 / AFTER=0 ⇒ 相等 ⇒ 判绿 ⇒ **恒真**（测不出隔离有没有生效）
  bad "真备份目录不存在（${REAL_BAK}）⇒ 这条断言**无法判定**（不能拿 0=0 当通过）⇒ 请先创建该目录或改判据"
elif [ "$REAL_BAK_BEFORE" = "$REAL_BAK_AFTER" ]; then
  ok "真备份目录零变化（条目+大小指纹一致，$(printf '%s\n' "$REAL_BAK_AFTER" | wc -l | tr -d ' ') 项）"
else
  bad "真备份目录被改动了（${REAL_BAK}）⇒ 台架漏了隔离"
fi

# ★★ B 边 §80.12：**case 数守恒** —— 漏跑一个 case 只会让「通过数」变小，**不会变红**
#   ⇒ 期望值**硬编码**（不是从脚本自身 grep ⇒ 那样删调用时两边同步变 ⇒ 反而恒真）
#   ⇒ ★ 纪律：**新增/删除 case 必须同步改这一行**（宁红勿漏 —— 红了只说明要同步，绿了才说明真跑到了）
#   ⇒ 16 = 12 个正向用例（S1~S5g ＋ S5d 复跑）＋ 4 个负向对照（N1~N4）
EXPECTED_CASES=16
if [ "$CASE_DONE" -eq "$EXPECTED_CASES" ]; then
  ok "case 数守恒（实际执行 ${CASE_DONE} == 期望 ${EXPECTED_CASES}，无漏跑）"
else
  bad "case 数不守恒（实际执行 ${CASE_DONE} != 期望 ${EXPECTED_CASES}）⇒ 有 case 没跑到 ⇒ 断言覆盖不全"
fi
# ★★ B 边 §64：增量口径 —— 只查「本次运行**新增**的」被测脚本 workdir（并发时别人的目录不算我的）
#   ⇒ ★★ B 边 §77 已把 glob 改成**宽度无关**的唯一列举函数 `_sbfb_list()`
#     （`.sbfb.*` 且**排除守卫自己的** `.sbfb-guard.*` ＋ 旧命名空间 `sbfb-e2e.*`）
#   ⇒ ⚠️⚠️ **不要再写死位数**：原来的 `/private/tmp/.sbfb.?????????`（9 个 `?`）
#     与被测脚本模板 `mktemp -d /private/tmp/.sbfb.XXXXXXXX`（**8 位**）不符
#     ⇒ **命中恒为 0** ⇒ 「本次运行零残留」这条断言**恒真** ⇒ 假绿（A 边 §70.2 实测抓到）。
#     ⇒ 唯一的列举口径是 `_sbfb_list()`（宽度无关 ＋ 覆盖历史形状 ＋ 排除守卫自己）——
#        快照与跑后**共用同一个函数** ⇒ 口径永远一致。
_sbfb_list > "$SBFB_AFTER"
LEFTOVER="$(comm -13 "$SBFB_SNAP" "$SBFB_AFTER" 2>/dev/null | wc -l | tr -d ' ')"
if [ "$LEFTOVER" = "0" ]; then
  ok "被测脚本本次运行零残留（宽度无关 glob 增量口径：.sbfb.*（排除 .sbfb-guard.）+ 旧命名空间 sbfb-e2e.*；全局现存 $(wc -l < "$SBFB_AFTER" | tr -d ' ') 个可能来自并发方）"
else
  bad "本次运行新增 ${LEFTOVER} 个被测脚本 workdir（.sbfb.* / sbfb-e2e.*）⇒ 被测脚本 cleanup 未生效"
fi

# ★★ 2026-10-27（B 边 §59）：守卫**自己**的 sandbox 也必须零残留。
#   ⇒ 这条断言存在的意义：曾实测「每次跑净增 1 个 .sbfb-guard.*」（累计 49 个），
#     而旧断言只查 `.sbfb.*` ⇒ **查不到自己的残留 ⇒ 缺陷隐身**。
#   ⇒ ★ 判据 = **实测 cleanup 之后自己的目录还在不在**（`CLEANUP_OK`），
#     **不是**抄跑前快照 —— 抄快照就是恒真断言（§16 Y 第 10/13 条）。
#   ⇒ 因为 cleanup 原本只在 `exit` 时由 trap 触发（那时已无法判定），
#     ⇒ 故在汇总**之前**显式调一次 cleanup，让断言能拿到真值。
cleanup
if [ "${CLEANUP_OK:-no}" = "yes" ]; then
  ok "守卫自身 sandbox 已清理（/private/tmp/.sbfb-guard.* 零残留）"
else
  bad "守卫自身 sandbox 清理失败 ⇒ 残留 /private/tmp/.sbfb-guard.*（cleanup 被 rm shim 静默失效）"
fi

echo ""
echo "=== 汇总：正反用例共 ${PASS} 项通过，${FAIL} 项失败 ==="
if [ "$FAIL" -eq 0 ]; then
  echo "GUARD-EXIT-CODES-OK"
  exit 0
fi
echo "GUARD-EXIT-CODES-FAILED"
exit 1
