"""
Douyin Parser Module
====================
抖音视频解析模块（从 solobrave-server.py 拆分）
功能：
  1. 链接检测与短链解析
  2. Web API 调用与 HTML 降级解析
  3. 结构化数据提取（标题/作者/标签/统计等）
  4. 无水印视频地址提取
  5. 视频下载 + ffmpeg 音频/封面提取
  6. 硅基流动 API 语音转文字
  7. ffprobe 媒体信息获取

仅使用 Python 标准库，零外部依赖。
"""

import base64
import json
import mimetypes
import os
import re
import shutil
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.request
import uuid


# ─── 常量 ───────────────────────────────────────────────

_IPHONE_UA = (
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) '
    'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 '
    'Mobile/15E148 Safari/604.1'
)


# ─── 链接检测与解析 ─────────────────────────────────────

def is_douyin_share_text(text):
    """判断文本是否包含抖音分享链接"""
    if not text:
        return False
    url_pattern = r'https?://(?:v\.douyin\.com/[\w-]+|www\.douyin\.com/video/\d+|douyin\.com/video/\d+)'
    return bool(re.search(url_pattern, text))


def detect_douyin_links(text):
    """从文本中提取抖音链接列表"""
    if not text:
        return []
    url_pattern = r'https?://(?:v\.douyin\.com/[\w-]+|www\.douyin\.com/video/\d+|douyin\.com/video/\d+)'
    found = re.findall(url_pattern, text)
    if not found:
        share_pattern = r'https?://v\.douyin\.com/[\w-]+'
        found = re.findall(share_pattern, text)
    return list(set(found))


def parse_douyin_video_quick(share_link):
    """快速模式：只解析元信息，不下载视频、不转录语音"""
    resolved_url, video_id, err = _resolve_douyin_url(share_link)
    if err:
        return {'success': False, 'error': err}
    html, err = _fetch_douyin_page(resolved_url)
    if err:
        return {'success': False, 'error': err}
    video_info = _parse_douyin_html(html, resolved_url)
    if not video_info:
        return {'success': False, 'error': '无法从页面提取视频信息'}
    author = video_info.get('author', {})
    stats = video_info.get('stats', {})
    return {
        'success': True,
        'video_info': {
            'video_id': video_info.get('video_id', ''),
            'title': video_info.get('title', ''),
            'author': author.get('nickname', ''),
            'author_id': author.get('uid', ''),
            'desc': video_info.get('desc', ''),
            'cover_url': video_info.get('cover', ''),
            'video_url': video_info.get('video_url', ''),
            'tags': video_info.get('hashtags', []),
            'duration': video_info.get('duration', 0),
            'stats': stats,
            'share_url': share_link,
            'real_url': resolved_url,
            'create_time': video_info.get('create_time', ''),
        },
        'transcribed': False,
    }


def parse_douyin_video(share_link, api_key=None, transcribe=True):
    """完整解析：链接 → 元信息 → 下载视频 → 提取音频 → 语音转文字
    api_key 不传则跳过语音转录
    """
    result = {
        'success': False,
        'video_info': {},
        'media_info': {},
        'text_content': '',
        'transcribed': False,
        'transcribe_error': '',
        'download_error': '',
    }
    resolved_url, video_id, err = _resolve_douyin_url(share_link)
    if err:
        result['error'] = err
        return result
    video_info = None
    if video_id:
        api_data, api_err = _call_douyin_web_api(video_id)
        if api_data:
            video_info = _parse_aweme_detail(api_data, resolved_url)
    if not video_info:
        html, err = _fetch_douyin_page(resolved_url)
        if err:
            result['error'] = err
            return result
        video_info = _parse_douyin_html(html, resolved_url)
        if video_info is None:
            result['error'] = '无法解析视频信息，Web API 和 HTML 解析均失败'
            return result
    if video_id and not video_info.get('video_id'):
        video_info['video_id'] = video_id
    author = video_info.get('author', {})
    stats = video_info.get('stats', {})
    result['video_info'] = {
        'video_id': video_info.get('video_id', ''),
        'title': video_info.get('title', ''),
        'author': author.get('nickname', ''),
        'author_id': author.get('uid', ''),
        'desc': video_info.get('desc', ''),
        'cover_url': video_info.get('cover', ''),
        'video_url': video_info.get('video_url', ''),
        'tags': video_info.get('hashtags', []),
        'duration': video_info.get('duration', 0),
        'stats': stats,
        'share_url': share_link,
        'real_url': resolved_url,
        'create_time': video_info.get('create_time', ''),
    }
    result['success'] = True
    # 语音转文字
    if transcribe:
        if not api_key:
            result['transcribe_error'] = '缺少 API Key，无法进行语音转文字'
        elif not video_info.get('video_url'):
            result['transcribe_error'] = '未能获取视频播放地址，无法转录'
        elif not _check_ffmpeg():
            result['transcribe_error'] = '服务器未安装 ffmpeg，无法提取音频'
        else:
            temp_dir = None
            try:
                video_path, temp_dir = _download_video_to_temp(video_info['video_url'])
                audio_path = _extract_audio_with_ffmpeg(video_path)
                if audio_path:
                    text = _transcribe_audio_siliconflow(audio_path, api_key)
                    if text is not None:
                        result['text_content'] = text
                        result['transcribed'] = True
                    else:
                        result['transcribe_error'] = '语音识别 API 调用失败'
                else:
                    result['transcribe_error'] = 'ffmpeg 音频提取失败'
                # 媒体信息
                try:
                    media = _get_media_info(video_path)
                    if media:
                        result['media_info'] = media
                except Exception:
                    pass
            except Exception as e:
                result['transcribe_error'] = f'视频处理失败: {e}'
            finally:
                if temp_dir and os.path.isdir(temp_dir):
                    shutil.rmtree(temp_dir, ignore_errors=True)
    return result


