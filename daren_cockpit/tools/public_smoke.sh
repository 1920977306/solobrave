#!/bin/bash
# 公网驾驶舱鉴权冒烟（隧道域名变更后复用）
# 用法: public_smoke.sh [域名]      公网冒烟（默认，自动从洛链 API 拉当前隧道域名）
#       public_smoke.sh --local      本地直连 7188 冒烟（跳过 TLS 预检，用于分段定位鉴权层）
#
# 安全: 本脚本不内嵌 token，从环境变量或已 gitignore 的 frpc.toml 读取，
#       因此本文件可以安全入库。切勿在此写死 token（曾踩：git add . 会带进仓库）。
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
FRPC_TOML="${FRPC_TOML:-$ROOT/frpc.toml}"

TUNNEL_ID="${TUNNEL_ID:-31718}"
KEYS_FILE="${KEYS_FILE:-$ROOT/cockpit_keys.json}"
LOCAL_PORT="${SMOKE_LOCAL_PORT:-7188}"

LOCAL=0
DOMAIN_ARG=""
for a in "$@"; do
  case "$a" in
    --local) LOCAL=1 ;;
    -h|--help)
      echo "用法: public_smoke.sh [域名] | public_smoke.sh --local"
      echo "  --local   直连 127.0.0.1:${LOCAL_PORT} 验鉴权层（不验 TLS）"
      echo "环境变量: LOLIA_TOKEN / FRPC_TOML / TUNNEL_ID / KEYS_FILE / SMOKE_LOCAL_PORT"
      exit 0 ;;
    *) DOMAIN_ARG="$a" ;;
  esac
done

if [ "$LOCAL" -eq 0 ]; then
  if [ -n "${LOLIA_TOKEN:-}" ]; then
    TOKEN="$LOLIA_TOKEN"
  elif [ -f "$FRPC_TOML" ]; then
    TOKEN=$(sed -n "s/.*token *= *['\"]\\([^'\"]*\\)['\"].*/\\1/p" "$FRPC_TOML" | head -1)
  fi
  [ -n "${TOKEN:-}" ] || { echo "取不到 token：请设 LOLIA_TOKEN，或确保 $FRPC_TOML 存在"; exit 1; }
fi

DOMAIN="$DOMAIN_ARG"
if [ "$LOCAL" -eq 0 ] && [ -z "$DOMAIN" ]; then
  # 服务端下发的配置里 customDomains 可能是 ['a'] 或 ["a", "b"] 或多行数组，
  # 全部域名取第一个非空（冒烟只验一个主域名即可）。
  DOMAIN=$(curl -s --max-time 15 "https://api.lolia.link/api/v1/tunnel/frpc/config?token=${TOKEN}&id=${TUNNEL_ID}" \
    | python3 -c "
import json,sys,base64,re
try:
    c = base64.b64decode(json.load(sys.stdin)['data']['config']).decode()
except Exception:
    sys.exit(0)
m = re.search(r'customDomains\s*=\s*\[(.*?)\]', c, re.S)
if not m:
    sys.exit(0)
doms = re.findall(r'[\"\']([^\"\']+)[\"\']', m.group(1))
print(next((d.strip() for d in doms if d.strip()), ''))
")
fi

if [ "$LOCAL" -eq 1 ]; then
  echo "目标: 本地直连 127.0.0.1:${LOCAL_PORT}（--local：只验鉴权层，跳过 TLS 预检）"
  R=""
  B="http://127.0.0.1:${LOCAL_PORT}"
else
  [ -z "$DOMAIN" ] && { echo "无法获取隧道域名（服务端配置里没解析到 customDomains）"; exit 1; }
  echo "目标域名: $DOMAIN"
  IP=$(dig +short "$DOMAIN" | tail -1)
  echo "解析 IP: ${IP:-解析失败}"
  [ -z "$IP" ] && exit 1
  R="--resolve ${DOMAIN}:443:${IP}"
  B="https://${DOMAIN}"
fi

K_BOSS=$(python3 -c "import json; print(json.load(open('$KEYS_FILE'))['daren_boss'])")
K_TEST=$(python3 -c "import json; print(json.load(open('$KEYS_FILE'))['daren_test2'])")
OPS=$(python3 -c "import json; print(json.load(open('$KEYS_FILE'))['_ops'])")

echo "== 连通性预检 =="
if [ "$LOCAL" -eq 1 ]; then
  echo "  ⏭  --local 模式跳过（本地无 TLS 层）"
else
# 主判据：直接打真实鉴权端点，能不能拿到 HTTP 码。
#
# ⚠️ 不要用 openssl 输出里的 "SSL handshake has read N bytes" 当失败特征 ——
#    成功握手**也会**打印这行（实测 "SSL handshake has read 4858 bytes"）。
#    曾踩：预检据此误报「443 无证书」，实际证书已签好、握手完全正常。
#    失败时的特征是读到的字节数极小（服务器回落成明文 HTTP），但那不该靠字符串猜。
#    现在用「探针能否拿到 HTTP 码」+ 明确的失败串双重判定。
HTTP_TITLE=$(curl -s --max-time 10 "http://${DOMAIN}/" 2>/dev/null | grep -oE '<title>[^<]*</title>' | head -1)
TLS_OUT=$(openssl s_client -connect "${DOMAIN}:443" -servername "$DOMAIN" </dev/null 2>&1)
PROBE_CODE=$(curl -s $R -o /dev/null -w '%{http_code}' --max-time 15 "$B/api/cockpit?t=daren_boss")
if [ "$PROBE_CODE" = "000" ] || echo "$TLS_OUT" | grep -qE "wrong version number|alert protocol version|no peer certificate available"; then
  echo "  ❌ 443 TLS 握手失败 —— 443 没有可用证书"
  echo "$TLS_OUT" | grep -E "wrong version number|alert protocol version" | head -2 | sed 's/^/     /'
  echo "     探针返回: ${PROBE_CODE:-无}   80 端口自检: ${HTTP_TITLE:-（无响应）}  ← type=https 时 80 返 404 是正常表现"
  echo "     排查顺序："
  echo "       1) 面板 → 隧道「高级配置」→ 打开 Auto TLS → 保存"
  echo "       2) 重启隧道让 frpc 拉新配置：launchctl kickstart -k gui/\$(id -u)/com.solobrave.daren-tunnel"
  echo "       3) 确认证书签下来了：ls daren_cockpit/.autotls-cache/ 且 frpc 日志无证书报错"
  echo "          （若目录写不进去 → 检查 plist 的 WorkingDirectory，macOS 根卷只读）"
  echo
  echo "结果: 通过 0 / 失败 0（未执行 —— 443 无证书）"
  exit 2
fi
CERT_INFO=$(echo "$TLS_OUT" | grep -E "subject=|issuer=|Verify return code" | head -3 | tr '\n' ' ')
echo "  ✅ 443 TLS 正常（探针返回 ${PROBE_CODE}，裸访问 403 属预期）"
echo "     ${CERT_INFO}"
fi

pass=0; fail=0
check() { # $1=描述 $2=预期(多个用 | 分隔) $3=实际
  case "|$2|" in *"|$3|"*) pass=$((pass+1)); printf "  ✅ %-42s %s\n" "$1" "$3";;
  *) fail=$((fail+1)); printf "  ❌ %-42s 预期%s 实际%s\n" "$1" "$2" "$3";; esac
}

