#!/usr/bin/env python3
"""
SoloBrave 大脑系统 - 全面接口自测脚本
=====================================
运行前请确保服务器已启动:
    python solobrave-server.py --port 8080
然后执行:
    python run_full_tests.py
"""

import json
import urllib.request
import urllib.error
import time
import uuid

BASE_URL = 'http://localhost:8080'
TEST_USERNAME = 'test_auto_' + uuid.uuid4().hex[:8]
TEST_PASSWORD = 'test123'

# ═══════════════════════════════════════════════════
# 测试框架
# ═══════════════════════════════════════════════════

results = []

def log(method, endpoint, status, detail=''):
    mark = '✅' if status == 'PASS' else '❌'
    results.append({'method': method, 'endpoint': endpoint, 'status': status, 'detail': detail})
    print(f"{mark} {method:6} {endpoint:40} [{status}] {detail}")

def request(method, path, body=None, headers=None):
    url = BASE_URL + path
    data = json.dumps(body).encode('utf-8') if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('Content-Type', 'application/json')
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode('utf-8', errors='replace')
            try:
                return resp.status, json.loads(raw) if raw else None
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', errors='replace')
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw
    except Exception as e:
        return -1, str(e)

# ═══════════════════════════════════════════════════
# 测试用例
# ═══════════════════════════════════════════════════

def test_health():
    code, data = request('GET', '/api/health')
    log('GET', '/api/health', 'PASS' if code == 200 and data and data.get('status') == 'ok' else 'FAIL',
        f"status={data.get('status') if isinstance(data, dict) else '?'}")

def test_auth():
    global token, user_id
    # 注册
    code, data = request('POST', '/api/auth/register', {
        'username': TEST_USERNAME,
        'password': TEST_PASSWORD,
        'displayName': 'Auto Tester'
    })
    log('POST', '/api/auth/register', 'PASS' if code == 200 else 'FAIL', f"code={code}")

    # 登录
    code, data = request('POST', '/api/auth/login', {
        'username': TEST_USERNAME,
        'password': TEST_PASSWORD
    })
    if code == 200 and isinstance(data, dict) and data.get('token'):
        token = data['token']
        user_id = data.get('user', {}).get('id')
        log('POST', '/api/auth/login', 'PASS', f"token={token[:16]}...")
    else:
        log('POST', '/api/auth/login', 'FAIL', f"code={code} data={data}")
        token = None

def auth_headers():
    return {'Authorization': f'Bearer {token}'} if token else {}

# ─── 商品库 ───────────────────────────────────────

def test_products():
    global product_id
    # POST 创建商品
    body = {
        'name': '自动测试商品-' + uuid.uuid4().hex[:6],
        'sku': 'SKU-' + uuid.uuid4().hex[:6].upper(),
        'category': '美妆护肤',
        'price': 129.0,
        'stock': 100,
        'tags': ['测试', '自动'],
        'description': '自动测试创建的商品',
        'status': 'active',
        'selling_points': ['卖点A', '卖点B'],
        'product_card': '这是商品手卡',
        'commission_rate': 10
    }
    code, data = request('POST', '/api/products', body, auth_headers())
    product_id = data.get('id') if isinstance(data, dict) else None
    log('POST', '/api/products', 'PASS' if code == 200 and product_id else 'FAIL', f"id={product_id}")

    # GET list
    code, data = request('GET', '/api/products', None, auth_headers())
    has_list = isinstance(data, dict) and 'products' in data
    log('GET', '/api/products', 'PASS' if code == 200 and has_list else 'FAIL', f"count={len(data.get('products', [])) if has_list else '?'}")

    # GET detail
    if product_id:
        code, data = request('GET', f'/api/products/{product_id}', None, auth_headers())
        log('GET', f'/api/products/{product_id}', 'PASS' if code == 200 and isinstance(data, dict) and data.get('id') == product_id else 'FAIL')

    # PUT update
    if product_id:
        code, data = request('PUT', f'/api/products/{product_id}', {'stock': 200, 'price': 139.0}, auth_headers())
        log('PUT', f'/api/products/{product_id}', 'PASS' if code == 200 else 'FAIL')

    # POST search
    code, data = request('POST', '/api/products/search', {'keyword': '自动测试'}, auth_headers())
    log('POST', '/api/products/search', 'PASS' if code == 200 else 'FAIL')

    # GET matches
    if product_id:
        code, data = request('GET', f'/api/products/{product_id}/matches', None, auth_headers())
        log('GET', f'/api/products/{product_id}/matches', 'PASS' if code == 200 else 'FAIL')

# ─── 达人库 ───────────────────────────────────────

def test_influencers():
    global influencer_id
    body = {
        'name': '自动测试达人-' + uuid.uuid4().hex[:6],
        'platform': '抖音',
        'accountId': 'test_' + uuid.uuid4().hex[:8],
        'category': '美妆护肤',
        'followerCount': 150000,
        'cooperationPrice': 5000,
        'priceUnit': '元/条',
        'engagementRate': 5.5,
        'avgViews': 50000,
        'tags': ['种草', '测评'],
        'contentStyle': '短视频+直播',
        'contact': '微信 test123',
        'bio': '自动测试创建的达人',
        'notes': '备注信息',
        'status': 'available'
    }
    code, data = request('POST', '/api/influencers', body, auth_headers())
    influencer_id = data.get('id') if isinstance(data, dict) else None
    log('POST', '/api/influencers', 'PASS' if code == 200 and influencer_id else 'FAIL', f"id={influencer_id}")

    # GET list
    code, data = request('GET', '/api/influencers', None, auth_headers())
    has_list = isinstance(data, dict) and 'influencers' in data
    log('GET', '/api/influencers', 'PASS' if code == 200 and has_list else 'FAIL', f"count={len(data.get('influencers', [])) if has_list else '?'}")

    # GET detail (本次修复的核心)
    if influencer_id:
        code, data = request('GET', f'/api/influencers/{influencer_id}', None, auth_headers())
        detail_ok = code == 200 and isinstance(data, dict) and data.get('id') == influencer_id
        log('GET', f'/api/influencers/{influencer_id}', 'PASS' if detail_ok else 'FAIL', f"name={data.get('name') if isinstance(data, dict) else '?'}")

    # PUT update
    if influencer_id:
        code, data = request('PUT', f'/api/influencers/{influencer_id}', {'followerCount': 200000}, auth_headers())
        log('PUT', f'/api/influencers/{influencer_id}', 'PASS' if code == 200 else 'FAIL')

    # POST search
    if influencer_id:
        code, data = request('POST', '/api/influencers/search', {'q': '自动测试'}, auth_headers())
        log('POST', '/api/influencers/search', 'PASS' if code == 200 else 'FAIL')

