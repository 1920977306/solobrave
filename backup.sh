#!/bin/bash
# SoloBrave 自动备份脚本
# 建议crontab: 0 3 * * * /Users/qichen/Desktop/solobrave-test/backup.sh

BACKUP_DIR="/Users/qichen/Desktop/solobrave-backups"
DATE=$(date +%Y%m%d)
KEEP_DAYS=7

mkdir -p "$BACKUP_DIR"

# 1. SQLite数据库备份 (在线备份，不锁库)
sqlite3 /Users/qichen/Desktop/solobrave-test/data/solobrave.db ".backup '$BACKUP_DIR/solobrave_db_$DATE.db'"

# 2. OpenClaw配置备份
cp /Users/qichen/.openclaw/openclaw.json "$BACKUP_DIR/openclaw_config_$DATE.json"

# 3. SoloBrave代码快照 (排除data目录)
cd /Users/qichen/Desktop/solobrave-test && git archive --format=tar HEAD > "$BACKUP_DIR/solobrave_code_$DATE.tar"

# 4. 清理超过KEEP_DAYS天的旧备份
find "$BACKUP_DIR" -name "*_$DATE.*" -type f > /dev/null 2>&1
find "$BACKUP_DIR" -mtime +$KEEP_DAYS -type f -delete

echo "[$DATE] Backup completed: DB + config + code → $BACKUP_DIR"