def build_douyin_context(parse_result):
    """将解析结果转为 AI 可读的上下文文本，用于注入到聊天消息中"""
    if not parse_result or not parse_result.get('success'):
        return ''
    vi = parse_result.get('video_info', {})
    lines = ['[系统自动注入 - 抖音视频解析数据]']
    if vi.get('title'):
        lines.append(f"标题: {vi['title']}")
    if vi.get('author'):
        lines.append(f"作者: {vi['author']} (@{vi.get('author_id', '')})")
    if vi.get('desc'):
        lines.append(f"描述: {vi['desc']}")
    stats = vi.get('stats', {})
    stats_lines = []
    if stats.get('play_count'):
        stats_lines.append(f"播放 {stats['play_count']}")
    if stats.get('digg_count'):
        stats_lines.append(f"点赞 {stats['digg_count']}")
    if stats.get('comment_count'):
        stats_lines.append(f"评论 {stats['comment_count']}")
    if stats.get('share_count'):
        stats_lines.append(f"分享 {stats['share_count']}")
    if stats.get('collect_count'):
        stats_lines.append(f"收藏 {stats['collect_count']}")
    if stats_lines:
        lines.append(f"数据: {' | '.join(stats_lines)}")
    if vi.get('tags'):
        lines.append(f"话题: {' '.join(['#' + t for t in vi['tags']])}")
    text_content = parse_result.get('text_content', '')
    if text_content:
        lines.append('')
        lines.append('【视频文案/口播内容】')
        lines.append(text_content)
    return '\n'.join(lines)


def _detect_and_parse_douyin_links(text):
    """检测文本中的抖音链接并解析，返回格式化后的分析文本
    支持格式：纯URL、分享文本（如 7.43 pda:/ ... https://v.douyin.com/xxxxx）
    """
    found_urls = detect_douyin_links(text)
    if not found_urls:
        return None
    results = []
    for url in found_urls:
        resolved_url, video_id, err = _resolve_douyin_url(url)
        if err:
            results.append(f'[抖音链接解析失败] {url}\n原因: {err}')
            continue
        html, err = _fetch_douyin_page(resolved_url)
        if err:
            results.append(f'[抖音链接解析失败] {url}\n原因: {err}')
            continue
        video_info = _parse_douyin_html(html, resolved_url)
        if not video_info:
            results.append(f'[抖音链接解析失败] {url}\n原因: 无法从页面提取视频信息')
            continue
        # 格式化输出
        lines = [f'[抖音视频解析结果] {url}']
        if video_info.get('title'):
            lines.append(f"标题: {video_info['title']}")
        if video_info.get('desc'):
            lines.append(f"描述: {video_info['desc']}")
        if video_info.get('transcript'):
            lines.append(f"口播文案: {video_info['transcript']}")
        if video_info.get('hashtags'):
            lines.append(f"话题: {' '.join(['#' + h for h in video_info['hashtags']])}")
        author = video_info.get('author', {})
        if author.get('nickname'):
            lines.append(f"作者: {author['nickname']} (@{author.get('uid', '')})")
        stats = video_info.get('stats', {})
        stats_lines = []
        if stats.get('digg_count'):
            stats_lines.append(f"点赞 {stats['digg_count']}")
        if stats.get('comment_count'):
            stats_lines.append(f"评论 {stats['comment_count']}")
        if stats.get('share_count'):
            stats_lines.append(f"分享 {stats['share_count']}")
        if stats.get('play_count'):
            stats_lines.append(f"播放 {stats['play_count']}")
        if stats.get('collect_count'):
            stats_lines.append(f"收藏 {stats['collect_count']}")
        if stats_lines:
            lines.append(f"数据: {' | '.join(stats_lines)}")
        if video_info.get('duration'):
            lines.append(f"时长: {video_info['duration']}秒")
        if video_info.get('create_time'):
            lines.append(f"发布时间: {video_info['create_time']}")
        music = video_info.get('music')
        if music and music.get('title'):
            lines.append(f"BGM: {music['title']} - {music.get('author', '')}")
        images = video_info.get('images')
        if images:
            lines.append(f"图集: 共 {len(images)} 张图片")
            for i, img_url in enumerate(images[:3], 1):
                lines.append(f"  图片{i}: {img_url}")
            if len(images) > 3:
                lines.append(f"  ... 还有 {len(images) - 3} 张")
        results.append('\n'.join(lines))
    return '\n\n'.join(results) if results else None


