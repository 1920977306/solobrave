import re

with open('index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# ========== 修复1: confirmAddMembers 添加成员后更新成员数 ==========
old_confirm = """  if(count > 0){
    saveGroups();
    renderGroupDetailMembers(group);
    renderGroupItems();
    showToast('已添加 ' + count + ' 个成员');
  }
  closeModal();"""

new_confirm = """  if(count > 0){
    saveGroups();
    renderGroupDetailMembers(group);
    renderGroupItems();
    
    // 更新成员数显示
    var validMembers = (group.members || []).filter(function(mid){
      return emps.some(function(e){ return e.id === mid; });
    });
    var memberCount = validMembers.length;
    var headerEl = document.getElementById('groupHeaderMemberCount');
    if(headerEl) headerEl.textContent = memberCount + ' 位成员';
    var infoEl = document.getElementById('groupInfoMemberCount');
    if(infoEl) infoEl.textContent = memberCount;
    var statusEl = document.getElementById('groupDetailStatus');
    if(statusEl) statusEl.innerHTML = ' ' + memberCount + ' 位成员';
    
    showToast('已添加 ' + count + ' 个成员');
  }
  closeModal();"""

if old_confirm in content:
    content = content.replace(old_confirm, new_confirm)
    print("[OK] confirmAddMembers 修复成功")
else:
    print("[WARN] confirmAddMembers 未找到匹配文本")

# ========== 修复2: saveGroupDetail 添加成功提示 + 自动关闭 ==========
old_save = """  } else {
    showToast('✅ 群组已保存');
  }
}"""

new_save = """  } else {
    showToast('✅ 群组已保存');
  }
  
  // 自动关闭群组详情抽屉
  closeGroupDetail();
}"""

if old_save in content:
    content = content.replace(old_save, new_save)
    print("[OK] saveGroupDetail 修复成功")
else:
    print("[WARN] saveGroupDetail 未找到匹配文本")

with open('index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("[DONE] 修复完成")
