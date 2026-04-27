
const employees=[
{id:"e1",name:"小龙虾",role:"产品经理",avatar:"🦞",status:"online",msg:"好的，我来安排",time:"刚刚"},
{id:"e2",name:"大龙虾",role:"工程师",avatar:"🦞",status:"busy",msg:"代码写一半...",time:"10分钟前"},
{id:"e3",name:"章鱼哥",role:"设计师",avatar:"🐙",status:"online",msg:"设计稿好了",time:"30分钟前"},
{id:"e4",name:"海星",role:"营销专家",avatar:"⭐",status:"offline",msg:"周一见",time:"昨天"}
];
const avatars=["🦞","🐙","⭐","🦀","🐠","🦑","🐬","🦈","🐳","🦩"];
const roles=[
{icon:"📋",name:"产品经理"},
{icon:"💻",name:"工程师"},
{icon:"🎨",name:"设计师"},
{icon:"📢",name:"营销专家"}
];
let currentTab="employees";  // "employees" | "projects"
let current=employees[0];
let selAvatar="🦞";
let selRole="产品经理";

// ===== Tab 切换 =====
// 页面类型检测
const _isLobsterPage = () => document.querySelector('.nav-tabs') !== null;

// 龙虾办公室标签页切换
function _lobsterSwitchTab(tab) {
  document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  const tabs = ['overview','skills','memory','config','employees','tasks','tools'];
  const idx = tabs.indexOf(tab);
  const navTabs = document.querySelectorAll('.nav-tab');
  if (navTabs[idx]) navTabs[idx].classList.add('active');
  const content = document.getElementById('tab-' + tab);
  if (content) content.classList.add('active');
}

// 统一的 switchTab
function switchTab(tab){
  // 如果是龙虾办公室页面，使用对应的切换逻辑
  if (_isLobsterPage() && document.querySelector('.nav-tabs .nav-tab') !== null) {
    _lobsterSwitchTab(tab);
    return;
  }
  
  // 原有的 Brave 标签页系统
  currentTab = tab;
  renderTabs();
  const addBtn = document.getElementById("addBtn");
  if(addBtn){
    addBtn.textContent = tab === "employees" ? "+ 添加员工" : "+ 创建项目组";
  }
  if(tab === "employees"){
    renderEmployeeList();
  } else {
    renderProjectList();
  }
}

function renderTabs(){
  document.querySelectorAll(".tab").forEach((el,i)=>{
    const isActive=(i===0&&currentTab==="employees")||(i===1&&currentTab==="projects");
    el.classList.toggle("active",isActive);
  });
}

function renderEmployeeList(){
  const list=document.getElementById("contactList");
  list.innerHTML=employees.map(e=>`<div class="list-item ${e.id===current.id?'active':''}" data-id="${e.id}">
<div class="list-avatar">${e.avatar}</div>
<div class="list-info"><div class="list-name">${e.name}</div><div class="list-msg">${e.msg}</div></div>
<div class="list-meta"><span class="list-time">${e.time}</span><div class="list-dot ${e.status}"></div></div>
</div>`).join("");
  list.querySelectorAll(".list-item").forEach(el=>el.onclick=()=>{current=employees.find(e=>e.id===el.dataset.id);renderEmployeeList();updateHeader();renderMsgs()});
}

function renderProjectList(){
  const list=document.getElementById("contactList");
  const projects=Store.getProjects();
  if(projects.length===0){
    list.innerHTML=`<div class="empty-state">
      <div class="empty-icon">📋</div>
      <div class="empty-text">暂无项目组</div>
      <div class="empty-sub">点击下方按钮创建第一个项目组</div>
    </div>`;
    return;
  }
  list.innerHTML=projects.map(p=>{
    const owner=employees.find(e=>e.id===p.ownerId)||employees[0];
    return `<div class="project-card" data-id="${p.id}">
<div class="project-icon">📁</div>
<div class="project-info"><div class="project-name">${p.name}</div><div class="project-meta">👤 ${owner.name} · ${p.memberIds.length}人</div></div>
<div class="project-arrow">›</div>
</div>`;
  }).join("");
  list.querySelectorAll(".project-card").forEach(el=>el.onclick=()=>{openProjectView(el.dataset.id)});
}

// ===== 项目组详情页 =====
let currentProjectId=null;

function openProjectView(projectId){
  currentProjectId=projectId;
  const project=Store.getProject(projectId);
  if(!project)return;
  
  // 更新标题
  document.getElementById("projectName").textContent="📁 "+project.name;
  document.getElementById("projectMembers").textContent=project.memberIds.length+" 人";
  
  // 切换视图
  document.getElementById("chatView").style.display="none";
  document.getElementById("projectView").style.display="flex";
  
  // 渲染消息
  renderProjectMessages();
  
  // 渲染看板
  renderBoard();
  
  // 加载公告
  renderAnnouncement();
  
  // 启动督促定时器
  startUrgeTimer();
}