def _resolve_douyin_url(url):
    """解析抖音短链接，跟随重定向获取真实长链接，同时提取 video_id
    返回: (resolved_url, video_id, error)
    """
    url = url.strip()
    if not url.startswith('http'):
        url = 'https://' + url

    douyin_domains = ('v.douyin.com', 'www.douyin.com', 'douyin.com')
    if not any(d in url for d in douyin_domains):
        return None, None, '无效的抖音链接'

    # 如果已经是长链接，直接提取 video_id
    vid_match = re.search(r'/video/(\d+)', url)
    if vid_match:
        return url, vid_match.group(1), None

    _douyin_headers = {
        'User-Agent': _IPHONE_UA,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh-Hans;q=0.9',
        'Referer': 'https://www.douyin.com/',
    }
    req = urllib.request.Request(url, headers=_douyin_headers, method='HEAD')
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        resp = urllib.request.urlopen(req, timeout=15, context=ctx)
        final_url = resp.geturl()
        vid_match = re.search(r'/video/(\d+)', final_url)
        video_id = vid_match.group(1) if vid_match else None
        return final_url, video_id, None
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 307, 308):
            location = e.headers.get('Location', '')
            if location:
                if location.startswith('aweme://'):
                    vid_match = re.search(r'/detail/(\d+)', location)
                    if vid_match:
                        video_id = vid_match.group(1)
                        final_url = f"https://www.douyin.com/video/{video_id}"
                        return final_url, video_id, None
                    elif 'lynxview' in location or 'groupon' in location:
                        return None, None, '链接解析失败: 该链接不是视频链接（可能是团购/POI页面）'
                    else:
                        return None, None, '链接解析失败: 无法从App跳转链接提取视频ID'
                elif location.startswith('/'):
                    from urllib.parse import urlparse
                    parsed = urlparse(url)
                    location = f"{parsed.scheme}://{parsed.netloc}{location}"
                vid_match = re.search(r'/video/(\d+)', location)
                video_id = vid_match.group(1) if vid_match else None
                return location, video_id, None
        if e.code in (405, 403):
            req = urllib.request.Request(url, headers=_douyin_headers, method='GET')
            try:
                resp = urllib.request.urlopen(req, timeout=15, context=ctx)
                final_url = resp.geturl()
                vid_match = re.search(r'/video/(\d+)', final_url)
                video_id = vid_match.group(1) if vid_match else None
                return final_url, video_id, None
            except Exception as e2:
                return None, None, f'链接解析失败: {e2}'
        return None, None, f'链接解析失败: HTTP {e.code}'
    except Exception as e:
        return None, None, f'链接解析失败: {e}'


# ─── Web API ────────────────────────────────────────────

def _call_douyin_web_api(video_id):
    """调用抖音 Web API 获取视频详情
    返回: (video_data_dict, error_msg)
    """
    if not video_id:
        return None, '缺少 video_id'
    api_url = (
        f'https://www.douyin.com/aweme/v1/web/aweme/detail/'
        f'?aweme_id={video_id}'
        f'&aid=1128&version_name=23.5.0&device_platform=webapp'
        f'&cookie_enabled=true&screen_width=1920&screen_height=1080'
        f'&browser_language=zh-CN&browser_platform=MacIntel'
    )
    headers = {
        'User-Agent': _IPHONE_UA,
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh-Hans;q=0.9',
        'Referer': f'https://www.douyin.com/video/{video_id}',
    }
    try:
        req = urllib.request.Request(api_url, headers=headers)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        resp = urllib.request.urlopen(req, timeout=15, context=ctx)
        raw = resp.read()
        if not raw or len(raw) == 0:
            return None, 'Web API 返回空响应（可能需要签名/Cookie）'
        data = json.loads(raw.decode('utf-8', errors='replace'))
        aweme = data.get('aweme_detail')
        if not aweme:
            return None, 'Web API 返回数据中无 aweme_detail'
        return aweme, None
    except urllib.error.HTTPError as e:
        return None, f'Web API HTTP {e.code}'
    except json.JSONDecodeError:
        return None, 'Web API 返回非 JSON 数据'
    except Exception as e:
        return None, f'Web API 调用失败: {e}'


