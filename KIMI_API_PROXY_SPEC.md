# Kimi API 代理端点 — 积分管控方案

## 目标

飞书消息走OpenClaw直连Kimi API，绕过SoloBrave积分系统。需要在SoloBrave server新增代理端点，拦截所有经过OpenClaw的Kimi API调用，实现积分检查+扣减。

## 架构

```
飞书消息 → OpenClaw网关 → SoloBrave代理(http://localhost:8081/api/proxy/kimi)
                           → 1. 从API Key提取agent_id
                           → 2. 检查积分余额（余额0则返回403）
                           → 3. 替换为真实Kimi API Key，转发到 https://api.kimi.com/coding/v1/messages
                           → 4. 返回响应给OpenClaw
                           → 5. 从响应中提取token用量，扣减积分
```

## 需要修改的文件

### 1. solobrave-server.py — 新增代理端点

在路由分发中添加 `/api/proxy/kimi` 路径处理。

#### 代理端点逻辑

```python
def _handle_proxy_kimi(self):
    """POST /api/proxy/kimi/* — Kimi API代理，带积分管控"""

    # 1. 从请求头提取API Key（anthropic格式用x-api-key）
    proxy_key = self.headers.get('x-api-key', '') or ''
    if not proxy_key:
        auth_header = self.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            proxy_key = auth_header[7:]

    # 2. 从proxy_key提取agent_id
    # proxy_key格式: "proxy_emp_xxxxx"
    if proxy_key.startswith('proxy_'):
        agent_id = proxy_key[6:]  # 去掉"proxy_"前缀
    else:
        # 非代理key，直接转发（兼容旧配置）
        agent_id = None

    # 3. 检查积分余额
    if agent_id:
        balance, has_credits = _check_credit_balance(agent_id)
        if not has_credits:
            # 返回anthropic格式错误
            self.send_response(403)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                'type': 'error',
                'error': {
                    'type': 'insufficient_credits',
                    'message': '积分余额不足，请联系管理员充值'
                }
            }).encode())
            return

    # 4. 读取请求体
    body = self._read_body()
    if not body:
        self._send_json_error(400, 'Empty body')
        return

    # 5. 构造转发请求到真实Kimi API
    # 真实API Key从环境变量或硬编码获取
    KIMI_REAL_API_KEY = 'sk-02e90cc3c5b147fcb945c7334ed94008'
    KIMI_REAL_URL = 'https://api.kimi.com/coding/v1/messages'

    # 提取原始请求路径中的子路径（如/v1/messages）
    path_suffix = self.path.replace('/api/proxy/kimi', '', 1)
    if not path_suffix:
        path_suffix = '/v1/messages'
    target_url = 'https://api.kimi.com/coding' + path_suffix

    # 构造请求头（用真实API Key替换proxy key）
    forward_headers = {
        'Content-Type': 'application/json',
        'x-api-key': KIMI_REAL_API_KEY,
        'anthropic-version': self.headers.get('anthropic-version', '2023-06-01'),
    }

    req_body = json.dumps(body).encode('utf-8')
    forward_headers['Content-Length'] = str(len(req_body))

    # 6. 判断是否为流式请求
    is_streaming = body.get('stream', False)

    # 7. 转发请求
    req = urllib.request.Request(target_url, data=req_body, headers=forward_headers, method='POST')

    try:
        resp = urllib.request.urlopen(req, timeout=120)
    except urllib.error.HTTPError as e:
        # 转发Kimi的错误响应
        self.send_response(e.code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(e.read())
        return
    except Exception as e:
        self._send_json_error(502, f'Proxy error: {str(e)}')
        return

    # 8. 处理响应
    if is_streaming:
        # 流式响应：逐行转发SSE，同时解析usage
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()

        input_tokens = 0
        output_tokens = 0
        buffer = b''

        for chunk in iter(lambda: resp.read(4096), b''):
            # 转发给客户端
            self.wfile.write(chunk)
            self.wfile.flush()

            # 解析SSE事件提取usage
            buffer += chunk
            while b'\n\n' in buffer:
                event_str, buffer = buffer.split(b'\n\n', 1)
                try:
                    event_text = event_str.decode('utf-8')
                    if event_text.startswith('data: '):
                        data_str = event_text[6:].strip()
                        if data_str and data_str != '[DONE]':
                            data_json = json.loads(data_str)
                            if data_json.get('type') == 'message_start':
                                usage = data_json.get('message', {}).get('usage', {})
                                input_tokens = usage.get('input_tokens', 0)
                            elif data_json.get('type') == 'message_delta':
                                usage = data_json.get('usage', {})
                                output_tokens = usage.get('output_tokens', output_tokens)
                except Exception:
                    pass

        # 9. 扣减积分
        if agent_id and (input_tokens or output_tokens):
            conn = _db_conn()
            try:
                _record_credit_usage(conn, agent_id, input_tokens, output_tokens, 0)
                conn.commit()
            finally:
                conn.close()

    else:
        # 非流式响应：读取完整响应，提取usage，扣减积分，返回
        resp_body = resp.read()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(resp_body)

        # 解析usage
        try:
            resp_json = json.loads(resp_body)
            usage = resp_json.get('usage', {})
            input_tokens = usage.get('input_tokens', 0)
            output_tokens = usage.get('output_tokens', 0)

            if agent_id and (input_tokens or output_tokens):
                conn = _db_conn()
                try:
                    _record_credit_usage(conn, agent_id, input_tokens, output_tokens, 0)
                    conn.commit()
                finally:
                    conn.close()
        except Exception:
            pass
```

