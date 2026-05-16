#!/usr/bin/env python3
"""
S1-2 + S1-3 批量修改脚本
"""
import re

# 读取文件
with open('index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# ========== S1-2: 技能Tab升级 ==========

# 1. 替换 Skills Tab HTML
old_skills_tab = '''<!-- Skills Tab -->
<div class="emp-tab-pane" id="emp-tab-skills">
<div class="skills-header">
<div class="skills-title">🛠️ 技能配置</div>
<button class="skills-add-btn" onclick="addSkill()">
<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
添加技能
</button>
</div>
<div class="skills-list" id="skillsList"></div>
<div class="skills-empty" id="skillsEmpty">
<div class="skills-empty-icon">🎯</div>
<div class="skills-empty-text">还没有添加技能</div>
<button class="skills-add-btn-lg" onclick="addSkill()">+ 添加第一个技能</button>
</div>
<div class="skills-presets">
<div class="skills-presets-title">快速添加</div>
<div class="skills-presets-grid" id="skillsPresets"></div>
</div>
</div>'''

new_skills_tab = '''<!-- Skills Tab -->
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
<input type="text" id="ocSkillSearchInput" placeholder="搜索ClawHub技能..." style="flex:1;padding:8px 12px;border:1px solid var(--separator);border-radius:8px;font-size:13px;" onkeydown="if(event.key===\'Enter\')searchOpenClawSkills()">
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
</div>'''

if old_skills_tab in content:
    content = content.replace(old_skills_tab, new_skills_tab)
    print("[OK] S1-2: Skills Tab HTML 替换成功")
else:
    print("[WARN] S1-2: Skills Tab HTML 未找到匹配")

# 2. 在 deleteSkill 函数后添加 OpenClaw 技能管理函数
# 先找到 deleteSkill 函数
oc_skills_js = '''
// ========== OpenClaw技能管理 ==========
var ocSkillsLoaded = false;

function loadOpenClawSkills(){
  fetch(\'/api/openclaw/skills/list\')
    .then(function(r){ return r.json(); })
    .then(function(data){
      var skills = data.skills || [];
      var list = document.getElementById(\'ocSkillList\');
      var empty = document.getElementById(\'ocSkillEmpty\');
      if(!list) return;
      if(skills.length === 0){
        list.innerHTML = \'\';
        list.style.display = \'none\';
        empty.style.display = \'block\';
      } else {
        list.style.display = \'flex\';
        empty.style.display = \'none\';
        list.innerHTML = skills.map(function(s){
          return \'<div class="skill-item" style="padding:10px 12px;">\' +
            \'<div class="skill-emoji">\' + escapeHtml(s.emoji || \'[WRENCH]\') + \'</div>\' +
            \'<div class="skill-info">\' +
              \'<div class="skill-name">\' + escapeHtml(s.name || s.slug) + \'</div>\' +
              \'<div style="font-size:11px;color:var(--text-tertiary);">\' + escapeHtml(s.description || \'\') + (s.version ? \' v\' + s.version : \'\') + \'</div>\' +
            \'</div>\' +
            \'<button class="skill-delete" onclick="removeOpenClawSkill(\'\' + escapeAttr(s.slug) + \'\')" title="卸载">\' +
              \'<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>\' +
            \'</button>\' +
          \'</div>\';
        }).join(\'\');
      }
      ocSkillsLoaded = true;
    })
    .catch(function(e){
      console.warn(\'[Skills] 加载OpenClaw技能失败:\', e);
    });
}

function searchOpenClawSkills(){
  var input = document.getElementById(\'ocSkillSearchInput\');
  var query = (input && input.value.trim()) || \'\';
  if(!query) return;
  var resultsDiv = document.getElementById(\'ocSkillSearchResults\');
  var listDiv = document.getElementById(\'ocSkillSearchList\');
  if(!resultsDiv || !listDiv) return;
  listDiv.innerHTML = \'<div style="text-align:center;padding:16px;color:var(--text-tertiary);">搜索中...</div>\';
  resultsDiv.style.display = \'block\';
  fetch(\'/api/openclaw/skills/search?q=\' + encodeURIComponent(query))
    .then(function(r){ return r.json(); })
    .then(function(data){
      var results = data.results || [];
      if(results.length === 0){
        listDiv.innerHTML = \'<div style="text-align:center;padding:16px;color:var(--text-tertiary);">未找到匹配的技能</div>\';
      } else {
        listDiv.innerHTML = results.map(function(s){
          return \'<div class="skill-item" style="padding:10px 12px;">\' +
            \'<div class="skill-emoji">\' + escapeHtml(s.emoji || \'🔍\') + \'</div>\' +
            \'<div class="skill-info">\' +
              \'<div class="skill-name">\' + escapeHtml(s.name || s.slug) + \'</div>\' +
              \'<div style="font-size:11px;color:var(--text-tertiary);">\' + escapeHtml(s.description || \'\') + \'</div>\' +
            \'</div>\' +
            \'<button onclick="installOpenClawSkill(\'\' + escapeAttr(s.slug) + \'\')" style="padding:6px 12px;background:var(--accent);color:white;border:none;border-radius:6px;font-size:12px;cursor:pointer;">安装</button>\' +
          \'</div>\';
        }).join(\'\');
      }
    })
    .catch(function(e){
      listDiv.innerHTML = \'<div style="text-align:center;padding:16px;color:var(--text-tertiary);">搜索失败</div>\';
    });
}

function installOpenClawSkill(slug){
  if(!slug) return;
  showToast(\'正在安装 \' + slug + \'...\');
  fetch(\'/api/openclaw/skills/install\', {
    method: \'POST\',
    headers: {\'Content-Type\': \'application/json\'},
    body: JSON.stringify({skillName: slug})
  })
  .then(function(r){ return r.json(); })
  .then(function(data){
    if(data.success){
      showToast(\'[OK] \' + slug + \' 安装成功\');
      loadOpenClawSkills();
    } else {
      showToast(\'❌ 安装失败: \' + (data.error || \'未知错误\'));
    }
  })
  .catch(function(e){
    showToast(\'❌ 安装失败\');
  });
}

function removeOpenClawSkill(slug){
  if(!slug) return;
  if(!confirm(\'确定要卸载技能 \' + slug + \' 吗？\')) return;
  fetch(\'/api/openclaw/skills/remove\', {
    method: \'POST\',
    headers: {\'Content-Type\': \'application/json\'},
    body: JSON.stringify({skillName: slug})
  })
  .then(function(r){ return r.json(); })
  .then(function(data){
    if(data.success){
      showToast(\'[OK] \' + slug + \' 已卸载\');
      loadOpenClawSkills();
    } else {
      showToast(\'❌ 卸载失败: \' + (data.error || \'未知错误\'));
    }
  })
  .catch(function(e){
    showToast(\'❌ 卸载失败\');
  });
}
'''

# 找到 deleteSkill 函数的位置，在其后插入
# 先找到 deleteSkill 函数的结束位置
delete_skill_pattern = r'function deleteSkill\(.*?\}\s*\}'
match = re.search(delete_skill_pattern, content, re.DOTALL)
if match:
    insert_pos = match.end()
    content = content[:insert_pos] + oc_skills_js + content[insert_pos:]
    print("[OK] S1-2: OpenClaw技能管理函数添加成功")
else:
    print("[WARN] S1-2: deleteSkill函数未找到")

# 3. 修改 switchEmpDetailTab 函数，添加 loadOpenClawSkills() 调用
old_switch_skills = "if(tab==='skills'&&currentEmpId){\nrenderSkills();\nrenderSkillPresets();\n}"
new_switch_skills = "if(tab==='skills'&&currentEmpId){\nrenderSkills();\nrenderSkillPresets();\nloadOpenClawSkills();\n}"

if old_switch_skills in content:
    content = content.replace(old_switch_skills, new_switch_skills)
    print("[OK] S1-2: switchEmpDetailTab 修改成功")
else:
    print("[WARN] S1-2: switchEmpDetailTab 未找到匹配")

# 4. 修改 /skill 斜杠命令
old_skill_cmd = "case '/skill': showToast('🛠️ 技能管理开发中...'); break;"
new_skill_cmd = """case '/skill':
  var skillEmp = getCurrentEmployeeInfo();
  if(skillEmp && skillEmp.id){
    openEmpDetail(skillEmp.id);
    setTimeout(function(){ switchEmpDetailTab('skills'); }, 150);
  } else {
    showToast('请先选择一个员工');
  }
  break;"""

if old_skill_cmd in content:
    content = content.replace(old_skill_cmd, new_skill_cmd)
    print("[OK] S1-2: /skill 斜杠命令修改成功")
else:
    print("[WARN] S1-2: /skill 斜杠命令未找到匹配")

# ========== S1-3: 向导技能选择 ==========

# 1. 在 wiz3 中添加技能选择区域
# 找到 wiz3 的结束位置（在 wiz4 之前）
wiz3_skill_section = '''
<!-- Step3 技能快速安装 -->
<div style="margin-top:20px;padding-top:16px;border-top:0.5px solid var(--separator);">
<div style="font-size:14px;font-weight:600;margin-bottom:4px;">[WRENCH] 常用技能</div>
<div style="font-size:12px;color:var(--text-secondary);margin-bottom:12px;">创建后可随时在员工详情中管理</div>
<div id="wizSkillGrid" style="display:grid;grid-template-columns:1fr 1fr;gap:8px;"></div>
</div>
'''

# 找到 wiz3 的结束标记
wiz3_end_marker = '<!-- Step 4 -->'
if wiz3_end_marker in content:
    content = content.replace(wiz3_end_marker, wiz3_skill_section + wiz3_end_marker)
    print("[OK] S1-3: wiz3 技能选择区域添加成功")
else:
    print("[WARN] S1-3: wiz3 结束标记未找到")

# 2. 添加热门技能列表和渲染函数
wiz_skill_js = '''
// ========== 向导技能选择 ==========
var wizHotSkills = [
  {slug:'web-search', name:'网页搜索', emoji:'🔍', desc:'搜索互联网信息'},
  {slug:'code-review', name:'代码审查', emoji:'🐛', desc:'代码质量检查'},
  {slug:'doc-writer', name:'文档生成', emoji:'📝', desc:'自动生成文档'},
  {slug:'data-viz', name:'数据可视化', emoji:'📊', desc:'生成图表'},
  {slug:'pdf-handler', name:'PDF处理', emoji:'📄', desc:'PDF读写与提取'},
  {slug:'image-gen', name:'图片生成', emoji:'🎨', desc:'AI生成图片'}
];
var wizSelectedSkills = [];

function renderWizSkillGrid(){
  var grid = document.getElementById('wizSkillGrid');
  if(!grid) return;
  grid.innerHTML = wizHotSkills.map(function(s){
    var selected = wizSelectedSkills.indexOf(s.slug) >= 0;
    return '<div class="wiz-skill-card' + (selected ? ' selected' : '') + '" ' +
      'onclick="toggleWizSkill(\'' + escapeAttr(s.slug) + '\')" ' +
      'style="display:flex;align-items:center;gap:8px;padding:10px 12px;' +
      'border:1px solid ' + (selected ? 'var(--accent)' : 'var(--separator)') + ';' +
      'border-radius:8px;cursor:pointer;transition:all 0.2s;' +
      'background:' + (selected ? 'rgba(0,122,255,0.08)' : 'transparent') + ';">' +
      '<span style="font-size:18px;">' + escapeHtml(s.emoji) + '</span>' +
      '<div style="flex:1;min-width:0;">' +
        '<div style="font-size:13px;font-weight:500;">' + escapeHtml(s.name) + '</div>' +
        '<div style="font-size:11px;color:var(--text-tertiary);">' + escapeHtml(s.desc) + '</div>' +
      '</div>' +
      (selected ? '<span style="color:var(--accent);">✓</span>' : '') +
    '</div>';
  }).join('');
}

function toggleWizSkill(slug){
  var idx = wizSelectedSkills.indexOf(slug);
  if(idx >= 0){
    wizSelectedSkills.splice(idx, 1);
  } else {
    wizSelectedSkills.push(slug);
  }
  renderWizSkillGrid();
}
'''

# 在 openWizard 函数之前插入
open_wizard_marker = 'function openWizard()'
if open_wizard_marker in content:
    content = content.replace(open_wizard_marker, wiz_skill_js + '\n' + open_wizard_marker)
    print("[OK] S1-3: 技能选择函数添加成功")
else:
    print("[WARN] S1-3: openWizard函数未找到")

# 3. 修改 openWizard 函数，重置技能选择
old_open_wizard = 'function openWizard(){\nwizStep=1;\nupdateWizardUI();\n}'
new_open_wizard = 'function openWizard(){\nwizStep=1;\nwizSelectedSkills=[];\nrenderWizSkillGrid();\nupdateWizardUI();\n}'

if old_open_wizard in content:
    content = content.replace(old_open_wizard, new_open_wizard)
    print("[OK] S1-3: openWizard 修改成功")
else:
    print("[WARN] S1-3: openWizard 未找到匹配")

# 4. 修改 finishWizard 函数，安装选中技能
# 找到 finishWizard 中创建 newEmp 的位置，在其后添加技能安装代码
old_finish_wizard = "newEmp.openclawName = emp._agentId || agentName;\n  saveEmployees();"
new_finish_wizard = """newEmp.openclawName = emp._agentId || agentName;
  newEmp.ocSkills = wizSelectedSkills.slice();
  saveEmployees();
  
  // 安装选中技能
  if(wizSelectedSkills.length > 0 && newEmp.openclawName){
    wizSelectedSkills.forEach(function(slug){
      fetch('/api/openclaw/skills/install', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({skillName: slug})
      }).then(function(r){ return r.json(); })
      .then(function(data){
        if(data.success) console.log('[Wizard] 技能安装成功:', slug);
        else console.warn('[Wizard] 技能安装失败:', slug, data.error);
      })
      .catch(function(e){
        console.warn('[Wizard] 技能安装失败:', slug, e);
      });
    });
  }"""

if old_finish_wizard in content:
    content = content.replace(old_finish_wizard, new_finish_wizard)
    print("[OK] S1-3: finishWizard 修改成功")
else:
    print("[WARN] S1-3: finishWizard 未找到匹配")

# 写入文件
with open('index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("\n[DONE] S1-2 + S1-3 修改完成！")
print("请验证：")
print("  1. 员工详情 → 技能Tab → 看到OpenClaw技能+自定义标签两个区域")
print("  2. 搜索技能 → 安装/卸载功能")
print("  3. 创建员工向导 → Step3 → 看到常用技能选择")
print("  4. 选中技能 → 完成创建 → 技能自动安装")