// 公告展开/收起状态
let announcementExpanded=false;

function renderAnnouncement(){
  const announcement=Store.getAnnouncement(currentProjectId);
  const bar=document.getElementById("announcementBar");
  const empty=document.getElementById("announcementEmpty");
  
  if(announcement&&announcement.content){
    bar.style.display="block";
    empty.style.display="none";
    
    const preview=bar.querySelector(".announcement-preview")||createAnnouncementPreview();
    const content=bar.querySelector(".announcement-content");
    
    preview.textContent=announcement.content.substring(0,50)+(announcement.content.length>50?"...":"");
    content.textContent=announcement.content;
    
    if(announcementExpanded){
      content.classList.add("expanded");
      bar.querySelector(".announcement-toggle").textContent="收起 ▲";
    } else {
      content.classList.remove("expanded");
      bar.querySelector(".announcement-toggle").textContent="展开 ▼";
    }
  } else {
    bar.style.display="none";
    empty.style.display="flex";
  }
}

function createAnnouncementPreview(){
  const bar=document.getElementById("announcementBar");
  const preview=document.createElement("div");
  preview.className="announcement-preview";
  bar.insertBefore(preview,bar.querySelector(".announcement-content"));
  return preview;
}

function toggleAnnouncement(){
  announcementExpanded=!announcementExpanded;
  renderAnnouncement();
}

function openAnnouncementEditor(){
  const announcement=Store.getAnnouncement(currentProjectId);
  document.getElementById("announcementEditor").value=announcement?.content||"";
  document.getElementById("announcementModal").classList.add("show");
}

function saveAnnouncement(){
  const content=document.getElementById("announcementEditor").value;
  Store.setAnnouncement(currentProjectId,content);
  document.getElementById("announcementModal").classList.remove("show");
  renderAnnouncement();
}

function clearAnnouncement(){
  Store.setAnnouncement(currentProjectId,"");
  document.getElementById("announcementEditor").value="";
  document.getElementById("announcementModal").classList.remove("show");
  renderAnnouncement();
}

function closeProjectView(){
  currentProjectId=null;
  document.getElementById("chatView").style.display="block";
  document.getElementById("projectView").style.display="none";
  // 刷新项目列表
  renderProjectList();
}

function renderProjectMessages(){
  const container=document.getElementById("projectMessages");
  const messages=Store.getMessages(currentProjectId);
  
  if(messages.length===0){
    container.innerHTML=`<div class="empty-state">
      <div class="empty-icon">💬</div>
      <div class="empty-text">暂无消息</div>
      <div class="empty-sub">发送消息开始讨论吧</div>
    </div>`;
    return;
  }
  
  container.innerHTML=messages.map(m=>{
    if(m.type==="system"){
      return `<div class="system-msg">${m.text}</div>`;
    }
    const sender=employees.find(e=>e.id===m.sender)||{avatar:"👤",name:"未知"};
    const isMe=m.sender==="user";
    const isUrge=m.type==='urge';
    return `<div class="msg-wrap ${isMe?'you':'other'} ${isUrge?'msg-urge':''}">
<div class="msg-avatar">${sender.avatar}</div>
<div class="msg-content">
<div class="msg-bubble"><div>${m.content}</div>${m.mentioned&&m.mentioned.length>0?`<div class="msg-mention">${m.mentioned.map(id=>'@'+employees.find(e=>e.id===id)?.name).join(' ')}</div>`:''}</div>
<div class="msg-time">${formatTime(m.timestamp)}</div>
</div>
</div>`;
  }).join("");
  
  container.scrollTop=container.scrollHeight;
}

