// 浏览器控制台执行：去重 emps 数组并同步到服务器
// 使用方法：打开浏览器控制台(F12)，复制粘贴全部代码执行

(function() {
  console.log('[去重] 开始检查 emps 数组...');
  
  if (typeof emps === 'undefined') {
    console.error('[去重] 错误：emps 未定义！请确认页面已加载完成。');
    return;
  }
  
  console.log('[去重] 去重前 emps 数量:', emps.length);
  
  // 按 id 去重，保留第一个出现的
  var seen = {};
  var originalLength = emps.length;
  var duplicates = [];
  
  var uniqueEmps = emps.filter(function(e) {
    if (!e || !e.id) {
      console.log('[去重] 跳过无效员工:', e);
      return false;
    }
    if (seen[e.id]) {
      duplicates.push(e.name + '(' + e.id + ')');
      return false;
    }
    seen[e.id] = true;
    return true;
  });
  
  console.log('[去重] 发现重复:', originalLength - uniqueEmps.length, '个');
  if (duplicates.length > 0) {
    console.log('[去重] 重复员工:', duplicates.join(', '));
  }
  
  // 直接修改 emps 数组（现在 emps 是 var 声明，可以修改）
  emps.length = 0;
  uniqueEmps.forEach(function(e) {
    emps.push(e);
  });
  
  console.log('[去重] 去重后 emps 数量:', emps.length);
  
  // 保存到 localStorage
  if (typeof saveEmployees === 'function') {
    saveEmployees();
    console.log('[去重] 已调用 saveEmployees()');
  } else {
    localStorage.setItem('sb_employees', JSON.stringify(emps));
    console.log('[去重] 已直接保存到 localStorage');
  }
  
  // 刷新列表
  if (typeof renderEmployeeList === 'function') {
    renderEmployeeList();
    console.log('[去重] 已刷新员工列表');
  }
  
  console.log('[去重] 完成！请刷新页面验证。');
})();
