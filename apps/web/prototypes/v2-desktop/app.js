// 一次性桌面交互原型：全部状态只存在浏览器内存，不访问生产服务。
const app = document.querySelector('#app');

const paths = {
  plus: '<path d="M12 5v14M5 12h14"/>',
  x: '<path d="m6 6 12 12M18 6 6 18"/>',
  arrow: '<path d="m5 12 7-7 7 7M12 19V5"/>',
  up: '<path d="m6 14 6-6 6 6"/>',
  down: '<path d="m6 10 6 6 6-6"/>',
  file: '<path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M13 2v7h7M8 14h8M8 18h5"/>',
  clip: '<path d="M8 12.5 14.5 6a3 3 0 0 1 4.3 4.2L10 19a5 5 0 0 1-7-7l8.3-8.3"/>',
  book: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20M4 19.5V4a2 2 0 0 1 2-2h14v20H6.5A2.5 2.5 0 0 1 4 19.5Z"/><path d="M8 6h8M8 10h6"/>',
  route: '<circle cx="6" cy="18" r="2"/><circle cx="18" cy="6" r="2"/><path d="M8 18h4a4 4 0 0 0 0-8h-1a4 4 0 0 1 0-4h5"/>',
  video: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m10 9 5 3-5 3z"/>',
  message: '<path d="M21 11.5a8.4 8.4 0 0 1-.9 3.8A8.5 8.5 0 0 1 12.5 20a8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7A8.4 8.4 0 0 1 4 11.5 8.5 8.5 0 0 1 12.5 3h.5a8.5 8.5 0 0 1 8 8v.5Z"/>',
  briefcase: '<rect x="3" y="7" width="18" height="14" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 12h18"/>',
  code: '<path d="m8 9-4 3 4 3m8-6 4 3-4 3m-3-9-2 18"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v10H3z"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="m11 12 9-9 2 2-2 2-2-2-2 2 2 2-2 2"/>',
  lock: '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  chevron: '<path d="m9 18 6-6-6-6"/>'
};
const icon = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.file}</svg>`;
const escapeHtml = (value = '') => String(value).replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));

const modules = [
  {id:'paper', name:'论文搜索', desc:'从学术来源找到适合阅读的论文', icon:'book'},
  {id:'commute', name:'校园通勤', desc:'规划华东交通大学校内路线', icon:'route'},
  {id:'resources', name:'学习资料推荐', desc:'找书籍与哔哩哔哩学习视频', icon:'video'},
  {id:'tieba', name:'贴吧信息搜集', desc:'整理华东交通大学吧的公开信息', icon:'message'},
  {id:'career', name:'职业规划', desc:'查看岗位要求、薪资样本与方向', icon:'briefcase'},
  {id:'github', name:'GitHub 项目推荐', desc:'寻找与你的想法相关的开源项目', icon:'code'}
];
const moduleById = (id) => modules.find((item) => item.id === id);
const phaseNames = ['书页识别','辅助预习','辅导','复盘','总结'];
const reviewQuestions = [
  {question:'示例题 1：请用自己的话说明本节核心概念，以及它解决了什么问题。', answer:'应说明概念的定义、使用场景，并指出书页中的一个关键条件。', hints:/定义|概念|条件|场景/},
  {question:'示例题 2：本节的关键步骤之间有什么因果或先后关系？', answer:'应按书页顺序讲清关键步骤，并解释每一步为什么需要前一步的结果。', hints:/步骤|先|然后|因为|所以/},
  {question:'示例题 3：遇到一个类似问题时，你会怎样判断能否应用本节方法？', answer:'先核对适用条件，再选择方法，最后检查结果是否满足原条件。', hints:/条件|判断|检查|方法/}
];

let state = {
  view:'chat', activeId:null, draftMode:'companion', newModule:null, text:'', attachments:[],
  menuOpen:false, accountOpen:false, menuIndex:0, notice:'', busy:false, dragIndex:null,
  conversations:[], nextId:1,
  profile:[
    {id:1, text:'正在学习 Python 数据分析，希望从基础例子开始。', date:'2026-09-24'},
    {id:2, text:'更喜欢先看清楚步骤，再自己动手练习。', date:'2026-09-23'},
    {id:3, text:'正在准备软件开发相关岗位的实习。', date:'2026-09-22'}
  ],
  profileEdit:null, profileDraft:'', profileDelete:null,
  settingsDraft:{}, settingsStatus:{qwen:true,tavily:true,amap:false,mapjs:false,mapsecurity:false,model:'qwen3.6-plus'},
  settingsResult:{}, kbFiles:[]
};

const current = () => state.conversations.find((item) => item.id === state.activeId) || null;
const mode = () => current()?.mode || state.draftMode;
const selectedModule = () => current() ? current().selectedModule : state.newModule;
const card = (heading, body, note = '') => `<div class="answer-card"><h3>${heading}</h3>${body}${note ? `<p class="source-note">${note}</p>` : ''}</div>`;

function sidebar() {
  const history = state.conversations.map((conversation) => `<button type="button" class="history-button ${state.activeId === conversation.id && state.view === 'chat' ? 'active' : ''}" data-action="open-thread" data-id="${conversation.id}"><span class="history-title">${escapeHtml(conversation.title)}</span><span class="history-mode">${conversation.mode === 'study' ? '学习模式' : '日常陪伴'}</span></button>`).join('');
  return `<aside class="sidebar"><div class="brand"><span class="brand-name">BridGes</span><span class="prototype-mark">V2 原型</span></div>
    <nav class="nav" aria-label="主导航">
      <button type="button" class="nav-button ${state.view === 'chat' && !current() ? 'active' : ''}" data-action="new-chat">${icon('plus')}新聊天</button>
      <button type="button" class="nav-button ${state.view === 'knowledge' ? 'active' : ''}" data-action="view" data-view="knowledge">${icon('folder')}知识库</button>
    </nav><div class="history-label">最近对话</div><div class="history-list">${history || '<div class="history-mode" style="padding:5px 11px">开始一段新对话</div>'}</div><div class="side-spacer"></div>
    <div class="account-anchor"><button type="button" class="account-button" data-action="account" aria-expanded="${state.accountOpen}"><span class="avatar">桥</span><span class="account-text"><strong>学生账户</strong><small>本机演示</small></span>${icon('chevron')}</button>
      ${state.accountOpen ? `<div class="account-popover" role="menu"><button type="button" role="menuitem" data-action="view" data-view="profile">${icon('user')}用户画像</button><button type="button" role="menuitem" data-action="view" data-view="settings">${icon('key')}密钥与模型管理</button></div>` : ''}
    </div></aside>`;
}

function topbar() {
  const title = state.view === 'chat' ? (current()?.title || '新聊天') : ({profile:'用户画像', settings:'密钥与模型管理', knowledge:'知识库'}[state.view]);
  const right = state.view === 'chat' ? (current()
    ? `<span class="mode-readonly" title="当前对话模式已锁定">${icon('lock')}${mode() === 'study' ? '学习模式' : '日常陪伴'}</span>`
    : `<div class="mode-switch" role="group" aria-label="新聊天模式"><button type="button" data-action="mode" data-mode="companion" aria-pressed="${mode() === 'companion'}">日常陪伴</button><button type="button" data-action="mode" data-mode="study" aria-pressed="${mode() === 'study'}">学习模式</button></div>`)
    : '<span class="demo-label">原型演示 · 不连接真实服务</span>';
  return `<header class="topbar"><div class="topbar-title"><span class="overline">BridGes / V2</span><h1>${escapeHtml(title)}</h1></div><div class="topbar-right">${right}</div></header>`;
}

function welcome() {
  if (mode() === 'study') return `<section class="welcome"><p class="welcome-eyebrow">STUDY MODE · 一节一对话</p><h2>从这一节开始，慢慢学懂。</h2><p>先上传书本这一节的照片。系统识别后会给出预习问题，接着陪你弄懂难点，学完再逐题复盘。</p><div class="welcome-hints"><span class="hint">先上传书页照片</span><span class="hint">预习问题暂时不用回答</span><span class="hint">错题直接看到正确答案</span></div></section>`;
  return `<section class="welcome"><p class="welcome-eyebrow">COMPANION MODE · 华东交通大学</p><h2>今天想解决什么？</h2><p>可以直接聊天，也可以从输入框左侧的“+”选择论文、通勤、资料、贴吧、职业或 GitHub 模块。</p><div class="welcome-hints"><span class="hint">一个对话里可切换不同模块</span><span class="hint">前文会作为相关上下文</span></div></section>`;
}

function renderMessage(message) {
  const name = message.module ? moduleById(message.module)?.name : null;
  const label = message.role === 'user' ? (name ? `<strong>${escapeHtml(name)}</strong> · 用户` : '用户') : 'BridGes';
  const files = message.files?.length ? `<div class="attachment-names">${message.files.map((name) => `<span>${escapeHtml(name)}</span>`).join('')}</div>` : '';
  return `<article class="message ${message.role}"><div class="message-kicker">${label}</div><div class="bubble">${files}${message.html || `<p>${escapeHtml(message.text)}</p>`}</div></article>`;
}

function studySteps() {
  if (mode() !== 'study' || !current()) return '';
  const phase = current().phase;
  const phaseIndex = {recognizing:0,preview:1,tutoring:2,review:3,summary:4}[phase] ?? 0;
  return `<div class="study-steps" aria-label="学习阶段">${phaseNames.map((name, index) => `<span class="study-step ${index === phaseIndex ? 'current' : index < phaseIndex ? 'done' : ''}">${name}</span>${index < phaseNames.length - 1 ? '<span class="study-step-line" aria-hidden="true"></span>' : ''}`).join('')}</div>`;
}

function attachmentTray() {
  if (!state.attachments.length) return '';
  return `<div class="attachment-tray" aria-label="待发送附件">${state.attachments.map((attachment, index) => `<div class="attachment-tile" draggable="true" data-attachment-index="${index}">${attachment.url ? `<img src="${attachment.url}" alt="第 ${index + 1} 页预览" />` : `<span class="attachment-placeholder">${icon('file')}</span>`}<div class="attachment-meta"><strong title="${escapeHtml(attachment.name)}">${escapeHtml(attachment.name)}</strong><small>第 ${index + 1} 项</small></div><div class="attachment-actions"><button type="button" data-action="move-attachment" data-index="${index}" data-step="-1" aria-label="将第 ${index + 1} 项上移" ${index === 0 ? 'disabled' : ''}>${icon('up')}</button><button type="button" data-action="remove-attachment" data-index="${index}" aria-label="移除第 ${index + 1} 项">${icon('x')}</button><button type="button" data-action="move-attachment" data-index="${index}" data-step="1" aria-label="将第 ${index + 1} 项下移" ${index === state.attachments.length - 1 ? 'disabled' : ''}>${icon('down')}</button></div></div>`).join('')}</div>`;
}

function plusMenu() {
  if (!state.menuOpen) return '';
  const duringReview = mode() === 'study' && current()?.phase === 'review';
  return `<div class="plus-menu" role="menu" aria-label="添加功能或文件"><div class="plus-menu-head">添加</div><button type="button" class="menu-item ${state.menuIndex === 0 ? 'menu-active' : ''}" role="menuitem" data-action="upload" ${duringReview ? 'disabled' : ''}><span class="menu-icon">${icon('clip')}</span><span class="menu-copy"><strong>添加照片和文件</strong><small>${duringReview ? '先返回辅导，再追加本节照片' : '从电脑选择，也可拖入或粘贴图片'}</small></span></button>${mode() === 'companion' ? `<div class="menu-separator"></div><div class="plus-menu-head">选择工作模块</div>${modules.map((module, index) => `<button type="button" class="menu-item ${state.menuIndex === index + 1 ? 'menu-active' : ''}" role="menuitem" data-action="module" data-module="${module.id}"><span class="menu-icon">${icon(module.icon)}</span><span class="menu-copy"><strong>${module.name}</strong><small>${module.desc}</small></span></button>`).join('')}` : ''}</div>`;
}

function composer() {
  const module = moduleById(selectedModule());
  const placeholder = mode() === 'study' ? (current()?.phase === 'review' ? '回答当前问题…' : '问我哪里看不懂，或告诉我“学完了”…') : '给 BridGes 发消息…';
  return `<div class="composer-zone"><div class="composer-wrap">${plusMenu()}<div class="composer" id="composer"><input class="sr-only" type="file" id="local-file" ${mode() === 'study' ? 'accept="image/*"' : 'accept="image/*,.pdf,.doc,.docx,.txt,.md"'} multiple />${attachmentTray()}${state.notice ? `<div class="notice" role="status">${escapeHtml(state.notice)}</div>` : ''}<div class="composer-main">${module && mode() === 'companion' ? `<span class="composer-chip">${module.name}<button type="button" aria-label="移除${module.name}模块" data-action="clear-module">${icon('x')}</button></span>` : ''}<textarea id="chat-text" aria-label="输入消息" rows="1" placeholder="${placeholder}" ${state.busy ? 'disabled' : ''}>${escapeHtml(state.text)}</textarea></div><div class="composer-bottom"><div class="composer-left"><button type="button" class="icon-button" data-action="menu" aria-label="添加功能或文件" aria-expanded="${state.menuOpen}" aria-controls="module-menu">${icon('plus')}</button><span class="composer-tip">${mode() === 'study' ? '一节一对话 · 书页只在此对话使用' : (module ? `已选择 ${module.name}` : '普通聊天')}</span></div><div class="composer-right"><span class="composer-tip">Enter 发送</span><button type="button" class="send-button" data-action="send" aria-label="发送消息" ${state.busy ? 'disabled' : ''}>${icon('arrow')}</button></div></div></div><div class="composer-disclaimer">交互原型 · 附件与输入只存在当前浏览器内存，刷新后清空</div></div></div>`;
}

function chatPage() {
  const thread = current();
  return `<div class="chat-area">${studySteps()}<div class="chat-scroll" id="chat-scroll">${thread ? `<div class="messages">${thread.messages.map(renderMessage).join('')}</div>` : welcome()}</div>${composer()}</div>`;
}

function profilePage() {
  return `<div class="page-scroll"><div class="page-inner"><div class="page-header"><span class="eyebrow">YOUR CONTEXT</span><h2>用户画像</h2><p>每一行是一条可修改、可删除的信息。这里只展示长期有用的事实，不按固定类别分组。</p></div><div class="list-card">${state.profile.length ? state.profile.map((item) => `<div class="profile-row"><div class="profile-content">${state.profileEdit === item.id ? `<label class="sr-only" for="profile-input">修改画像内容</label><div class="edit-controls"><input id="profile-input" data-input="profile" value="${escapeHtml(state.profileDraft)}" /><button type="button" data-action="save-profile" data-id="${item.id}">保存</button><button type="button" data-action="cancel-profile">取消</button></div>` : `<p>${escapeHtml(item.text)}</p><small>更新于 ${item.date} · 示例记录</small>${state.profileDelete === item.id ? `<div class="confirm-actions"><span>确定删除这条画像？</span><button type="button" class="danger" data-action="confirm-profile-delete" data-id="${item.id}">确认删除</button><button type="button" data-action="cancel-profile-delete">取消</button></div>` : ''}`}</div>${state.profileEdit === item.id || state.profileDelete === item.id ? '' : `<div class="row-actions"><button type="button" data-action="edit-profile" data-id="${item.id}">修改</button><button type="button" class="delete" data-action="delete-profile" data-id="${item.id}">删除</button></div>`}</div>`).join('') : '<div class="profile-row">暂无画像记录。</div>'}</div><p class="demo-banner">此页记录为演示内容。正式版的修改与删除会立即影响后续对话的画像引用。</p></div></div>`;
}

function settingsCard(id, title, description, fieldLabel, placeholder, type = 'password') {
  const status = state.settingsStatus[id];
  const result = state.settingsResult[id];
  return `<section class="settings-card"><div class="card-head"><div><h3>${title}</h3><p>${description}</p></div><span class="status ${status ? '' : 'off'}">${status ? '已配置' : '未配置'}</span></div><div class="settings-fields"><label>${fieldLabel}<input type="${type}" data-setting="${id}" value="${escapeHtml(state.settingsDraft[id] || '')}" placeholder="${placeholder}" autocomplete="off" /></label><button type="button" class="primary-action" data-action="save-setting" data-setting="${id}">${id === 'model' ? '验证并保存' : '验证并更换'}</button></div>${result ? `<div class="form-result ${result.type}" role="status">${escapeHtml(result.text)}</div>` : ''}</section>`;
}

function settingsPage() {
  return `<div class="page-scroll"><div class="page-inner"><div class="page-header"><span class="eyebrow">MODEL & KEYS</span><h2>密钥与模型管理</h2><p>首次启动仍在终端配置 Qwen 和 Tavily。此处可在以后更换；旧密钥不回显。</p></div><div class="settings-grid">${settingsCard('qwen','Qwen API Key','用于聊天、图片理解和结构化工作流。','输入新的 Qwen Key','请勿在原型输入真实密钥')}${settingsCard('tavily','Tavily API Key','用于公开信息的网页检索。','输入新的 Tavily Key','请勿在原型输入真实密钥')}${settingsCard('amap','高德路线 Web Service Key','仅校园通勤请求路线时使用。','输入路线规划 Key','请勿在原型输入真实密钥')}${settingsCard('mapjs','高德浏览器地图 Key','用于展示地图卡；与路线 Web Service Key 分开。','输入 JavaScript API Key','请勿在原型输入真实密钥')}${settingsCard('mapsecurity','高德地图安全密钥','正式版由服务端代理保护，不写入浏览器静态代码。','输入安全密钥','请勿在原型输入真实密钥')}${settingsCard('model','Qwen 主模型 ID','不提供预设列表；正式版会查询百炼并做真实能力探测。当前演示：'+escapeHtml(state.settingsStatus.model),'手动输入模型 ID','例如 qwen3.6-plus','text')}</div><p class="demo-banner">这里的验证只模拟反馈，不会联网；示例“成功”不能证明模型或 Key 真实可用。正式版失败时保留原配置。</p></div></div>`;
}

function knowledgePage() {
  return `<div class="page-scroll"><div class="page-inner"><div class="page-header"><span class="eyebrow">YOUR LIBRARY</span><h2>知识库</h2><p>只在这里主动添加全局资料。聊天中的书页照片和文件不会自动出现在此处。</p></div><div class="kb-card"><h3>我的资料</h3><p>正式版保留上传、解析、检索与删除。本原型只展示入口与归属关系。</p><div class="kb-list"><div class="kb-item"><span>课程笔记-示例.pdf</span><span class="status">已就绪 · 演示</span></div>${state.kbFiles.map((name) => `<div class="kb-item"><span>${escapeHtml(name)}</span><span class="status off">仅原型内存</span></div>`).join('')}</div><button type="button" class="quiet-action" data-action="kb-upload">从电脑添加到知识库（演示）</button><input class="sr-only" id="kb-file" type="file" multiple /></div><p class="demo-banner">聊天附件与知识库资料属于不同范围；正式版不会在聊天中诱导将附件加入知识库。</p></div></div>`;
}

function render(focusComposer = false) {
  app.innerHTML = `<div class="layout">${sidebar()}<main class="main">${topbar()}${state.view === 'chat' ? chatPage() : state.view === 'profile' ? profilePage() : state.view === 'settings' ? settingsPage() : knowledgePage()}</main></div>`;
  if (state.menuOpen) document.querySelector('.plus-menu')?.setAttribute('id','module-menu');
  if (focusComposer && state.view === 'chat') document.querySelector('#chat-text')?.focus();
  if (current() && state.view === 'chat') document.querySelector('#chat-scroll')?.scrollTo(0, document.querySelector('#chat-scroll').scrollHeight);
}

function clearAttachments() {
  state.attachments.forEach((item) => item.url && URL.revokeObjectURL(item.url));
  state.attachments = [];
}

function addFiles(files) {
  if (mode() === 'study' && current()?.phase === 'review') { state.notice = '请先返回辅导，再追加本节书页照片。'; render(true); return; }
  const incoming = Array.from(files || []);
  let rejected = 0;
  for (const file of incoming) {
    const allowed = mode() === 'study' ? file.type.startsWith('image/') : (file.type.startsWith('image/') || /\.(pdf|doc|docx|txt|md)$/i.test(file.name));
    if (!allowed) { rejected += 1; continue; }
    state.attachments.push({file, name:file.name, url:file.type.startsWith('image/') ? URL.createObjectURL(file) : null});
  }
  state.notice = rejected ? `有 ${rejected} 个文件类型不支持；其他文件仍保留。` : '';
  render(true);
}

function ensureConversation(text) {
  let conversation = current();
  if (conversation) return conversation;
  conversation = {id:state.nextId++, title:text.trim().slice(0,25) || (mode() === 'study' ? '这一节的学习' : '附件对话'), mode:state.draftMode, selectedModule:state.newModule, phase:state.draftMode === 'study' ? 'recognizing' : null, reviewIndex:0, answers:[], messages:[]};
  state.conversations.unshift(conversation);
  state.activeId = conversation.id;
  return conversation;
}

function companionAnswer(conversation, message, originalText) {
  const query = escapeHtml(originalText || '当前问题');
  switch (message.module) {
    case 'paper': return card('论文搜索 · 结果结构示意', `<p><strong>检索词位置：</strong>${/\bTransformer\b/i.test(originalText) ? 'Transformer（示例）' : '待工作流解析（原型）'}</p><ol><li>入门综述：标题、作者、年份、来源链接与适读理由</li><li>基础论文：核心贡献与阅读顺序</li><li>近期研究：与前两篇的关系</li></ol>`, '原型不调用 arXiv；正式版会保留原始名词，先消歧再生成真实查询词与结果。');
    case 'commute': return card('校园通勤 · 路线卡示意', `<p><strong>用户请求：</strong>${query}</p><div class="route-stats"><span>方式：步行（演示）</span><span>距离：示意</span><span>高德耗时：待接入</span><span>课间缓冲：规则估计</span></div><div class="map-mock" role="img" aria-label="原型示意地图，未绘制真实路线"><span class="map-route"></span><span class="map-dot start"></span><span class="map-dot end"></span><span class="map-label start">起点示意</span><span class="map-label end">终点示意</span></div>`, '这是一张布局示意图，不是高德实时路线；正式版会分别显示三种方式的真实耗时。');
    case 'resources': return card('学习资料推荐 · 列表示意', `<p><strong>学习方向：</strong>${query}</p><ol><li>书籍 1：入门阶段、推荐理由、直达链接</li><li>书籍 2：进阶阶段、推荐理由、直达链接</li><li>哔哩哔哩视频 1–3：标题、作者、适用阶段与链接</li></ol>`, '原型没有搜索图书或观看视频，不生成虚构链接。');
    case 'tieba': return card('贴吧信息搜集 · 降级示意', `<p><strong>问题：</strong>${query}</p><p>若只找到帖子但读取不到回复，这里只显示已核实帖子链接，并说明“未取得回复内容”。</p>`, '原型不访问贴吧；不会虚构吧友回复。');
    case 'career': return card('职业规划 · 岗位样本示意', `<p><strong>岗位意图：</strong>${query}</p><p>岗位卡将显示城市、发布时间、薪资原文、技能要求和直达链接；汇总时标注样本数与检索日期。</p>`, '原型没有搜集真实招聘信息，不能据此判断薪资。');
    case 'github': return card('GitHub 项目推荐 · 结果示意', `<p><strong>项目想法：</strong>${query}</p><ol><li>整体功能相近的仓库：覆盖范围、可借鉴点与局限</li><li>关键组件仓库：适用子功能与维护证据</li></ol>`, '正式版核对仓库元数据和实际读到的文档后，返回 2–3 个真实链接。');
    default: {
      const suggestion = /论文|文献|arxiv/i.test(originalText) ? 'paper' : /贴吧|吧友/i.test(originalText) ? 'tieba' : null;
      return suggestion ? card('可以使用专用模块', `<p>你的请求可能更适合「${moduleById(suggestion).name}」。点击后会沿用原消息发起该模块任务。</p><button type="button" class="inline-action" data-action="suggest-module" data-module="${suggestion}" data-prompt="${query}">使用${moduleById(suggestion).name}</button>`, '建议不会自动调用模块。') : `<p>已收到你的消息。这是普通聊天的演示回复；当前对话后续仍可通过“+”选择任意日常模块。</p>`;
    }
  }
}

function studyFirstAnswer() {
  return card('辅助预习 · 示例问题', '<p>阅读这一节时带着下面的问题思考，<strong>现在不需要回答</strong>：</p><ol><li>本节最重要的概念是什么？它试图解决什么问题？</li><li>书页中哪些条件决定了这个方法能否使用？</li><li>几个关键步骤之间有什么关系？你能用自己的话解释吗？</li></ol><button type="button" class="inline-action" data-action="start-review">我已经学完，开始复盘</button>', '原型不识别上传的照片；正式版问题会只根据该节实际书页生成，数量随知识密度调整。');
}

function reviewPrompt(conversation) {
  const index = conversation.reviewIndex;
  if (index >= reviewQuestions.length) return '';
  return card(`复盘 · 第 ${index + 1} / ${reviewQuestions.length} 题`, `<p>${reviewQuestions[index].question}</p><button type="button" class="quiet-action" data-action="pause-review">返回辅导，稍后继续</button>`, '题目内容为原型示例；正式版只考上传书页覆盖的知识。');
}

function freezeActions(conversation, actions) {
  for (const message of conversation.messages) {
    if (message.role !== 'assistant' || !message.html) continue;
    for (const action of actions) message.html = message.html.replaceAll(`data-action="${action}"`, 'disabled aria-disabled="true"');
  }
}

function startReview() {
  const conversation = current();
  if (!conversation || conversation.mode !== 'study' || conversation.phase !== 'tutoring') return;
  if (state.attachments.length) { state.notice = '请先发送待补充的书页，再开始复盘。'; render(true); return; }
  freezeActions(conversation, ['start-review','resume-review']);
  conversation.phase = 'review';
  conversation.messages.push({role:'assistant', html:reviewPrompt(conversation)});
  state.notice = '';
  render(true);
}

function studyAnswer(conversation, text, hadFiles) {
  if (conversation.phase === 'review') {
    const question = reviewQuestions[conversation.reviewIndex];
    const correct = question.hints.test(text);
    conversation.answers.push({index:conversation.reviewIndex, correct});
    conversation.reviewIndex += 1;
    let feedback = card(correct ? '本题回答有关键点' : '本题有遗漏 · 直接看正确答案', `<p><strong>参考答案：</strong>${question.answer}</p><p>${correct ? '你提到了一个关键点；正式版会依据书页逐项判断完整度。' : '请对照原书页检查自己的思路，继续下一题，不需要补答本题。'}</p>`, '判定仅为原型演示，不代表真实教学评价。');
    if (conversation.reviewIndex < reviewQuestions.length) feedback += reviewPrompt(conversation);
    else {
      conversation.phase = 'summary';
      freezeActions(conversation, ['pause-review']);
      const missed = conversation.answers.filter((item) => !item.correct).length;
      feedback += card('本节总结 · 示例', `<p><strong>学到了什么：</strong>围绕本节核心概念、适用条件和步骤完成了学习。</p><p><strong>复盘情况：</strong>完成 ${conversation.answers.length} 题；其中 ${missed} 题需要再对照书页理解。</p><p><strong>接下来：</strong>仍可在本对话继续追问本节内容；学习下一节请新建对话。</p>`, '正式版只总结实际书页和真实作答，不夸大掌握程度。');
    }
    return feedback;
  }
  if (hadFiles) return card('书页已加入当前对话 · 演示', '<p>新增照片会按页序进入本节知识范围。如果已在复盘中，正式版只会重排尚未问出的题。</p>', '原型没有读取图片，也不会自动加入全局知识库。');
  if (/学完了|开始复盘|学好了/.test(text)) {
    freezeActions(conversation, ['start-review','resume-review']);
    conversation.phase = 'review';
    return reviewPrompt(conversation);
  }
  const resume = conversation.reviewIndex > 0 && conversation.phase === 'tutoring' && conversation.reviewIndex < reviewQuestions.length ? '<button type="button" class="inline-action" data-action="resume-review">继续复盘未问的问题</button>' : '<button type="button" class="inline-action" data-action="start-review">我已学完，开始复盘</button>';
  return card('辅导 · 回答结构示意', `<p>我会先找你上传书页中的对应位置，用简单的例子解释，再标清哪些是知识库、网页或模型知识的补充。</p>${resume}`, '原型不识别图片，无法对你的具体问题作真实讲解。');
}

function sendMessage(overrideText = null) {
  if (state.busy) return;
  const text = (overrideText ?? state.text).trim();
  const files = state.attachments.map((item) => item.name);
  if (!text && !files.length) { state.notice = '请输入消息或添加附件。'; render(true); return; }
  if (mode() === 'study' && !current() && !state.attachments.some((item) => item.file.type.startsWith('image/'))) {
    state.notice = '学习模式首轮需要至少一张书页照片。'; render(true); return;
  }
  const conversation = ensureConversation(text);
  const module = conversation.mode === 'companion' ? conversation.selectedModule : null;
  conversation.messages.push({role:'user', text, files, module});
  const reply = {role:'assistant', html:'<div class="progress-row"><span class="progress-dot"></span>正在理解请求…</div>'};
  conversation.messages.push(reply);
  state.text = ''; state.notice = ''; state.menuOpen = false; state.busy = true;
  clearAttachments();
  render();
  window.setTimeout(() => {
    if (conversation.mode === 'study') {
      if (conversation.messages.filter((item) => item.role === 'user').length === 1) { conversation.phase = 'tutoring'; reply.html = studyFirstAnswer(); }
      else reply.html = studyAnswer(conversation, text, files.length > 0);
    } else reply.html = companionAnswer(conversation, {module}, text);
    state.busy = false;
    render(true);
  }, 550);
}

function saveSetting(id) {
  const value = (state.settingsDraft[id] || '').trim();
  if (!value) state.settingsResult[id] = {type:'error', text:'请先填写新值；原设置保持不变。'};
  else if (id === 'model') {
    if (value === 'qwen3.6-plus') { state.settingsStatus.model = value; state.settingsResult.model = {type:'success', text:'原型演示：文本、图片、工具、结构化输出与上下文检查均显示通过；正式版必须实际探测后才保存。下一条消息起使用。'}; state.settingsDraft.model = ''; }
    else state.settingsResult.model = {type:'error', text:'原型演示：未通过验证，保留原模型。正式版会给出百炼元数据或真实调用的失败原因。'};
  } else if (value.length < 8) state.settingsResult[id] = {type:'error', text:'原型演示：格式检查未通过，原密钥保持有效。'};
  else { state.settingsStatus[id] = true; state.settingsResult[id] = {type:'success', text:'原型演示：验证通过并清空输入框；正式版会先调用服务商验证，再原子替换。'}; state.settingsDraft[id] = ''; }
  render();
}

function handleAction(button) {
  const action = button.dataset.action;
  const id = Number(button.dataset.id);
  if (action === 'new-chat') { clearAttachments(); state.activeId = null; state.draftMode = 'companion'; state.newModule = null; state.view = 'chat'; state.text = ''; state.notice = ''; state.menuOpen = false; state.accountOpen = false; render(true); }
  else if (action === 'open-thread') { clearAttachments(); state.activeId = id; state.view = 'chat'; state.text = ''; state.notice = ''; state.menuOpen = false; state.accountOpen = false; render(); }
  else if (action === 'view') { state.view = button.dataset.view; state.accountOpen = false; state.menuOpen = false; render(); }
  else if (action === 'account') { state.accountOpen = !state.accountOpen; state.menuOpen = false; render(); }
  else if (action === 'mode' && !current()) { clearAttachments(); state.draftMode = button.dataset.mode; state.newModule = null; state.notice = ''; state.menuOpen = false; render(true); }
  else if (action === 'menu') { state.menuOpen = !state.menuOpen; state.accountOpen = false; state.menuIndex = 0; render(); if (state.menuOpen) document.querySelector('.plus-menu .menu-item')?.focus(); else document.querySelector('[data-action="menu"]')?.focus(); }
  else if (action === 'upload') { state.menuOpen = false; render(); document.querySelector('#local-file')?.click(); }
  else if (action === 'module' && mode() === 'companion') { if (current()) current().selectedModule = button.dataset.module; else state.newModule = button.dataset.module; state.menuOpen = false; render(true); }
  else if (action === 'clear-module') { if (current()) current().selectedModule = null; else state.newModule = null; render(true); }
  else if (action === 'send') sendMessage();
  else if (action === 'suggest-module') { if (current()) current().selectedModule = button.dataset.module; sendMessage(button.dataset.prompt); }
  else if (action === 'remove-attachment') { const index = Number(button.dataset.index); const removed = state.attachments.splice(index,1)[0]; if (removed?.url) URL.revokeObjectURL(removed.url); render(true); }
  else if (action === 'move-attachment') { const index = Number(button.dataset.index), target = index + Number(button.dataset.step); if (target >= 0 && target < state.attachments.length) { const [item] = state.attachments.splice(index,1); state.attachments.splice(target,0,item); render(true); } }
  else if (action === 'start-review' || action === 'resume-review') startReview();
  else if (action === 'pause-review' && current()?.phase === 'review') { freezeActions(current(), ['pause-review']); current().phase = 'tutoring'; current().messages.push({role:'assistant', html:card('已返回辅导', '<p>刚才完成的题目已保留。你可以继续提问，之后再从未问的题继续复盘。</p><button type="button" class="inline-action" data-action="resume-review">继续复盘</button>')}); render(true); }
  else if (action === 'edit-profile') { state.profileEdit = id; state.profileDraft = state.profile.find((item) => item.id === id)?.text || ''; state.profileDelete = null; render(); document.querySelector('#profile-input')?.focus(); }
  else if (action === 'save-profile') { const item = state.profile.find((entry) => entry.id === id); if (item && state.profileDraft.trim()) { item.text = state.profileDraft.trim(); item.date = '2026-09-24'; state.profileEdit = null; render(); } }
  else if (action === 'cancel-profile') { state.profileEdit = null; render(); }
  else if (action === 'delete-profile') { state.profileDelete = id; state.profileEdit = null; render(); }
  else if (action === 'confirm-profile-delete') { state.profile = state.profile.filter((item) => item.id !== id); state.profileDelete = null; render(); }
  else if (action === 'cancel-profile-delete') { state.profileDelete = null; render(); }
  else if (action === 'save-setting') saveSetting(button.dataset.setting);
  else if (action === 'kb-upload') document.querySelector('#kb-file')?.click();
}

app.addEventListener('click', (event) => { const button = event.target.closest('[data-action]'); if (button) handleAction(button); else if (state.menuOpen && !event.target.closest('.plus-menu')) { state.menuOpen = false; render(); } });
app.addEventListener('input', (event) => { if (event.target.id === 'chat-text') state.text = event.target.value; else if (event.target.dataset.input === 'profile') state.profileDraft = event.target.value; else if (event.target.dataset.setting) state.settingsDraft[event.target.dataset.setting] = event.target.value; });
app.addEventListener('change', (event) => { if (event.target.id === 'local-file') addFiles(event.target.files); if (event.target.id === 'kb-file') { state.kbFiles.push(...Array.from(event.target.files || []).map((file) => file.name)); render(); } });
app.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && state.menuOpen) { event.preventDefault(); state.menuOpen = false; render(); document.querySelector('[data-action="menu"]')?.focus(); }
  else if (state.menuOpen && ['ArrowDown','ArrowUp'].includes(event.key)) { event.preventDefault(); const count = mode() === 'study' ? 1 : modules.length + 1; state.menuIndex = (state.menuIndex + (event.key === 'ArrowDown' ? 1 : -1) + count) % count; document.querySelectorAll('.plus-menu .menu-item')[state.menuIndex]?.focus(); }
  else if (event.target.id === 'chat-text' && event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); sendMessage(); }
});
app.addEventListener('paste', (event) => { if (event.target.id !== 'chat-text') return; const images = Array.from(event.clipboardData?.files || []).filter((file) => file.type.startsWith('image/')); if (images.length) { event.preventDefault(); addFiles(images); } });
app.addEventListener('dragover', (event) => { if (event.target.closest('.attachment-tile')) { event.preventDefault(); return; } if (event.dataTransfer?.types.includes('Files')) { event.preventDefault(); document.querySelector('#composer')?.classList.add('dragover'); } });
app.addEventListener('dragleave', (event) => { if (!event.relatedTarget?.closest?.('#composer')) document.querySelector('#composer')?.classList.remove('dragover'); });
app.addEventListener('dragstart', (event) => { const tile = event.target.closest('.attachment-tile'); if (tile) state.dragIndex = Number(tile.dataset.attachmentIndex); });
app.addEventListener('drop', (event) => { const tile = event.target.closest('.attachment-tile'); if (tile && state.dragIndex !== null) { event.preventDefault(); const target = Number(tile.dataset.attachmentIndex); const [item] = state.attachments.splice(state.dragIndex,1); state.attachments.splice(target,0,item); state.dragIndex = null; render(true); return; } if (event.dataTransfer?.files.length) { event.preventDefault(); addFiles(event.dataTransfer.files); } document.querySelector('#composer')?.classList.remove('dragover'); });

render();