function sendProjectMessage(){
  const input=document.getElementById("projectMsgInput");
  const text=input.value.trim();
  if(!text)return;
  
  const project=Store.getProject(currentProjectId);
  if(!project)return;
  
  // 解析 @ 提及
  const mentioned=text.match(/@(\S+)/g)||[];
  const mentionedIds=mentioned.map(m=>{
    const name=m.substring(1);
    return employees.find(e=>e.name===name)?.id;
  }).filter(Boolean);
  
  // 保存用户消息
  Store.addMessage(currentProjectId,{
    sender:"user",
    content:text,
    mentioned:mentionedIds,
    type:"text"
  });
  
  input.value="";
  renderProjectMessages();
  
  // 任务关键词检测
  const detectedTask=detectTaskFromMessage(text);
  if(detectedTask){
    // 自动创建任务
    const assignee=mentionedIds.length>0?mentionedIds[0]:project.ownerId;
    Store.addTask(currentProjectId,{
      title:detectedTask,
      assignee,
      priority:"medium",
      source:"chat"
    });
    renderBoard();
    
    // 添加系统消息提示
    setTimeout(()=>{
      Store.addMessage(currentProjectId,{
        sender:"system",
        content:`📋 已自动创建任务：「${detectedTask}」`,
        type:"system"
      });
      renderProjectMessages();
    },500);
  }
  if(mentionedIds.length===0){
    showTyping();
    setTimeout(()=>{
      const owner=employees.find(e=>e.id===project.ownerId);
      const aiResponses=[
        "好的，我来安排一下工作。",
        "收到，大家继续推进！",
        "明白，我来分析一下需求。",
        "好的，有问题随时同步。"
      ];
      const response=aiResponses[Math.floor(Math.random()*aiResponses.length)];
      Store.addMessage(currentProjectId,{
        sender:project.ownerId,
        content:response,
        mentioned:[],
        type:"text"
      });
      renderProjectMessages();
    },1200);
  } else {
    // 被 @ 的人回复
    showTyping();
    setTimeout(()=>{
      mentionedIds.forEach(id=>{
        const emp=employees.find(e=>e.id===id);
        if(emp){
          const responses=[
            `收到 @${emp.name}，我来处理。`,
            `好的，我来看看。`,
            `明白，马上处理。`
          ];
          const response=responses[Math.floor(Math.random()*responses.length)];
          Store.addMessage(currentProjectId,{
            sender:id,
            content:response,
            mentioned:[],
            type:"text"
          });
        }
      });
      renderProjectMessages();
    },1200);
  }
}

function showTyping(){
  const container=document.getElementById("projectMessages");
  container.innerHTML+=`<div class="typing" id="typingIndicator">
<span></span><span></span><span></span>
</div>`;
  container.scrollTop=container.scrollHeight;
}

// ===== 看板 =====
function renderBoard(){
  const tasks=Store.getTasks(currentProjectId)||[];
  const todoTasks=tasks.filter(t=>t.status==="todo");
  const doingTasks=tasks.filter(t=>t.status==="doing");
  const doneTasks=tasks.filter(t=>t.status==="done");
  
  document.getElementById("todoCount").textContent=todoTasks.length;
  document.getElementById("doingCount").textContent=doingTasks.length;
  document.getElementById("doneCount").textContent=doneTasks.length;
  
  document.getElementById("todoTasks").innerHTML=todoTasks.map(t=>renderTaskCard(t)).join("");
  document.getElementById("doingTasks").innerHTML=doingTasks.map(t=>renderTaskCard(t)).join("");
  document.getElementById("doneTasks").innerHTML=doneTasks.map(t=>renderTaskCard(t)).join("");
  
  // 任务卡片点击事件
  document.querySelectorAll(".task-card").forEach(card=>{
    card.onclick=(e)=>{
      // 如果点击的是删除按钮，不处理
      if(e.target.classList.contains("task-delete"))return;
      showTaskPopup(card, card.dataset.id);
    };
  });
  
  // 删除按钮事件
  document.querySelectorAll(".task-delete").forEach(btn=>{
    btn.onclick=(e)=>{
      e.stopPropagation();
      Store.deleteTask(currentProjectId, btn.dataset.taskId);
      renderBoard();
    };
  });
}

// ===== 任务操作浮层 =====
let currentTaskId=null;

function showTaskPopup(card, taskId){
  currentTaskId=taskId;
  const popup=document.getElementById("taskActionPopup");
  const rect=card.getBoundingClientRect();
  const boardRect=document.querySelector(".board-section").getBoundingClientRect();
  
  // 定位浮层
  popup.style.display="block";
  popup.style.left=(rect.left-boardRect.left+rect.width/2-70)+"px";
  popup.style.top=(rect.top-boardRect.top-rect.height-10)+"px";
}

function hideTaskPopup(){
  document.getElementById("taskActionPopup").style.display="none";
  currentTaskId=null;
}

// 初始化任务操作浮层事件
document.addEventListener("click",(e)=>{
  if(!e.target.closest(".task-action-popup")&&!e.target.closest(".task-card")){
    hideTaskPopup();
  }
});

document.querySelectorAll(".popup-item[data-status]").forEach(item=>{
  item.onclick=()=>{
    const status=item.dataset.status;
    Store.updateTaskStatus(currentProjectId,currentTaskId,status);
    renderBoard();
    hideTaskPopup();
  };
});

