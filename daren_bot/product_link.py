"""商品链接 → 抖音商品ID。

为什么需要它（2026-10-21 老大要求）：
> 「商品链接直接打开没有用 没有数据 必须拿到商品链接上的商品ID 然后去巨量找这个商品 然后看数据」

实测证据（不是推测）：
- 抖音电商官方口径：商品详情页链接跳转完成后，**链接里的 19 位数字就是商品ID**
  （来源：抖音电商知识产权保护平台《投诉商品ID获取说明》）
- 常见形态：
    https://haohuo.jinritemai.com/views/product/detail?id=3446348913060228570&...
    https://haohuo.jinritemai.com/views/product/item2?id=3502057979786706281
    https://www.douyin.com/product/<19位id>
- 实测：`haohuo.jinritemai.com/views/product/detail?id=...` 的**页面正文里没有商品数据**
  （只有「打开抖音APP / 页面已下线 / 版本过低」这类壳），留存现场 HTML 389KB
  里 `product_id` 出现 0 次 —— 所以**不能靠页面正文提取**，必须走 ID 查接口。
"""
import re

# 商品ID 是 17-20 位数字。抖音官方口径是 19 位，但历史数据里见过 18 位。
_LONG_NUM = re.compile(r'(?<!\d)(\d{17,20})(?!\d)')

# 带明确 id 参数的链接：优先取参数值（比全文扫数字准）
_ID_PARAM = re.compile(r'(?:[?&](?:id|product_id|promotion_id)=)(\d{15,22})',
                       re.I)
# /product/<id> 形态
_PATH_SEG = re.compile(r'/product/(\d{15,22})')


def extract_product_id(url: str) -> str:
    """从商品链接里抽商品ID；抽不到返回空串。

    顺序（从准到粗）：
      ① `?id=` / `?product_id=` 参数值
      ② `/product/<id>` 路径段
      ③ 全文里最长的 17-20 位数字（兜底）
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
    return ""


def is_product_link(url: str) -> bool:
    """是否像商品链接（能抽出商品ID）。"""
    return bool(extract_product_id(url))
