#!/bin/bash
# SoloBrave 服务健康检查
# 用法：
#   ./health_check.sh [BASE_URL]     默认 http://127.0.0.1:8080
#   服务启动后运行，自动等待服务就绪（最多30秒），然后依次检查核心接口，
#   任一接口返回非 200 即输出明显报警并以非0退出码结束。
#
# 可覆盖的环境变量：
#   HEALTH_ENDPOINTS   要检查的接口（空格分隔，默认 /api/health）
#   HEALTH_READY_PATH  就绪探测用的接口（默认 /api/health）
#
# ★ 2026-10-26 修两处：
#   ① 【$code（ 吞掉状态码】bash 3.2 会把「$VAR 后紧跟的全角字符」并进变量名
#      ⇒ `$code（` 被当成变量 `code（` ⇒ 报警里**看不到真实状态码**（还多出乱码）
#      ⇒ 一律写 ${code}（。同类坑 2026-10-24 已在 scripts/verify_github_pat.sh 踩过。
#   ② 【永久假警报】原默认列表 /api/products /api/tasks /api/agents 三个接口
#      **全部需要鉴权（实测 401）** ⇒ 这脚本在 8080 上**永远报警**、永远非 0 退出
#      ⇒ 真正的健康端点是 /api/health（实测 200）⇒ 默认列表已改。
#      原列表仍可用：HEALTH_ENDPOINTS="/api/products /api/tasks /api/agents" ./health_check.sh

BASE_URL="${1:-http://127.0.0.1:8080}"
ENDPOINTS="${HEALTH_ENDPOINTS:-/api/health}"
READY_PATH="${HEALTH_READY_PATH:-/api/health}"
WAIT_SECONDS=30

echo "== SoloBrave 健康检查: $BASE_URL =="

# 等待服务就绪（任一接口有响应即认为已启动）
ready=0
for i in $(seq 1 $WAIT_SECONDS); do
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 "$BASE_URL$READY_PATH" 2>/dev/null)
    if [ "$code" != "000" ]; then
        ready=1
        break
    fi
    sleep 1
done
if [ "$ready" != "1" ]; then
    echo ""
    echo "########################################################"
    echo "#  [报警] 服务在 ${WAIT_SECONDS}s 内未启动，无法连接 $BASE_URL"
    echo "########################################################"
    exit 2
fi

fail=0
for ep in $ENDPOINTS; do
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$BASE_URL$ep" 2>/dev/null)
    if [ "$code" = "200" ]; then
        echo "[OK]   $ep -> 200"
    else
        echo ""
        echo "########################################################"
        echo "#  [报警] $ep 返回 ${code}（期望 200），服务异常！"
        echo "########################################################"
        fail=1
    fi
done

echo ""
if [ "$fail" = "0" ]; then
    echo "== 全部接口正常 =="
else
    echo "== 存在异常接口，请检查服务日志！ =="
fi
exit $fail
