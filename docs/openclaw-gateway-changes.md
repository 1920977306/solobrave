# OpenClaw Gateway 配置变更日志

> 老大手工参照: `~/.openclaw/openclaw.json` **不在 git**, 老大需要在多台机器手动同步。
> 每次改完要写到这个文件一份。

---

## 2026-09-18 21:48 — 安全收紧 (commit `86d32f8` retry backoff 配套)

**改动**: `gateway.bind: "lan" → "loopback"`, `gateway.controlUi.allowedOrigins: ["*"] → ["http://localhost:8080", "https://localhost:8443", "http://127.0.0.1:8080"]`

**原因**:
- `bind="lan"` 让 18789 端口暴露到 LAN, 任何设备可直连 (含 token 截获风险)
- `allowedOrigins=["*"]` 让任何 origin 浏览器都能访问 OpenClaw UI (CORS 全开)

**验证**:
```
curl 127.0.0.1:18789/health       → {"ok":true,"status":"live"} ✓
curl 192.168.1.25:18789/health   → 拒绝 (loopback-only) ✓
```

LAN 设备要访问 OpenClaw 必须走本服务的 `wss://:8444` 代理 (已配 Origin 透传)。

**重启 gateway**:
```bash
kill <pid>  # 找 ps aux | grep openclaw
/opt/homebrew/bin/openclaw gateway --port 18789 > /tmp/openclaw-gateway.log 2>&1 &
```

---

## 修改模板 (老大后续手动同步用)

```diff
 {
   "gateway": {
     "auth": {...},
     "mode": "local",
     "port": 18789,
-    "bind": "lan",
+    "bind": "loopback",
     "tailscale": {...},
     "controlUi": {
-      "allowedOrigins": ["*"]
+      "allowedOrigins": [
+        "http://localhost:8080",
+        "https://localhost:8443",
+        "http://127.0.0.1:8080"
+      ]
     }
   }
```

---

## 相关 commit

- `86d02f8` perf(connect): retry-with-backoff + OpenClaw gateway bind=loopback
  (其中 openclaw.json 不在 git, 但 solobrave-server.py retry 逻辑在 git)