document.getElementById("deleteTaskBtn")?.addEventListener("click",()=>{
  Store.deleteTask(currentProjectId,currentTaskId);
  renderBoard();
  hideTaskPopup();
});

// ===== 新建任务 =====
function openCreateTaskModal(){
  const project=Store.getProject(currentProjectId);
  if(!project)return;
  
  // 填充负责人下拉
  const select=document.getElementById("taskAssignee");
  select.innerHTML='<option value="">选择负责人</option>'+
    project.memberIds.map(id=>{
      const emp=employees.find(e=>e.id===id);
      return emp?`<option value="${emp.id}">${emp.avatar} ${emp.name}</option>`:"";
    }).join("");
  
  // 重置表单
  document.getElementById("taskName").value="";
  document.querySelectorAll(".priority-option").forEach(el=>{
    el.classList.toggle("selected",el.dataset.priority==="medium");
  });
  
  document.getElementById("createTaskModal").classList.add("show");
}

function handleCreateTask(){
  const name=document.getElementById("taskName").value.trim();
  const assignee=document.getElementById("taskAssignee").value;
  const priority=document.querySelector(".priority-option.selected")?.dataset.priority||"medium";
  
  if(!name){
    alert("请输入任务名称");
    return;
  }
  
  Store.addTask(currentProjectId,{
    title:name,
    assignee:assignee||employees[0].id,
    priority
  });
  
  document.getElementById("createTaskModal").classList.remove("show");
  renderBoard();
}

// ===== 任务关键词检测 =====
const taskKeywords=['任务','做','完成','处理','实现','写','开发','设计','修复','提交','检查','优化'];

function detectTaskFromMessage(content){
  const lower=content.toLowerCase();
  
  for(const keyword of taskKeywords){
    const idx=lower.indexOf(keyword);
    if(idx!==-1){
      // 提取关键词后的内容
      let taskName=content.substring(idx+keyword.length).trim();
      // 去掉前面的助词
      taskName=taskName.replace(/^[的地得把了]/g,'').trim();
      // 去掉前面的 @xxx
      taskName=taskName.replace(/^@\S+\s*/g,'').trim();
      
      if(taskName.length>=2){
        return taskName.substring(0,30); // 限制长度
      }
    }
  }
  return null;
}

// ===== 督促功能 =====
let urgeTimer=null;
let lastActivityTime=Date.now();

function openUrgeSettings(){
  const project=Store.getProject(currentProjectId);
  if(!project)return;
  
  const settings=Store.getUrgeSettings(currentProjectId);
  
  // 填充当前设置
  document.getElementById("urgeToggle").classList.toggle("active",settings.enabled);
  document.getElementById("triggerNoUpdate").checked=settings.trigger==='no-update';
  document.getElementById("urgeMessage").value=settings.message||'';
  
  // 频率选项
  document.querySelectorAll(".freq-option").forEach(el=>{
    el.classList.toggle("selected",parseInt(el.dataset.min)===(settings.interval||5));
  });
  
  document.getElementById("urgeSettingsModal").classList.add("show");
}

function saveUrgeSettings(){
  const enabled=document.getElementById("urgeToggle").classList.contains("active");
  const interval=parseInt(document.querySelector(".freq-option.selected")?.dataset.min)||5;
  const triggerNoUpdate=document.getElementById("triggerNoUpdate").checked;
  const triggerNoResponse=document.getElementById("triggerNoResponse").checked;
  // 优先使用"消息无更新"，否则用"成员未响应"
  const trigger=triggerNoUpdate?'no-update':'no-response';
  const message=document.getElementById("urgeMessage").value.trim();
  
  Store.setUrgeSettings(currentProjectId,{
    enabled,
    interval,
    trigger,
    message
  });
  
  // 重启定时器
  startUrgeTimer();
  
  document.getElementById("urgeSettingsModal").classList.remove("show");
}

function startUrgeTimer(){
  // 停止之前的定时器
  if(urgeTimer){
    clearInterval(urgeTimer);
    urgeTimer=null;
  }
  
  const settings=Store.getUrgeSettings(currentProjectId);
  if(!settings.enabled)return;
  
  const project=Store.getProject(currentProjectId);
  if(!project)return;
  
  lastActivityTime=Date.now();
  
  urgeTimer=setInterval(()=>{
    const messages=Store.getMessages(currentProjectId);
    const lastMsg=messages[messages.length-1];
    
    if(!lastMsg)return;
    
    const elapsed=(Date.now()-lastMsg.timestamp)/1000/60; // 分钟
    
    if(elapsed>=settings.interval){
      const defaultMessages=[
        '大家继续推进，有进展同步一下',
        '项目进度检查，请汇报当前工作状态',
        '⏰ 检测到项目进度停滞，请及时推进'
      ];
      const content=settings.message||defaultMessages[Math.floor(Math.random()*defaultMessages.length)];
      
      Store.addMessage(currentProjectId,{
        sender:project.ownerId,
        content,
        type:'urge'
      });
      
      lastActivityTime=Date.now();
      renderProjectMessages();
    }
  },settings.interval*60*1000);
}

