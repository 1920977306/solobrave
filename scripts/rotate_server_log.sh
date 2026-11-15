#!/bin/bash
# server.log 轮转（交接单 §162 防复发③，2026-11-15）。
# copytruncate 式：进程持有 fd 不断流（launchd stdout 重定向，mv 不释放空间）。
# 阈值 4GB，保留最近 3 份压缩归档。日志膨胀实证：Helen 泄漏单日贡献 ~1.3GB。
set -euo pipefail

LOG_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$LOG_DIR/server.log"
MAX_BYTES=$((4 * 1024 * 1024 * 1024))
KEEP=3

[ -f "$LOG" ] || exit 0
SIZE=$(stat -f%z "$LOG")
if [ "$SIZE" -lt "$MAX_BYTES" ]; then
    exit 0
fi

TS=$(date +%Y%m%d-%H%M%S)
gzip -c "$LOG" > "$LOG.$TS.gz.tmp"
mv "$LOG.$TS.gz.tmp" "$LOG.$TS.gz"
: > "$LOG"   # copytruncate：清空原文件，fd 保持有效

# 只留最近 KEEP 份
ls -1t "$LOG".*.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs rm -f || true

echo "rotated $LOG (${SIZE} bytes) -> $LOG.$TS.gz"
