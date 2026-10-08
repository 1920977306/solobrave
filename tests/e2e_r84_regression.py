#!/usr/bin/env python3
"""r84 回归用例 (老大 2026-10-09 01:10「可以」固化)

覆盖两块:
  A. 签到 owner 口径 (commit c59d4c5) — 客户任一员工签过即整户已签, 重复签拒不加钱
  B. 一批六注 UI (commit d3fe5f8) — 群卡片头/积分跳积分中心/创建向导/群详情/铃铛通知中心/维护工具合一页

用法:
  python3 tests/e2e_r84_regression.py                  # 默认打 127.0.0.1:18210
  BASE=https://192.168.1.25:8443 python3 tests/e2e_r84_regression.py   # 打 prod (需 -k, 见下)
  VERIFY_TLS=-k BASE=https://192.168.1.25:8443 python3 tests/e2e_r84_regression.py

依赖: playwright (pip install playwright && playwright install chromium)
账号: admin / admin123 (TEST_USER/TEST_PASS 环境变量可覆盖)
"""
import json, os, subprocess, sys

BASE = os.environ.get('BASE', 'http://127.0.0.1:18210').rstrip('/')
K = os.environ.get('VERIFY_TLS', '')
USER = os.environ.get('TEST_USER', 'admin')
PASS = os.environ.get('TEST_PASS', 'admin123')
OUT = os.environ.get('SHOT_DIR', '/tmp')

passed = failed = 0
def check(name, cond, extra=''):
    global passed, failed
    if cond: passed += 1; print(f'PASS {name}' + (f' | {extra}' if extra else ''))
    else: failed += 1; print(f'FAIL {name}' + (f' | {extra}' if extra else ''))

def curl(args, body=None):
    cmd = ['curl', '-s', '-m', '8'] + ([K] if K else []) + args
    if body is not None:
        cmd += ['-H', 'Content-Type: application/json', '-d', json.dumps(body)]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    try: return json.loads(out)
    except Exception: return {'_raw': out[:300]}

# ---------- A. 签到 owner 口径 ----------
print('== A. 签到 owner 口径 ==')
login = curl(['-X', 'POST', BASE + '/api/auth/login', '-H', 'Content-Type: application/json',
              '-d', json.dumps({'username': USER, 'password': PASS})])
TOKEN = login.get('token')
if not TOKEN:
    print('FATAL 登录失败:', json.dumps(login, ensure_ascii=False)[:200]); sys.exit(1)
AUTH = ['-H', 'Authorization: Bearer ' + TOKEN]

emps = curl(AUTH + [BASE + '/api/agents'])
emp_ids = [e['id'] for e in emps if e.get('id')][:2] if isinstance(emps, list) else []
if len(emp_ids) < 2:
    print('FATAL 员工不足 2 个, 无法测 owner 口径:', json.dumps(emps, ensure_ascii=False)[:200]); sys.exit(1)
A, B = emp_ids[0], emp_ids[1]

st_a0 = curl(AUTH + [BASE + f'/api/credits/checkin/status?agent_id={A}'])
check('A1 status 接口可用', st_a0.get('already_checked_in') is not None, json.dumps(st_a0, ensure_ascii=False)[:150])

if st_a0.get('already_checked_in'):
    # 本户今天已有人签过 → 全员应已签, 重复签拒
    st_b = curl(AUTH + [BASE + f'/api/credits/checkin/status?agent_id={B}'])
    check('A2 owner 口径: 未签员工 B 也显已签', st_b.get('already_checked_in') is True, json.dumps(st_b, ensure_ascii=False)[:150])
    r = curl(['-X', 'POST', BASE + '/api/credits/checkin'] + AUTH + ['-H', 'Content-Type: application/json'],
             {'agent_id': B})
    check('A3 重复签拒且不加钱', r.get('already_checked_in') is True and (r.get('delta') or 0) == 0,
          json.dumps(r, ensure_ascii=False)[:150])
