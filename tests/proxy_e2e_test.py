# -*- coding: utf-8 -*-
"""
端到端冒烟：真实 solobrave-server 进程 + 本地 mock 上游(127.0.0.1:9999)

验证（PROXY_ENGINE=chain / legacy 各一轮）：
  1. /api/products、/api/tasks 核心接口 200（非代理接口不受重构影响）
  2. POST /api/proxy/kimi/v1/messages 经 mock 上游 200（chain 带 X-Proxy-Provider 头）
  3. 流式请求 SSE 透传
  4. kimi 403 → settings.json minimax 兜底（X-Proxy-Fallback-From: kimi）
  5. legacy 模式行为不变（无 chain 头）

用法：python tests/proxy_e2e_test.py   （从仓库根目录）
mock 端口固定 9999（对应 KIMI_PROXY_BASE_URL=http://127.0.0.1:9999）。
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOCK_PORT = 9999
SERVER_PORT = 18099

PASS = []
FAIL = []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f'  [{"PASS" if cond else "FAIL"}] {name}' + (f'  [{detail}]' if detail and not cond else ''))


class MockUpstream(BaseHTTPRequestHandler):
    """path 决定行为：/403/* → 403；/sse/* → anthropic SSE；其余 → anthropic 200；
    /minimax/* → openai 200。"""

    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0))
        self.rfile.read(n)
        if self.path.startswith('/minimax'):
            self._json(200, {'id': 'mm', 'choices': [{'index': 0, 'message': {
                'role': 'assistant', 'content': 'minimax 兜底回复'}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 11, 'completion_tokens': 7, 'total_tokens': 18}})
        elif self.path.startswith('/403'):
            self._json(403, {'error': {'message': 'quota exhausted'}})
        elif self.path.startswith('/sse'):
            sys.path.insert(0, ROOT)
            import provider_adapters as pa
            payload = pa.anthropic_sse_from_text('流式回复内容', 'kimi-for-coding',
                                                 input_tokens=20, output_tokens=9)
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            third = max(1, len(payload) // 3)
            for i in range(0, len(payload), third):
                self.wfile.write(payload[i:i + third])
                self.wfile.flush()
                time.sleep(0.01)
        else:
            self._json(200, {'id': 'm1', 'type': 'message', 'role': 'assistant',
                             'content': [{'type': 'text', 'text': 'kimi 回复内容'}],
                             'model': 'kimi-for-coding', 'stop_reason': 'end_turn',
                             'usage': {'input_tokens': 20, 'output_tokens': 9}})

    def _json(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


def wait_port(port, timeout=45):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def http(method, port, path, body=None, headers=None, timeout=30):
    url = f'http://127.0.0.1:{port}{path}'
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def start_server(engine, kimi_base, data_dir):
    env = dict(os.environ)
    env['KIMI_PROXY_BASE_URL'] = kimi_base
    env['PROXY_ENGINE'] = engine
    proc = subprocess.Popen(
        [sys.executable, 'solobrave-server.py', '--data', data_dir, str(SERVER_PORT)],
        cwd=ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not wait_port(SERVER_PORT):
        proc.kill()
        raise RuntimeError(f'server 启动失败 (engine={engine})')
    return proc


def register_and_login():
    """注册临时用户再登录拿 JWT（临时 data 目录，无污染）"""
    uname = f'e2e_{int(time.time())}'
    http('POST', SERVER_PORT, '/api/auth/register',
         {'username': uname, 'password': 'e2e-pass-123', 'nickname': 'e2e'})
    st, _, body = http('POST', SERVER_PORT, '/api/auth/login',
                       {'username': uname, 'password': 'e2e-pass-123'})
    token = ''
    try:
        d = json.loads(body)
        token = d.get('token') or d.get('data', {}).get('token') or ''
    except Exception:
        pass
    if not token:
        print(f'    [debug] login status={st} body={body[:200]}')
    return st, token


def run_round(engine, scenario, data_dir, tag):
    """scenario: direct(直连200) / stream(SSE流式) / f403(403降级)"""
    suffix = {'direct': '', 'stream': '/sse', 'f403': '/403'}[scenario]
    kimi_base = f'http://127.0.0.1:{MOCK_PORT}{suffix}'
    print(f'── engine={engine} scenario={scenario} kimi_base={kimi_base} ──')
    proc = start_server(engine, kimi_base, data_dir)
    try:
        st, token = register_and_login()
        check(f'{tag} 注册/登录可用({st})', st in (200, 201) and bool(token))
        auth = {'Authorization': f'Bearer {token}'}

        st, _, _ = http('GET', SERVER_PORT, '/api/products', headers=auth)
        check(f'{tag} /api/products 200', st == 200, f'status={st}')
        st, _, _ = http('GET', SERVER_PORT, '/api/tasks', headers=auth)
        check(f'{tag} /api/tasks 200', st == 200, f'status={st}')

        kimi_body = {'model': 'kimi-for-coding',
                     'messages': [{'role': 'user', 'content': 'hi'}]}
        headers = {'x-api-key': 'plain-e2e-key'}

        if scenario == 'direct':
            st, hd, body = http('POST', SERVER_PORT, '/api/proxy/kimi/v1/messages',
                                kimi_body, headers)
            d = json.loads(body) if body else {}
            check(f'{tag} /api/proxy/kimi 非流式 200',
                  st == 200 and bool(d.get('content', [{}])[0].get('text')),
                  f'status={st} body={body[:120]}')
            if engine == 'chain':
                check(f'{tag} chain 命中头 X-Proxy-Provider=kimi',
                      hd.get('X-Proxy-Provider') == 'kimi',
                      f'headers={ {k: v for k, v in hd.items() if k.startswith("X-Proxy")} }')
            else:
                check(f'{tag} legacy 无 chain 头', 'X-Proxy-Provider' not in hd)
        elif scenario == 'stream':
            st, hd, body = http('POST', SERVER_PORT, '/api/proxy/kimi/v1/messages',
                                dict(kimi_body, stream=True), headers)
            check(f'{tag} 流式 SSE 透传', st == 200 and b'message_start' in body
                  and '流式回复内容'.encode() in body, f'status={st} body={body[:120]}')
            if engine == 'legacy':
                check(f'{tag} legacy 流式无 chain 头', 'X-Proxy-Provider' not in hd)
        elif scenario == 'f403':
            st, hd, body = http('POST', SERVER_PORT, '/api/proxy/kimi/v1/messages',
                                kimi_body, headers)
            if engine == 'chain':
                check(f'{tag} 403 → minimax 兜底 + 降级头',
                      st == 200 and 'minimax 兜底回复'.encode() in body
                      and hd.get('X-Proxy-Fallback-From') == 'kimi',
                      f'status={st} body={body[:120]}')
            else:
                check(f'{tag} legacy 403 兜底(minimax 非流式 JSON)',
                      st == 200 and 'minimax 兜底回复'.encode() in body, f'status={st}')
    finally:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def main():
    mock = HTTPServer(('127.0.0.1', MOCK_PORT), MockUpstream)
    threading.Thread(target=mock.serve_forever, daemon=True).start()

    base_dir = os.path.join(ROOT, '.tmp', 'e2e_data')
    shutil.rmtree(base_dir, ignore_errors=True)

    for engine in ('chain', 'legacy'):
        # 每轮独立 data 目录；settings.json 配 minimax 兜底指 mock
        data_dir = os.path.join(base_dir, engine)
        os.makedirs(data_dir, exist_ok=True)
        with open(os.path.join(data_dir, 'settings.json'), 'w', encoding='utf-8') as f:
            json.dump({'llm': {'providers': [{
                'name': 'minimax', 'apiKey': 'mm-e2e-key',
                'baseUrl': f'http://127.0.0.1:{MOCK_PORT}/minimax',
                'model': 'MiniMax-Text-01', 'priority': 1}]}}, f)

        for scenario, tag in (('direct', '直连'), ('stream', '流式'), ('f403', '403降级')):
            run_round(engine, scenario, data_dir, f'[{engine}:{tag}]')

    mock.shutdown()
    shutil.rmtree(base_dir, ignore_errors=True)

    print()
    print(f'通过 {len(PASS)}/{len(PASS) + len(FAIL)}')
    if FAIL:
        print('失败用例:', FAIL)
        sys.exit(1)
    print('ALL GREEN')


if __name__ == '__main__':
    main()