# ─── HTML 页面抓取 ──────────────────────────────────────

def _fetch_douyin_page(url):
    """获取抖音视频页面 HTML（降级方案）"""
    _douyin_page_headers = {
        'User-Agent': _IPHONE_UA,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh-Hans;q=0.9',
        'Referer': 'https://www.douyin.com/',
        'Cache-Control': 'max-age=0',
    }
    req = urllib.request.Request(url, headers=_douyin_page_headers)
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        resp = urllib.request.urlopen(req, timeout=20, context=ctx)
        html = resp.read().decode('utf-8', errors='replace')
        return html, None
    except urllib.error.HTTPError as e:
        return None, f'页面获取失败: HTTP {e.code}'
    except Exception as e:
        return None, f'页面获取失败: {e}'


# ─── 数据解析 ───────────────────────────────────────────

def _parse_aweme_detail(aweme, page_url):
    """解析抖音 Web API 返回的 aweme_detail 数据结构"""
    if not isinstance(aweme, dict):
        return None
    result = {
        'title': '',
        'desc': '',
        'transcript': '',
        'hashtags': [],
        'create_time': '',
        'region': '',
        'music': None,
        'images': None,
        'author': {'nickname': '', 'uid': '', 'avatar': ''},
        'cover': '',
        'video_url': '',
        'duration': 0,
        'stats': {'digg_count': 0, 'comment_count': 0, 'share_count': 0, 'play_count': 0, 'collect_count': 0},
        'page_url': page_url,
        'video_id': '',
        'source': '',
    }
    video = aweme.get('video', {})
    author = aweme.get('author', {})
    stats = aweme.get('statistics', {})
    if _fill_result_from_video(result, video, author, stats, aweme):
        result['source'] = 'web_api'
        result['video_id'] = aweme.get('aweme_id', '')
        return result
    return None


