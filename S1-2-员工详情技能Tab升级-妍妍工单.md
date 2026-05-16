# S1-2: 员工详情面板技能Tab升级

## 背景
当前员工详情面板的"技能"Tab是手动添加标签系统（emoji+名字+1-5星等级），没有实际功能。S1-1已经添加了后端技能API，现在需要升级前端UI来对接。

## 修改文件
`index.html`（约10073行）

## 当前技能Tab结构（第2487行附近）
```html
<!-- Skills Tab -->
<div class="emp-tab-pane" id="emp-tab-skills">
  <div class="skills-header">
    <div class="skills-title">🛠️ 技能配置</div>
    <button class="skills-add-btn" onclick="addSkill()">添加技能</button>
  </div>
  <div class="skills-list" id="skillsList"></div>
  <div class="skills-empty" id="skillsEmpty">...</div>
  <div class="skills-presets">
    <div class="skills-presets-title">快速添加</div>
    <div class="skills-presets-grid" id="skillsPresets"></div>
  </div>
</div>
```

## 当前技能JS（第6931行附近）
- `skillPresets` 数组：12个预设技能
- `renderSkills()`：渲染已添加技能列表
- `renderSkillPresets()`：渲染预设快捷按钮
- `addSkill()`：手动添加（prompt输入名字+等级+emoji）
- `addSkillPreset()`：从预设添加
- `deleteSkill()`：删除技能

## 目标UI设计

把技能Tab改为两个区域：

### 区域1：OpenClaw技能（主要区域）
```
┌─────────────────────────────────────┐
│ 🛠️ OpenClaw技能                    │
│ ┌─────────────────────────────────┐ │
│ │ 🔍 搜索技能...         [搜索]   │ │
│ └─────────────────────────────────┘ │
│                                     │
│ 已安装技能：                        │
│ ┌─────────────────────────────────┐ │
│ │ 🌤️ weather-now    v1.2.0   [×] │ │
│ │    天气查询技能                  │ │
│ ├─────────────────────────────────┤ │
│ │ 📊 data-viz       v2.0.1   [×] │ │
│ │    数据可视化生成                │ │
│ └─────────────────────────────────┘ │
│                                     │
│ 搜索结果：                          │
│ ┌─────────────────────────────────┐ │
│ │ 🔍 code-review      [安装]      │ │
│ │    代码审查，发现潜在问题        │ │
│ ├─────────────────────────────────┤ │
│ │ 🔍 pdf-handler      [安装]      │ │
│ │    PDF文件处理与提取             │ │
│ └─────────────────────────────────┘ │
└─────────────────────────────────────┘
```

### 区域2：自定义标签（次要区域，保留现有功能）
```
┌─────────────────────────────────────┐
│ 🏷️ 自定义标签                      │
│ ┌─────────────────────────────────┐ │
│ │ 🔍 代码审查  ★★★    [×]        │ │
│ │ 🎨 UI设计    ★★★    [×]        │ │
│ └─────────────────────────────────┘ │
│ 快速添加：[前端开发] [API设计] ...  │
└─────────────────────────────────────┘
```

## 具体修改步骤

### 步骤1：修改Skills Tab HTML
替换 `id="emp-tab-skills"` 的内容（第2487行附近的 `<div class="emp-tab-pane" id="emp-tab-skills">` 整个div）：

