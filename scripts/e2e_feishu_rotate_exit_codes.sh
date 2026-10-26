#!/bin/bash
# ============================================================================
# 飞书 secret 一条龙脚本 · 退出码契约守卫
#
# 断言什么（依据 AGENTS.md §16 Y 第 9 条 + 交接单 §44.3）：
#   ① 显式 exit N 的业务失败路径 ⇒ 退出码必须真实：3 / 4 / 5
#   ② 成功路径 ⇒ 退出码 0 + 末行哨兵
#   ③ 失败路径 ⇒ 假 store 与假 db 必须零写入
#   ④ 失败路径 ⇒ 末行**不能**出现成功哨兵（否则「哨兵」这个判据本身是恒真的）
#
# ★ 为什么这个台架零生产接触（两条设计，别删）：
#   ① HOME 指向 sandbox ⇒ 脚本的默认备份目录 $HOME/.solobrave-backups 也落在 sandbox
#      ⇒ 连「故意不传 --bak-dir」的负向对照也只写 sandbox，不会往真备份目录丢副本
#   ② --db / --store-db 都指向 sandbox 里的假库 ⇒ 真 store 与真生产 db 零读写
#
# 用法：
#   bash scripts/e2e_feishu_rotate_exit_codes.sh                  # 用默认被测脚本
#   bash scripts/e2e_feishu_rotate_exit_codes.sh --script <path>  # 指定被测脚本
#   bash scripts/e2e_feishu_rotate_exit_codes.sh --strict          # 被测脚本缺失 => exit 1
#
# 被测脚本是交付物、放在 repo 外 ⇒ 本机没装就没有可测对象
#   ⇒ 默认打印 SKIP 并 exit 0（--strict 则 exit 1）
#   ⚠️ SKIP 不等于 PASS：输出里会写明「跳过原因」，不要把 SKIP 当通过
# ============================================================================

# ⚠️ 注意：这里的 HOME 是**真** HOME（下面才被改掉）⇒ 默认被测脚本路径要用真 HOME 取
REAL_HOME="${HOME}"
SCRIPT="${REAL_HOME}/.solobrave-secrets/rotate_feishu_secret_and_db.sh"
STRICT=0
while [ $# -gt 0 ]; do
  case "$1" in
    --script) SCRIPT="${2:-}"; shift 2 ;;
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

APP="cli_aa420db06a78dcc5"
TEN="t_default"
NAME="FEISHU_APPSECRET_T_DEFAULT"
OLD_V='OLDfakeSECRET_t_default_00000AAA'
NEW_V='NEWfakeSECRET_t_default_00000BBB'
# ★ 长度自洽：直接按 NEW_V 截 31 字符 ⇒ 不手数（手数过一次，写成 30 实际 30 ⇒ 守卫自己 exit 9）
BAD_V="${NEW_V:0:31}"                                # 31 字符 ⇒ 触发长度闸
[ "${#OLD_V}" -eq 32 ] || { echo "fixture 常量长度 ${#OLD_V} != 32" >&2; exit 9; }
[ "${#NEW_V}" -eq 32 ] || { echo "fixture 常量长度 ${#NEW_V} != 32" >&2; exit 9; }
[ "${#BAD_V}" -eq 31 ] || { echo "fixture 常量长度 ${#BAD_V} != 31" >&2; exit 9; }

SANDBOX="$(mktemp -d /private/tmp/.sbfb-guard.XXXXXXXX)"
cleanup() {
  [ -n "${SANDBOX:-}" ] && [ -d "${SANDBOX}" ] && rm -rf "${SANDBOX}" 2>/dev/null
}
trap cleanup EXIT

# ★ HOME 改到 sandbox ⇒ 脚本默认备份目录随之进 sandbox
export HOME="${SANDBOX}/home"
mkdir -p "${HOME}" "${SANDBOX}/bin" "${SANDBOX}/bak"
REAL_BAK="${REAL_HOME}/.solobrave-backups"
REAL_BAK_BEFORE=0
if [ -d "${REAL_BAK}" ]; then
  REAL_BAK_BEFORE="$(ls "${REAL_BAK}" | wc -l | tr -d ' ')"
fi
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