def _parse_douyin_html(html, page_url):
    """从抖音页面 HTML 中解析视频信息（5级降级策略）"""
    result = {
        'title': '',
        'desc': '',
        'transcript': '',
        'hashtags': [],
        'create_time': '',
        'region': '',
        'music': None,
        'images': None,
        'author': {'nickname': '', 'uid': '', 'avatar': ''},
        'cover': '',
        'video_url': '',
        'duration': 0,
        'stats': {'digg_count': 0, 'comment_count': 0, 'share_count': 0, 'play_count': 0, 'collect_count': 0},
        'page_url': page_url,
        'video_id': '',
        'source': '',
    }

    # 策略 1: 提取 window._ROUTER_DATA
    router_match = re.search(
        r'window\._ROUTER_DATA\s*=\s*(\{.*?\});?\s*</script>',
        html, re.S
    )
    if router_match:
        try:
            raw = router_match.group(1)
            data = json.loads(raw)

            def _find_aweme_in_router(obj):
                if isinstance(obj, dict):
                    if 'aweme_id' in obj and 'video' in obj:
                        return obj
                    for v in obj.values():
                        found = _find_aweme_in_router(v)
                        if found:
                            return found
                elif isinstance(obj, list):
                    for item in obj:
                        found = _find_aweme_in_router(item)
                        if found:
                            return found
                return None

            aweme = _find_aweme_in_router(data)
            if aweme:
                video = aweme.get('video', {})
                author = aweme.get('author', {})
                stats = aweme.get('statistics', {})
                if _fill_result_from_video(result, video, author, stats, aweme):
                    result['source'] = 'html_router'
                    return result
        except Exception:
            pass

    # 策略 2: 提取 <script id="RENDER_DATA"> 中的 JSON
    render_data_match = re.search(
        r'<script[^>]*id=["\']RENDER_DATA["\'][^>]*>(.*?)</script>',
        html, re.S | re.I
    )
    if render_data_match:
        try:
            raw = render_data_match.group(1)
            from urllib.parse import unquote
            raw_decoded = unquote(raw)
            import html as _html_module
            raw_decoded = _html_module.unescape(raw_decoded)
            data = json.loads(raw_decoded)
            app_data = data.get('app', data)
            video_info = None
            if 'videoInfo' in app_data:
                video_info = app_data['videoInfo']
            elif isinstance(app_data, dict):
                for k, v in app_data.items():
                    if isinstance(v, dict) and 'video' in v:
                        video_info = v
                        break
            if video_info:
                video = video_info.get('video', video_info)
                author = video.get('author', {}) if isinstance(video, dict) else {}
                stats = video.get('statistics', video.get('stats', {})) if isinstance(video, dict) else {}
                if _fill_result_from_video(result, video, author, stats, video_info):
                    result['source'] = 'html_render'
                    return result
        except Exception:
            pass

    # 策略 3: 提取 window._SSR_HYDRATED_DATA
    ssr_match = re.search(
        r'window\._SSR_HYDRATED_DATA\s*=\s*(\{.*?\});?\s*</script>',
        html, re.S
    )
    if ssr_match:
        try:
            raw = ssr_match.group(1)
            data = json.loads(raw)
            video_detail = None
            if 'videoDetail' in data:
                video_detail = data['videoDetail']
            else:
                def _find_video_detail(obj):
                    if isinstance(obj, dict):
                        if 'video' in obj and 'author' in obj:
                            return obj
                        for v in obj.values():
                            found = _find_video_detail(v)
                            if found:
                                return found
                    elif isinstance(obj, list):
                        for item in obj:
                            found = _find_video_detail(item)
                            if found:
                                return found
                    return None

                video_detail = _find_video_detail(data)
            if video_detail:
                video = video_detail.get('video', {})
                author = video_detail.get('author', {})
                stats = video_detail.get('statistics', {})
                if _fill_result_from_video(result, video, author, stats, video_detail):
                    result['source'] = 'html_ssr'
                    return result
        except Exception:
            pass

    # 策略 4: 提取 window.__INITIAL_STATE__
    initial_match = re.search(
        r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\});?\s*</script>',
        html, re.S
    )
    if initial_match:
        try:
            raw = initial_match.group(1)
            data = json.loads(raw)
            video_data = None
            if 'video' in data:
                video_data = data['video']
            elif 'state' in data:
                video_data = data['state'].get('video')
            if video_data:
                author = video_data.get('author', {})
                stats = video_data.get('statistics', {})
                if _fill_result_from_video(result, video_data, author, stats, video_data):
                    result['source'] = 'html_initial'
                    return result
        except Exception:
            pass

    # 策略 5: 兜底——正则提取 OG 标签
    og_title = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', html, re.I)
    if og_title:
        result['title'] = og_title.group(1).strip()
    og_desc = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)', html, re.I)
    if og_desc:
        result['desc'] = og_desc.group(1).strip()
        result['transcript'] = result['desc']
    og_image = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', html, re.I)
    if og_image:
        result['cover'] = og_image.group(1).strip()
    og_video = re.search(r'<meta[^>]+property=["\']og:video["\'][^>]+content=["\']([^"\']+)', html, re.I)
    if og_video:
        result['video_url'] = og_video.group(1).strip()

    # 如果没有任何信息，认为解析失败
    if not result['title'] and not result['desc'] and not result['video_url'] and not result['cover']:
        return None

    result['source'] = 'html_og'
    return result


def _fill_result_from_video(result, video, author, stats=None, extra=None):
    """统一填充解析结果（避免三处策略重复代码）"""
    if not isinstance(video, dict):
        return False
    # aweme 级别字段回退（抖音数据结构中 desc/create_time/text_extra 在 aweme 级别）
    aweme_desc = extra.get('desc', '') if isinstance(extra, dict) else ''
    result['title'] = video.get('title') or video.get('desc', '') or aweme_desc
    result['desc'] = video.get('desc', '') or aweme_desc
    result['cover'] = _extract_cover(video.get('cover'))
    result['video_url'] = _extract_watermark_free_video(video)
    result['duration'] = _extract_duration(video)
    result['transcript'] = _extract_subtitle_text(video)
    # hashtags 可能来自 aweme 级别的 text_extra
    text_extra = video.get('text_extra', [])
    if not text_extra and isinstance(extra, dict):
        text_extra = extra.get('text_extra', [])
    result['hashtags'] = _extract_hashtags({'text_extra': text_extra})
    result['music'] = _extract_music(video)
    result['images'] = _extract_images(video)

    # 发布时间（aweme 级别）
    create_time = video.get('create_time', 0) or (extra.get('create_time', 0) if isinstance(extra, dict) else 0)
    if isinstance(create_time, (int, float)) and create_time > 0:
        try:
            from datetime import datetime
            result['create_time'] = datetime.fromtimestamp(create_time).isoformat()
        except Exception:
            result['create_time'] = str(create_time)
    else:
        result['create_time'] = ''

    # 地区
    result['region'] = video.get('region', '')

    # 统计
    if isinstance(stats, dict):
        result['stats']['digg_count'] = stats.get('digg_count', 0)
        result['stats']['comment_count'] = stats.get('comment_count', 0)
        result['stats']['share_count'] = stats.get('share_count', 0)
        result['stats']['play_count'] = stats.get('play_count', 0)
        result['stats']['collect_count'] = stats.get('collect_count', 0)

    # 作者
    if isinstance(author, dict):
        result['author']['nickname'] = author.get('nickname', '')
        result['author']['uid'] = author.get('unique_id') or author.get('short_id', '')
        result['author']['avatar'] = _extract_avatar(author.get('avatar_thumb'))

    # video_id（从 extra/aweme 中提取）
    if isinstance(extra, dict) and extra.get('aweme_id'):
        result['video_id'] = extra['aweme_id']

    return True


