#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# 一次性 GitHub PAT 推送验证脚本
#
# 用法: bash scripts/verify_github_pat.sh
# 前提: 老大已把 PAT 写入 ~/.solobrave_github_pat (umask 077, 600 权限)
#
# 行为:
#   1. 读 PAT（只进内存，不 echo、不进日志、不进 git config）
#   2. 用 PAT 走一次真实 push 到 origin/dev（验证写权限，不只是读权限）
#   3. 验证完立即删除 PAT 文件
#   4. 不改动任何 git 配置（keychain 里的旧条目保持原样）
#
# ★ 安全: PAT 不写进 ~/.gitconfig、不进 shell history、不进 git 的 credential store
#   —— 只在这一次 push 的进程内存里存在
# ═══════════════════════════════════════════════════════════════════════
set -uo pipefail

PAT_FILE="$HOME/.solobrave_github_pat"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$REPO_ROOT" || exit 1

echo "═══════════════════════════════════════════════════════════════════"
echo " GitHub PAT 推送验证"
echo "═══════════════════════════════════════════════════════════════════"

# ── 1. PAT 文件检查 ────────────────────────────────────────────────
if [ ! -f "$PAT_FILE" ]; then
    echo "❌ 未找到 $PAT_FILE"
    echo ""
    echo "请在本机终端执行（注意替换成真实 token）："
    echo "  umask 077 && printf '%s' '<你的PAT>' > ~/.solobrave_github_pat"
    echo ""
    echo "★ umask 077 保证文件权限 600；<你的PAT> 换成 GitHub 后台刚生成的真 token"
    exit 1
fi

PERMS=$(stat -f "%Lp" "$PAT_FILE" 2>/dev/null || echo "unknown")
echo "✓ 找到 PAT 文件，权限 = $PERMS"
if [ "$PERMS" != "600" ] && [ "$PERMS" != "400" ]; then
    echo "⚠️  权限不是 600/400，建议先 chmod 600 $PAT_FILE"
fi

PAT=$(cat "$PAT_FILE" 2>/dev/null | tr -d '\r\n')
if [ -z "$PAT" ]; then
    echo "❌ PAT 文件为空"
    exit 1
fi
if [ ${#PAT} -lt 20 ]; then
    echo "❌ PAT 长度异常（${#PAT}），像是没填完整"
    echo "   常见原因: 把 '<你的PAT>' 占位符原样存进去了"
    exit 1
fi
echo "✓ PAT 长度 ${#PAT}（正常）"

# ── 2. 真实 push 验证（写权限，不是只读）─────────────────────────────
echo ""
echo "─── 执行真实 push 验证写权限 ───"
# 用 -c 临时指定 credential helper，PAT 只在这个子进程内存里
# 用法: https://<user>:<token>@github.com/...  —— 但这样 token 会出现在进程列表
#   ⇒ 改用环境变量 GIT_ASKPASS，token 不进 argv、不进进程列表
export GIT_ASKPASS="$REPO_ROOT/scripts/_gh_askpass.sh"
export PAT_VALUE="$PAT"
export GIT_TERMINAL_PROMPT=0

if git push origin HEAD:dev 2>&1 | sed -E 's/(https:\/\/)[^@]*@/\1[REDACTED]@/g'; then
    PUSH_OK=1
else
    PUSH_OK=0
fi

unset PAT_VALUE

# ── 3. 清理 PAT 文件 ────────────────────────────────────────────────
if [ -f "$PAT_FILE" ]; then
    rm -f "$PAT_FILE"
    echo ""
    echo "✓ 已删除 $PAT_FILE（PAT 用完即焚）"
fi

# ── 4. 结果 ────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════════════"
if [ "$PUSH_OK" = "1" ]; then
    echo "✅ 推送成功 —— PAT 有效且有写权限"
    git log --oneline origin/dev -1
else
    echo "❌ 推送失败 —— PAT 无效 / 权限不足 / 仓库路径不对"
    echo ""
    echo "排查顺序:"
    echo "  1. PAT 是否填完整（不是 '<你的PAT>' 占位符）"
    echo "  2. fine-grained token 的 Repository access 是否勾了 solobrave"
    echo "  3. Contents 权限是否 Read and write"
    echo "  4. token 是否已过期"
fi
echo "═══════════════════════════════════════════════════════════════════"
exit "$((1 - PUSH_OK))"
