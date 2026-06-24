# 抖音解析模块回归测试报告

**测试时间**: 2026-06-24 14:10:57
**测试链接**: `https://v.douyin.com/jV_u-PVtvEI/`
**视频ID**: 7645153121840539506

## 汇总

| 结果 | 数量 |
|------|------|
| PASS | 31 |
| FAIL | 1 |
| SKIP | 0 |

**结论: 有失败项，需修复**

## 1-解析模块

| 测试项 | 结果 | 详情 |
|--------|------|------|
| success=True | PASS PASS | - |
| video_id=7645153121840539506 | PASS PASS | - |
| title=coolchap设计师款凉鞋 #凉鞋 #沙滩凉鞋 #凉鞋女 ... | PASS PASS | - |
| author=李馒头 | PASS PASS | - |
| duration=50.5s | PASS PASS | - |
| stats={"digg_count": 11, "comment_count": 0, "share_count": 2, "play_count": 0, "collect_count": 3} | PASS PASS | - |
| transcribed=False (transcribe=False) | PASS PASS | - |
| transcribe_error为空 | PASS PASS | - |
| quick.success=True | PASS PASS | - |
| context含[系统自动注入] | PASS PASS | - |
## 2-转录降级

| 测试项 | 结果 | 详情 |
|--------|------|------|
| transcribe=True无key: HTTP200 | PASS PASS | - |
| transcribed=False | PASS PASS | - |
| transcribe_error='缺少 API Key，无法进行语音转文字' | PASS PASS | - |
| ffmpeg存在=False | PASS PASS | - |
| 无ffmpeg时正确降级 | PASS PASS | - |
## 3-接口

| 测试项 | 结果 | 详情 |
|--------|------|------|
| url字段 → HTTP 200 | PASS PASS | - |
| 返回含video_info | PASS PASS | - |
| 返回含transcribed | PASS PASS | - |
| text字段 | FAIL FAIL | code=422 |
| text无链接 → HTTP 400 | PASS PASS | code=400 |
| error含"未检测到" | PASS PASS | - |
## 4-错误场景

| 测试项 | 结果 | 详情 |
|--------|------|------|
| 无认证 → 401 | PASS PASS | code=401 |
| 无认证 success=false | PASS PASS | - |
| 错误Token → 401 | PASS PASS | - |
| 错误Token success=false | PASS PASS | - |
| 缺参数 → 400 | PASS PASS | - |
| 缺参数 success=false | PASS PASS | - |
| 失效链接 → 422 | PASS PASS | - |
| 失效链接 success=false | PASS PASS | - |
| 失效链接有error字段 | PASS PASS | - |
## 5-transcribe

| 测试项 | 结果 | 详情 |
|--------|------|------|
| 缺api_key → 400 | PASS PASS | code=400 |
| success=false | PASS PASS | - |
