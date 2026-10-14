'use strict';
const $ = id => document.getElementById(id);
const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
const styleNames = {original:'原话整理', natural:'自然叙述', literary:'文学表达'};
const statusNames = {new:'选择照片', preparing:'正在看照片…', active:'访谈进行中', responding:'正在接续你的讲述…', closing:'正在整理最后的材料…', review:'访谈已结束，可以选择表达', generating:'正在写游记…', completed:'游记已保存', paused:'处理暂停，材料已保存', stopped:'已停止，材料已保存'};
let config, state = null, files = [], previewURLs = [], selectedPhoto = null, pollBusy = false;
let messageSignature = '', photoSignature = '', fragmentSignature = '', articleSignature = '';
let pendingSend = null;
let shownStateError = '';
const drafts = new Map(), metricStarts = new Map();
function notice(text) { $('notice').textContent = text || ''; $('notice').classList.toggle('hidden', !text); }
async function api(url, opts = {}) {
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 20000);
  try {
    const r = await fetch(url, {...opts, signal:controller.signal});
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请求未完成，请检查输入后重试。');
    return data;
  } catch(e) { if (e.name === 'AbortError') throw new Error('网页连接超时，材料不会丢失，请重试。'); throw e; }
  finally { clearTimeout(timer); }
}
const jsonOpts = (data, method='POST') => ({method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
const base = () => '/api/sessions/' + state.id;
function busy() { return state && ['preparing','responding','closing','generating'].includes(state.status); }
async function listSessions() {
  const rows = await api('/api/sessions'); $('sessions').replaceChildren(); $('mobileSessions').replaceChildren();
  const start=el('option','','开始新回忆');start.value='';$('mobileSessions').append(start);
  for (const row of rows) {
    const option=el('option','',row.title);option.value=row.id;$('mobileSessions').append(option);
    const b = el('button', row.id === state?.id ? 'active' : '', row.title);
    b.dataset.sessionId=row.id;
    b.append(el('small', '', new Date(row.updated_at*1000).toLocaleDateString('zh-CN')));
    b.onclick = () => run(async () => { const s = await api('/api/sessions/'+row.id); resetView(); accept(s); });
    $('sessions').append(b);
  }
  $('mobileSessions').value=state?.id || '';
}
$('mobileSessions').onchange=e=>{if(!e.target.value){$('newTrip').click();return;}run(async()=>{const s=await api('/api/sessions/'+e.target.value);resetView();accept(s);});};
function resetView() { messageSignature = photoSignature = fragmentSignature = articleSignature = ''; selectedPhoto = null; }
function accept(s) { if(state?.id===s.id && s.updated_at < state.updated_at) return; state = s; localStorage.setItem('travelDemoSession', s.id); render(); }
async function run(fn) { try { notice(''); await fn(); } catch(e) { notice(e.message); } }
function render() {
  if (!state) return;
  const isNew = state.status === 'new';
  $('welcome').classList.toggle('hidden', !isNew); $('workspace').classList.toggle('hidden', isNew);
  $('pageTitle').textContent = state.title;
  $('mobileSessions').value=state.id;
  for(const b of $('sessions').querySelectorAll('button')) b.classList.toggle('active',b.dataset.sessionId===state.id);
  $('status').textContent = statusNames[state.status] || state.status;
  $('send').disabled = !['active','review'].includes(state.status);
  $('answer').disabled = !['active','review'].includes(state.status);
  $('working').classList.toggle('hidden', !busy());
  $('working').querySelector('small').textContent = statusNames[state.status] || '正在处理…';
  $('finish').disabled = !['active','review'].includes(state.status);
  $('stop').disabled = ['new','stopped','completed'].includes(state.status);
  $('resume').classList.toggle('hidden', !(['paused','stopped'].includes(state.status) || state.record_error || state.write_error));
  $('resume').disabled = busy();
  $('generate').disabled = !['review','completed','stopped'].includes(state.status) || state.recording;
  $('settingsBtn').disabled = busy();
  const problem=state.error || state.record_error || state.write_error;
  if(problem) notice(problem);
  else if(shownStateError && $('notice').textContent===shownStateError) notice('');
  shownStateError=problem || '';
  $('memoryCount').textContent = state.nodes.length;
  $('memoryTopics').replaceChildren(...state.nodes.slice(-5).map(n => el('div','memory-topic',n.topic)));
  $('backgroundNote').textContent = state.recording ? '正在保存这轮讲述，可以继续聊。' : state.writing ? '正在准备三种表达，可以继续聊。' : '讲述和修改都已保存在本机。';
  renderPhotos(); renderMessages(); renderFragments(); renderArticle();
  for (const m of state.messages) {
    if (m.role !== 'assistant' || !metricStarts.has(m.reply_for)) continue;
    const submitted = metricStarts.get(m.reply_for), now = Date.now(); metricStarts.delete(m.reply_for);
    // Responses are shown as complete messages, so first substantive and completed times coincide.
    api(base()+'/metrics',jsonOpts({seq:m.reply_for,submitted_at:submitted,first_response_at:now,completed_at:now})).catch(() => {});
  }
}
function renderPhotos() {
  const sig = JSON.stringify([state.photos.map(p => p.id), state.current_photo, selectedPhoto]);
  if (sig === photoSignature) return; photoSignature = sig; $('photoStrip').replaceChildren();
  for (const p of state.photos) {
    const b = el('button','photo-card' + ((selectedPhoto || state.current_photo) === p.id ? ' current' : ''));
    const img = el('img'); img.src = p.url; img.alt = p.name;
    b.append(img,el('span','',p.name)); b.title = '从这张照片继续讲';
    b.onclick = () => { selectedPhoto = p.id; renderPhotos(); if (!busy()) $('answer').focus(); };
    $('photoStrip').append(b);
  }
}
function renderMessages() {
  const sig = JSON.stringify(state.messages.map(m => [m.seq,m.text])); if (sig === messageSignature) return;
  messageSignature = sig; const box = $('messages'); const nearBottom = box.scrollHeight-box.scrollTop-box.clientHeight < 100;
  box.replaceChildren();
  for (const m of state.messages) {
    const row = el('div','message '+m.role+(m.type === 'edit' ? ' edit' : ''));
    row.append(el('small','',m.type === 'edit' ? '你修改的片段' : m.role === 'user' ? '你' : '旅行片刻'),el('p','',m.text)); box.append(row);
  }
  if (nearBottom || busy()) box.scrollTop = box.scrollHeight;
}
function renderFragments() {
  const sig = JSON.stringify(state.fragments.map(f => [f.id,f.revision,f.status,f.variants,f.selected_style,f.selected_text])) + String(busy()) + state.recording;
  if (sig === fragmentSignature) return; fragmentSignature = sig;
  const box = $('fragments'); box.replaceChildren();
  if (!state.fragments.length) { box.append(el('div','empty-fragments',state.recording ? '正在整理刚才的讲述…' : '继续讲述具体经历后，合适的片段会出现在这里。')); return; }
  for (const f of state.fragments) {
    const section = el('section','fragment'); section.append(el('h3','',f.title));
    if (f.status !== 'ready') { section.append(el('p','fragment-state',f.status === 'pending_check' ? '新讲述已保存，正在更新这段经历。你的修改原文会保留。' : '正在准备三种表达…')); box.append(section); continue; }
    const grid = el('div','variant-grid');
    for (const v of f.variants) {
      const card = el('div','variant'+(f.selected_style === v.style ? ' selected' : ''));
      const key = state.id+'/'+f.id+'/'+v.style;
      const label = el('label','',styleNames[v.style]); const area = el('textarea'); area.id = 'edit-'+f.id+'-'+v.style; label.htmlFor = area.id;
      const saved = f.selected_style === v.style && f.selected_text ? f.selected_text : v.text;
      const draft = drafts.get(key); area.value = draft?.revision === f.revision ? draft.text : saved;
      // Retain a dirty edit across material revisions and make the conflict visible.
      if (draft && draft.revision !== f.revision) {
        area.value = draft.text; card.append(el('p','fragment-state','材料已有更新，这里保留了你尚未保存的修改。请核对后保存。'));
      }
      area.setAttribute('aria-label', f.title+' · '+styleNames[v.style]);
      area.disabled = busy() || state.recording || state.status === 'stopped';
      area.oninput = () => { drafts.set(key,{text:area.value,revision:f.revision}); button.textContent = '保存修改并选用'; };
      const button = el('button','', f.selected_style === v.style ? (f.edited ? '已选用 · 已修改' : '已选用') : '选用这一版');
      button.disabled = area.disabled;
      button.onclick = () => run(async () => {
        button.disabled = true;
        try {
          const result = await api(base()+'/fragments/'+f.id+'/choice',jsonOpts({style:v.style,text:area.value,revision:f.revision,request_id:crypto.randomUUID()}));
          drafts.delete(key); accept(result);
        } finally { button.disabled = false; }
      });
      card.append(label,area,button); grid.append(card);
    }
    section.append(grid); box.append(section);
  }
}
function renderArticle() {
  $('articleHint').textContent = state.article_stale ? '材料已经更新；这篇是之前保存的版本，可重新生成。' : state.status === 'completed' ? '这篇游记已经保存，可以复制，或调整片段后重新生成。' : state.status === 'review' ? '可以先选择喜欢的片段表达，再生成。未选择的材料由记录员选材。' : '结束访谈后，可以查看片段，再生成游记。';
  const sig = JSON.stringify(state.article); if (sig === articleSignature) return; articleSignature = sig;
  $('article').replaceChildren(); $('article').classList.toggle('hidden',!state.article); $('copyArticle').classList.toggle('hidden',!state.article);
  if (state.article) $('article').append(el('h2','',state.article.title),el('p','',state.article.body));
}
function setFiles(items) {
  files = Array.from(items); for (const url of previewURLs) URL.revokeObjectURL(url); previewURLs=[]; $('previews').replaceChildren();
  if (files.length>20) { notice('一次最多选取 20 张照片。'); files=[]; return; }
  for (const file of files) { const img=el('img'); img.src=URL.createObjectURL(file); previewURLs.push(img.src); img.alt=file.name; $('previews').append(img); }
}
$('photos').onchange = e => setFiles(e.target.files);
for (const name of ['dragover','dragleave','drop']) $('dropZone').addEventListener(name,e => {
  e.preventDefault(); $('dropZone').classList.toggle('drag',name === 'dragover'); if(name === 'drop') setFiles(e.dataTransfer.files);
});
$('begin').onclick = () => run(async () => {
  if (!files.length && !state?.photos.length) throw new Error('请先选几张旅行照片。');
  $('begin').disabled=true;
  try {
    if (!state || state.status !== 'new') state=await api('/api/sessions',jsonOpts({title:$('tripTitle').value,settings:config.settings}));
    if (files.length) { const body=new FormData(); files.forEach(f=>body.append('files',f)); accept(await api(base()+'/photos',{method:'POST',body})); setFiles([]); }
    accept(await api(base()+'/start',jsonOpts({}))); await listSessions();
  } finally { $('begin').disabled=false; }
});
$('newTrip').onclick = () => { state=null; localStorage.removeItem('travelDemoSession'); resetView(); setFiles([]); $('tripTitle').value=''; $('answer').value=''; pendingSend=null; $('welcome').classList.remove('hidden'); $('workspace').classList.add('hidden'); $('pageTitle').textContent='把旅行，再慢慢讲一遍。'; notice(''); };
$('sendForm').onsubmit = e => { e.preventDefault(); run(async () => {
  const text=$('answer').value.trim(); if(!text) return;
  const submitted=Date.now();
  if (!pendingSend || pendingSend.text!==text) pendingSend={text,request_id:crypto.randomUUID(),photo_id:selectedPhoto || state.current_photo,client_submitted_at:submitted};
  const result=await api(base()+'/messages',jsonOpts(pendingSend));
  const m=result.messages.find(m=>m.role==='user' && m.text===text && m.type==='conversation' && m.seq===result.pending_reply_seq);
  if(m) metricStarts.set(m.seq,pendingSend.client_submitted_at);
  pendingSend=null; $('answer').value=''; selectedPhoto=null; accept(result);
}); };
$('answer').addEventListener('keydown',e => { if(e.key==='Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); $('sendForm').requestSubmit(); } });
for (const [id,route] of [['finish','finish'],['stop','stop'],['resume','resume'],['generate','generate']]) {
  $(id).onclick=()=>run(async()=>{ $(id).disabled=true; try{accept(await api(base()+'/'+route,jsonOpts({})));}finally{render();} });
}
$('export').onclick=()=>{ const a=el('a'); a.href=base()+'/export'; a.download='travel-record.json'; a.click(); };
$('copyArticle').onclick=()=>run(async()=>{ await navigator.clipboard.writeText(state.article.title+'\n\n'+state.article.body); notice('游记已复制。'); });
const fields=[['request_timeout','单次处理等待上限（秒）',10,180,1,'包括排队与重试的总等待'],['retries','请求重试次数',0,2,1,'失败后在总等待内重试'],['photo_soft_minutes','照片软检查点（分钟）',0.1,30,0.1,'到点后判断是否自然收束'],['photo_hard_minutes','照片收束检查点（分钟）',0.1,60,0.1,'到点后不再开启新问题'],['history_turns','近期对话范围（轮）',4,200,1,'原始记录仍完整保留'],['recovery_attempts','恢复次数上限',0,5,1,'调整设置会重置恢复计数']];
$('settingsBtn').onclick=()=>{
  $('settingsFields').replaceChildren();
  for(const [key,label,min,max,step,hint] of fields){ const row=el('label','',label),input=el('input'); input.type='number';input.name=key;input.min=min;input.max=max;input.step=step;input.required=true;input.value=(state?.settings || config.settings)[key];row.append(input,el('small','',hint));$('settingsFields').append(row); }
  $('settingsDialog').showModal();
};
$('closeSettings').onclick=()=>$('settingsDialog').close();
$('settingsForm').onsubmit=e=>{e.preventDefault();run(async()=>{const settings=Object.fromEntries(Array.from(new FormData(e.target),([k,v])=>[k,Number(v)]));if(settings.photo_hard_minutes<settings.photo_soft_minutes)throw new Error('收束检查点不能早于软检查点。');if(state)accept(await api(base()+'/settings',jsonOpts(settings,'PUT')));else config.settings=settings;$('settingsDialog').close();});};
async function poll(){if(!state || pollBusy || state.status==='new')return;pollBusy=true;const sid=state.id;try{const result=await api('/api/sessions/'+sid);if(state?.id===sid)accept(result);}catch(e){notice(e.message);}finally{pollBusy=false;}}
run(async()=>{config=await api('/api/config');await listSessions();const sid=localStorage.getItem('travelDemoSession');if(sid){try{state=await api('/api/sessions/'+sid);render();}catch{localStorage.removeItem('travelDemoSession');}}if(!config.key_ready)notice('请先配置 MiniMax Key，再开始访谈。');});
setInterval(poll,1200);
