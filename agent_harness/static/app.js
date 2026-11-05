'use strict';
const $ = (id) => document.getElementById(id);
let csrf = '', current = '', metadata = {}, sessions = [], snapshot = '', refreshBusy = false, sending = false;
let renaming = '', renameSaving = false, listRefresh = 0, listSnapshot = '';
const examples = [
  ['计算与待办', '算清费用，记下安排', '↗', '帮我计算 (128+72)*3，然后添加待办：周五交 600 元报名费。'],
  ['连续对话', '接着聊，记得你的想法', '↳', '我叫小林，正在准备 Agent 技术面试，请记住这件事。'],
  ['天气与行动', '查天气，安排出行', '☀', '查一下广州明天天气，并记一条明天带伞的待办。'],
  ['搜索与总结', '找资料，附上来源', '⌕', '搜索 agent runtime，简要说明它做什么，并附上来源链接。']
];
function node(tag, cls, text) { const el = document.createElement(tag); if (cls) el.className = cls; if (text !== undefined) el.textContent = text; return el; }
function notice(text = '') { $('notice').textContent = text; }
async function api(path, method = 'GET', body) {
  const options = {method, headers: {'X-CSRF-Token': csrf}, credentials: 'same-origin'};
  if (body !== undefined) { options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(body); }
  const response = await fetch('/api' + path, options);
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401 && path !== '/visitor') { $('workspace').classList.add('hidden'); $('login-screen').classList.remove('hidden'); }
    throw new Error(typeof data.detail === 'string' ? data.detail : '请求格式不正确，请检查输入');
  }
  return data;
}
function welcome() {
  const el = node('div', 'welcome');
  el.append(node('div', 'spark', '✳'), node('h1', '', '今天，一起完成什么？'), node('p', '', '一个想法，或一件小事。')); 
  const intro = node('button', 'welcome-guide', '◎ 2 分钟，体验 Agent 的核心能力  →'); intro.onclick = () => openGuide(0); el.append(intro);
  const cards = node('div', 'examples');
  examples.forEach(([title, subtitle, icon, text]) => { const b = node('button', 'example'); b.type = 'button'; b.append(node('span', 'example-icon', icon), node('strong', '', title), node('span', 'example-description', subtitle)); b.onclick = () => { $('message').value = text; $('message').focus(); }; cards.append(b); });
  el.append(cards); return el;
}
function renderConversation(state) {
  const key = JSON.stringify([state.id, state.messages, state.busy]);
  if (key === snapshot) return;
  snapshot = key;
  const area = $('conversation');
  const nearBottom = area.scrollHeight - area.scrollTop - area.clientHeight < 130;
  area.replaceChildren();
  if (!state.messages.length) area.append(welcome());
  state.messages.forEach(m => {
    if (m.role === 'tool') return;
    if (m.tool_calls?.length) { area.append(node('div', 'tool-inline', '↳ 调用工具  ' + m.tool_calls.map(c => c.function.name).join(' · '))); }
    if (!m.content) return;
    const wrap = node('article', 'message ' + m.role);
    const body = m.role === 'assistant'
      ? AgentMarkdown.render(m.content)
      : node('div', 'message-body', m.content);
    wrap.append(node('div', 'message-label', m.role === 'user' ? '你' : 'Minimal Agent'), body);
    area.append(wrap);
  });
  if (state.busy) area.append(node('div', 'typing', '● 正在处理…'));
  if (nearBottom || state.busy) area.scrollTop = area.scrollHeight;
}
function renderState(state) {
  $('session-title').textContent = state.title;
  const listed = sessions.find(s => s.id === state.id);
  if (listed && listed.title !== state.title) { listed.title = state.title; renderSessions(); }
  $('steps').textContent = state.steps + ' / ' + (metadata.max_steps || 8);
  $('context-size').textContent = (state.context_bytes / 1024).toFixed(1) + ' KB';
  $('context-progress').max = state.budget_bytes;
  $('context-progress').value = state.context_bytes;
  $('run-status').textContent = state.busy ? '执行中' : state.active ? '待恢复' : '就绪';
  $('run-status').classList.toggle('busy', state.busy);
  $('resume').classList.toggle('hidden', state.busy || !state.active);
  $('send').disabled = sending || state.busy || state.active;
  renderConversation(state);
  $('todo-count').textContent = state.todos.length;
  const todos = $('todos'); todos.replaceChildren();
  if (!state.todos.length) todos.append(node('p', 'empty', '暂无待办'));
  state.todos.forEach(t => { const el = node('div', 'todo' + (t.done ? ' done' : '')); el.append(node('span', '', t.done ? '☑' : '□'), node('span', '', t.text)); todos.append(el); });
  $('summary').textContent = state.summary || '暂无摘要';
  const trace = $('trace');
  const opened = new Set([...trace.querySelectorAll('details[open]')].map(x => x.dataset.seq));
  trace.replaceChildren();
  const labels = {user:'收到任务', model_start:'请求模型', decision:'选择工具', tool:'执行工具', final:'生成回复', compaction:'压缩历史', error:'任务未完成', limit:'达到步数上限', protocol_error:'协议重试'};
  if (!state.events.length) trace.append(node('p', 'empty', '等待第一个任务'));
  [...state.events].reverse().forEach(e => {
    const el = node('div', 'trace-item'), title = node('div', 'trace-title');
    title.append(node('span', '', labels[e.kind] || e.kind), node('time', '', new Date(e.at).toLocaleTimeString('zh-CN', {hour12:false})));
    el.append(title); const p = e.payload;
    let info = '';
    if (e.kind === 'model_start') info = '第 ' + p.step + ' 步 · ' + (p.prompt_bytes/1024).toFixed(1) + ' KB 输入';
    if (e.kind === 'decision') info = p.tool_calls.map(c => c.function.name).join(' → ');
    if (e.kind === 'tool') info = p.name + ' · ' + (p.result.ok ? '成功' : '失败') + ' · ' + p.duration_ms + ' ms';
    if (e.kind === 'compaction') info = '归档 ' + p.archived_count + ' 条消息，生成历史摘要';
    if (info) el.append(node('p', 'trace-info', info));
    if (['tool','decision','compaction','error','protocol_error'].includes(e.kind)) {
      const d = node('details'); d.dataset.seq = String(e.seq); d.open = opened.has(String(e.seq));
      d.append(node('summary', '', '查看详情'), node('pre', '', JSON.stringify(p, null, 2))); el.append(d);
    }
    trace.append(el);
  });
  observeGuide(state);
}
function renderSessions() {
  const key = JSON.stringify([current, sessions.map(s => [s.id, s.title])]);
  if (key === listSnapshot) return;
  listSnapshot = key;
  $('session-count').textContent = sessions.length;
  $('sessions').replaceChildren();
  sessions.forEach(s => {
    const row = node('div', 'session-row' + (s.id === current ? ' selected' : ''));
    row.dataset.session = s.id;
    const b = node('button', 'session', s.title); b.title = s.title;
    if (s.id === current) b.setAttribute('aria-current', 'page');
    b.onclick = () => select(s.id).catch(e => notice(e.message));
    const edit = node('button', 'session-edit', '✎');
    edit.setAttribute('aria-label', '重命名：' + s.title); edit.title = '重命名';
    edit.onclick = () => openRename(s);
    row.append(b, edit); $('sessions').append(row);
  });
}
async function loadSessions() {
  sessions = await api('/sessions'); renderSessions();
}
function openRename(session) {
  renaming = session.id;
  $('rename-title').value = session.title; $('rename-error').textContent = '';
  $('rename-dialog').showModal(); $('rename-title').focus(); $('rename-title').select();
}
$('rename-cancel').onclick = () => $('rename-dialog').close();
$('rename-dialog').addEventListener('cancel', event => { if (renameSaving) event.preventDefault(); });
$('rename-form').onsubmit = async event => {
  event.preventDefault(); if (renameSaving) return;
  const title = $('rename-title').value.trim();
  if (!title) { $('rename-error').textContent = '标题不能为空'; return; }
  renameSaving = true; $('rename-save').disabled = true; $('rename-cancel').disabled = true;
  $('rename-error').textContent = '';
  try {
    const result = await api('/sessions/' + renaming, 'PATCH', {title});
    const session = sessions.find(s => s.id === result.id);
    if (session) session.title = result.title;
    if (current === result.id) $('session-title').textContent = result.title;
    $('rename-dialog').close(); renderSessions();
    const edit = [...$('sessions').querySelectorAll('.session-edit')][sessions.findIndex(s => s.id === result.id)];
    edit?.focus();
  } catch(e) { $('rename-error').textContent = e.message; }
  finally { renameSaving = false; $('rename-save').disabled = false; $('rename-cancel').disabled = false; }
};
async function select(id) {
  current = id; snapshot = ''; localStorage.setItem('minimal-agent-session', id); notice();
  renderSessions();
  const state = await api('/sessions/' + id); if (current === id) { renderState(state); guideSessionSelected(id); }
}
async function createSession(title = '新对话') {
  const s = await api('/sessions', 'POST', {title}); await loadSessions(); await select(s.id); guideSessionCreated();
}
async function start() {
  await api('/visitor', 'POST', {});
  metadata = await api('/me'); csrf = metadata.csrf;
  $('model-name').textContent = metadata.model;
  $('login-screen').classList.add('hidden'); $('workspace').classList.remove('hidden');
  await loadSessions(); const previous = localStorage.getItem('minimal-agent-session');
  if (!sessions.length) await createSession();
  else await select(sessions.find(s => s.id === previous)?.id || sessions[0].id);
  if (!localStorage.getItem('minimal-agent-guide-v1')) openGuide(0);
}
const guideSteps = [
  {icon:'↗', title:'一句话，多个工具', description:'模型根据工具描述自主选择计算器和待办，再根据结果继续执行。核心循环由项目自行实现。', preview:'计算 (128+72)*3，再添加待办：周五交报名费。', action:'填入示例', kind:'prompt'},
  {icon:'◫', title:'每一步，都能看见', description:'打开执行面板，展开“查看详情”，检查工具参数、返回结果和耗时。这里展示真实执行记录。', preview:'模型请求 → 选择工具 → 工具结果 → 最终回复', action:'查看执行面板', kind:'trace'},
  {icon:'◌', title:'不同会话，互不串线', description:'新建一个会话查询待办。上一会话的报名费不会出现在这里，刷新后各自的记录仍会保留。', preview:'列出这个会话的所有待办。', action:'新建会话并填入', kind:'new'},
  {icon:'↳', title:'接着说，它能跟上', description:'回到第一个会话，用“刚才那条”继续追问。Agent 根据上下文定位待办，并更新它的状态。', preview:'把刚才那条报名费待办标记为完成。', action:'回到原会话并填入', kind:'return'},
  {icon:'✳', title:'长对话，也有边界', description:'上下文超预算时自动压缩旧对话，保留近期消息和独立的待办状态。任务设有步数上限，中断后可恢复。', preview:'执行面板可查看上下文用量和历史摘要。短对话尚未触发压缩时，摘要为空是正常的。', action:'查看上下文', kind:'memory'}
];
let guideStep = 0, guideOrigin = '', guideBusy = false, guidePhase = '', guidePending = null, lastEventSeq = 0, guideTimer = 0;
function openGuide(step = guideStep) {
  if (step !== guideStep) { guidePhase = ''; guidePending = null; clearTimeout(guideTimer); }
  guideStep = step;
  if (!guideOrigin || !sessions.some(s => s.id === guideOrigin)) guideOrigin = current;
  const item = guideSteps[guideStep];
  $('guide-progress').textContent = (guideStep + 1) + ' / ' + guideSteps.length;
  $('guide-icon').textContent = item.icon; $('guide-title').textContent = item.title;
  $('guide-description').textContent = item.description + ' 完成后自动继续。'; $('guide-preview').textContent = item.preview;
  $('guide-action').textContent = item.action; $('guide-error').textContent = '';
  $('guide-prev').disabled = guideStep === 0;
  $('guide-next').textContent = guideStep === guideSteps.length - 1 ? '完成导览 ✓' : '下一步 →';
  $('guide-dots').textContent = guideSteps.map((_, i) => i === guideStep ? '●' : '○').join(' ');
  if (!$('guide-dialog').open) $('guide-dialog').show();
  startSpotlight();
  localStorage.setItem('minimal-agent-guide-v1', 'seen');
}
let spotlightFrame = 0;
const spotlight = node('div', 'tour-spotlight hidden');
spotlight.setAttribute('aria-hidden', 'true');
const shades = Array.from({length:4}, () => node('div', 'tour-shade'));
const ring = node('div', 'tour-ring'); spotlight.append(...shades, ring); document.body.append(spotlight);
function guideTarget() {
  const kind = guideSteps[guideStep].kind;
  if (kind === 'prompt' || guidePhase === 'query') return $('chat-form');
  if (kind === 'new') return $('new-session');
  if (kind === 'return') return [...$('sessions').children].find(row => row.dataset.session === guideOrigin) || $('sessions');
  if (kind === 'memory') return $('memory');
  return document.querySelector('.trace-section');
}
function startSpotlight() {
  cancelAnimationFrame(spotlightFrame);
  const kind = guideSteps[guideStep].kind;
  if (['trace', 'memory'].includes(kind)) setPanel(true);
  else if (window.innerWidth <= 850) setPanel(false);
  const target = guideTarget();
  target?.scrollIntoView({block:'nearest', inline:'nearest'});
  spotlight.classList.remove('hidden');
  function position() {
    if (!$('guide-dialog').open) { spotlight.classList.add('hidden'); return; }
    const target = guideTarget();
    if (!target) return;
    const r = target.getBoundingClientRect(), w = innerWidth, h = innerHeight;
    const x = Math.max(0,r.left-7), y = Math.max(0,r.top-7), right = Math.min(w,r.right+7), bottom = Math.min(h,r.bottom+7);
    function box(el,l,t,width,height) { Object.assign(el.style,{left:l+'px',top:t+'px',width:Math.max(0,width)+'px',height:Math.max(0,height)+'px'}); }
    box(shades[0],0,0,w,y); box(shades[1],0,bottom,w,h-bottom);
    box(shades[2],0,y,x,bottom-y); box(shades[3],right,y,w-right,bottom-y);
    box(ring,x,y,right-x,bottom-y);
    const card = $('guide-dialog'), cw = Math.min(360,w-28);
    card.style.width = cw+'px';
    let left, top;
    if (right+cw+28<w) { left=right+18; top=Math.max(14,Math.min(y,h-card.offsetHeight-14)); }
    else if (x>cw+28) { left=x-cw-18; top=Math.max(14,Math.min(y,h-card.offsetHeight-14)); }
    else { left=Math.max(14,Math.min(x,w-cw-14)); top=y>h/2?Math.max(14,y-card.offsetHeight-18):Math.min(h-card.offsetHeight-14,bottom+18); }
    card.style.left=left+'px'; card.style.top=Math.max(14,top)+'px';
    spotlightFrame=requestAnimationFrame(position);
  }
  position();
}
$('guide-dialog').addEventListener('close',()=>{ guidePending = null; clearTimeout(guideTimer); cancelAnimationFrame(spotlightFrame); spotlight.classList.add('hidden'); });
document.addEventListener('keydown',e=>{ if(e.key==='Escape' && $('guide-dialog').open) { e.preventDefault(); $('guide-dialog').close(); } });
// Clicking in the cutout uses the actual page controls, not a duplicate tutorial UI.
$('start-guide').onclick = () => openGuide();
$('guide-close').onclick = () => $('guide-dialog').close();
$('guide-prev').onclick = () => openGuide(Math.max(0, guideStep - 1));
$('guide-next').onclick = () => { if (guideStep === guideSteps.length - 1) $('guide-dialog').close(); else openGuide(guideStep + 1); };
function guideQuery() {
  guidePhase = 'query';
  $('message').value = guideSteps[guideStep].preview;
  $('guide-action').textContent = '发送示例';
  $('guide-description').textContent = '点击高亮区域的发送按钮。任务完成后，导览会自动继续。';
  startSpotlight(); $('message').focus();
}
function guideSessionSelected(id) {
  if (!$('guide-dialog').open || guidePending) return;
  if (guideStep === 3 && id === guideOrigin) guideQuery();
}
function guideSessionCreated() {
  if ($('guide-dialog').open && guideStep === 2) guideQuery();
}
function observeGuide(state) {
  lastEventSeq = Math.max(0, ...state.events.map(e => e.seq));
  if (!$('guide-dialog').open || !guidePending || guidePending.id !== state.id || state.busy || state.active) return;
  const finish = state.events.find(e => e.seq > guidePending.seq && ['final','error','limit'].includes(e.kind));
  if (!finish) return;
  const step = guidePending.step; guidePending = null;
  if (finish.kind !== 'final') { $('guide-error').textContent = '任务未完成，可重试或跳过此步。'; return; }
  if (step === 3 && !state.todos.some(t => t.done && t.text.includes('报名费'))) {
    $('guide-error').textContent = '还没有完成报名费待办，可重试或跳过此步。'; return;
  }
  openGuide(step + 1);
}
$('trace').addEventListener('click', event => {
  if (!$('guide-dialog').open || guideStep !== 1 || !event.target.closest('summary')) return;
  const detail = event.target.closest('details');
  clearTimeout(guideTimer);
  guideTimer = setTimeout(() => {
    if ($('guide-dialog').open && guideStep === 1 && detail.open) openGuide(2);
  }, 1400);
});
$('memory').addEventListener('toggle', () => {
  if ($('guide-dialog').open && guideStep === 4 && $('memory').open) {
    clearTimeout(guideTimer);
    guideTimer = setTimeout(() => {
      if ($('guide-dialog').open && guideStep === 4) { $('guide-dialog').close(); notice('导览完成，可以开始自己的任务了。'); }
    }, 1800);
  }
});
$('guide-action').onclick = async () => {
  if (guideBusy) return;
  guideBusy = true; $('guide-action').disabled = true;
  try {
    if (guidePhase === 'query') { $('chat-form').requestSubmit(); return; }
    if (guideStep === 0) { guideOrigin = current; guideQuery(); }
    else if (guideStep === 1) {
      const detail = $('trace').querySelector('details');
      if (detail) detail.querySelector('summary').click();
      else $('guide-error').textContent = '先完成一次工具调用，或点击下一步跳过。';
    }
    else if (guideStep === 2) await createSession();
    else if (guideStep === 3) await select(guideOrigin);
    else { $('memory').open = false; requestAnimationFrame(() => { $('memory').open = true; }); }
  } catch(e) { $('guide-error').textContent = e.message; }
  finally { guideBusy = false; $('guide-action').disabled = false; }
};
function setPanel(open) {
  $('workspace').classList.toggle('panel-open', open);
  $('toggle-panel').setAttribute('aria-expanded', String(open));
}
setPanel(window.innerWidth > 850);
$('toggle-panel').onclick = () => setPanel(!$('workspace').classList.contains('panel-open'));
$('close-panel').onclick = () => { setPanel(false); $('toggle-panel').focus(); };
document.addEventListener('keydown', e => { if (e.key === 'Escape' && !$('rename-dialog').open && !$('guide-dialog').open && $('workspace').classList.contains('panel-open')) { setPanel(false); $('toggle-panel').focus(); } });
$('retry-start').onclick = () => connect();
$('new-session').onclick = async () => {
  const b = $('new-session'); b.disabled = true;
  try { await createSession(); } catch(e) { notice(e.message); } finally { b.disabled = false; }
};
$('chat-form').onsubmit = async (event) => {
  event.preventDefault(); if (sending || $('send').disabled) return;
  const text = $('message').value.trim(); if (!text || !current) return;
  sending = true; $('send').disabled = true; notice(); const id = current;
  const submittedGuide = $('guide-dialog').open && [0,2,3].includes(guideStep) ? {id, step:guideStep, seq:lastEventSeq} : null;
  try { await api('/sessions/' + id + '/messages', 'POST', {text}); if ($('guide-dialog').open && submittedGuide && submittedGuide.step === guideStep) guidePending = submittedGuide; $('message').value = ''; const state = await api('/sessions/' + id); if (id === current) renderState(state); await loadSessions(); }
  catch(e) { notice(e.message); } finally { sending = false; }
};
$('message').onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); $('chat-form').requestSubmit(); } };
$('resume').onclick = async () => { try { await api('/sessions/' + current + '/resume', 'POST', {}); notice('正在恢复任务…'); } catch(e) { notice(e.message); } };
setInterval(async () => {
  if (!current || refreshBusy || document.hidden || $('workspace').classList.contains('hidden')) return;
  refreshBusy = true; const id = current;
  try { const state = await api('/sessions/' + id); if (id === current) renderState(state); if (++listRefresh % 4 === 0 && !$('rename-dialog').open) await loadSessions(); }
  catch(e) { notice(e.message); } finally { refreshBusy = false; }
}, 1600);
function connect() {
  $('retry-start').classList.add('hidden');
  $('login-error').textContent = '正在准备你的会话…';
  start().catch(e => { $('login-error').textContent = e.message; $('retry-start').classList.remove('hidden'); });
}
connect();
