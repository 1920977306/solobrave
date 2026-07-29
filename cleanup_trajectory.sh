#!/bin/bash
# Trajectory日志清理脚本
# 保留30天，7天前压缩
# 建议crontab: 0 4 * * * /Users/qichen/Desktop/solobrave-test/cleanup_trajectory.sh

AGENTS_DIR="/Users/qichen/.openclaw/agents"
COMPRESS_DAYS=7
DELETE_DAYS=30

# 压缩7天前的trajectory文件
find "$AGENTS_DIR" -name "*.trajectory.jsonl" -mtime +$COMPRESS_DAYS -not -name "*.gz" -exec gzip {} \;

# 删除30天前的已压缩文件
find "$AGENTS_DIR" -name "*.trajectory.jsonl.gz" -mtime +$DELETE_DAYS -delete

echo "[$(date +%Y%m%d)] Trajectory cleanup done"
