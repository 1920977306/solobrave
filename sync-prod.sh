#!/bin/bash
set -e

PROD_DIR="$HOME/Desktop/solobrave-prod"
TEST_DIR="$HOME/Desktop/solobrave-test"
BACKUP_DIR="$HOME/Desktop/solobrave-backups/pre-sync"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

echo "=== 1. 备份 prod 数据库 ==="
mkdir -p "$BACKUP_DIR/$TIMESTAMP"
sqlite3 "$PROD_DIR/data/solobrave.db" ".backup '$BACKUP_DIR/$TIMESTAMP/prod_db_backup.db'"
echo "备份完成: $BACKUP_DIR/$TIMESTAMP/prod_db_backup.db"

echo "=== 2. 同步代码（排除数据目录）==="
rsync -av --exclude='data/' --exclude='__pycache__' "$TEST_DIR/" "$PROD_DIR/"

echo "=== 3. 记录版本 ==="
git -C "$TEST_DIR" log -1 --format="%h %s" > "$PROD_DIR/.deployed-version"
echo "版本: $(cat $PROD_DIR/.deployed-version)"

echo "=== 4. 同步完成 ==="
echo "请手动重启 prod 服务:"
echo "  lsof -ti:8080 | xargs kill -9; cd ~/Desktop/solobrave-prod && python3 solobrave-server.py 8080"
