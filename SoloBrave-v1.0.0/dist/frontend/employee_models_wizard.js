/**
 * Solo Brave - 员工管理向导部分
 */

  openAddEmployeeModal() {
    let modal = document.getElementById('addEmpModal');
    if (modal) modal.remove();
    
    this.addWizardStep = 1;
    this.addWizardData = { name: '', emoji: '👤', template: 'frontend', model: 'qwen-plus', temp: 0.7 };
    
    modal = document.createElement('div');
    modal.className = 'modal-overlay';
    modal.id = 'addEmpModal';
    document.body.appendChild(modal);
    
    this.renderAddWizardStep();
    requestAnimationFrame(() => modal.classList.add('show'));
  },
  
  renderAddWizardStep() {
    const modal = document.getElementById('addEmpModal');
    if (!modal) return;
    const step = this.addWizardStep;
    const d = this.addWizardData;
    const t = this.templates;
    const cm = this.chinaModels;
    const gm = this.globalModels;
    
    let stepIndicator = '';
    if (step === 1) {
      stepIndicator = '<span class="step active">1</span><span class="step-line"></span><span class="step">2</span><span class="step-line"></span><span class="step">3</span>';
    } else if (step === 2) {
      stepIndicator = '<span class="step done">✓</span><span class="step-line"></span><span class="step active">2</span><span class="step-line"></span><span class="step">3</span>';
    } else {
      stepIndicator = '<span class="step done">✓</span><span class="step-line"></span><span class="step done">✓</span><span class="step-line"></span><span class="step active">3</span>';
    }
    
    let body = '';
    if (step === 1) {
      const emojis = ['🦞','👨‍💻','👩‍💻','🐙','⭐','🦈','🤖','👤'];
      const emojiOpts = emojis.map(e => '<div class="emoji-opt' + (d.emoji === e ? ' selected' : '') + '" onclick="EmployeeModels.selectEmoji(\'' + e + '\')">' + e + '</div>').join('');
      body = '<div class="form-group"><label>姓名</label><input type="text" id="wizName" placeholder="请输入员工姓名" value="' + d.name + '" oninput="EmployeeModels.addWizardData.name = this.value"></div><div class="form-group"><label>选择头像</label><div class="emoji-picker">' + emojiOpts + '</div></div>';
    } else if (step === 2) {
      const roles = t.filter(x => x.id !== 'custom').map(x => '<div class="role-card' + (d.template === x.id ? ' selected' : '') + '" onclick="EmployeeModels.selectTemplate(\'' + x.id + '\')"><div class="role-icon">' + x.emoji + '</div><div class="role-name">' + x.name + '</div><div class="role-desc">' + x.desc + '</div></div>').join('');
      body = '<div class="role-grid">' + roles + '</div>';
    } else {
      const chinaOpts = cm.map(m => '<option value="' + m.id + '"' + (d.model === m.id ? ' selected' : '') + '>' + m.name + '</option>').join('');
      const globalOpts = gm.map(m => '<option value="' + m.id + '"' + (d.model === m.id ? ' selected' : '') + '>' + m.name + '</option>').join('');
      body = '<div class="form-group"><label>AI 模型</label><select id="wizModel" onchange="EmployeeModels.addWizardData.model = this.value"><optgroup label="🇨🇳 国内模型">' + chinaOpts + '</optgroup><optgroup label="🌍 国际模型">' + globalOpts + '</optgroup></select></div><div class="form-group"><label>温度参数 <span id="tempLabel">' + Math.round(d.temp * 100) + '%</span></label><input type="range" id="wizTemp" min="0" max="100" value="' + (d.temp * 100) + '" oninput="EmployeeModels.addWizardData.temp = this.value/100; document.getElementById(\'tempLabel\').textContent = Math.round(this.value) + \'%\'" style="width:100%"></div>';
    }
    
    let footer = '';
    if (step === 1) {
      footer = '<button class="btn-secondary" onclick="EmployeeModels.closeAddModal()">取消</button><button class="btn-primary" onclick="EmployeeModels.nextWizardStep()">下一步 →</button>';
    } else if (step === 2) {
      footer = '<button class="btn-secondary" onclick="EmployeeModels.prevWizardStep()">← 上一步</button><button class="btn-primary" onclick="EmployeeModels.nextWizardStep()">下一步 →</button>';
    } else {
      footer = '<button class="btn-secondary" onclick="EmployeeModels.prevWizardStep()">← 上一步</button><button class="btn-primary" onclick="EmployeeModels.finishAddWizard()">完成 ✓</button>';
    }
    
    let title = step === 1 ? '添加新员工' : step === 2 ? '选择角色' : '配置 AI 模型';
    
    modal.innerHTML = '<div class="modal-content large"><div class="wizard-step"><div class="wizard-header"><h3>' + title + '</h3><div class="wizard-steps">' + stepIndicator + '</div></div><div class="wizard-body">' + body + '</div><div class="wizard-footer">' + footer + '</div></div></div>';
  },
  
  selectEmoji(emoji) {
    this.addWizardData.emoji = emoji;
    this.renderAddWizardStep();
  },
  
  selectTemplate(id) {
    this.addWizardData.template = id;
    this.renderAddWizardStep();
  },
  
  nextWizardStep() {
    if (this.addWizardStep < 3) {
      this.addWizardStep++;
      this.renderAddWizardStep();
    }
  },
  
  prevWizardStep() {
    if (this.addWizardStep > 1) {
      this.addWizardStep--;
      this.renderAddWizardStep();
    }
  },
  
  finishAddWizard() {
    const d = this.addWizardData;
    if (!d.name.trim()) {
      this.addWizardStep = 1;
      this.renderAddWizardStep();
      return;
    }
    const template = this.templates.find(t => t.id === d.template);
    const newEmp = {
      id: 'emp_' + Date.now(),
      name: d.name.trim(),
      emoji: d.emoji,
      role: d.template,
      status: 'online',
      modelId: d.model
    };
    this.employees.push(newEmp);
    this.saveEmployees();
    this.renderEmployeeList();
    this.closeAddModal();
    if (typeof UI !== 'undefined' && UI.toast) UI.toast('员工已添加', 'success');
  },
  
  closeAddModal() {
    const m = document.getElementById('addEmpModal');
    if (m) { m.classList.remove('show'); setTimeout(() => m.remove(), 300); }
  },
