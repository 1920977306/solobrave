#!/usr/bin/env bash
# GitHub 凭据注入器（供 scripts/verify_github_pat.sh 调用）
#
# ★ 为什么用 GIT_ASKPASS 而不是 URL 里带 token:
#   URL 形式 (https://user:token@github.com/...) 会让 token 出现在进程 argv,
#   `ps aux` 任何人都能看到 ⇒ 泄密。
#   askpass 形式 token 只在环境变量里, 不进 argv、不进 shell history。
#
# ★ Git 会按顺序问两个问题:
#   "Username for 'https://github.com': "      → 要返回用户名
#   "Password for 'https://1920977306@...': "  → 要返回 PAT
#   同一个脚本要按 prompt 内容分流, 否则会把 PAT 当用户名返回(认证必失败)。
#
# 用法: 只由 verify_github_pat.sh 调用, 不要单独跑
prompt="${1:-}"

case "$prompt" in
  *[Uu]sername*)
    printf '%s\n' "${GITHUB_USERNAME:-1920977306}"
    ;;
  *)
    printf '%s\n' "${PAT_VALUE:-}"
    ;;
esac
