// 设计还原探针 — InAppBrowser evaluate 粘贴执行，返回 JSON 指标
// 用法: 原型 tab 和 prod tab 各跑一次（视口 1618×940），结果存 JSON 后 diff
// 采集前先把模块切到 messages（聊天批注③④需要）
(async () => {
  const g = el => el ? getComputedStyle(el) : null;
  const gr = el => el ? {
    x: Math.round(el.getBoundingClientRect().x),
    y: Math.round(el.getBoundingClientRect().top),
    w: Math.round(el.getBoundingClientRect().width),
    h: Math.round(el.getBoundingClientRect().height),
  } : null;
  const out = { viewport: { w: window.innerWidth, h: window.innerHeight }, url: location.href };

  // rail（批注①⑤）
  const rail = document.querySelector('.sb2-rail') || document.querySelector('.rail');
  const logo = document.querySelector('.sb2-rail-logo') || document.querySelector('.rail-logo');
  if (rail) { const c = g(rail); out.rail = { geo: gr(rail), top: c.top, paddingTop: c.paddingTop, width: c.width }; }
  if (logo) { const c = g(logo); out.logo = { geo: gr(logo), text: logo.textContent.trim(), w: c.width, h: c.height, radius: c.borderRadius, fontSize: c.fontSize, bg: c.backgroundColor }; }

  // crumb（批注②）— 需在「有侧栏模块」页面执行
  const crumbBox = document.querySelector('#sb2TopbarCrumb');
  if (crumbBox) { const c = g(crumbBox); out.crumb = { display: c.display, visible: !!(crumbBox.offsetWidth || crumbBox.offsetHeight) }; }

  // 聊天员工项（批注③）— 需切到 messages 模块
  const item = document.querySelector('.sb2-side-item') || document.querySelector('.side-item');
  if (item) { const c = g(item); out.chatItem = { geo: gr(item), h: c.height, padding: c.padding, radius: c.borderRadius, fontSize: c.fontSize, lineHeight: c.lineHeight, bg: c.backgroundColor, gap: c.gap, badge: !!item.querySelector('[class*=badge]') }; }
  const act = document.querySelector('.sb2-side-item.active, .side-item.active');
  if (act) { const c = g(act); out.chatItemActive = { bg: c.backgroundColor, color: c.color }; }

  // 聊天主区（批注④）— 需切到 messages 模块
  const shell = document.querySelector('.sb2-chat-shell');
  const card = document.querySelector('#s-chat .card');
  const surf = shell || card;
  if (surf) {
    const c = g(surf);
    const msgs = surf.querySelector('.sb2-chat-messages, [class*=messages]');
    const input = surf.querySelector('.sb2-chat-input, [class*=input]');
    out.chatSurface = { geo: gr(surf), bg: c.backgroundColor, maxW: c.maxWidth, radius: c.borderRadius,
      msgsBg: msgs ? getComputedStyle(msgs).backgroundColor : null,
      inputBg: input ? getComputedStyle(input).backgroundColor : null };
  }
  return out;
})()
