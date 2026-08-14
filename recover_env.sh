#!/usr/bin/env bash
# 8081 崩溃时从 8080 生产环境恢复测试环境
# 用法: bash recover_env.sh

set -e

TEST_DIR=~/Developer/solubrave-test
PROD_DIR=~/Developer/solubrave-prod
PYTHON=/Library/Developer/CommandLineTools/usr/bin/python3

echo "==> 1. 停止 8081 进程"
PID=$(lsof -ti:8081 || true)
if [ -n "$PID" ]; then
    kill $PID
    sleep 1
    echo "    已停止 PID: $PID"
else
    echo "    8081 无进程运行"
fi

echo "==> 2. 从 prod 恢复数据库"
# 先清除 test 侧残留的 WAL 伴随文件，避免与 prod 的 db 混用
rm -f "$TEST_DIR/data/solubrave.db-wal" "$TEST_DIR/data/solubrave.db-shm"
cp -v "$PROD_DIR/data/solubrave.db" "$TEST_DIR/data/solubrave.db"
for ext in -wal -shm; do
    if [ -f "$PROD_DIR/data/solubrave.db$ext" ]; then
        cp -v "$PROD_DIR/data/solubrave.db$ext" "$TEST_DIR/data/solubrave.db$ext"
    fi
done

echo "==> 3. 启动 8081 (test)"
cd "$TEST_DIR"
# 环境变量从 test.env 加载（含密钥，不进 git）
if [ ! -f "$TEST_DIR/test.env" ]; then
    echo "❌ 缺少 $TEST_DIR/test.env，请从仓库根目录复制 test.env 到该路径"
    exit 1
fi
set -a
source "$TEST_DIR/test.env"
set +a
nohup "$PYTHON" solobrave-server.py --data data 8081 > server.log 2>&1 &

echo "==> 4. 校验启动结果"
sleep 2
if lsof -ti:8081 > /dev/null; then
    echo "✅ 恢复完成，8081 已启动 (PID: $(lsof -ti:8081))"
else
    echo "❌ 8081 启动失败，最近日志:"
    tail -20 server.log
    exit 1
fi