# ─── 字段提取辅助函数 ───────────────────────────────────

def _extract_cover(cover):
    """从封面字段提取 URL"""
    if isinstance(cover, dict):
        return cover.get('url_list', [''])[0] if cover.get('url_list') else cover.get('uri', '')
    elif isinstance(cover, str):
        return cover
    return ''


def _extract_avatar(avatar):
    """从头像字段提取 URL"""
    if isinstance(avatar, dict):
        return avatar.get('url_list', [''])[0] if avatar.get('url_list') else ''
    elif isinstance(avatar, str):
        return avatar
    return ''


def _extract_video_url(play_addr):
    """从 play_addr 提取视频地址（无水印：将 playwm 替换为 play）"""
    url = ''
    if isinstance(play_addr, dict):
        url_list = play_addr.get('url_list', [])
        url = url_list[0] if url_list else ''
    elif isinstance(play_addr, str):
        url = play_addr
    # 替换 playwm 为 play 获取无水印版本
    if url and 'playwm' in url:
        url = url.replace('playwm', 'play')
    return url


def _extract_duration(video):
    """提取视频时长（秒）"""
    duration = video.get('duration', 0)
    if duration and duration > 1000:
        return round(duration / 1000, 1)
    elif duration:
        return duration
    return 0


def _extract_subtitle_text(video):
    """从视频数据中提取字幕/口播文案"""
    subtitles = []
    # 1. subtitle_infos（自动字幕）
    sub_infos = video.get('subtitle_infos', video.get('subtitleInfo', []))
    if isinstance(sub_infos, list):
        for item in sub_infos:
            content = item.get('content', '') if isinstance(item, dict) else ''
            if content:
                subtitles.append(content)
    # 2. cla_subtitle（字幕信息）
    cla = video.get('cla_subtitle')
    if isinstance(cla, dict):
        sub_list = cla.get('subtitles', [])
        if isinstance(sub_list, list):
            for item in sub_list:
                content = item.get('content', '') if isinstance(item, dict) else ''
                if content:
                    subtitles.append(content)
    # 3. text_extra + desc 组合成完整口播
    text_extra = video.get('text_extra', [])
    desc = video.get('desc', '')
    if not subtitles and desc:
        # 没有字幕时，desc 本身就是口播文案
        return desc
    # 去重拼接
    seen = set()
    unique = []
    for s in subtitles:
        if s not in seen:
            seen.add(s)
            unique.append(s)
    return '\n'.join(unique) if unique else desc


def _extract_hashtags(video):
    """提取话题标签"""
    hashtags = []
    text_extra = video.get('text_extra', [])
    if isinstance(text_extra, list):
        for item in text_extra:
            if isinstance(item, dict):
                tag = item.get('hashtag_name') or item.get('hashTagName')
                if tag:
                    hashtags.append(tag)
    return hashtags


def _extract_music(video):
    """提取背景音乐信息"""
    music = video.get('music')
    if not isinstance(music, dict):
        return None
    return {
        'title': music.get('title', ''),
        'author': music.get('author', ''),
        'cover': _extract_cover(music.get('cover')),
        'url': music.get('play_url', {}).get('uri', '') if isinstance(music.get('play_url'), dict) else '',
        'duration': _extract_duration(music),
    }


