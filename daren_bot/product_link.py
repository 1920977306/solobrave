"""商品链接 → 抖音商品ID。

为什么需要它（2026-10-21 老大要求）：
> 「商品链接直接打开没有用 没有数据 必须拿到商品链接上的商品ID 然后去巨量找这个商品 然后看数据」

实测证据（不是推测）：
- 抖音电商官方口径：商品详情页链接跳转完成后，**链接里的 19 位数字就是商品ID**
  （来源：抖音电商知识产权保护平台《投诉商品ID获取说明》）
- 老大口径（2026-11-03 重申）：「商品id就是链接里面连在一起的数字」。
- 常见形态：
    https://haohuo.jinritemai.com/views/product/detail?id=3446348913060228570&...
    https://haohuo.jinritemai.com/views/product/item2?id=3502057979786706281
    https://www.douyin.com/product/<19位id>
- 短链形态（v.douyin.com/<code>/，链接本身**没有数字**）：
    单次 HTTP HEAD 拿 302 Location（aweme://webcast_webview?url=<商品推广H5>），
    解出内层参数。实测两个真实样本：
      - 仅带 promotion_id=3848235384622416590
      - 同时带 product_id=3721152102001672246 & commodity_id=… & promotion_id=…
    ⚠️ 优先级 product_id > commodity_id > promotion_id（三者可能不同，
       material_list search_text 认的是商品ID；promotion_id 有时≠product_id）。
    ⚠️ 短链也可能指向**视频**（Location 是 aweme/detail 或 /video/，没有商品参数）
       —— 那种情况如实返回空，绝不能把视频ID当商品ID。
- 实测：`haohuo.jinritemai.com/views/product/detail?id=...` 的**页面正文里没有商品数据**
  （只有「打开抖音APP / 页面已下线 / 版本过低」这类壳），留存现场 HTML 389KB
  里 `product_id` 出现 0 次 —— 所以**不能靠页面正文提取**，必须走 ID 查接口。
"""
import re
import urllib.error
import urllib.parse
import urllib.request

# 商品ID 是 17-20 位数字。抖音官方口径是 19 位，但历史数据里见过 18 位。
_LONG_NUM = re.compile(r'(?<!\d)(\d{17,20})(?!\d)')

# 带明确 id 参数的链接：优先取参数值（比全文扫数字准）
_ID_PARAM = re.compile(r'(?:[?&](?:id|product_id|promotion_id)=)(\d{15,22})',
                       re.I)
# /product/<id> 形态
_PATH_SEG = re.compile(r'/product/(\d{15,22})')

# 短链域名：链接本体没有数字，必须解析重定向
_SHORT_LINK_HOSTS = ('v.douyin.com', 'vt.tiktok.com')

# 解析出的 aweme:// Location 里的商品参数（按可信度排序，只认这三个名字，
# 不认裸 id= —— 视频页也有 id 类参数，误认会把视频ID当商品ID）
_RESOLVED_PID_PARAMS = ('product_id', 'commodity_id', 'promotion_id')
_RESOLVED_PARAM_RE = {
    name: re.compile(rf'[?&]{name}=(\d{{15,22}})') for name in _RESOLVED_PID_PARAMS
}


def _resolve_short_link_location(url: str, timeout: float = 5.0) -> str:
    """短链 → 302 Location（不跟随重定向，不需要浏览器）。

    v.douyin.com 对 HEAD 直接返回 302，Location 是 aweme://webcast_webview?url=<编码H5>。
    任何网络异常/超时都返回空串（调用方按「提取不到」处理，不炸主链路）。
    """

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(
        url, method='HEAD',
        headers={'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                                'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36')})
    try:
        resp = opener.open(req, timeout=timeout)
        return resp.headers.get('Location') or ''
    except urllib.error.HTTPError as e:
        # 302 在 NoRedirect 下以 HTTPError 形式抛出，Location 在错误头里
        return (e.headers or {}).get('Location') or ''
    except Exception:
        return ''


def _product_id_from_resolved(location: str) -> str:
    """从 aweme://…url=<编码H5> 的 Location 里解商品ID。

    Location 是多层 URL 编码的（实测编码 3 层），统一先反复 unquote 再逐个参数匹配；
    只认 product_id/commodity_id/promotion_id 三个显式商品参数。"""
    text = location or ''
    for _ in range(4):
        decoded = urllib.parse.unquote(text)
        if decoded == text:
            break
        text = decoded
    for name in _RESOLVED_PID_PARAMS:   # 按可信度顺序，命中即返回
        m = _RESOLVED_PARAM_RE[name].search(text)
        if m:
            return m.group(1)
    return ''


def extract_product_id(url: str) -> str:
    """从商品链接里抽商品ID；抽不到返回空串。

    顺序（从准到粗）：
      ① `?id=` / `?product_id=` 参数值
      ② `/product/<id>` 路径段
      ③ 全文里最长的 17-20 位数字（兜底）
      ④ 短链（v.douyin.com 等，链接本体无数字）→ HTTP 解析 302 Location
         → 解 product_id/commodity_id/promotion_id；指向视频则返回空
    """
    u = (url or "").strip()
    if not u:
        return ""
    m = _ID_PARAM.search(u)
    if m:
        return m.group(1)
    m = _PATH_SEG.search(u)
    if m:
        return m.group(1)
    nums = _LONG_NUM.findall(u)
    if nums:
        return max(nums, key=len)
    # ④ 短链解析（只对本链接形态，且前面都没抽到才走网络）
    try:
        host = urllib.parse.urlparse(u).netloc.lower()
    except Exception:
        host = ''
    if any(host == h or host.endswith('.' + h) for h in _SHORT_LINK_HOSTS):
        loc = _resolve_short_link_location(u)
        if loc:
            return _product_id_from_resolved(loc)
    return ""


def is_product_link(url: str) -> bool:
    """是否像商品链接（能抽出商品ID）。"""
    return bool(extract_product_id(url))