function stopUrgeTimer(){
  if(urgeTimer){
    clearInterval(urgeTimer);
    urgeTimer=null;
  }
}

function renderTaskCard(task){
  const assignee=employees.find(e=>e.id===task.assignee)||{avatar:"👤",name:"未知"};
  const sourceTag=task.source==='chat'?'<div class="task-source">来自群聊</div>':'';
  return `<div class="task-card" data-id="${task.id}" data-priority="${task.priority||'medium'}">
<div class="task-title">${task.title}</div>
${sourceTag}
<div class="task-meta">${assignee.avatar} ${assignee.name}</div>
<button class="task-delete" data-task-id="${task.id}">×</button>
</div>`;
}

// ===== 工具函数 =====
function formatTime(ts){
  const d=new Date(ts);
  return d.getHours()+":"+String(d.getMinutes()).padStart(2,"0");
}

// ===== 创建项目组向导 =====

// ===== 创建项目组向导 =====
let createProjectStep=1;
let newProject={name:"",memberIds:[],ownerId:""};

function openCreateProjectModal(){
  createProjectStep=1;
  newProject={name:"",memberIds:[],ownerId:employees[0].id};
  document.getElementById("createProjectModal").classList.add("show");
  renderCreateProjectStep();
}

function renderCreateProjectStep(){
  const body=document.getElementById("createProjectBody");
  const title=document.getElementById("createProjectTitle");
  const step=document.getElementById("createProjectStep");
  const prevBtn=document.getElementById("createProjectPrev");
  const nextBtn=document.getElementById("createProjectNext");
  const submitBtn=document.getElementById("createProjectSubmit");
  
  step.textContent=`${createProjectStep}/3`;
  prevBtn.style.display=createProjectStep>1?"block":"none";
  nextBtn.style.display=createProjectStep<3?"block":"none";
  submitBtn.style.display=createProjectStep===3?"block":"none";
  
  if(createProjectStep===1){
    title.textContent="创建项目组";
    body.innerHTML=`<div class="form-group">
      <label class="form-label">项目组名称</label>
      <input type="text" class="form-input" id="createProjectNameInput" placeholder="输入项目组名称" value="${newProject.name}">
    </div>`;
  } else if(createProjectStep===2){
    title.textContent="选择成员";
    body.innerHTML=`<div class="form-group">
      <label class="form-label">选择参与成员（可多选）</label>
      <div class="member-list">${employees.map(e=>`<div class="member-option" data-id="${e.id}">
        <div class="member-checkbox ${newProject.memberIds.includes(e.id)?'checked':''}">${newProject.memberIds.includes(e.id)?'☑':'☐'}</div>
        <div class="member-avatar">${e.avatar}</div>
        <div class="member-info"><div class="member-name">${e.name}</div><div class="member-role">${e.role}</div></div>
      </div>`).join("")}</div>
    </div>`;
    document.querySelectorAll(".member-option").forEach(el=>{
      el.onclick=()=>{
        const id=el.dataset.id;
        if(newProject.memberIds.includes(id)){
          newProject.memberIds=newProject.memberIds.filter(m=>m!==id);
        } else {
          newProject.memberIds.push(id);
        }
        renderCreateProjectStep();
      };
    });
  } else if(createProjectStep===3){
    title.textContent="设置群主";
    body.innerHTML=`<div class="form-group">
      <label class="form-label">谁来负责统筹这个项目组？</label>
      <div class="member-list">${employees.filter(e=>newProject.memberIds.includes(e.id)).map(e=>`<div class="member-option ${newProject.ownerId===e.id?'selected':''}" data-id="${e.id}">
        <div class="member-radio ${newProject.ownerId===e.id?'checked':''}">${newProject.ownerId===e.id?'●':'○'}</div>
        <div class="member-avatar">${e.avatar}</div>
        <div class="member-info"><div class="member-name">${e.name}</div><div class="member-role">${e.role}</div></div>
      </div>`).join("")}</div>
    </div>`;
    document.querySelectorAll(".member-option").forEach(el=>{
      el.onclick=()=>{newProject.ownerId=el.dataset.id;renderCreateProjectStep();};
    });
  }
}

