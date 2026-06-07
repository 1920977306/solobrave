#!/usr/bin/env bash
# 抖音解析接口完整测试脚本
# 用法: ./test_api_douyin.sh [BASE_URL] [TOKEN]
# 例:   ./test_api_douyin.sh http://localhost:8080 eyJhbG...

set -euo pipefail

BASE_URL="${1:-http://localhost:8080}"
TOKEN="${2:-}"
TEST_URL="https://v.douyin.com/5msCxiOndsU/"
VIDEO_URL="https://aweme.snssdk.com/aweme/v1/play/?video_id=v0d00fg10000d8cgj97og65jl3556650&ratio=720p&line=0"

PASS=0
FAIL=0

pass() { ((PASS++)); echo "  [PASS] $1"; }
fail() { ((FAIL++)); echo "  [FAIL] $1"; }

# 颜色
RED='\033[0;31m'
GRN='\033[0;32m'
RST='\033[0m'

req() {
    local method="$1" path="$2" headers="${3:-}" body="${4:-}"
    local opts=("$BASE_URL$path" -s -w "\n%{http_code}")
    if [[ -n "$headers" ]]; then
        IFS='|' read -ra hs <<< "$headers"
        for h in "${hs[@]}"; do opts+=(-H "$h"); done
    fi
    if [[ -n "$body" ]]; then opts+=(-d "$body"); fi
    curl "${opts[@]}" 2>/dev/null || echo -e "\n000"
}

print_section() {
    echo ""
    echo "========================================"
    echo "$1"
    echo "========================================"
}

# ─── 认证 ──────────────────────────────────────────
if [[ -z "$TOKEN" ]]; then
    print_section "[认证] 尝试登录获取 Token"
    read -rp "用户名: " USERNAME
    read -rsp "密码: " PASSWORD; echo
    resp=$(req POST /api/auth/login \
        "Content-Type: application/json" \
        "{\"username\":\"$USERNAME\",\"password\":\"$PASSWORD\"}")
    code=$(echo "$resp" | tail -1)
    body=$(echo "$resp" | sed '$d')
    TOKEN=$(echo "$body" | grep -oP '"token"\s*:\s*"\K[^"]+' || true)
    if [[ -n "$TOKEN" ]]; then
        pass "登录成功，获取到 Token"
        echo "  Token: ${TOKEN:0:30}..."
    else
        fail "登录失败: $body"
        echo "  跳过认证相关测试"
    fi
else
    pass "使用传入的 Token"
fi

AUTH_H="Authorization: Bearer $TOKEN|Content-Type: application/json"

# ─── 测试1: 无认证 ─────────────────────────────────
print_section "[TEST 1/6] 无认证 → 401"
resp=$(req POST /api/douyin/parse \
    "Content-Type: application/json" \
    "{\"url\":\"$TEST_URL\"}")
code=$(echo "$resp" | tail -1)
body=$(echo "$resp" | sed '$d')
if [[ "$code" == "401" ]]; then pass "HTTP $code"; else fail "期望401, 得$code"; fi
if echo "$body" | grep -q '"success".*false'; then pass "success=false"; else fail "缺少 success=false"; fi
if echo "$body" | grep -q '"error"'; then pass "有 error 字段"; else fail "缺少 error 字段"; fi

# ─── 测试2: 错误 Token ─────────────────────────────
print_section "[TEST 2/6] 错误 Token → 401"
resp=$(req POST /api/douyin/parse \
    "Authorization: Bearer invalid_token|Content-Type: application/json" \
    "{\"url\":\"$TEST_URL\"}")
code=$(echo "$resp" | tail -1)
body=$(echo "$resp" | sed '$d')
if [[ "$code" == "401" ]]; then pass "HTTP $code"; else fail "期望401, 得$code"; fi
if echo "$body" | grep -q '"success".*false'; then pass "success=false"; else fail "缺少 success=false"; fi

# ─── 测试3: 缺少 url 参数 ──────────────────────────
print_section "[TEST 3/6] 缺少 url → 400"
resp=$(req POST /api/douyin/parse "$AUTH_H" "{}")
code=$(echo "$resp" | tail -1)
body=$(echo "$resp" | sed '$d')
if [[ "$code" == "400" ]]; then pass "HTTP $code"; else fail "期望400, 得$code"; fi
if echo "$body" | grep -q '"success".*false'; then pass "success=false"; else fail "缺少 success=false"; fi

