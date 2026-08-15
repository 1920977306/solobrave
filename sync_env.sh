#!/usr/bin/env bash
# 同步 8081 测试环境 -> 8080 生产环境
# 用法: bash sync_env.sh

set -e

TEST_DIR=~/Developer/solobrave-test
PROD_DIR=~/Developer/solobrave-prod
PYTHON=/Library/Developer/CommandLineTools/usr/bin/python3

FILES=(
    solobrave-server.py
    memory_pipeline.py
    knowledge_service.py
    index.html
    data/agents.json
    data/users.json
    data/groups.json
    data/solobrave.db
)

echo "==> 1. 停止 8080 进程"
PID=$(lsof -ti:8080 || true)
if [ -n "$PID" ]; then
    kill $PID
    sleep 1
    echo "    已停止 PID: $PID"
else
    echo "    8080 无进程运行"
fi

echo "==> 2. 复制文件 test -> prod"
for f in "${FILES[@]}"; do
    cp -v "$TEST_DIR/$f" "$PROD_DIR/$f"
done
# SQLite WAL 模式下的伴随文件（存在则一起复制，否则删除目标侧残留，避免新旧 WAL 混用）
for ext in -wal -shm; do
    if [ -f "$TEST_DIR/data/solobrave.db$ext" ]; then
        cp -v "$TEST_DIR/data/solobrave.db$ext" "$PROD_DIR/data/solobrave.db$ext"
    else
        rm -f "$PROD_DIR/data/solobrave.db$ext"
    fi
done

echo "==> 3. 启动 8080 (prod)"
cd "$PROD_DIR"
# 环境变量从 prod.env 加载（含密钥，不进 git）
if [ ! -f "$PROD_DIR/prod.env" ]; then
    echo "❌ 缺少 $PROD_DIR/prod.env，请从仓库根目录复制 prod.env 到该路径"
    exit 1
fi
set -a
source "$PROD_DIR/prod.env"
set +a
nohup "$PYTHON" solobrave-server.py --data data 8080 > server.log 2>&1 &

echo "==> 4. 校验启动结果"
sleep 2
if lsof -ti:8080 > /dev/null; then
    echo "✅ 同步完成，8080 已启动 (PID: $(lsof -ti:8080))"
else
    echo "❌ 8080 启动失败，最近日志:"
    tail -20 server.log
    exit 1
fi
