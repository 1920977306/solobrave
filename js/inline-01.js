/* ===== index.html 内联块 1 外置 (r39-20 ⑥体积治理) =====
   原内联 script, 内容零改动; 原首行: /* ui/selection-dashboard-v21: 看板数据加载 + 显示 + 联动聚焦 * / */
/* ui/selection-dashboard-v21: 看板数据加载 + 显示 + 联动聚焦 */
(function () {
  // Esc 关闭
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      var dash = document.getElementById('selectionDashboard');
      if (dash && !dash.hidden) closeSelectionDashboard();
    }
  });

  // ★ ui/selection-dashboard-v21-linkage: 看板聚焦状态 + 缓存全量数据
  var _dashState = {
    selected: null,  // { type:'talent'|'brand'|'product', id, name }
    cache: { talents: [], brands: [], products: [] }
  };

  window.openSelectionDashboard = function () {
    var dash = document.getElementById('selectionDashboard');
    if (!dash) return;
    dash.hidden = false;
    // 每次打开都重置聚焦, 避免上次残留
    _dashState.selected = null;
    loadSelectionData();
  };

  window.closeSelectionDashboard = function () {
    var dash = document.getElementById('selectionDashboard');
    if (dash) dash.hidden = true;
  };

  window.openBrandLibrary = function () {
    // 品牌库无独立 module, 暂引导到商品管理 (含品牌分类)
    if (typeof switchModule === 'function') switchModule('products');
    showToast && showToast('品牌库嵌在商品管理里, 跳转中', 'info');
  };

  // ★ 联动聚焦：清空 → 恢复默认三列
  window.clearDashFocus = function () {
    _dashState.selected = null;
    _refreshDashColumns();
    var bar = document.getElementById('dashFocusBar');
    if (bar) bar.hidden = true;
    if (typeof showToast === 'function') showToast('✦ 已清除聚焦');
  };

  // 数据加载 + 渲染
  function loadSelectionData() {
    var tok = localStorage.getItem('sb_auth_token') || '';
    var headers = tok ? { 'Authorization': 'Bearer ' + tok } : {};

    Promise.all([
      fetch('/api/talents?limit=500', { headers: headers }).then(function (r) { return r.ok ? r.json() : []; }).catch(function () { return []; }),
      fetch('/api/products?limit=500', { headers: headers }).then(function (r) { return r.ok ? r.json() : []; }).catch(function () { return []; }),
      fetch('/api/brands?limit=500', { headers: headers }).then(function (r) { return r.ok ? r.json() : []; }).catch(function () { return []; })
    ]).then(function (results) {
      var talents = results[0].talents || results[0] || [];
      var products = results[1].products || results[1] || [];
      var brands = results[2].brands || results[2] || [];
      _dashState.cache = { talents: talents, brands: brands, products: products };
      renderKpi(talents, brands, products);
      _refreshDashColumns();
    }).catch(function (e) {
      console.error('[selection-dashboard] load failed', e);
    });
  }

  // ★ 联动核心：根据 selected 状态决定三列内容
  function _refreshDashColumns() {
    var sel = _dashState.selected;
    var cache = _dashState.cache;
    if (!sel) {
      renderColTalents(cache.talents, null);
      renderColBrands(cache.brands, null);
      renderColProducts(cache.products, null);
      return;
    }
    // 自身列保持原始列表 + 高亮选中卡片
    if (sel.type === 'talent') {
      renderColTalents(cache.talents, sel.id);
      // 其他两列通过 /api/dashboard/linkage 拉取
      _loadLinkage(sel, 'talent', function (data) {
        renderColBrands(data.brands || [], null);
        renderColProducts(data.products || [], null);
      });
    } else if (sel.type === 'brand') {
      renderColBrands(cache.brands, sel.id);
      _loadLinkage(sel, 'brand', function (data) {
        renderColTalents(data.talents || [], null);
        renderColProducts(data.products || [], null);
      });
    } else if (sel.type === 'product') {
      renderColProducts(cache.products, sel.id);
      _loadLinkage(sel, 'product', function (data) {
        renderColTalents(data.talents || [], null);
        renderColBrands(data.brands || [], null);
      });
    }
    // 显示聚焦状态条
    var bar = document.getElementById('dashFocusBar');
    var nm = document.getElementById('dashFocusName');
    if (bar) bar.hidden = false;
    if (nm) nm.textContent = sel.name + ' (' + (sel.type === 'talent' ? '达人' : sel.type === 'brand' ? '品牌' : '商品') + ')';
  }

  // 拉取联动数据 (防御：失败也走默认列)
  function _loadLinkage(sel, type, cb) {
    var tok = localStorage.getItem('sb_auth_token') || '';
    var headers = tok ? { 'Authorization': 'Bearer ' + tok } : {};
    fetch('/api/dashboard/linkage?type=' + type + '&id=' + encodeURIComponent(sel.id), {
      headers: headers
    })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) {
          if (typeof showToast === 'function') showToast('⚠ 联动数据为空');
          return;
        }
        cb(data);
      })
      .catch(function (e) {
        console.error('[linkage] fetch failed', e);
        if (typeof showToast === 'function') showToast('❌ 联动查询失败');
      });
  }

  function renderKpi(talents, brands, products) {
    // 达人
    var tTotal = talents.length;
    var tActive = talents.filter(function (t) {
      var s = (t.cooperation_status || t.status || '').toLowerCase();
      return s === 'active' || s === 'resting' || s === 'available' || s === '可合作';
    }).length;
    // 新成员: tags / ai_tags 含 "新成员" 关键字 (t.role 字段不存在)
    var tNew = talents.filter(function (t) {
      var tagText = ((t.tags || []).join(' ') + ' ' + (t.ai_tags || []).join(' ')).toLowerCase();
      return tagText.indexOf('新成员') >= 0;
    }).length;
    setText('kpiTalentsCount', tTotal);
    setText('kpiTalentsActive', tActive);
    setText('kpiTalentsNew', tNew);

    // 品牌
    var bTotal = brands.length;
    var bAvg = bTotal > 0 ? Math.round(products.length / bTotal) : 0;
    setText('kpiBrandsCount', bTotal);
    setText('kpiBrandsAvgProducts', bAvg);

    // 商品
    var pTotal = products.length;
    var pLinked = products.filter(function (p) { return p.brand_id || p.brandId; }).length;
    setText('kpiProductsCount', pTotal);
    setText('kpiProductsLinked', pLinked);
  }

  // ★ 联动聚焦：卡片的 onclick 改为先聚焦，第二次同卡才跳转
  function renderColTalents(talents, focusedId) {
    setText('colTalentsCount', talents.length);
    var html = '';
    if (!talents.length) { html = '<div class="selection-empty">暂无达人</div>'; }
    else {
      talents.slice(0, 6).forEach(function (t) {
        var rating = t.ai_rating || t.rating_score || '';
        var ratingStr = rating ? '★ ' + (typeof rating === 'number' ? rating.toFixed(1) : rating) : '';
        var followers = formatNum(t.followers);
        var cat = (typeof t.category === 'string' ? t.category : '') || (typeof t.main_category === 'string' ? t.main_category : '');
        var status = t.cooperation_status || t.status || '';
        var focused = (focusedId && t.id === focusedId) ? ' focused' : '';
        var navFn = 'closeSelectionDashboard(); switchModule(\'influencers\'); setTimeout(function(){ if(typeof selectTalentItem===\'function\')selectTalentItem(\'' + escapeAttr(t.id || '') + '\'); }, 80);';
        // ★ 联动行为：第 1 次点击 → 聚焦；已聚焦则 → 跳转
        var onclickJs = focused
          ? navFn
          : 'focusDashEntity(\'talent\', \'' + escapeAttr(t.id) + '\', \'' + escapeAttr(t.name || '') + '\');';
        html += '<div class="selection-card' + focused + '" onclick="' + onclickJs + '">' +
          '<div class="selection-card-name">' + escapeHtml(t.name || t.display_name || '未命名') + '</div>' +
          '<div class="selection-card-meta">' + followers + ' 粉丝 · ' + escapeHtml(cat || '未分类') + '</div>' +
          '<div class="selection-card-chips">' +
            (ratingStr ? '<span class="selection-chip primary">' + escapeHtml(ratingStr) + '</span>' : '') +
            (status ? '<span class="selection-chip ' + chipClass(status) + '">' + escapeHtml(status) + '</span>' : '') +
          '</div>' +
        '</div>';
      });
    }
    document.getElementById('colTalentsList').innerHTML = html;
  }

  function renderColBrands(brands, focusedId) {
    setText('colBrandsCount', brands.length);
    var html = '';
    if (!brands.length) { html = '<div class="selection-empty">暂无品牌</div>'; }
    else {
      brands.slice(0, 6).forEach(function (b) {
        var cat = b.main_category || b.category || '';
        var count = b.total_products || 0;
        var talentCount = b.total_talents || 0;
        var focused = (focusedId && b.id === focusedId) ? ' focused' : '';
        var navFn = 'closeSelectionDashboard(); openBrandLibrary();';
        var onclickJs = focused
          ? navFn
          : 'focusDashEntity(\'brand\', \'' + escapeAttr(b.id) + '\', \'' + escapeAttr(b.name || '') + '\');';
        html += '<div class="selection-card' + focused + '" onclick="' + onclickJs + '">' +
          '<div class="selection-card-name">' + escapeHtml(b.name || '未命名') + '</div>' +
          '<div class="selection-card-meta">' + escapeHtml(cat || '未分类') + ' · ' + count + ' 个商品' + (talentCount ? ' · ' + talentCount + ' 达人' : '') + '</div>' +
          '<div class="selection-card-chips">' +
            (count ? '<span class="selection-chip success">' + count + ' 商品</span>' : '') +
            (talentCount ? '<span class="selection-chip primary">' + talentCount + ' 达人</span>' : '') +
          '</div>' +
        '</div>';
      });
    }
    document.getElementById('colBrandsList').innerHTML = html;
  }

  function renderColProducts(products, focusedId) {
    setText('colProductsCount', products.length);
    var html = '';
    if (!products.length) { html = '<div class="selection-empty">暂无商品</div>'; }
    else {
      products.slice(0, 6).forEach(function (p) {
        var cat = (p.category || p.main_category || '').toString();
        var price = p.price || p.suggest_price || '';
        var focused = (focusedId && p.id === focusedId) ? ' focused' : '';
        var navFn = 'closeSelectionDashboard(); switchModule(\'products\'); setTimeout(function(){ if(typeof selectProductItem===\'function\')selectProductItem(\'' + escapeAttr(p.id || '') + '\'); }, 80);';
        var onclickJs = focused
          ? navFn
          : 'focusDashEntity(\'product\', \'' + escapeAttr(p.id) + '\', \'' + escapeAttr(p.name || '') + '\');';
        html += '<div class="selection-card' + focused + '" onclick="' + onclickJs + '">' +
          '<div class="selection-card-name">' + escapeHtml(p.name || p.title || '未命名') + '</div>' +
          '<div class="selection-card-meta">' + escapeHtml(cat || '未分类') + (price ? ' · ¥' + price : '') + '</div>' +
          '<div class="selection-card-chips">' +
            (p.brand_id || p.brandId ? '<span class="selection-chip success">已关联品牌</span>' : '<span class="selection-chip">未关联</span>') +
          '</div>' +
        '</div>';
      });
    }
    document.getElementById('colProductsList').innerHTML = html;
  }

  // ★ 联动聚焦：global 函数 (onclick 引用)
  window.focusDashEntity = function (type, id, name) {
    if (!id) return;
    _dashState.selected = { type: type, id: id, name: name || id };
    _refreshDashColumns();
  };

  function setText(id, val) { var el = document.getElementById(id); if (el) el.textContent = val; }
  function escapeHtml(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : s; return d.innerHTML; }
  function escapeAttr(s) { return String(s == null ? '' : s).replace(/'/g, "\\'"); }
  function chipClass(status) {
    var s = status.toLowerCase();
    if (s.indexOf('active') >= 0 || s.indexOf('可') >= 0) return 'success';
    if (s.indexOf('resting') >= 0 || s.indexOf('rest') >= 0) return 'warn';
    return '';
  }
  function formatNum(n) {
    if (!n) return '—';
    n = Number(n);
    if (n >= 10000) return (n / 10000).toFixed(1).replace(/\.0$/, '') + 'w';
    if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, '') + 'k';
    return String(n);
  }
})();