function handleCreateProjectNext(){
  if(createProjectStep===1){
    const name=document.getElementById("createProjectNameInput").value.trim();
    if(!name){alert("请输入项目组名称");return;}
    newProject.name=name;
    // 默认选择所有成员
    newProject.memberIds=employees.map(e=>e.id);
  }
  createProjectStep++;
  renderCreateProjectStep();
}

function handleCreateProjectPrev(){
  createProjectStep--;
  renderCreateProjectStep();
}

function handleCreateProjectSubmit(){
  // 创建项目组
  const project=Store.createProject(newProject.name,newProject.ownerId,newProject.memberIds);
  // 添加系统消息
  const owner=employees.find(e=>e.id===newProject.ownerId);
  Store.addMessage(project.id,{
    type:"system",
    content:`${owner?owner.name:"系统"} 创建了项目组 "${newProject.name}"`
  });
  // 关闭模态框
  document.getElementById("createProjectModal").classList.remove("show");
  // 刷新项目组列表
  renderProjectList();
}

function render(){
renderEmployeeList();
document.getElementById("chatAvatar").textContent=current.avatar;
document.getElementById("chatName").textContent=current.name;
document.getElementById("chatStatus").textContent=current.status==="online"?"在线":current.status==="busy"?"忙碌":"离线";
document.getElementById("chatStatus").className="header-status"+(current.status==="offline"?" offline":"");
}
function updateHeader(){
document.getElementById("chatAvatar").textContent=current.avatar;
document.getElementById("chatName").textContent=current.name;
document.getElementById("chatStatus").textContent=current.status==="online"?"在线":current.status==="busy"?"忙碌":"离线";
}
function renderMsgs(){
const m=document.getElementById("messages");
m.innerHTML+=`<div class="msg-wrap you"><div class="msg-bubble"><div>不客气，随时问我</div><div class="msg-time">下午 2:33</div></div></div>`;
m.scrollTop=m.scrollHeight;
}
function send(){
const inp=document.getElementById("msgInput");
const v=inp.value.trim();
if(!v)return;
const m=document.getElementById("messages");
m.innerHTML+=`<div class="msg-wrap you"><div class="msg-bubble"><div>${v}</div><div class="msg-time">刚刚</div></div></div>`;
inp.value="";
m.scrollTop=m.scrollHeight;
}
// 关闭模态框（全局函数，供 HTML onclick 调用）
function closeModal(){
  document.getElementById("addModal").classList.remove("show");
}
function init(){
const avatarGrid=document.getElementById("avatarGrid");
if(avatarGrid){
  avatarGrid.innerHTML=avatars.map((a,i)=>`<div class="avatar-option ${i===0?"selected":""}" data="${a}">${a}</div>`).join("");
  avatarGrid.querySelectorAll(".avatar-option").forEach(el=>el.onclick=()=>{avatarGrid.querySelectorAll(".avatar-option").forEach(e=>e.classList.remove("selected"));el.classList.add("selected")});
}
const roleList=document.getElementById("roleList");
if(roleList){
  roleList.innerHTML=roles.map((r,i)=>`<div class="role-option ${i===0?"selected":""}"><span class="role-icon">${r.icon}</span><span class="role-name">${r.name}</span></div>`).join("");
  roleList.querySelectorAll(".role-option").forEach(el=>el.onclick=()=>{roleList.querySelectorAll(".role-option").forEach(e=>e.classList.remove("selected"));el.classList.add("selected")});
}
const addSubmit=document.getElementById("addSubmit");
if(addSubmit){
  addSubmit.onclick=()=>{const name=document.getElementById("empName").value.trim();const avatar=document.querySelector(".avatar-option.selected")?.dataset||"🦞";const role=document.querySelector(".role-option.selected .role-name")?.textContent||"员工";if(!name)return;employees.push({id:"e"+(employees.length+1),name,role,avatar,status:"online",msg:"新成员",time:"刚刚"});render();document.getElementById("addModal").classList.remove("show");document.getElementById("empName").value=""};
}
const closeBtn=document.querySelector(".modal-close");
if(closeBtn){
  closeBtn.onclick=closeModal;
}
const msgInput=document.getElementById("msgInput");
if(msgInput){
  msgInput.addEventListener("keydown",function(e){
    if(e.key==="Enter"&&!e.shiftKey){
      e.preventDefault();
      send();
    }
  });
  msgInput.addEventListener("input",function(){
    this.style.height="auto";
    this.style.height=Math.min(this.scrollHeight,100)+"px";
    // 启用/禁用发送按钮
    const sendBtn=document.getElementById("sendBtn");
    if(sendBtn){
      sendBtn.disabled=!this.value.trim();
    }
  });
}
// 发送按钮点击事件
const sendBtn=document.getElementById("sendBtn");
if(sendBtn){
  sendBtn.onclick=send;
}
// Tab 切换事件
document.querySelectorAll(".tab").forEach((el,i)=>{
  el.onclick=()=>switchTab(i===0?"employees":"projects");
});

// 搜索框事件
const searchInput=document.getElementById("searchInput");
if(searchInput){
  searchInput.addEventListener("input",function(){
    const keyword=this.value.toLowerCase().trim();
    // 过滤员工列表
    if(currentTab==="employees"){
      renderEmployeeList();
      document.querySelectorAll(".list-item").forEach(item=>{
        const name=item.querySelector(".list-name")?.textContent.toLowerCase()||"";
        const role=item.querySelector(".list-msg")?.textContent.toLowerCase()||"";
        item.style.display=name.includes(keyword)||role.includes(keyword)?"":"none";
      });
    } else {
      renderProjectList();
      document.querySelectorAll(".project-card").forEach(item=>{
        const name=item.querySelector(".project-name")?.textContent.toLowerCase()||"";
        item.style.display=name.includes(keyword)?"":"none";
      });
    }
  });
}

// 龙虾办公室按钮
const officeBtn=document.getElementById("officeBtn");
if(officeBtn){
  officeBtn.onclick=()=>switchTab("projects");
}
// 初始渲染
renderTabs();
render();

// 创建项目组模态框事件
const closeCreateProjectBtn=document.getElementById("closeCreateProject");
if(closeCreateProjectBtn){
  closeCreateProjectBtn.onclick=()=>{document.getElementById("createProjectModal").classList.remove("show")};
}
const createProjectPrevBtn=document.getElementById("createProjectPrev");
if(createProjectPrevBtn){
  createProjectPrevBtn.onclick=handleCreateProjectPrev;
}
const createProjectNextBtn=document.getElementById("createProjectNext");
if(createProjectNextBtn){
  createProjectNextBtn.onclick=handleCreateProjectNext;
}
const createProjectSubmitBtn=document.getElementById("createProjectSubmit");
if(createProjectSubmitBtn){
  createProjectSubmitBtn.onclick=handleCreateProjectSubmit;
}

// 添加按钮：根据 Tab 显示不同内容
const addBtnEl=document.getElementById("addBtn");
if(addBtnEl){
  addBtnEl.onclick=()=>{
    if(currentTab==="employees"){
      document.getElementById("addModal").classList.add("show");
    } else {
      openCreateProjectModal();
    }
  };
}

// 项目组详情页事件
const backBtn=document.getElementById("backBtn");
if(backBtn){
  backBtn.onclick=closeProjectView;
}

// 模态框关闭按钮
const closeCreateProject=document.getElementById("closeCreateProject");
if(closeCreateProject){
  closeCreateProject.onclick=()=>document.getElementById("createProjectModal").classList.remove("show");
}
const closeCreateTask=document.getElementById("closeCreateTask");
if(closeCreateTask){
  closeCreateTask.onclick=()=>document.getElementById("createTaskModal").classList.remove("show");
}
const closeUrgeSettings=document.getElementById("closeUrgeSettings");
if(closeUrgeSettings){
  closeUrgeSettings.onclick=()=>document.getElementById("urgeSettingsModal").classList.remove("show");
}
const closeAnnouncement=document.getElementById("closeAnnouncement");
if(closeAnnouncement){
  closeAnnouncement.onclick=()=>document.getElementById("announcementModal").classList.remove("show");
}

const projectSendBtn=document.getElementById("projectSendBtn");
if(projectSendBtn){
  projectSendBtn.onclick=sendProjectMessage;
}

const projectMsgInput=document.getElementById("projectMsgInput");
if(projectMsgInput){
  projectMsgInput.addEventListener("keydown",function(e){
    if(e.key==="Enter"&&!e.shiftKey){
      e.preventDefault();
      sendProjectMessage();
    }
  });
  projectMsgInput.addEventListener("input",function(){
    this.style.height="auto";
    this.style.height=Math.min(this.scrollHeight,100)+"px";
  });
}

// @ 按钮
const mentionBtn=document.getElementById("mentionBtn");
if(mentionBtn){
  mentionBtn.onclick=()=>showMentionDropdown();
}

// @ 提及浮层
function showMentionDropdown(){
  // 移除已存在的
  const existing=document.querySelector(".mention-dropdown");
  if(existing){existing.remove();return;}
  
  const project=Store.getProject(currentProjectId);
  if(!project)return;
  
  const dropdown=document.createElement("div");
  dropdown.className="mention-dropdown";
  dropdown.innerHTML=employees.filter(e=>project.memberIds.includes(e.id)).map(e=>`<div class="mention-item" data-id="${e.id}">
    <div class="mention-avatar">${e.avatar}</div>
    <div>
      <div class="mention-name">${e.name}</div>
      <div class="mention-role">${e.role}</div>
    </div>
  </div>`).join("");
  
  dropdown.querySelectorAll(".mention-item").forEach(item=>{
    item.onclick=()=>{
      const emp=employees.find(e=>e.id===item.dataset.id);
      if(emp){
        const input=document.getElementById("projectMsgInput");
        input.value+="@"+emp.name+" ";
        input.focus();
        dropdown.remove();
      }
    };
  });
  
  document.querySelector(".chat-section").appendChild(dropdown);
}

// 表情按钮（简化版）
const emojiBtn=document.getElementById("emojiBtn");
if(emojiBtn){
  emojiBtn.onclick=()=>{
    const emojis=["😊","😂","👍","❤️","🎉","🔥","✨","💡"];
    const input=document.getElementById("projectMsgInput");
    const randomEmoji=emojis[Math.floor(Math.random()*emojis.length)];
    input.value+=randomEmoji;
    input.focus();
  };
}

// 新建任务按钮
const addTaskBtn=document.getElementById("addTaskBtn");
if(addTaskBtn){
  addTaskBtn.onclick=openCreateTaskModal;
}

// 关闭新建任务模态框
const closeCreateTaskBtn=document.getElementById("closeCreateTask");
if(closeCreateTaskBtn){
  closeCreateTaskBtn.onclick=()=>{document.getElementById("createTaskModal").classList.remove("show")};
}

// 创建任务提交
const createTaskSubmitBtn=document.getElementById("createTaskSubmit");
if(createTaskSubmitBtn){
  createTaskSubmitBtn.onclick=handleCreateTask;
}

// 优先级选项
document.querySelectorAll(".priority-option").forEach(opt=>{
  opt.onclick=()=>{
    document.querySelectorAll(".priority-option").forEach(o=>o.classList.remove("selected"));
    opt.classList.add("selected");
  };
});

// 督促设置按钮
const settingsBtn=document.getElementById("settingsBtn");
if(settingsBtn){
  settingsBtn.onclick=openUrgeSettings;
}

// 公告按钮
const announceBtn=document.getElementById("announceBtn");
if(announceBtn){
  announceBtn.onclick=openAnnouncementEditor;
}

// 公告空白区点击
const announcementEmpty=document.getElementById("announcementEmpty");
if(announcementEmpty){
  announcementEmpty.onclick=openAnnouncementEditor;
}

// 公告展开/收起
const announcementToggle=document.getElementById("announcementToggle");
if(announcementToggle){
  announcementToggle.onclick=toggleAnnouncement;
}

// 关闭公告弹窗
const closeAnnouncementBtn=document.getElementById("closeAnnouncement");
if(closeAnnouncementBtn){
  closeAnnouncementBtn.onclick=()=>{document.getElementById("announcementModal").classList.remove("show")};
}

// 保存公告
const saveAnnouncementBtn=document.getElementById("saveAnnouncement");
if(saveAnnouncementBtn){
  saveAnnouncementBtn.onclick=saveAnnouncement;
}

// 清空公告
const clearAnnouncementBtn=document.getElementById("clearAnnouncement");
if(clearAnnouncementBtn){
  clearAnnouncementBtn.onclick=clearAnnouncement;
}

// 关闭督促设置
const closeUrgeBtn=document.getElementById("closeUrgeSettings");
if(closeUrgeBtn){
  closeUrgeBtn.onclick=()=>{document.getElementById("urgeSettingsModal").classList.remove("show")};
}

// 保存督促设置
const saveUrgeBtn=document.getElementById("saveUrgeSettings");
if(saveUrgeBtn){
  saveUrgeBtn.onclick=saveUrgeSettings;
}

// 督促开关
const urgeToggle=document.getElementById("urgeToggle");
if(urgeToggle){
  urgeToggle.onclick=()=>urgeToggle.classList.toggle("active");
}

// 频率选项
document.querySelectorAll(".freq-option").forEach(opt=>{
  opt.onclick=()=>{
    document.querySelectorAll(".freq-option").forEach(o=>o.classList.remove("selected"));
    opt.classList.add("selected");
  };
});
}

// 初始化
document.addEventListener("DOMContentLoaded", init);