#### 路由注册

在 `do_POST` 方法中添加路由匹配：
```python
if self.path.startswith('/api/proxy/kimi'):
    self._handle_proxy_kimi()
    return
```

同样在 `do_OPTIONS` 中确保CORS预检通过。

### 2. openclaw.json — 修改Kimi provider配置

**用Python脚本修改，不能用edit_file。**

需要为每个agent创建独立的proxy provider，使用唯一的proxy API key来识别agent身份：

```python
import json

p = '/Users/qichen/.openclaw/openclaw.json'
with open(p) as f:
    c = json.load(f)

# 保留原始kimi provider作为fallback
# 为每个agent创建独立的proxy provider
kimi_models = c['providers']['kimi']['models']

proxy_providers = {
    'kimi_proxy_helen': {
        'baseUrl': 'http://localhost:8081/api/proxy/kimi',
        'api': 'anthropic-messages',
        'apiKey': 'proxy_emp_1780199176680',
        'models': kimi_models
    },
    'kimi_proxy_shangguan': {
        'baseUrl': 'http://localhost:8081/api/proxy/kimi',
        'api': 'anthropic-messages',
        'apiKey': 'proxy_emp_1779955656118',
        'models': kimi_models
    },
    'kimi_proxy_diaochan': {
        'baseUrl': 'http://localhost:8081/api/proxy/kimi',
        'api': 'anthropic-messages',
        'apiKey': 'proxy_emp_1779430403964',
        'models': kimi_models
    },
    'kimi_proxy_kongming': {
        'baseUrl': 'http://localhost:8081/api/proxy/kimi',
        'api': 'anthropic-messages',
        'apiKey': 'proxy_emp_1780132768182',
        'models': kimi_models
    }
}

c['providers'].update(proxy_providers)

# 更新auth profiles，指向各自的proxy provider
c['auth']['profiles']['emp_1780199176680:manual']['provider'] = 'kimi_proxy_helen'
c['auth']['profiles']['emp_1779955656118:manual']['provider'] = 'kimi_proxy_shangguan'
c['auth']['profiles']['emp_1779430403964:manual']['provider'] = 'kimi_proxy_diaochan'
c['auth']['profiles']['emp_1780132768182:manual']['provider'] = 'kimi_proxy_kongming'

with open(p, 'w') as f:
    json.dump(c, f, indent=2, ensure_ascii=False)

print('done')
```

### 3. 积分充值

代理上线后需要给4个员工充值积分，否则飞书聊天全部被403拦截：

```python
import sqlite3
conn = sqlite3.connect('data/solobrave.db')
agents = [
    'emp_1780199176680',  # Helen
    'emp_1779955656118',  # 上官婉儿
    'emp_1779430403964',  # 貂蝉
    'emp_1780132768182',  # 孔明
]
for aid in agents:
    conn.execute("INSERT OR IGNORE INTO credit_accounts (agent_id, balance, total_recharged, total_consumed) VALUES (?, 0, 0, 0)", (aid,))
    conn.execute("UPDATE credit_accounts SET balance = balance + 10000, total_recharged = total_recharged + 10000 WHERE agent_id = ?", (aid,))
    print(f'{aid}: recharged 10000')
conn.commit()
conn.close()
```

## 关键设计决策

1. **为什么用独立provider而不是共用一个？**
   OpenClaw的API Key在provider级别配置，4个agent共用同一个kimi provider时API Key相同，代理无法区分是谁在调用。为每个agent创建独立proxy provider，用`proxy_<agent_id>`作为API Key，代理端从key中提取agent_id。

2. **流式响应处理**
   OpenClaw使用anthropic-messages格式，默认streaming。代理需要逐chunk转发SSE事件，同时解析`message_start`和`message_delta`事件中的usage数据。非流式请求直接读取完整响应。

3. **积分计算**
   复用现有`_record_credit_usage()`函数：`积分 = ceil((input_tokens + output_tokens) / 1000)`。代理在响应完成后扣减，不阻塞响应。

4. **Web端不受影响**
   Web端聊天走SoloBrave `_handle_post_chat` → `_call_chat_completion()`，直接调用Kimi API，不经过OpenClaw provider。Web端已有积分检查，无需改动。

5. **错误处理**
   - 积分不足：返回403 + anthropic格式错误体
   - Kimi API错误：原样转发错误响应
   - 代理内部错误：返回502

## 验证步骤

1. 启动server后，用curl测试代理：
```bash
curl -X POST http://localhost:8081/api/proxy/kimi/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: proxy_emp_1780199176680" \
  -H "anthropic-version: 2023-06-01" \
  -d '{"model":"k3","max_tokens":100,"messages":[{"role":"user","content":"hi"}]}'
```

2. 检查积分是否扣减：
```bash
sqlite3 data/solobrave.db "SELECT * FROM credit_accounts WHERE agent_id='emp_1780199176680'"
sqlite3 data/solobrave.db "SELECT * FROM credit_usage_log ORDER BY id DESC LIMIT 1"
```

3. 积分为0时测试403拦截

4. 修改openclaw.json后重启网关，在飞书发消息验证全链路