echo "== 鉴权冒烟 =="
# 鉴权端点是 /api/cockpit，不是 /。
# / 只回静态 HTML 壳（前端无硬编码密钥，ops key 走 sessionStorage 手输），
# 数据一律由前端 fetch('/api/cockpit?t=..&k=..') 取 —— 所以打 / 永远是 200，
# 结构上就不可能验出 403（曾踩：脚本打 / 导致 4 项永远失败）。
check "裸 /api/cockpit?t=daren_boss"     403 "$(curl -s $R -o /dev/null -w '%{http_code}' --max-time 15 "$B/api/cockpit?t=daren_boss")"
check "错误 k"                            403 "$(curl -s $R -o /dev/null -w '%{http_code}' --max-time 15 "$B/api/cockpit?t=daren_boss&k=wrongkey123")"
check "daren_boss 正确 k"                 200 "$(curl -s $R -o /dev/null -w '%{http_code}' --max-time 15 "$B/api/cockpit?t=daren_boss&k=$K_BOSS")"
check "daren_test2 正确 k"                200 "$(curl -s $R -o /dev/null -w '%{http_code}' --max-time 15 "$B/api/cockpit?t=daren_test2&k=$K_TEST")"
check "幽灵租户无 k"                   "403|404" "$(curl -s $R -o /dev/null -w '%{http_code}' --max-time 15 "$B/api/cockpit?t=ghost_tenant_xyz")"
check "幽灵租户假 k"                      403 "$(curl -s $R -o /dev/null -w '%{http_code}' --max-time 15 "$B/api/cockpit?t=ghost_tenant_xyz&k=fakefakefake")"
check "坏 tenant 字符"                    400 "$(curl -s $R -o /dev/null -w '%{http_code}' --max-time 15 "$B/api/cockpit?t=bad-name!")"
check "/api/roster 无 ops 头"             403 "$(curl -s $R -o /dev/null -w '%{http_code}' --max-time 15 "$B/api/roster")"
check "/api/roster 带 ops 头"             200 "$(curl -s $R -o /dev/null -w '%{http_code}' --max-time 15 -H "X-Ops-Key: $OPS" "$B/api/roster")"
echo "== PWA 资源 =="
# 只查 PWA 实际用到、且项目真的实现了的资源。
# sw.js 不查：service worker 本项目从未实现（index.html 无 navigator.serviceWorker.register，
# manifest 无 service_worker 字段，仓库无 sw.js）—— 浏览器根本不会去请求它，
# 查它是凭空造需求。将来真做了 offline 缓存再加回来。
for p in /manifest.webmanifest /icon-192.png /icon-512.png /apple-touch-icon.png; do
  check "$p" 200 "$(curl -s $R -o /dev/null -w '%{http_code}' --max-time 15 "$B$p")"
done

echo
echo "结果: 通过 $pass / 失败 $fail"
[ $fail -eq 0 ]
