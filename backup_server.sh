#!/bin/bash
# solobrave-server.py 秒级备份/回滚
#
# 用法：
#   ./backup_server.sh           备份：把当前 solobrave-server.py 复制为 .solobrave-server.py.bak
#   ./backup_server.sh rollback  回滚：用 .solobrave-server.py.bak 覆盖 solobrave-server.py
#
# 说明：git pre-commit 钩子会在每次提交 solobrave-server.py 时自动备份
# HEAD 版本到 .solobrave-server.py.bak，本脚本用于手动即时快照或回滚。

cd "$(dirname "$0")"
SRC="solobrave-server.py"
BAK=".solobrave-server.py.bak"

if [ "$1" = "rollback" ]; then
    if [ ! -f "$BAK" ]; then
        echo "[错误] 备份文件 $BAK 不存在，无法回滚"
        exit 1
    fi
    cp "$BAK" "$SRC"
    echo "[回滚完成] $SRC 已恢复为 $BAK 的内容"
    exit 0
fi

if [ ! -f "$SRC" ]; then
    echo "[错误] $SRC 不存在"
    exit 1
fi
cp "$SRC" "$BAK"
echo "[备份完成] $SRC -> $BAK"
