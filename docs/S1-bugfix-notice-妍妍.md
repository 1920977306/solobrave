# ⚠️ S1技能代码Bug通报 & 编码规范

妍妍，你做的 S1-2/S1-3 代码有严重bug，导致整个页面JS崩溃，用户登录后看不到正确的界面。已经修好了，但以下问题**绝对不允许再犯**：

## 本次Bug详情

### Bug：onclick引号嵌套导致JS语法错误（致命）

你写的代码：
```javascript
'<button onclick="removeOpenClawSkill('' + escapeAttr(s.slug) + '')" title="卸载">'
```

这行代码有**引号嵌套错误**——外层JS字符串用单引号`'`，onclick里的参数也用单引号`'`，导致JS解析时字符串提前截断，整个页面的JS直接崩掉。

**涉及3处**：
1. `removeOpenClawSkill` 卸载按钮 onclick
2. `installOpenClawSkill` 安装按钮 onclick
3. `toggleWizSkill` 向导技能选择 onclick

**正确写法**（单引号用 `\'` 转义）：
```javascript
'<button onclick="removeOpenClawSkill(\'' + escapeAttr(s.slug) + '\')" title="卸载">'
```

### 影响
JS语法错误导致整个页面无法执行任何脚本 → 员工数据加载不了 → 界面渲染失败 → 用户看到的是错误数据+错乱界面

## 编码规范（必须遵守）

### 1. onclick引号规则
在JS字符串拼接HTML时，onclick里的函数参数必须用 `\'` 转义单引号：
```javascript
// ❌ 错误
'<button onclick="foo(' + bar + ')">'
// ✅ 正确  
'<button onclick="foo(\'' + escapeAttr(bar) + '\')">"'

// 或者用双引号转义
"<button onclick=\"foo('" + escapeAttr(bar) + "')\">"
```

### 2. 禁止使用 `?.` 可选链
用 `(obj && obj.prop)` 替代

### 3. 禁止在 onclick 里用箭头函数
用普通函数调用

### 4. 提交前必须做语法检查
写完代码后，用 `node -c` 或浏览器Console检查JS语法，确保没有语法错误再提交。

### 5. emoji编码问题
Windows环境可能把emoji替换成文本，提交前检查emoji字符是否正确显示。

---
Jarvis已修复上述bug（commit c23130f），请拉取最新代码。