# ─── 匹配引擎 ─────────────────────────────────────

def test_match():
    if product_id and influencer_id:
        code, data = request('POST', '/api/match/product-to-influencer',
                             {'productId': product_id, 'limit': 5}, auth_headers())
        log('POST', '/api/match/product-to-influencer', 'PASS' if code == 200 else 'FAIL',
            f"results={len(data.get('results', [])) if isinstance(data, dict) else '?'}")

        code, data = request('POST', '/api/match/influencer-to-product',
                             {'influencerId': influencer_id, 'limit': 5}, auth_headers())
        log('POST', '/api/match/influencer-to-product', 'PASS' if code == 200 else 'FAIL',
            f"results={len(data.get('results', [])) if isinstance(data, dict) else '?'}")
    else:
        log('SKIP', '/api/match/*', 'SKIP', '缺少 product_id 或 influencer_id')

# ─── 知识库 ───────────────────────────────────────

def test_knowledge():
    body = {'name': '测试文档', 'content': '这是自动测试文档内容', 'icon': '📄'}
    code, data = request('POST', '/api/knowledge', body, auth_headers())
    doc_id = data.get('id') if isinstance(data, dict) else None
    log('POST', '/api/knowledge', 'PASS' if code == 200 and doc_id else 'FAIL', f"id={doc_id}")

    code, data = request('GET', '/api/knowledge', None, auth_headers())
    log('GET', '/api/knowledge', 'PASS' if code == 200 else 'FAIL')

    if doc_id:
        code, data = request('PUT', f'/api/knowledge/{doc_id}', {'content': '更新后的内容'}, auth_headers())
        log('PUT', f'/api/knowledge/{doc_id}', 'PASS' if code == 200 else 'FAIL')
        code, data = request('DELETE', f'/api/knowledge/{doc_id}', None, auth_headers())
        log('DELETE', f'/api/knowledge/{doc_id}', 'PASS' if code == 200 else 'FAIL')

# ─── 记忆系统 ─────────────────────────────────────

def test_memory():
    if not user_id:
        log('SKIP', '/api/memory/*', 'SKIP', '缺少 user_id')
        return
    # POST 添加记忆
    body = {'value': '自动测试记忆内容', 'key': 'auto_test', 'pool': 'daily'}
    code, data = request('POST', f'/api/memory/{user_id}', body, auth_headers())
    mem_id = data.get('id') if isinstance(data, dict) else None
    log('POST', f'/api/memory/{user_id}', 'PASS' if code == 200 and mem_id else 'FAIL', f"id={mem_id}")

    # GET 记忆
    code, data = request('GET', f'/api/memory/{user_id}', None, auth_headers())
    log('GET', f'/api/memory/{user_id}', 'PASS' if code == 200 else 'FAIL')

    # SEARCH 记忆
    code, data = request('GET', f'/api/memory/search?q=自动测试', None, auth_headers())
    log('GET', '/api/memory/search', 'PASS' if code == 200 else 'FAIL')

# ─── 清理 ─────────────────────────────────────────

def test_cleanup():
    if influencer_id:
        code, _ = request('DELETE', f'/api/influencers/{influencer_id}', None, auth_headers())
        log('DELETE', f'/api/influencers/{influencer_id}', 'PASS' if code == 200 else 'FAIL')
    if product_id:
        code, _ = request('DELETE', f'/api/products/{product_id}', None, auth_headers())
        log('DELETE', f'/api/products/{product_id}', 'PASS' if code == 200 else 'FAIL')

# ═══════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("SoloBrave 大脑系统 - 全面接口自测")
    print(f"BASE_URL: {BASE_URL}")
    print("=" * 70)

    # 检查服务器是否可连
    try:
        urllib.request.urlopen(BASE_URL + '/api/health', timeout=3)
    except Exception as e:
        print(f"\n❌ 无法连接到服务器: {e}")
        print("请先启动服务器: python solobrave-server.py --port 8080")
        return

    print("")
    test_health()
    test_auth()
    test_products()
    test_influencers()
    test_match()
    test_knowledge()
    test_memory()
    test_cleanup()

    # 汇总
    total = len(results)
    passed = sum(1 for r in results if r['status'] == 'PASS')
    failed = sum(1 for r in results if r['status'] == 'FAIL')
    skipped = sum(1 for r in results if r['status'] == 'SKIP')

    print("")
    print("=" * 70)
    print(f"测试结果: {passed}/{total} 通过 | {failed} 失败 | {skipped} 跳过")
    print("=" * 70)

    if failed > 0:
        print("\n失败的用例:")
        for r in results:
            if r['status'] == 'FAIL':
                print(f"  ❌ {r['method']:6} {r['endpoint']} - {r['detail']}")

if __name__ == '__main__':
    main()