else:
    # A 未签 → 签 A → B 应变已签 → B 重复签拒
    r1 = curl(['-X', 'POST', BASE + '/api/credits/checkin'] + AUTH + ['-H', 'Content-Type: application/json'], {'agent_id': A})
    check('A2 首签成功 +10', r1.get('ok') is True and (r1.get('delta') or 0) > 0, json.dumps(r1, ensure_ascii=False)[:150])
    st_b = curl(AUTH + [BASE + f'/api/credits/checkin/status?agent_id={B}'])
    check('A3 owner 口径: B 未签也显已签', st_b.get('already_checked_in') is True, json.dumps(st_b, ensure_ascii=False)[:150])
    r2 = curl(['-X', 'POST', BASE + '/api/credits/checkin'] + AUTH + ['-H', 'Content-Type: application/json'], {'agent_id': B})
    check('A4 B 重复签拒且不加钱', r2.get('already_checked_in') is True and (r2.get('delta') or 0) == 0,
          json.dumps(r2, ensure_ascii=False)[:150])

# ---------- B. 一批六注 UI ----------
print('== B. 一批六注 UI ==')
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print('SKIP B: 未装 playwright (pip install playwright && playwright install chromium)')
    print(f'=== A 块 {passed}/{passed+failed} ==='); sys.exit(0 if failed == 0 else 1)

