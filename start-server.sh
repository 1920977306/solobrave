#!/usr/bin/env bash
# SoloBrave 启动脚本（测试/生产通用）
# 自动注入硅基流动 embedding API key，避免手动配置

set -e

export EMBEDDING_OVERRIDE_PROVIDER=siliconflow
export EMBEDDING_OVERRIDE_API_KEY=sk-fvhyjaorelewvlykusqopvrrgygcrfubapyyljllikjsilsx

# 如需指定数据目录，可在此追加 --data data
python3 solobrave-server.py 8081
