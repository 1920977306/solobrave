#!/bin/bash
# SoloBrave 服务健康检查
# 用法：
#   ./health_check.sh [BASE_URL]     默认 http://127.0.0.1:8080
#   服务启动后运行，自动等待服务就绪（最多30秒），然后依次检查核心接口，
#   任一接口返回非 200 即输出明显报警并以非0退出码结束。

BASE_URL="${1:-http://127.0.0.1:8080}"
ENDPOINTS="/api/products /api/tasks /api/agents"
WAIT_SECONDS=30

echo "== SoloBrave 健康检查: $BASE_URL =="

# 等待服务就绪（任一接口有响应即认为已启动）
ready=0
for i in $(seq 1 $WAIT_SECONDS); do
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 "$BASE_URL/api/products" 2>/dev/null)
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
        echo "#  [报警] $ep 返回 $code（期望 200），服务异常！"
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