# ----------------------------------------------------------------- fixture
reset_fixture() {
  rm -f "${APP_DB}" "${APP_DB}-wal" "${APP_DB}-shm" "${SBFB_STORE}"
  python3 - "$APP_DB" "$OLD_V" "$APP" "$TEN" <<'PY'
import sqlite3, sys
db, old, app, ten = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
con = sqlite3.connect(db)
con.execute("PRAGMA journal_mode=WAL")
con.execute("""CREATE TABLE tenant_feishu_config(
  tenant_id TEXT PRIMARY KEY, app_id TEXT, app_secret TEXT, status TEXT,
  error TEXT, bound_at TEXT, updated_at INTEGER, route_agent_id TEXT)""")
con.execute("INSERT INTO tenant_feishu_config VALUES(?,?,?,'routed','','2026-01-01',111,'agent_x')", (ten, app, old))
con.commit(); con.close()
PY
  python3 - "$SBFB_STORE" "$OLD_V" <<'PY'
import sqlite3, sys
db, old = sys.argv[1], sys.argv[2]
con = sqlite3.connect(db)
con.execute("""CREATE TABLE secret_store_entries(
  scope_kind TEXT, scope_id TEXT, name TEXT, value TEXT, kind TEXT,
  created_at_ms INTEGER, updated_at_ms INTEGER, updated_by TEXT,
  deleted_at_ms INTEGER, allowed_hosts TEXT)""")
con.execute("INSERT INTO secret_store_entries VALUES('team','','FEISHU_APPSECRET_T_DEFAULT',?,'secret',0,0,'cli',NULL,NULL)", (old,))
con.commit(); con.close()
PY
  # ★ 造完库必须显式删掉 -wal/-shm：SQLite 关闭时只 checkpoint，不删 sidecar
  #   ⇒ 不删就不是「WAL 且无 sidecar」状态（生产同款条件就假了）
  rm -f "${APP_DB}-wal" "${APP_DB}-shm"
}

# 读假 store / 假 db 里的 secret 指纹（只输出指纹，不输出明文）
read_store_fp() {
  python3 - "$SBFB_STORE" "$NAME" <<'PY'
import hashlib, sqlite3, sys
con = sqlite3.connect(sys.argv[1])
row = con.execute("SELECT value FROM secret_store_entries WHERE name=?", (sys.argv[2],)).fetchone()
con.close()
print(hashlib.md5(str(row[0]).strip().encode()).hexdigest()[:12] if row else "MISSING")
PY
}
read_db_fp() {
  python3 - "$APP_DB" "$APP" <<'PY'
import hashlib, sqlite3, sys
con = sqlite3.connect(sys.argv[1])
row = con.execute("SELECT app_secret FROM tenant_feishu_config WHERE app_id=?", (sys.argv[2],)).fetchone()
con.close()
print(hashlib.md5(str(row[0]).strip().encode()).hexdigest()[:12] if row else "MISSING")
PY
}
fp_of() { python3 -c "import hashlib,sys;print(hashlib.md5(sys.argv[1].encode()).hexdigest()[:12])" "$1"; }
OLD_FP="$(fp_of "$OLD_V")"
NEW_FP="$(fp_of "$NEW_V")"

PASS=0
FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL+1)); echo "  ❌ $1"; }

# ------------------------------------------------------------------ 场景跑
# run_case <名称> <stub_code> <输入文件> <期望退出码> <期望store是否变> <期望db是否变>
# 返回 0 = 全部符合预期；1 = 有不符项
run_case() {
  local name="$1" stub_code="$2" infile="$3" exp_rc="$4" exp_store="$5" exp_db="$6"
  local rc out store_fp db_fp sentinel
  reset_fixture
  STUB_CODE="$stub_code" bash "$SCRIPT" "$NAME" \
      --db "$APP_DB" --store-db "$SBFB_STORE" --bak-dir "$SANDBOX/bak" \
      < "$infile" > "${SANDBOX}/out.txt" 2>&1
  rc=$?                       # ★ 直接取，不经过管道（管道会洗掉退出码）
  out="${SANDBOX}/out.txt"
  store_fp="$(read_store_fp)"
  db_fp="$(read_db_fp)"
  if grep -q '哨兵：全部步骤完成' "$out"; then sentinel="yes"; else sentinel="no"; fi

  local bad_items=""
  [ "$rc" = "$exp_rc" ] || bad_items="${bad_items} 退出码(实得 ${rc} 期望 ${exp_rc});"
  if [ "$exp_rc" = "0" ]; then
    [ "$sentinel" = "yes" ] || bad_items="${bad_items} 成功路径缺末行哨兵;"
  else
    [ "$sentinel" = "no" ] || bad_items="${bad_items} 失败路径竟打了成功哨兵;"
  fi
  if [ "$exp_store" = "yes" ]; then
    [ "$store_fp" = "$NEW_FP" ] || bad_items="${bad_items} store 指纹(实得 ${store_fp} 期望 ${NEW_FP});"
  else
    [ "$store_fp" = "$OLD_FP" ] || bad_items="${bad_items} store 被改(实得 ${store_fp} 期望旧值 ${OLD_FP});"
  fi
  if [ "$exp_db" = "yes" ]; then
    [ "$db_fp" = "$NEW_FP" ] || bad_items="${bad_items} db 指纹(实得 ${db_fp} 期望 ${NEW_FP});"
  else
    [ "$db_fp" = "$OLD_FP" ] || bad_items="${bad_items} db 被改(实得 ${db_fp} 期望旧值 ${OLD_FP});"
  fi

  if [ -n "$bad_items" ]; then
    echo "  ❌ ${name}"
    echo "${bad_items}" | sed 's/^/       - /'
    return 1
  fi
  echo "  ✅ ${name}（退出码 ${rc}；store/db 状态符合预期；哨兵 ${sentinel}）"
  return 0
}

