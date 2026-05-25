import re

with open('index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# 找到 renderMemory 函数并替换
old_func = '''function renderMemory(type){
  var contentEl = document.getElementById('memoryContent');
  var emptyEl = document.getElementById('memoryEmpty');
  if(!contentEl) return;
  // 从后端API加载记忆
  if(typeof apiFetch === 'function'){
    apiFetch('/api/memory/' + encodeURIComponent(currentEmpId))
      .then(function(r){ return r.json(); })
      .then(function(memories){
        if(!memories || memories.length === 0){
          contentEl.innerHTML = '';
          emptyEl.style.display = 'block';
          return;
        }
        emptyEl.style.display = 'none';
        var html = '';
        memories.forEach(function(m, i){
          var dateStr = m.time ? new Date(m.time).toLocaleDateString('zh-CN') : '';
          var keyLabel = m.key && m.key !== 'auto' ? '[' + escapeHtml(m.key) + '] ' : '';
          html += '<div class="memory-item">';
          html += '<div class="memory-item-content">';
          html += '<div class="memory-item-text">' + keyLabel + escapeHtml(m.value || '') + '</div>';
          html += '<div class="memory-item-date">' + escapeHtml(dateStr) + '</div>';
          html += '</div>';
          html += '<div class="memory-item-actions">';
          html += '<button class="memory-action-btn" onclick="deleteServerMemory(\\'' + escapeAttr(m.id) + '\\')" title="\\u5220\\u9664">';
          html += '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>';
          html += '</button>';
          html += '</div></div>';
        });
        contentEl.innerHTML = html;
      })
      .catch(function(e){
        contentEl.innerHTML = '<div style="padding:20px;color:#999;">\\u52a0\\u8f7d\\u5931\\u8d25</div>';
      });
  } else {
    contentEl.innerHTML = '';
    emptyEl.style.display = 'block';
  }
}

function deleteServerMemory(memoryId){
  if(!confirm('\\u786e\\u5b9a\\u5220\\u9664\\u8fd9\\u6761\\u8bb0\\u5fc6\\uff1f')) return;
  apiFetch('/api/memory/' + encodeURIComponent(currentEmpId) + '/' + encodeURIComponent(memoryId), {
    method: 'DELETE'
  }).then(function(){
    showToast('\\u2705 \\u8bb0\\u5fc6\\u5df2\\u5220\\u9664');
    renderMemory(currentMemoryTab || 'core');
  }).catch(function(e){
    showToast('\\u5220\\u9664\\u5931\\u8d25');
  });
}

function addMemory(){
  var memContent = prompt('\\u8f93\\u5165\\u8bb0\\u5fc6\\u5185\\u5bb9:');
  if(!memContent) return;
  apiFetch('/api/memory/' + encodeURIComponent(currentEmpId), {
    method: 'POST',
    body: JSON.stringify({key: 'manual', value: memContent, source: '\\u624b\\u52a8\\u6dfb\\u52a0'})
  }).then(function(){
    showToast('\\u2705 \\u8bb0\\u5fc6\\u5df2\\u6dfb\\u52a0');
    renderMemory(currentMemoryTab || 'core');
  }).catch(function(e){
    showToast('\\u6dfb\\u52a0\\u5931\\u8d25');
  });
}'''

new_func = '''function renderMemoryTab(empId){
  var contentEl = document.getElementById('memoryContent');
  var emptyEl = document.getElementById('memoryEmpty');
  var headerEl = document.querySelector('.memory-header .memory-title');
  if(!contentEl) return;
  
  // 显示加载中
  contentEl.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-tertiary);">加载中...</div>';
  if(emptyEl) emptyEl.style.display = 'none';
  
  // 从后端API加载记忆
  if(typeof apiFetch === 'function'){
    apiFetch('/api/memory/' + encodeURIComponent(empId))
      .then(function(r){ return r.json(); })
      .then(function(memories){
        // 更新记忆总数
        if(headerEl){
          var count = memories && memories.length ? memories.length : 0;
          headerEl.innerHTML = '🧠 记忆 <span style="font-size:12px;font-weight:500;color:var(--text-tertiary);">(' + count + ')</span>';
        }
        
        if(!memories || memories.length === 0){
          contentEl.innerHTML = '';
          if(emptyEl) emptyEl.style.display = 'block';
          return;
        }
        if(emptyEl) emptyEl.style.display = 'none';
        
        var html = '';
        memories.forEach(function(m){
          var timeStr = m.time ? formatTimeAgo(new Date(m.time)) : '';
          var sourceLabel = m.source ? '<span style="font-size:11px;color:var(--accent);background:var(--accent-light);padding:2px 6px;border-radius:4px;margin-right:6px;">' + escapeHtml(m.source) + '</span>' : '';
          html += '<div class="memory-item" style="padding:12px;background:var(--bg-secondary);border-radius:12px;margin-bottom:8px;">';
          html += '<div class="memory-item-content" style="flex:1;">';
          html += '<div style="margin-bottom:6px;">' + sourceLabel + '</div>';
          html += '<div class="memory-item-text" style="font-size:14px;line-height:1.5;color:var(--text-primary);">' + escapeHtml(m.value || '') + '</div>';
          html += '<div class="memory-item-date" style="font-size:11px;color:var(--text-tertiary);margin-top:6px;">' + escapeHtml(timeStr) + '</div>';
          html += '</div>';
          html += '<div class="memory-item-actions" style="display:flex;gap:4px;opacity:0;transition:opacity 0.2s;">';
          html += '<button class="memory-action-btn" onclick="deleteMemory(\\'' + escapeAttr(empId) + '\\',\\'' + escapeAttr(m.id) + '\\')" title="删除" style="width:28px;height:28px;border-radius:6px;background:transparent;border:none;cursor:pointer;color:var(--text-tertiary);display:flex;align-items:center;justify-content:center;">';
          html += '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>';
          html += '</button>';
          html += '</div>';
          html += '</div>';
        });
        contentEl.innerHTML = html;
        
        // 添加hover效果
        var items = contentEl.querySelectorAll('.memory-item');
        items.forEach(function(item){
          item.addEventListener('mouseenter', function(){
            var actions = this.querySelector('.memory-item-actions');
            if(actions) actions.style.opacity = '1';
          });
          item.addEventListener('mouseleave', function(){
            var actions = this.querySelector('.memory-item-actions');
            if(actions) actions.style.opacity = '0';
          });
        });
      })
      .catch(function(e){
        contentEl.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-tertiary);">加载失败，请重试</div>';
        console.warn('[Memory] 加载失败:', e);
      });
  } else {
    contentEl.innerHTML = '';
    if(emptyEl) emptyEl.style.display = 'block';
  }
}

function deleteMemory(empId, memoryId){
  if(!confirm('确定删除这条记忆？')) return;
  apiFetch('/api/memory/' + encodeURIComponent(empId) + '/' + encodeURIComponent(memoryId), {
    method: 'DELETE'
  }).then(function(){
    showToast('✅ 记忆已删除');
    renderMemoryTab(empId);
  }).catch(function(e){
    showToast('❌ 删除失败');
    console.warn('[Memory] 删除失败:', e);
  });
}

function addMemory(){
  // 在面板内显示输入框，不使用 prompt
  var contentEl = document.getElementById('memoryContent');
  if(!contentEl) return;
  
  // 检查是否已经有输入框
  var existingInput = contentEl.querySelector('.memory-add-input');
  if(existingInput){
    existingInput.querySelector('input').focus();
    return;
  }
  
  var inputHtml = '<div class="memory-add-input" style="padding:12px;background:var(--bg-secondary);border-radius:12px;margin-bottom:8px;border:2px solid var(--accent);">';
  inputHtml += '<input type="text" id="newMemoryInput" placeholder="输入记忆内容..." style="width:100%;padding:8px 12px;border:1px solid var(--separator);border-radius:8px;font-size:14px;background:var(--bg-primary);color:var(--text-primary);box-sizing:border-box;margin-bottom:8px;" onkeypress="if(event.key===\\'Enter\\')confirmAddMemory()">';
  inputHtml += '<div style="display:flex;gap:8px;justify-content:flex-end;">';
  inputHtml += '<button onclick="cancelAddMemory()" style="padding:6px 12px;border:1px solid var(--separator);border-radius:6px;background:transparent;font-size:12px;cursor:pointer;color:var(--text-secondary);">取消</button>';
  inputHtml += '<button onclick="confirmAddMemory()" style="padding:6px 12px;border:none;border-radius:6px;background:var(--accent);color:white;font-size:12px;cursor:pointer;">确认添加</button>';
  inputHtml += '</div></div>';
  
  contentEl.insertAdjacentHTML('afterbegin', inputHtml);
  document.getElementById('newMemoryInput').focus();
}

function cancelAddMemory(){
  var inputEl = document.querySelector('.memory-add-input');
  if(inputEl) inputEl.remove();
}

function confirmAddMemory(){
  var inputEl = document.getElementById('newMemoryInput');
  if(!inputEl) return;
  var memContent = inputEl.value.trim();
  if(!memContent){
    showToast('请输入记忆内容');
    return;
  }
  
  var empId = currentEmpId;
  if(!empId) return;
  
  apiFetch('/api/memory/' + encodeURIComponent(empId), {
    method: 'POST',
    body: JSON.stringify({key: 'manual', value: memContent, source: '手动添加'})
  }).then(function(){
    showToast('✅ 记忆已添加');
    renderMemoryTab(empId);
  }).catch(function(e){
    showToast('❌ 添加失败');
    console.warn('[Memory] 添加失败:', e);
  });
}'''

if old_func in content:
    content = content.replace(old_func, new_func)
    print('Replaced renderMemory functions')
else:
    print('Could not find old functions')

with open('index.html', 'w', encoding='utf-8') as f:
    f.write(content)
    
print('Done')