errors = []
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={'width': 1440, 'height': 900})
    pg.on('pageerror', lambda e: errors.append(str(e)))
    pg.goto(BASE + '/?v=r84reg&auth=1')
    pg.evaluate(f'localStorage.setItem("sb_auth_token",{json.dumps(TOKEN)});'
                f'localStorage.setItem("sb_current_user",{json.dumps(json.dumps(login.get("user") or {}))});')
    pg.reload(wait_until='networkidle')
    pg.wait_for_timeout(3000)
    pg.evaluate("document.querySelector('.onboard-wizard-overlay')?.remove()")
    # 造验证群 (无群则卡片断言无对象)
    grp = curl(['-X', 'POST', BASE + '/api/groups'] + AUTH + ['-H', 'Content-Type: application/json'],
               {'name': 'r84回归验证组', 'emoji': '🧪', 'members': [{'id': A}, {'id': B}], 'leadAgentId': A})
    gid = grp.get('id')

    # ① 群卡片头部
    pg.evaluate("if(typeof sb2Go==='function') sb2Go('groups')")
    pg.wait_for_timeout(1500)
    card = pg.evaluate("""() => {
      const c = document.querySelector('#sb2GroupsGrid .sb2-group-card');
      if (!c) return { found:false };
      return { found:true,
        stackInHead: !!c.querySelector('.sb2-group-card-head .sb2-group-card-stack'),
        membersRow: !!c.querySelector('.sb2-group-card-members'),
        titleCtx: !!c.querySelector('.sb2-group-card-head .sb2-group-card-titlectx') };
    }""")
    check('B① 群卡片存在', bool(card.get('found')))
    check('B① 头像堆叠在头部内', bool(card.get('stackInHead')))
    check('B① 独立成员行已移除', card.get('membersRow') is False)
    check('B① 标题区结构', bool(card.get('titleCtx')))
    pg.screenshot(path=f'{OUT}/r84reg_groups.png')

    # ② 积分芯片 → 积分中心悬浮窗
    pg.evaluate("sb2JumpToCreditDashboard()")
    pg.wait_for_timeout(600)
    credit = pg.evaluate("() => ({o: document.getElementById('sb2CreditOverlay')?.classList.contains('open'),"
                         "p: document.getElementById('sb2CreditPanel')?.classList.contains('open')})")
    check('B② 积分中心悬浮窗打开', bool(credit.get('o') and credit.get('p')), json.dumps(credit))
    pg.evaluate("if(typeof sb2CreditClose==='function') sb2CreditClose()")

    # ③ 创建向导
    wiz = pg.evaluate("""() => {
      try {
        if (typeof groups !== 'undefined' && Array.isArray(groups)) {
          groups.length = 0; (window.projects || []).forEach(function(g){ groups.push(g); });
        }
        openGroupWizard();
        return { ok:true, shown: document.getElementById('groupWizardOverlay').classList.contains('show') };
      } catch(e) { return { ok:false, err: e.message }; }
    }""")
    check('B③ 向导打开', bool(wiz.get('ok') and wiz.get('shown')), json.dumps(wiz, ensure_ascii=False)[:120])
    pg.evaluate("gwNext && (document.getElementById('gwName').value='回归组', gwNext())")
    pg.wait_for_timeout(300)
    check('B③ 向导第2步可进', pg.evaluate("() => document.getElementById('gwStep2').style.display !== 'none'"))
    pg.evaluate("closeGroupWizard()")

    # ④ 群详情
    det = pg.evaluate("""() => {
      try {
        if (typeof groups !== 'undefined' && Array.isArray(groups) && !groups.length) {
          (window.projects || []).forEach(function(g){ groups.push(g); });
        }
        if (!groups.length) return { ok:false, err:'no groups' };
        openGroupDetail(groups[0].id);
        return { ok:true, open: document.getElementById('groupDetailOverlay').classList.contains('open') };
      } catch(e) { return { ok:false, err: e.message }; }
    }""")
    check('B④ 群详情打开', bool(det.get('ok') and det.get('open')), json.dumps(det, ensure_ascii=False)[:120])
    tabpill = pg.evaluate("""() => {
      const t = document.querySelector('.group-detail-tab.active');
      return t ? getComputedStyle(t).borderRadius : null;
    }""")
    check('B④ 分段控件 pill 化', tabpill == '999px', f'radius={tabpill}')
    pg.evaluate("closeGroupDetail()")

    # ⑤ 铃铛 → 通知中心
    pg.evaluate("document.getElementById('sb2TopbarBell').click()")
    pg.wait_for_timeout(1200)
    notif = pg.evaluate("() => window._sb2SettingsSideSel")
    check('B⑤ 铃铛进设置通知分类', notif == 'notification', f'sel={notif}')

    # ⑥ 维护工具合一页
    pg.evaluate("if(typeof sb2Go==='function') sb2Go('settings')")
    pg.wait_for_timeout(1200)
    maint = pg.evaluate("""() => {
      const vis = id => { const el = document.getElementById(id); return el ? getComputedStyle(el).display !== 'none' : false; };
      const entries = [...document.querySelectorAll('#sb2Side .sb2-side-item')]
        .filter(e => e.textContent.replace(/\\s+/g,'').includes('维护工具')).length;
      return { sel: window._sb2SettingsSideSel,
        brain: vis('sb2ViewBrain'), rag: vis('sb2ViewRag'), events: vis('sb2ViewEvents'),
        legacyHidden: !vis('sb2SettingsLegacyArea'), entries };
    }""")
    check('B⑥ 默认落点=维护合一页', maint.get('sel') == 'maint', f"sel={maint.get('sel')}")
    check('B⑥ 三视图同屏', bool(maint.get('brain') and maint.get('rag') and maint.get('events')))
    check('B⑥ 系统管理承接区隐藏', bool(maint.get('legacyHidden')))
    check('B⑥ 侧栏单一维护入口', maint.get('entries') == 1, f"entries={maint.get('entries')}")
    pg.screenshot(path=f'{OUT}/r84reg_maint.png')

    # 签到条显已签到 (owner 口径在 UI 的落点)
    pg.evaluate("if(typeof sb2Go==='function') sb2Go('dashboard')")
    pg.wait_for_timeout(2500)
    chip = pg.evaluate("() => (document.getElementById('sb2DashCreditChip')||{textContent:''}).textContent")
    check('B⑦ 工作台签到条显已签到', '已签到' in chip, chip.replace('\n', ' ')[:80])

    print('pageerrors:', len(errors))
    for e in errors[:5]: print('  ', e[:200])
    b.close()

print(f'=== {passed}/{passed+failed} PASS, pageerror={len(errors)} ===')
sys.exit(0 if failed == 0 and not errors else 1)
