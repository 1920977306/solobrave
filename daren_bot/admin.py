"""达人机器人运营后台（admin 页，spec 运营工具）。

挂在 daren-bot 现有 HTTP 端口（默认 8092，绑 0.0.0.0，局域网可访问）：
  GET  /healthz              健康检查（原有）
  GET  /admin                管理页（token 鉴权）
  GET  /admin/api/state      租户/积分/任务/流水聚合 JSON
  POST /admin/api/grant      发放积分 {tenant_key, amount}
  POST /admin/api/bind       绑定/换绑抖音号 {tenant_key, douyin_id}

token 存 DATA_DIR/admin_token.txt，首次启动生成；访问
  http://<本机IP>:8092/admin?token=<token>
"""
import json
import logging
import secrets
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from . import config, db, ops_alert

log = logging.getLogger("daren-bot.admin")

_TOKEN_PATH = config.DATA_DIR / "admin_token.txt"


def _load_token() -> str:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if _TOKEN_PATH.exists():
        return _TOKEN_PATH.read_text().strip()
    token = secrets.token_urlsafe(16)
    _TOKEN_PATH.write_text(token)
    try:
        _TOKEN_PATH.chmod(0o600)
    except OSError:
        pass
    log.warning("admin token 首次生成: %s", token)
    return token


def _get_state(ws) -> dict:
    """聚合租户总览 + 最近流水/任务。"""
    tenants = []
    for t in db.get_active_tenants():
        tk = t["tenant_key"]
        week_no = db.current_week_no()
        tenants.append({
            "tenant_key": tk,
            "app_id": t["app_id"],
            "douyin_id": t["bind_douyin_id"],
            "balance": db.get_balance(tk),
            "week_count": db.get_week_count(tk, t["bind_douyin_id"], week_no)
                          if t["bind_douyin_id"] else 0,
            "ws_online": bool(ws and ws.stats().get("detail", {}).get(tk)),
            "bitable": t["bitable_app_token"] or "",
            "created_at": t["created_at"],
        })
    with db.db_ctx() as conn:
        ledger = [dict(r) for r in conn.execute(
            "SELECT * FROM bot_credits_ledger ORDER BY created_at DESC LIMIT 30")]
        tasks = [dict(r) for r in conn.execute(
            "SELECT * FROM bot_collect_tasks ORDER BY created_at DESC LIMIT 30")]
    # ★ 运营告警（2026-10-25）：采集通道掉登录这类事，**运营要一眼看到**
    alerts = ops_alert.recent(10)
    return {"tenants": tenants, "ledger": ledger, "tasks": tasks,
            "alerts": alerts,
            "week_no": db.current_week_no()}


_PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>达人机器人 · 运营后台</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { --b:#e5e7eb; --blue:#2563eb; --ok:#16a34a; --bad:#dc2626; }
  * { box-sizing:border-box; }
  body { font:14px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
         margin:0; background:#f5f6f8; color:#111827; }
  header { background:#111827; color:#fff; padding:14px 20px;
           display:flex; justify-content:space-between; align-items:center; }
  header h1 { font-size:16px; margin:0; }
  header .meta { font-size:12px; color:#9ca3af; }
  main { max-width:1200px; margin:20px auto; padding:0 16px; }
  section { background:#fff; border:1px solid var(--b); border-radius:10px;
            padding:16px; margin-bottom:16px; }
  h2 { font-size:14px; margin:0 0 12px; color:#374151; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th,td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--b);
          white-space:nowrap; }
  th { color:#6b7280; font-weight:500; }
  .pill { display:inline-block; padding:1px 8px; border-radius:999px;
          font-size:12px; }
  .on { background:#dcfce7; color:var(--ok); }
  .off { background:#fee2e2; color:var(--bad); }
  .bal { font-weight:600; }
  button { border:1px solid var(--b); background:#fff; border-radius:6px;
           padding:4px 10px; cursor:pointer; font-size:12px; }
  button:hover { border-color:var(--blue); color:var(--blue); }
  button.primary { background:var(--blue); color:#fff; border-color:var(--blue); }
  .hint { color:#6b7280; font-size:12px; margin-top:8px; }
  .toast { position:fixed; top:16px; left:50%; transform:translateX(-50%);
           background:#111827; color:#fff; padding:8px 16px; border-radius:8px;
           font-size:13px; display:none; z-index:9; }
  td.err { white-space:normal; color:var(--bad); max-width:280px;
           overflow:hidden; text-overflow:ellipsis; }
</style>
</head>
<body>
<header>
  <h1>达人机器人 · 运营后台</h1>
  <div class="meta" id="meta">加载中…</div>
</header>
<main>
  <section>
    <h2>租户总览</h2>
    <table id="tenants">
      <thead><tr>
        <th>租户</th><th>App ID</th><th>绑定抖音号</th><th>余额</th>
        <th>本周抓取</th><th>长连接</th><th>多维表格</th><th>操作</th>
      </tr></thead>
      <tbody></tbody>
    </table>
    <div class="hint">本周抓取按 ISO 周计数，每号每周限 1 次；多维表格 ✓ 表示已建表。</div>
  </section>
  <section>
    <h2>最近积分流水（30 条）</h2>
    <table id="ledger">
      <thead><tr><th>时间</th><th>租户</th><th>动作</th><th>变动</th><th>余额</th></tr></thead>
      <tbody></tbody>
    </table>
  </section>
  <section>
    <h2>最近任务（30 条）</h2>
    <table id="tasks">
      <thead><tr><th>时间</th><th>租户</th><th>类型</th><th>目标</th><th>状态</th><th>扣分</th><th>报错</th></tr></thead>
      <tbody></tbody>
    </table>
  </section>
  <section>
    <h2>运营告警（最近 10 条）</h2>
    <table id="alerts">
      <thead><tr><th>时间</th><th>级别</th><th>标题</th><th>详情</th></tr></thead>
      <tbody></tbody>
    </table>
    <div class="hint">采集通道掉登录会出现在这里（同时弹本机通知）。扫码登录达人工作台后自动恢复。</div>
  </section>
</main>
<div class="toast" id="toast"></div>
<script>
const token = new URLSearchParams(location.search).get('token') || '';
function esc(s){ return String(s??'').replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function fmt(ts){ const d=new Date(ts*1000); const p=n=>String(n).padStart(2,'0');
  return `${d.getMonth()+1}-${d.getDate()} ${p(d.getHours())}:${p(d.getMinutes())}`; }
function toast(msg, bad){ const t=document.getElementById('toast');
  t.textContent=msg; t.style.background=bad?'#dc2626':'#111827';
  t.style.display='block'; setTimeout(()=>t.style.display='none', 2500); }
async function api(path, body){
  const opt = body ? {method:'POST', headers:{'Content-Type':'application/json'},
                      body: JSON.stringify(body)} : {};
  const r = await fetch(`${path}?token=${encodeURIComponent(token)}`, opt);
  const j = await r.json().catch(()=>({ok:false, error:'响应非JSON'}));
  if(!r.ok || j.ok===false) throw new Error(j.error || `HTTP ${r.status}`);
  return j;
}
async function grant(tk){
  const v = prompt(`给 ${tk} 发放积分（正数）：`, '100');
  if(v===null) return;
  const amount = parseInt(v,10);
  if(!amount || amount<=0) return toast('数量无效', true);
  try { const j = await api('/admin/api/grant', {tenant_key:tk, amount});
        toast(`已发放，当前余额 ${j.balance}`); load(); }
  catch(e){ toast(e.message, true); }
}
async function bind(tk){
  const v = prompt(`给 ${tk} 绑定/换绑抖音号（纯数字或字母组合）：`, '');
  if(v===null) return;
  try { await api('/admin/api/bind', {tenant_key:tk, douyin_id:v.trim()});
        toast('已绑定'); load(); }
  catch(e){ toast(e.message, true); }
}
function render(s){
  document.getElementById('meta').textContent =
    `ISO 周 ${s.week_no} · ${s.tenants.length} 个租户 · 每 15 秒自动刷新`;
  const tt = {douyin:'抖音号采集', product:'商品链接', image:'商品截图', qa:'问答'};
  document.querySelector('#tenants tbody').innerHTML = s.tenants.map(t=>`<tr>
    <td><b>${esc(t.tenant_key)}</b></td><td>${esc(t.app_id)}</td>
    <td>${esc(t.douyin_id)||'<span style="color:#9ca3af">未绑定</span>'}</td>
    <td class="bal">${t.balance}</td><td>${t.week_count}/1</td>
    <td><span class="pill ${t.ws_online?'on':'off'}">${t.ws_online?'在线':'离线'}</span></td>
    <td>${t.bitable?'✓':'—'}</td>
    <td><button onclick="grant('${esc(t.tenant_key)}')">充值</button>
        <button onclick="bind('${esc(t.tenant_key)}')">绑号</button></td>
  </tr>`).join('');
  document.querySelector('#ledger tbody').innerHTML = s.ledger.map(l=>`<tr>
    <td>${fmt(l.created_at)}</td><td>${esc(l.tenant_key)}</td>
    <td>${esc(l.action)}${l.cache_hit?' <span class="pill on">缓存</span>':''}</td>
    <td style="color:${l.cost>0?'#dc2626':'#16a34a'}">${l.cost>0?'-':'+'}${Math.abs(l.cost)}</td>
    <td>${l.balance_after}</td></tr>`).join('') ||
    '<tr><td colspan="5" style="color:#9ca3af">暂无流水</td></tr>';
  document.querySelector('#tasks tbody').innerHTML = s.tasks.map(t=>`<tr>
    <td>${fmt(t.created_at)}</td><td>${esc(t.tenant_key)}</td>
    <td>${tt[t.type]||esc(t.type)}</td>
    <td style="max-width:240px;overflow:hidden;text-overflow:ellipsis">${esc(t.target)}</td>
    <td><span class="pill ${t.status==='done'?'on':(t.status==='failed'?'off':'')}">${esc(t.status)}</span></td>
    <td>${t.credits_cost||''}</td><td class="err">${esc(t.error)}</td></tr>`).join('') ||
    '<tr><td colspan="7" style="color:#9ca3af">暂无任务</td></tr>';
  const al = s.alerts || [];
  document.querySelector('#alerts tbody').innerHTML = al.map(a=>`<tr>
    <td>${esc(a.time||'')}</td>
    <td><span class="pill ${String(a.kind||'').indexOf('login_lost')>=0?'off':'on'}">${esc(a.kind||'')}</span></td>
    <td>${esc(a.title)}</td><td class="err">${esc(a.body)}</td></tr>`).join('') ||
    '<tr><td colspan="4" style="color:#9ca3af">暂无告警</td></tr>';
}
async function load(){
  try { render(await api('/admin/api/state')); }
  catch(e){ toast(e.message, true); }
}
load(); setInterval(load, 15000);
</script>
</body>
</html>"""


def make_handler(state: dict):
    """返回同时处理 /healthz 和 /admin* 的 Handler 类。state: {"ws": WsManager}"""
    token = _load_token()

    class _AdminHandler(BaseHTTPRequestHandler):
        def _auth(self, query: dict) -> bool:
            got = (query.get("token") or [""])[0]
            return bool(got) and secrets.compare_digest(got, token)

        def _json(self, code: int, obj: dict):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path.rstrip("/") in ("", "/healthz"):
                return self._json(200, {
                    "ok": True, "service": "daren-bot",
                    "db": str(config.DB_PATH),
                    "ws": state["ws"].stats() if state["ws"] else {}})
            if u.path == "/admin":
                q = parse_qs(u.query)
                if not self._auth(q):
                    return self._json(403, {"ok": False, "error": "token 无效"})
                body = _PAGE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                return self.wfile.write(body)
            if u.path == "/admin/api/state":
                q = parse_qs(u.query)
                if not self._auth(q):
                    return self._json(403, {"ok": False, "error": "token 无效"})
                try:
                    return self._json(200, {"ok": True, **_get_state(state["ws"])})
                except Exception as e:  # noqa: BLE001 后台页不抛栈给前端
                    log.exception("state failed")
                    return self._json(500, {"ok": False, "error": str(e)})
            self._json(404, {"ok": False, "error": "not found"})

        def do_POST(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path not in ("/admin/api/grant", "/admin/api/bind",
                              "/admin/api/coop-scan",
                              "/admin/api/plan-scan"):
                return self._json(404, {"ok": False, "error": "not found"})
            if not self._auth(q):
                return self._json(403, {"ok": False, "error": "token 无效"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                return self._json(400, {"ok": False, "error": "请求体必须是 JSON"})
            if u.path == "/admin/api/coop-scan":
                # 合作机会扫描：运营改完状态想立刻推用。
                # ⚠️ 默认 dry_run=true（只决策不推送）——「一按就推」太危险，
                #    真推必须显式传 {"dry_run": false}。
                from . import coop_notify
                dry = body.get("dry_run", True)
                dry = True if dry is None else bool(dry)
                try:
                    stat = coop_notify.scan_and_push(state["ws"], dry_run=dry)
                except Exception as e:  # noqa: BLE001
                    log.exception("coop scan failed")
                    return self._json(500, {"ok": False, "error": str(e)})
                log.info("admin coop-scan dry_run=%s -> %s", dry,
                         {k: v for k, v in stat.items() if k != "details"})
                return self._json(200, {"ok": True, "dry_run": dry, **stat})
            if u.path == "/admin/api/plan-scan":
                # 排品扫描：达人加了新品想立刻看排品表 / 验收提醒用。
                # ⚠️ 默认 dry_run=true（只决策不推送），真推要显式传 dry_run=false。
                from . import plan_notify
                dry = body.get("dry_run", True)
                dry = True if dry is None else bool(dry)
                try:
                    stat = plan_notify.scan_and_push(state["ws"], dry_run=dry)
                except Exception as e:  # noqa: BLE001
                    log.exception("plan scan failed")
                    return self._json(500, {"ok": False, "error": str(e)})
                log.info("admin plan-scan dry_run=%s -> %s", dry,
                         {k: v for k, v in stat.items() if k != "details"})
                return self._json(200, {"ok": True, "dry_run": dry, **stat})
            tk = str(body.get("tenant_key") or "").strip()
            if not db.get_tenant(tk):
                return self._json(400, {"ok": False, "error": f"租户不存在: {tk}"})
            if u.path == "/admin/api/grant":
                amount = int(body.get("amount") or 0)
                try:
                    bal = db.grant(tk, amount)
                except ValueError as e:
                    return self._json(400, {"ok": False, "error": str(e)})
                log.info("admin grant: %s +%d -> %d", tk, amount, bal)
                return self._json(200, {"ok": True, "balance": bal})
            dyid = str(body.get("douyin_id") or "").strip()
            if not dyid:
                return self._json(400, {"ok": False, "error": "抖音号不能为空"})
            db.bind_douyin_id(tk, dyid)
            log.info("admin bind: %s -> %s", tk, dyid)
            return self._json(200, {"ok": True, "douyin_id": dyid})

        def log_message(self, *args):  # 静默访问日志
            pass

    return _AdminHandler