```html
<!-- Skills Tab -->
<div class="emp-tab-pane" id="emp-tab-skills">
  <!-- OpenClaw技能区域 -->
  <div class="emp-doc-section" style="margin-bottom:20px;">
    <div class="skills-header">
      <div class="skills-title">🛠️ OpenClaw技能</div>
      <button class="skills-add-btn" onclick="searchOpenClawSkills()">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        搜索技能
      </button>
    </div>
    <!-- 搜索框 -->
    <div style="display:flex;gap:8px;margin-bottom:12px;" id="ocSkillSearchBox">
      <input type="text" id="ocSkillSearchInput" placeholder="搜索ClawHub技能..." style="flex:1;padding:8px 12px;border:1px solid var(--separator);border-radius:8px;font-size:13px;" onkeydown="if(event.key==='Enter')searchOpenClawSkills()">
      <button onclick="searchOpenClawSkills()" style="padding:8px 16px;background:var(--accent);color:white;border:none;border-radius:8px;font-size:13px;cursor:pointer;">搜索</button>
    </div>
    <!-- 搜索结果 -->
    <div id="ocSkillSearchResults" style="display:none;margin-bottom:12px;">
      <div style="font-size:12px;color:var(--text-secondary);margin-bottom:8px;">搜索结果</div>
      <div id="ocSkillSearchList" style="display:flex;flex-direction:column;gap:6px;"></div>
    </div>
    <!-- 已安装技能 -->
    <div style="font-size:12px;color:var(--text-secondary);margin-bottom:8px;">已安装</div>
    <div id="ocSkillList" style="display:flex;flex-direction:column;gap:6px;"></div>
    <div id="ocSkillEmpty" style="text-align:center;padding:24px 16px;color:var(--text-tertiary);font-size:13px;">
      <div style="font-size:28px;margin-bottom:8px;opacity:0.5;">🛠️</div>
      暂未安装OpenClaw技能
    </div>
  </div>

  <!-- 自定义标签区域（保留原有功能） -->
  <div class="emp-doc-section">
    <div class="skills-header">
      <div class="skills-title">🏷️ 自定义标签</div>
      <button class="skills-add-btn" onclick="addSkill()">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
        添加标签
      </button>
    </div>
    <div class="skills-list" id="skillsList"></div>
    <div class="skills-empty" id="skillsEmpty">
      <div class="skills-empty-icon">🏷️</div>
      <div class="skills-empty-text">还没有添加标签</div>
      <button class="skills-add-btn-lg" onclick="addSkill()">+ 添加第一个标签</button>
    </div>
    <div class="skills-presets">
      <div class="skills-presets-title">快速添加</div>
      <div class="skills-presets-grid" id="skillsPresets"></div>
    </div>
  </div>
</div>
```

### 步骤2：新增OpenClaw技能JS函数
在现有的 `deleteSkill` 函数后面添加以下函数：

```javascript
// ========== OpenClaw技能管理 ==========
var ocSkillsLoaded = false;

function loadOpenClawSkills(){
  fetch('/api/openclaw/skills/list')
    .then(function(r){ return r.json(); })
    .then(function(data){
      var skills = data.skills || [];
      var list = document.getElementById('ocSkillList');
      var empty = document.getElementById('ocSkillEmpty');
      if(!list) return;
      if(skills.length === 0){
        list.innerHTML = '';
        list.style.display = 'none';
        empty.style.display = 'block';
      } else {
        list.style.display = 'flex';
        empty.style.display = 'none';
        list.innerHTML = skills.map(function(s){
          return '<div class="skill-item" style="padding:10px 12px;">' +
            '<div class="skill-emoji">' + escapeHtml(s.emoji || '🔧') + '</div>' +
            '<div class="skill-info">' +
              '<div class="skill-name">' + escapeHtml(s.name || s.slug) + '</div>' +
              '<div style="font-size:11px;color:var(--text-tertiary);">' + escapeHtml(s.description || '') + (s.version ? ' v' + s.version : '') + '</div>' +
            '</div>' +
            '<button class="skill-delete" onclick="removeOpenClawSkill(\'' + escapeAttr(s.slug) + '\')" title="卸载">' +
              '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>' +
            '</button>' +
          '</div>';
        }).join('');
      }
      ocSkillsLoaded = true;
    })
    .catch(function(e){
      console.warn('[Skills] 加载OpenClaw技能失败:', e);
    });
}

function searchOpenClawSkills(){
  var input = document.getElementById('ocSkillSearchInput');
  var query = (input && input.value.trim()) || '';
  if(!query) return;
  var resultsDiv = document.getElementById('ocSkillSearchResults');
  var listDiv = document.getElementById('ocSkillSearchList');
  if(!resultsDiv || !listDiv) return;
  listDiv.innerHTML = '<div style="text-align:center;padding:16px;color:var(--text-tertiary);">搜索中...</div>';
  resultsDiv.style.display = 'block';
  fetch('/api/openclaw/skills/search?q=' + encodeURIComponent(query))
    .then(function(r){ return r.json(); })
    .then(function(data){
      var results = data.results || [];
      if(results.length === 0){
        listDiv.innerHTML = '<div style="text-align:center;padding:16px;color:var(--text-tertiary);">未找到匹配的技能</div>';
      } else {
        listDiv.innerHTML = results.map(function(s){
          return '<div class="skill-item" style="padding:10px 12px;">' +
            '<div class="skill-emoji">' + escapeHtml(s.emoji || '🔍') + '</div>' +
            '<div class="skill-info">' +
              '<div class="skill-name">' + escapeHtml(s.name || s.slug) + '</div>' +
              '<div style="font-size:11px;color:var(--text-tertiary);">' + escapeHtml(s.description || '') + '</div>' +
            '</div>' +
            '<button onclick="installOpenClawSkill(\'' + escapeAttr(s.slug) + '\')" style="padding:6px 12px;background:var(--accent);color:white;border:none;border-radius:6px;font-size:12px;cursor:pointer;">安装</button>' +
          '</div>';
        }).join('');
      }
    })
    .catch(function(e){
      listDiv.innerHTML = '<div style="text-align:center;padding:16px;color:var(--text-tertiary);">搜索失败</div>';
    });
}

function installOpenClawSkill(slug){
  if(!slug) return;
  showToast('正在安装 ' + slug + '...');
  fetch('/api/openclaw/skills/install', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({slug: slug})
  })
  .then(function(r){ return r.json(); })
  .then(function(data){
    if(data.success){
      showToast('✅ ' + slug + ' 安装成功');
      loadOpenClawSkills();
    } else {
      showToast('❌ 安装失败: ' + (data.message || '未知错误'));
    }
  })
  .catch(function(e){
    showToast('❌ 安装失败');
  });
}

function removeOpenClawSkill(slug){
  if(!slug) return;
  if(!confirm('确定要卸载技能 ' + slug + ' 吗？')) return;
  fetch('/api/openclaw/skills/remove', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({slug: slug})
  })
  .then(function(r){ return r.json(); })
  .then(function(data){
    if(data.success){
      showToast('✅ ' + slug + ' 已卸载');
      loadOpenClawSkills();
    } else {
      showToast('❌ 卸载失败: ' + (data.message || '未知错误'));
    }
  })
  .catch(function(e){
    showToast('❌ 卸载失败');
  });
}
```

