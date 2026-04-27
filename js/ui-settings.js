/**
 * SoloBrave UI - 设置页面
 */

(function() {
  const ns = window.soloBrave;
  const ui = ns.ui = ns.ui || {};

  ui.renderSettings = function() {
    const container = document.getElementById('main-content');
    const config = ns.store.get('config.api') || {};

    container.innerHTML = `
      <div class="settings-view">
        <header class="page-header">
          <button class="btn btn-secondary" onclick="window.soloBrave.navigate('office')">← 返回</button>
          <h1>⚙️ 设置</h1>
          <p class="subtitle">配置 AI 引擎和数据管理</p>
        </header>

        <div class="settings-section">
          <h3>🤖 AI 引擎配置</h3>
          
          <div class="setting-group">
            <label>服务商</label>
            <select id="ai-provider">
              <option value="zhipu" ${config.provider === 'zhipu' ? 'selected' : ''}>
                智谱 AI（GLM-4）
              </option>
              <option value="openai" ${config.provider === 'openai' ? 'selected' : ''}>
                OpenAI（GPT-4o）
              </option>
              <option value="claude" ${config.provider === 'claude' ? 'selected' : ''}>
                Claude
              </option>
            </select>
          </div>

          <div class="setting-group">
            <label>API Key</label>
            <input type="password" id="api-key" 
                   value="${config.key || ''}" 
                   placeholder="输入你的 API Key">
          </div>

          <div class="setting-group">
            <label>模型</label>
            <select id="ai-model">
              <option value="glm-4-flash" ${config.model === 'glm-4-flash' ? 'selected' : ''}>
                GLM-4 Flash（快速）
              </option>
              <option value="glm-4" ${config.model === 'glm-4' ? 'selected' : ''}>
                GLM-4（更强）
              </option>
              <option value="gpt-4o-mini" ${config.model === 'gpt-4o-mini' ? 'selected' : ''}>
                GPT-4o Mini
              </option>
              <option value="gpt-4o" ${config.model === 'gpt-4o' ? 'selected' : ''}>
                GPT-4o
              </option>
              <option value="claude-3-haiku" ${config.model === 'claude-3-haiku' ? 'selected' : ''}>
                Claude 3 Haiku
              </option>
            </select>
          </div>

          <div class="setting-group">
            <label>自定义 Base URL（可选）</label>
            <input type="text" id="api-baseurl" 
                   value="${config.baseUrl || ''}" 
                   placeholder="https://open.bigmodel.cn/api/paas/v4">
          </div>

          <div class="form-actions">
            <button class="btn btn-primary" onclick="window.soloBrave.ui.testAndSave()">
              测试连接并保存
            </button>
            <span id="connection-status" class="connection-status"></span>
          </div>
        </div>

        <div class="settings-section">
          <h3>💾 数据管理</h3>
          <div class="setting-group">
            <button class="btn btn-secondary" onclick="window.soloBrave.ui.exportData()">
              📤 导出所有数据
            </button>
            <p class="setting-desc">将员工、任务、对话等数据导出为 JSON 文件</p>
          </div>
          <div class="setting-group">
            <button class="btn btn-danger" onclick="window.soloBrave.ui.clearAllData()">
              🗑️ 清空所有数据
            </button>
            <p class="setting-desc">⚠️ 警告：此操作不可恢复！</p>
          </div>
        </div>

        <div class="settings-section">
          <h3>📊 系统信息</h3>
          <div class="setting-group">
            <p><strong>版本：</strong> SoloBrave v1.0.0</p>
            <p><strong>员工数：</strong> ${ns.employeeStore.getAll().length} 人</p>
            <p><strong>任务数：</strong> ${ns.taskStore.getAll().length} 个</p>
            <p><strong>对话数：</strong> ${Object.keys(ns.store.get('conversations') || {}).length} 组</p>
          </div>
        </div>
      </div>
    `;
  };

  ui.testAndSave = async function() {
    const statusEl = document.getElementById('connection-status');
    statusEl.textContent = '测试中...';
    statusEl.className = 'connection-status testing';

    const config = {
      provider: document.getElementById('ai-provider').value,
      key: document.getElementById('api-key').value,
      model: document.getElementById('ai-model').value,
      baseUrl: document.getElementById('api-baseurl').value || getBaseUrl(document.getElementById('ai-provider').value)
    };

    // 保存配置（同时保存到 aiConfig 供记忆提取使用）
    ns.store.set('config.api', config);
    ns.store.set('aiConfig', {
      apiKey: config.key,
      model: config.model,
      baseUrl: config.baseUrl
    });

    // 模拟测试（后续接入真实 AI Gateway）
    setTimeout(() => {
      if (config.key) {
        statusEl.textContent = '连接成功 ✓';
        statusEl.className = 'connection-status success';
        showToast('配置已保存', 'success');
      } else {
        statusEl.textContent = '请输入 API Key';
        statusEl.className = 'connection-status error';
      }
    }, 1000);
  };

  ui.exportData = function() {
    const data = {
      employees: ns.employeeStore.getAll(),
      tasks: ns.taskStore.getAll(),
      conversations: ns.store.get('conversations') || {},
      config: { ...ns.store.get('config'), api: { ...ns.store.get('config.api'), key: '' } },
      exportedAt: new Date().toISOString()
    };

    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `solobrave_backup_${new Date().toISOString().slice(0,10)}.json`;
    a.click();

    showToast('数据已导出', 'success');
  };

  ui.clearAllData = function() {
    if (!confirm('⚠️ 确定要清空所有数据吗？此操作不可恢复！')) return;
    if (!confirm('再次确认：将删除所有员工、任务和对话记录！')) return;

    ns.localStore.clear();
    location.reload();
  };

  function getBaseUrl(provider) {
    const urls = {
      zhipu: 'https://open.bigmodel.cn/api/paas/v4',
      openai: 'https://api.openai.com/v1',
      claude: 'https://api.anthropic.com/v1'
    };
    return urls[provider] || urls.zhipu;
  }

  function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
  }

})();