def _extract_watermark_free_video(video):
    """提取无水印/高清视频地址（将 playwm 替换为 play）"""
    url = ''
    # 1. 尝试 bit_rate 列表（通常包含多清晰度无水印地址）
    bit_rate = video.get('bit_rate')
    if isinstance(bit_rate, list) and bit_rate:
        # 取最高清晰度
        best = max(bit_rate, key=lambda x: x.get('bit_rate', 0) if isinstance(x, dict) else 0)
        if isinstance(best, dict):
            play_addr = best.get('play_addr')
            if isinstance(play_addr, dict):
                url_list = play_addr.get('url_list', [])
                if url_list:
                    url = url_list[0]
    # 2. 尝试 play_addr_h264
    if not url:
        play_addr_h264 = video.get('play_addr_h264')
        if isinstance(play_addr_h264, dict):
            url_list = play_addr_h264.get('url_list', [])
            if url_list:
                url = url_list[0]
    # 3. 回退到普通 play_addr
    if not url:
        url = _extract_video_url(video.get('play_addr'))
    # 替换 playwm 为 play 获取无水印版本
    if url and 'playwm' in url:
        url = url.replace('playwm', 'play')
    return url


def _extract_images(video):
    """提取图集图片地址列表（如果是图集类型）"""
    images = video.get('images')
    if not isinstance(images, list):
        return None
    result = []
    for img in images:
        if isinstance(img, dict):
            url_list = img.get('url_list', [])
            if url_list:
                result.append(url_list[0])
            else:
                uri = img.get('uri', '')
                if uri:
                    result.append(uri)
        elif isinstance(img, str):
            result.append(img)
    return result if result else None


# ─── 视频下载 ───────────────────────────────────────────

def _download_video_to_temp(video_url, max_size_mb=100):
    """下载视频到临时文件，返回 (video_path, temp_dir)"""
    max_bytes = max_size_mb * 1024 * 1024
    temp_dir = tempfile.mkdtemp(prefix='douyin_')
    video_path = os.path.join(temp_dir, 'video.mp4')
    req = urllib.request.Request(video_url, headers={
        'User-Agent': _IPHONE_UA,
        'Accept': '*/*',
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'Referer': 'https://www.douyin.com/',
    })
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    resp = urllib.request.urlopen(req, timeout=60, context=ctx)
    content_length = resp.headers.get('Content-Length')
    if content_length and int(content_length) > max_bytes:
        raise ValueError(f'视频大小超过限制 {max_size_mb}MB')
    downloaded = 0
    with open(video_path, 'wb') as f:
        while True:
            chunk = resp.read(64 * 1024)
            if not chunk:
                break
            downloaded += len(chunk)
            if downloaded > max_bytes:
                raise ValueError(f'视频大小超过限制 {max_size_mb}MB')
            f.write(chunk)
    return video_path, temp_dir


# ─── ffmpeg 工具链 ──────────────────────────────────────

def _check_ffmpeg():
    """检查 ffmpeg 是否可用，返回版本字符串或 None"""
    try:
        result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.splitlines()[0].strip()
    except Exception:
        pass
    return None


def _extract_audio_with_ffmpeg(video_path):
    """用 ffmpeg 提取音频为 mp3，返回音频文件路径（失败返回 None）"""
    audio_path = os.path.splitext(video_path)[0] + '.mp3'
    cmd = [
        'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
        '-i', video_path,
        '-vn',
        '-acodec', 'libmp3lame',
        '-q:a', '0',
        audio_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f'  [Douyin] ffmpeg error: {result.stderr}', flush=True)
        return None
    if not os.path.isfile(audio_path) or os.path.getsize(audio_path) == 0:
        return None
    return audio_path


def _extract_cover_from_video(video_path):
    """用 ffmpeg 从视频提取第一帧封面，返回封面文件路径（失败返回 None）"""
    cover_path = os.path.splitext(video_path)[0] + '_cover.jpg'
    cmd = [
        'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
        '-i', video_path,
        '-vframes', '1',
        '-q:v', '2',
        cover_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f'  [Douyin] ffmpeg cover error: {result.stderr}', flush=True)
        return None
    if not os.path.isfile(cover_path) or os.path.getsize(cover_path) == 0:
        return None
    return cover_path


