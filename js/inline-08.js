/* ===== index.html 内联块 8 外置 (r39-20 ⑥体积治理) =====
   原内联 script, 内容零改动; 原首行: function syncFeishuProductsFromSettings() { */

function syncFeishuProductsFromSettings() {
  var token = localStorage.getItem('sb_auth_token');
  fetch('/api/products/sync-feishu', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token } })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.success) {
        showToast('商品同步完成：共' + data.total + '条，新增' + data.created + '条，更新' + data.updated + '条，跳过' + data.skipped + '条', 'success');
        if (typeof loadProducts === 'function') loadProducts();
      } else {
        showToast('同步失败: ' + (data.error || '未知错误'), 'error');
      }
    })
    .catch(function(e) { showToast('网络错误: ' + e, 'error'); });
}
