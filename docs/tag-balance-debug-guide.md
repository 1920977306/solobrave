# 标签不平衡定位方法：以 app-main 被踢出 app-container 为例

## 一、现象确认（浏览器端）

```js
// Console 运行：检查 app-container 的实际子节点
document.querySelector('.app-container').children
```

- **正常**：包含 `aside`、`div`(lounge-drawer)、`div`(top-banner)、`main`
- **异常**：只包含 `aside`，`main` 跑到了 `body` 下

---

## 二、缩小范围：定位到问题标签区间

### 方法 A：二分法注释排查

1. 在 `index.html` 中，把 `app-container` 内部的内容分成两半
2. 注释掉下半部分，刷新浏览器
3. 如果 `main` 仍然被踢出 → 问题在上半部分
4. 如果 `main` 正常 → 问题在下半部分
5. 重复二分，直到锁定到具体区间

### 方法 B：Response 源码审查（排除缓存干扰）

```js
// Console 运行：看服务器实际返回的源码
fetch('/index.html')
  .then(r => r.text())
  .then(t => {
    const start = t.indexOf('class="app-container"');
    const end = t.indexOf('</main>');
    console.log(t.substring(start, end + 20));
  });
```

对比源码和 Elements 面板：
- 如果源码中 `</aside>` 和 `<main>` 之间有 `</div>` → **源码问题**
- 如果源码正确但 Elements 显示异常 → **JS 运行时修改了 DOM**

---

## 三、计数验证：统计目标区间内 div 开闭数量

### 浏览器 Console 版

```js
// 统计 <aside> 内部的 div 开闭数量
const html = document.documentElement.outerHTML;
const asideStart = html.indexOf('<aside');
const asideEnd = html.indexOf('</aside>', asideStart) + 8;
const segment = html.substring(asideStart, asideEnd);

const opens = (segment.match(/<div[>\s]/g) || []).length;
const closes = (segment.match(/<\/div>/g) || []).length;
console.log('aside 内 div 开启:', opens, '闭合:', closes);
console.log(closes > opens ? '❌ 多出 ' + (closes - opens) + ' 个 </div>' : '✅ 平衡');
```

### Node.js 本地文件版

```bash
node -e "
const fs = require('fs');
const lines = fs.readFileSync('index.html', 'utf-8').split('\n');
let inAside = false, open = 0, close = 0;
for (let i = 0; i < lines.length; i++) {
  if (lines[i].includes('<aside')) { inAside = true; console.log('=== <aside> start at line', i+1); }
  if (inAside) {
    open += (lines[i].match(/<div[>\s]/g) || []).length;
    close += (lines[i].match(/<\\/div>/g) || []).length;
  }
  if (lines[i].includes('</aside>')) {
    console.log('=== </aside> end at line', i+1);
    console.log('div opens:', open, 'closes:', close);
    console.log(close > open ? '❌ 多出 ' + (close-open) + ' 个 </div>' : '✅ 平衡');
    break;
  }
}
"
```

---

## 四、逐行追踪：找到具体是哪一行多余

### 带栈追踪的脚本

```bash
node -e "
const fs = require('fs');
const lines = fs.readFileSync('index.html', 'utf-8').split('\n');
const stack = [];
let inAside = false;

for (let i = 0; i < lines.length; i++) {
  if (lines[i].includes('<aside')) inAside = true;
  if (!inAside) continue;
  
  const regex = /<\\/?(div)[^>]*>/g;
  let m;
  while ((m = regex.exec(lines[i])) !== null) {
    if (m[0].startsWith('</')) {
      if (stack.length === 0) {
        console.log('❌ 行', i+1, ': 多余的 </div>（栈为空）');
      } else {
        const popped = stack.pop();
        console.log('  行', i+1, ': </div> 匹配 行', popped.line, popped.html.substring(0,50));
      }
    } else if (!m[0].endsWith('/>')) {
      stack.push({line: i+1, html: m[0]});
    }
  }
  
  if (lines[i].includes('</aside>')) break;
}
"
```

**输出示例**（发现问题时）：
```
  行 2179 : </div> 匹配 行 2176 <div class="sidebar-search">
  行 2191 : </div> 匹配 行 2182 <div class="sidebar-actions">
❌ 行 2202 : 多余的 </div>（栈为空）    ← 找到了！
  行 2207 : </div> 匹配 行 2205 <div class="list-section hidden"
```

---

## 五、根因确认口诀

| 现象 | 根因 | 定位方法 |
|------|------|----------|
| `.app-container` 只包含 `aside` | `<aside>` 内部多了 `</div>` | 统计 aside 内 div 开闭数量，用栈追踪找多余 |
| `.app-container` 包含 `aside` + `lounge-drawer` 但 `top-banner` 和 `main` 在外面 | `lounge-drawer` 内部标签未闭合 | 统计 lounge-drawer 内开闭数量 |
| 整个 `.app-container` 提前结束，后面所有元素都在 body 下 | `app-container` 之前有未闭合标签 | 从 `<body>` 开始逐层统计 div 开闭 |
| 源码正确但 Elements 显示异常 | JS 运行时修改 DOM | `MutationObserver` 监听 + Disable JS 测试 |

---

## 六、本次工单的实际定位过程

```
1. F12 确认 .app-container 只包含 aside
   → 推断：某个 </div> 在 <main> 之前提前关闭了 app-container

2. fetch('/index.html') 查看服务器返回的源码
   → 确认源码中确实有问题（排除 JS 运行时因素）

3. 统计 <aside> 内 div 开闭数量
   → 发现 开6 闭7，多出1个 </div>

4. 栈追踪脚本逐行分析
   → 定位到第 2202 行：多余的 </div>（栈为空时遇到闭合）

5. 删除第 2202 行 </div>
   → 重新统计：开6 闭6，净值0，问题解决
```