def _get_media_info(video_path):
    """用 ffprobe 获取视频媒体信息（-show_streams + -show_format），返回 dict（失败返回 None）"""
    cmd = [
        'ffprobe', '-v', 'quiet',
        '-print_format', 'json',
        '-show_streams',
        '-show_format',
        video_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        streams = data.get('streams', [])
        fmt = data.get('format', {})
        video_stream = None
        audio_stream = None
        for s in streams:
            if s.get('codec_type') == 'video' and video_stream is None:
                video_stream = s
            elif s.get('codec_type') == 'audio' and audio_stream is None:
                audio_stream = s

        info = {}
        if video_stream:
            info['width'] = video_stream.get('width', 0)
            info['height'] = video_stream.get('height', 0)
            info['fps'] = _parse_fps(video_stream.get('avg_frame_rate', ''))
            info['video_codec'] = video_stream.get('codec_name', '')
            info['video_bitrate'] = int(video_stream.get('bit_rate', 0)) // 1000 if video_stream.get('bit_rate') else 0
        if audio_stream:
            info['audio_codec'] = audio_stream.get('codec_name', '')
            info['audio_bitrate'] = int(audio_stream.get('bit_rate', 0)) // 1000 if audio_stream.get('bit_rate') else 0
            info['sample_rate'] = int(audio_stream.get('sample_rate', 0))
            info['channels'] = audio_stream.get('channels', 0)

        # format 容器信息（-show_format 提供）
        info['format'] = fmt.get('format_name', '').split(',')[0]
        info['format_long'] = fmt.get('format_long_name', '')
        info['duration'] = round(float(fmt.get('duration', 0)), 2)
        info['file_size'] = int(fmt.get('size', 0))
        info['total_bitrate'] = int(fmt.get('bit_rate', 0)) // 1000 if fmt.get('bit_rate') else 0
        return info
    except Exception as e:
        print(f'  [Douyin] ffprobe error: {e}', flush=True)
        return None


def _parse_fps(fps_str):
    """解析 ffprobe 返回的帧率字符串（如 '30/1' 或 '2997/100'）"""
    if not fps_str:
        return 0
    try:
        if '/' in fps_str:
            num, den = fps_str.split('/')
            return round(int(num) / int(den), 2)
        return round(float(fps_str), 2)
    except Exception:
        return 0


# ─── SiliconFlow 语音转文字 ─────────────────────────────

def _build_multipart_body(fields, files):
    """构造 multipart/form-data body（标准库，零依赖）
    fields: dict {name: value}
    files:  dict {name: filepath}
    返回 (body_bytes, content_type)
    """
    boundary = '----WebKitFormBoundary' + uuid.uuid4().hex
    body = b''
    for name, value in fields.items():
        body += f'--{boundary}\r\n'.encode('utf-8')
        body += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode('utf-8')
        body += str(value).encode('utf-8') + b'\r\n'
    for name, filepath in files.items():
        filename = os.path.basename(filepath)
        mime_type = mimetypes.guess_type(filepath)[0] or 'application/octet-stream'
        body += f'--{boundary}\r\n'.encode('utf-8')
        body += f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode('utf-8')
        body += f'Content-Type: {mime_type}\r\n\r\n'.encode('utf-8')
        with open(filepath, 'rb') as f:
            body += f.read()
        body += b'\r\n'
    body += f'--{boundary}--\r\n'.encode('utf-8')
    content_type = f'multipart/form-data; boundary={boundary}'
    return body, content_type


def _transcribe_audio_siliconflow(audio_path, api_key, model='FunAudioLLM/SenseVoiceSmall'):
    """调用硅基流动 API 进行语音转文字，返回 text（失败返回 None）"""
    url = 'https://api.siliconflow.cn/v1/audio/transcriptions'
    body, content_type = _build_multipart_body(
        {'model': model},
        {'file': audio_path}
    )
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': content_type,
        'Content-Length': str(len(body))
    }
    req = urllib.request.Request(url, data=body, headers=headers, method='POST')
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        resp = urllib.request.urlopen(req, timeout=120, context=ctx)
        resp_data = json.loads(resp.read().decode('utf-8'))
        return resp_data.get('text', '')
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8', errors='replace')
        print(f'  [Douyin] SiliconFlow API error: HTTP {e.code} {e.reason}', flush=True)
        print(f'      Response: {error_body}', flush=True)
        return None
    except Exception as e:
        print(f'  [Douyin] SiliconFlow API error: {e}', flush=True)
        return None


__all__ = [
    'is_douyin_share_text',
    'detect_douyin_links',
    'parse_douyin_video_quick',
    'parse_douyin_video',
    'build_douyin_context',
    '_detect_and_parse_douyin_links',
    '_IPHONE_UA',
    '_resolve_douyin_url',
    '_fetch_douyin_page',
    '_call_douyin_web_api',
    '_parse_aweme_detail',
    '_parse_douyin_html',
    '_fill_result_from_video',
    '_extract_cover',
    '_extract_avatar',
    '_extract_video_url',
    '_extract_duration',
    '_extract_subtitle_text',
    '_extract_hashtags',
    '_extract_music',
    '_extract_watermark_free_video',
    '_extract_images',
    '_download_video_to_temp',
    '_extract_audio_with_ffmpeg',
    '_extract_cover_from_video',
    '_get_media_info',
    '_parse_fps',
    '_build_multipart_body',
    '_transcribe_audio_siliconflow',
    '_check_ffmpeg',
]