# ------------------------------------------------------------------ 正式用例
echo "=== 飞书一条龙脚本 · 退出码契约守卫 ==="
echo "被测脚本: ${SCRIPT}"
echo ""
printf '%s' "$NEW_V"  > "${SANDBOX}/in_ok.txt"
printf '%s' "$BAD_V"  > "${SANDBOX}/in_short.txt"
: >                      "${SANDBOX}/in_empty.txt"

echo "--- 正向用例 ---"
if run_case "S1 成功：code=0 ⇒ 退出码 0 + 末行哨兵 + store/db 都写入新值" 0 "${SANDBOX}/in_ok.txt"    0 yes yes; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi
if run_case "S2 空输入(EOF) ⇒ 退出码 3 + 零写入"                            0 "${SANDBOX}/in_empty.txt" 3 no  no;  then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi
if run_case "S3 长度 31 ⇒ 退出码 4 + 零写入"                                 0 "${SANDBOX}/in_short.txt" 4 no  no;  then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi
if run_case "S4 飞书拒收(code=10014) ⇒ 退出码 5 + 零写入"                    10014 "${SANDBOX}/in_ok.txt" 5 no  no; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi

echo ""
echo "--- 负向对照（证明断言不是恒真：故意用错期望，必须被判失败）---"
if run_case "N1 期望退出码写成 0（实为 5）=> 必须失败" 10014 "${SANDBOX}/in_ok.txt" 0 no no; then
  bad "负向对照 N1 没被抓住 => 退出码断言是恒真，守卫无效"
else
  ok "负向对照 N1 被正确抓住"
fi
if run_case "N2 期望 store 被改（实为零写入）=> 必须失败" 10014 "${SANDBOX}/in_ok.txt" 5 yes no; then
  bad "负向对照 N2 没被抓住 => 零写入断言是恒真，守卫无效"
else
  ok "负向对照 N2 被正确抓住"
fi

echo ""
echo "--- 生产隔离断言 ---"
REAL_BAK_AFTER=0
if [ -d "${REAL_BAK}" ]; then
  REAL_BAK_AFTER="$(ls "${REAL_BAK}" | wc -l | tr -d ' ')"
fi
if [ "$REAL_BAK_BEFORE" = "$REAL_BAK_AFTER" ]; then
  ok "真备份目录零增长（${REAL_BAK_BEFORE} -> ${REAL_BAK_AFTER}）"
else
  bad "真备份目录增长了（${REAL_BAK_BEFORE} -> ${REAL_BAK_AFTER}）⇒ 台架漏了隔离"
fi
LEFTOVER="$(ls -d /private/tmp/.sbfb.* 2>/dev/null | wc -l | tr -d ' ')"
if [ "$LEFTOVER" = "0" ]; then
  ok "被测脚本的 /private/tmp/.sbfb.* 零残留"
else
  bad "残留 ${LEFTOVER} 个 /private/tmp/.sbfb.* 工作目录"
fi

echo ""
echo "=== 汇总：正反用例共 ${PASS} 项通过，${FAIL} 项失败 ==="
if [ "$FAIL" -eq 0 ]; then
  echo "GUARD-EXIT-CODES-OK"
  exit 0
fi
echo "GUARD-EXIT-CODES-FAILED"
exit 1