# ─── 测试4: 正常解析（不转录）───────────────────────
print_section "[TEST 4/6] 正常解析 transcribe=false → 200"
resp=$(req POST /api/douyin/parse "$AUTH_H" "{\"url\":\"$TEST_URL\",\"transcribe\":false}")
code=$(echo "$resp" | tail -1)
body=$(echo "$resp" | sed '$d')
if [[ "$code" == "200" ]]; then pass "HTTP $code"; else fail "期望200, 得$code"; fi
if echo "$body" | grep -q '"success".*true'; then pass "success=true"; else fail "缺少 success=true"; fi
if echo "$body" | grep -q '"video_info"'; then pass "有 video_info"; else fail "缺少 video_info"; fi
if echo "$body" | grep -q '"video_id"'; then pass "有 video_id"; else fail "缺少 video_id"; fi
if echo "$body" | grep -q '"stats"'; then pass "有 stats"; else fail "缺少 stats"; fi
if echo "$body" | grep -q '"transcribed".*false'; then pass "transcribed=false"; else fail "transcribed 不为 false"; fi

# 提取 video_id 和 video_url 供后续测试使用
VIDEO_ID=$(echo "$body" | grep -oP '"video_id"\s*:\s*"\K[^"]+' | head -1 || true)
EXTRACTED_VIDEO_URL=$(echo "$body" | grep -oP '"video_url"\s*:\s*"\K[^"]+' | head -1 || true)
echo "  video_id: ${VIDEO_ID:-N/A}"
echo "  video_url: ${EXTRACTED_VIDEO_URL:0:80}..."

# ─── 测试5: 转录模式（无 ffmpeg 或 无 key）─────────
print_section "[TEST 5/6] 转录模式 transcribe=true → 200（可能无转录结果）"
resp=$(req POST /api/douyin/parse "$AUTH_H" "{\"url\":\"$TEST_URL\",\"transcribe\":true}")
code=$(echo "$resp" | tail -1)
body=$(echo "$resp" | sed '$d')
if [[ "$code" == "200" ]]; then pass "HTTP $code"; else fail "期望200, 得$code"; fi
if echo "$body" | grep -q '"success".*true'; then pass "success=true"; else fail "缺少 success=true"; fi
if echo "$body" | grep -q '"transcribe_error"'; then pass "有 transcribe_error 字段"; else fail "缺少 transcribe_error"; fi
# 检查 transcribe_error 是否有内容（无ffmpeg或无key时应有提示）
err=$(echo "$body" | grep -oP '"transcribe_error"\s*:\s*"\K[^"]*' | head -1 || true)
if [[ -n "$err" ]]; then
    pass "transcribe_error 有值: $err"
else
    pass "transcribe_error 为空（ffmpeg+key都满足时正常）"
fi

# ─── 测试6: /api/douyin/transcribe（直接转录）──────
print_section "[TEST 6/6] 直接转录 /api/douyin/transcribe"
if [[ -n "$EXTRACTED_VIDEO_URL" ]]; then
    resp=$(req POST /api/douyin/transcribe "$AUTH_H" \
        "{\"video_url\":\"$EXTRACTED_VIDEO_URL\"}")
    code=$(echo "$resp" | tail -1)
    body=$(echo "$resp" | sed '$d')
    echo "  HTTP $code"
    # 可能 200(成功), 400(缺key), 503(无ffmpeg), 502(提取失败)
    if [[ "$code" == "200" ]]; then
        pass "转录成功"
        if echo "$body" | grep -q '"text"'; then pass "有 text 字段"; else fail "缺少 text"; fi
    elif [[ "$code" == "400" ]]; then
        pass "缺 API Key → 400 (预期)"
    elif [[ "$code" == "503" ]]; then
        pass "无 ffmpeg → 503 (预期)"
    else
        fail "意外状态码: $code, body: ${body:0:100}"
    fi
else
    echo "  跳过（未获取到 video_url）"
fi

# ─── 汇总 ──────────────────────────────────────────
print_section "测试汇总"
echo -e "  ${GRN}PASS: $PASS${RST}"
if [[ $FAIL -gt 0 ]]; then
    echo -e "  ${RED}FAIL: $FAIL${RST}"
    exit 1
else
    echo -e "  ${GRN}全部通过${RST}"
fi