### 步骤3：修改 switchEmpDetailTab
在现有的 `switchEmpDetailTab` 函数中（第7274行附近），找到 `if(tab==='skills'` 的分支，添加 `loadOpenClawSkills()` 调用：

当前代码：
```javascript
if(tab==='skills'&&currentEmpId){
renderSkills();
renderSkillPresets();
}
```

改为：
```javascript
if(tab==='skills'&&currentEmpId){
renderSkills();
renderSkillPresets();
loadOpenClawSkills();
}
```

### 步骤4：修改 /skill 斜杠命令
找到第6560行：
```javascript
case '/skill': showToast('🛠️ 技能管理开发中...'); break;
```

改为：
```javascript
case '/skill':
  var skillEmp = getCurrentEmployeeInfo();
  if(skillEmp && skillEmp.id){
    openEmpDetail(skillEmp.id);
    setTimeout(function(){ switchEmpDetailTab('skills'); }, 150);
  } else {
    showToast('请先选择一个员工');
  }
  break;
```

## 约束
- **禁止使用 `?.` 可选链** → 用 `(obj && obj.prop)` 替代
- **禁止在 onclick 里用箭头函数** → 用普通函数调用
- onclick引号用 `\'` 转义
- 保留原有的自定义标签功能（skillsList/skillsEmpty/skillsPresets），不要删除
- 保留 `skillPresets` 数组和 `renderSkills/renderSkillPresets/addSkill/addSkillPreset/deleteSkill` 函数
- CSS样式复用现有的 `.skill-item/.skill-emoji/.skill-info/.skill-name/.skill-delete` 等类
- `escapeHtml` 和 `escapeAttr` 函数已存在，直接使用

## 验证
1. 打开员工详情面板 → 技能Tab → 看到两个区域（OpenClaw技能 + 自定义标签）
2. 在搜索框输入关键词 → 点击搜索 → 看到搜索结果
3. 点击"安装"按钮 → 技能安装成功 → 已安装列表刷新
4. 点击已安装技能的"×"按钮 → 确认卸载 → 列表刷新
5. 自定义标签区域：原有功能不变
6. 聊天输入 `/skill` → 自动打开当前员工详情面板的技能Tab

完成后提交，commit message格式：`feat: 技能Tab升级 — OpenClaw技能搜索/安装/卸载 + 自定义标签`
