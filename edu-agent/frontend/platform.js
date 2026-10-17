import { platformRequest, readState } from './api.js';

/* ================= 常量与基础工具 ================= */
const SCHOOL_NAMES = { jlu: '吉林大学', heu: '哈尔滨工程大学', neu: '东北大学' };
const PAGES = ['welcome', 'prepare', 'lab', 'plan', 'profile', 'reference'];
const CONFIRM_NOTE = '在工作台确认';

let state = null;            // 最近一次 yt:state 的 detail
let sources = null;          // /meta/sources 结果（仅读取一次，失败可手动重读）
let sourcesError = '';
let activity = { busy: false, pending: false };
let uidSeq = 0;
let noticeNode = null;
const dirtyForms = new Set(); // 有未保存草稿的表单，同步时不得覆盖
const navButtons = [];

const mountEl = (name) => document.querySelector(`[data-platform="${name}"]`);
const sessionOf = () => (state && state.session) || {};
const sid = () => state && state.session_id;
const nameOf = (id) => SCHOOL_NAMES[id] || String(id || '');

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = String(text);
  return n;
}
function clear(node) { while (node && node.firstChild) node.removeChild(node.firstChild); }
function metricVal(m) { return (m && typeof m === 'object' && 'value' in m) ? m.value : m; }
function textOf(it) {
  if (it == null) return '';
  if (typeof it === 'string') return it;
  if (typeof it === 'object') return it.text || it.reason || it.message || '暂无可读说明';
  return String(it);
}
function fieldRow(labelText, input) {
  const row = el('div', 'field');
  const label = el('label', null, labelText);
  input.id = 'pf-' + ++uidSeq;
  label.setAttribute('for', input.id);
  row.append(label, input);
  return row;
}
function makeError() { const p = el('p', 'portal-error'); p.setAttribute('aria-live', 'polite'); p.hidden = true; return p; }
function setError(node, msg) { node.textContent = msg || ''; node.hidden = !msg; }
function lockable(node) { node.setAttribute('data-lock', '1'); return node; }
function applyLock() {
  const locked = !!(writing || activity.busy || activity.pending);
  document.querySelectorAll('[data-lock]').forEach((c) => { c.disabled = locked || c.dataset.busy === '1'; });
  // 写入/实验忙碌期间真实禁用平台所有表单输入：键盘不可编辑，值与草稿不改动，结束后自动恢复
  document.querySelectorAll('.portal-form input, .portal-form textarea, .portal-form select').forEach((n) => { n.disabled = locked; });
  if (welcomeRefs.button) welcomeRefs.button.disabled = locked || !state || !sources;
}
function markBusy(btn, on) { btn.dataset.busy = on ? '1' : '0'; applyLock(); }
let writing = false; // 本地写入标志：runWrite 期间禁用全部 [data-lock] 写控件
async function runWrite(task) {
  if (writing) throw new Error('有写入正在进行，请稍候。');
  writing = true;
  applyLock();
  try { return await task(); }
  finally {
    writing = false;
    applyLock();
    window.dispatchEvent(new Event('yt:refresh')); // 成功或失败都回读
  }
}
function notify(msg, isError, busyNote) {
  if (!noticeNode) return;
  noticeNode.textContent = msg;
  noticeNode.setAttribute('role', isError ? 'alert' : 'status');
  if (busyNote) noticeNode.dataset.busyNote = '1'; else delete noticeNode.dataset.busyNote;
  noticeNode.hidden = false;
}

/* ================= 会话草稿（sessionStorage，按 sid 隔离） ================= */
const DRAFT_PREFIX = 'ytw:draft:';
let draftStorageOk = true;
let draftWarned = false;
let draftRestoredSid = '';
let pendingDrafts = {}; // 尚未取得 sid 时的内存输入，首次关联 sid 后清空
function draftUnavailable() {
  draftStorageOk = false;
  if (!draftWarned) { draftWarned = true; notify('草稿未能暂存，刷新可能丢失未保存输入', true); }
  return null;
}
function draftStore() {
  if (!draftStorageOk) return null;
  try {
    const s = window.sessionStorage;
    if (!s) throw new Error('sessionStorage unavailable');
    return s;
  } catch (e) { return draftUnavailable(); }
}
function draftKey(kind, id) { return DRAFT_PREFIX + String(id) + ':' + kind; }
function draftHasValue(values) {
  return Object.values(values || {}).some((v) => String(v == null ? '' : v) !== '');
}
function readDraft(kind, id) {
  const s = draftStore();
  if (!s || !id) return null;
  try {
    const raw = s.getItem(draftKey(kind, id));
    if (!raw) return null;
    const data = JSON.parse(raw);
    return (data && typeof data === 'object' && !Array.isArray(data)) ? data : null;
  } catch (e) { return draftUnavailable(); }
}
function writeDraft(kind, values) {
  const id = sid();
  if (!id) { pendingDrafts[kind] = values; return; }
  const s = draftStore();
  if (!s) return;
  try {
    const key = draftKey(kind, id);
    if (draftHasValue(values)) s.setItem(key, JSON.stringify(values));
    else s.removeItem(key);
  } catch (e) { draftUnavailable(); }
}
function clearDraft(kind, id = sid()) {
  const s = draftStore();
  if (!s || !id) return;
  try { s.removeItem(draftKey(kind, id)); } catch (e) { /* 清草稿失败不阻断主流程 */ }
}
function draftRefs(kind) { return kind === 'profile' ? profileRefs : (kind === 'plan' ? planRefs : matRefs); }
function draftValues(kind) {
  const r = draftRefs(kind);
  if (kind === 'profile') return { year: r.year.value, major: r.major.value, regions: r.regions.value, score: r.score.value, minutes: r.minutes.value };
  if (kind === 'plan') return { minutes: r.minutes.value };
  return { title: r.title.value, text: r.text.value };
}
function markDraft(kind, form) { dirtyForms.add(form); writeDraft(kind, draftValues(kind)); }
function setDraftValues(kind, values) {
  const r = draftRefs(kind);
  if (!r.form || !values) return;
  if (kind === 'profile') {
    r.year.value = values.year || '';
    r.major.value = values.major || '';
    r.regions.value = values.regions || '';
    r.score.value = values.score || '';
    r.minutes.value = values.minutes || '';
  } else if (kind === 'plan') {
    r.minutes.value = values.minutes || '';
  } else {
    r.title.value = values.title || '';
    r.text.value = values.text || '';
  }
}
function applyDraftValues(kind, values) {
  setDraftValues(kind, values);
  const r = draftRefs(kind);
  if (r.form && draftHasValue(values)) dirtyForms.add(r.form);
}
function clearSessionFormState() {
  [profileRefs.err, planRefs.err, matRefs.err].forEach(node=>{if(node)setError(node,'');});
  [profileRefs.form, planRefs.form, matRefs.form].forEach((form) => { if (form) dirtyForms.delete(form); });
  pendingDrafts = {};
  draftRestoredSid = '';
  if (profileRefs.form) setDraftValues('profile', { year: '', major: '', regions: '', score: '', minutes: '' });
  if (planRefs.form) setDraftValues('plan', { minutes: '' });
  if (matRefs.form) setDraftValues('material', { title: '', text: '' });
}
function restoreDraftsFor(id, preferPending) {
  if (!id) return;
  draftRestoredSid = id;
  ['profile', 'plan', 'material'].forEach((kind) => {
    const r = draftRefs(kind);
    if (!r.form) return;
    const pending = pendingDrafts[kind];
    const stored = readDraft(kind, id);
    const usableStored = stored && draftHasValue(stored) ? stored : null;
    const usablePending = pending && draftHasValue(pending) ? pending : null;
    const values = (preferPending && usablePending) ? usablePending : (usableStored || usablePending);
    if (!values) return;
    applyDraftValues(kind, values);
    if (values === usablePending && (!usableStored || preferPending)) writeDraft(kind, usablePending);
  });
  pendingDrafts = {};
}
function adoptState(detail) {
  const before = sid();
  const inSid = detail && typeof detail.session_id === 'string' && detail.session_id ? detail.session_id : null;
  const curRev = state && Number.isInteger(state.revision) && state.revision >= 0 ? state.revision : null;
  const inRev = detail && Number.isInteger(detail.revision) && detail.revision >= 0 ? detail.revision : null;
  if (before && inSid && before === inSid && curRev !== null && inRev !== null && inRev < curRev) return; // 同 sid 低 revision 直接忽略，不回退预算
  state = detail || state;
  const after = sid();
  if (after !== before) {
    if (welcomeRefs.form) { welcomeRefs.form.reset(); welcomeRefs.dirty = false; welcomeRefs.editing = false; setError(welcomeRefs.err, ''); }
    if (targetRefs.form) { targetRefs.dirty = false; setError(targetRefs.err, ''); }
    if(noticeNode){noticeNode.hidden=true;noticeNode.textContent='';delete noticeNode.dataset.busyNote;}
    clearAgentState(); // sid 变化先彻底隔离助手上下文，不串会话
    if (before) clearSessionFormState(); else draftRestoredSid = '';
    if (after) restoreDraftsFor(after, !before);
  } else if (after && draftRestoredSid !== after) {
    restoreDraftsFor(after, false);
  }
  renderAll();
}

/* ================= 顶部导航（hash 可恢复） ================= */
function hashPage() { const h = (location.hash || '').replace('#', ''); return PAGES.includes(h) ? h : 'prepare'; }
function readyForStudy() {
  const s = sessionOf(), p = s.profile || {};
  // Preserve ongoing work from earlier versions; never discard an existing lab to enforce a new screen.
  return !!(state?.lab_state || state?.journey?.can_return ||
    (s.selected_school_id && p.exam_year && (p.major || p.target_major) && p.target_score && p.daily_minutes));
}
function showPage(page, focus) {
  if (!PAGES.includes(page)) page = 'prepare';
  if (!readyForStudy() && page !== 'welcome') page = 'welcome';
  if (readyForStudy() && page === 'welcome') page = 'prepare';
  PAGES.forEach((p) => { const sec = document.getElementById('page-' + p); if (sec) sec.hidden = p !== page; });
  document.body.dataset.page = page;
  navButtons.forEach((b) => {
    if (b.dataset.page === (page === 'welcome' ? 'prepare' : page)) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
    b.disabled = !state || (!readyForStudy() && b.dataset.page !== 'prepare');
  });
  // While the first state is loading, retain the requested page so an existing
  // learner can reload directly into the lab/plan without losing navigation.
  if (state && location.hash !== '#' + page) history.replaceState(null, '', '#' + page);
  if (focus) {
    const sec = document.getElementById('page-' + page);
    if (sec) { if (!sec.hasAttribute('tabindex')) sec.setAttribute('tabindex', '-1'); sec.focus({ preventScroll: true }); }
  }
}
function setupNav() {
  const m = mountEl('notice');
  if (m) { noticeNode = el('p', 'notice-line'); noticeNode.hidden = true; m.appendChild(noticeNode); }
  document.querySelectorAll('#platform-nav button[data-page], #workspace-nav button[data-page]').forEach((b) => {
    navButtons.push(b);
    b.addEventListener('click', () => showPage(b.dataset.page, true));
  });
  window.addEventListener('hashchange', () => showPage(hashPage(), false));
  showPage(hashPage(), false);
}

/* First visit reuses the existing profile and explicit target-school APIs. */
const welcomeRefs = {};
const targetRefs = {};
function schoolSelect() {
  const select = el('select', 'input');
  const placeholder = el('option', null, '请选择目标院校'); placeholder.value = '';
  select.appendChild(placeholder); select.required = true;
  return select;
}
function fillSchools(select, selected) {
  if (!select || !sources) return;
  const value = selected === undefined ? select.value : selected;
  select.replaceChildren();
  const placeholder = el('option', null, '请选择目标院校'); placeholder.value = '';
  select.appendChild(placeholder);
  (sources.coverage || []).forEach(school => {
    const option = el('option', null, school.school_name || nameOf(school.school_id));
    option.value = school.school_id; select.appendChild(option);
  });
  select.value = value || '';
}
function buildWelcome() {
  const mount = mountEl('welcome'); if (!mount) return;
  const form = el('form', 'portal-form setup-form');
  const year = el('input', 'input'); Object.assign(year, {type:'number', min:'2000',max:'2100',step:'1',required:true});
  const major = el('input', 'input'); Object.assign(major,{type:'text',required:true,maxLength:100});
  const regions = el('input','input'); Object.assign(regions,{type:'text',maxLength:120,placeholder:'如：吉林、黑龙江；也可以留空'});
  const score = el('input', 'input'); Object.assign(score,{type:'number',min:'100',max:'500',step:'1',required:true,placeholder:'如 220'});
  const minutes = el('input', 'input'); Object.assign(minutes,{type:'number',min:'1',max:'1440',step:'1',required:true});
  form.append(fieldRow('报考方向',major),fieldRow('考研年份',year),fieldRow('目标总分',score),fieldRow('每日学习时间（分钟）',minutes),fieldRow('期望地区（选填）',regions));
  const hint = el('p', 'block-hint setup-note', '先了解你的情况，再推荐院校。目标分数是备考打算，比较结果不代表录取概率。');
  const err = makeError(); const button = lockable(el('button','btn btn-primary','看看适合我的院校 →')); button.type='submit';
  form.append(hint,err,button); mount.appendChild(form);
  Object.assign(welcomeRefs,{form,year,major,regions,score,minutes,err,button,dirty:false,editing:false});
  document.getElementById('edit-first-profile')?.addEventListener('click',()=>{welcomeRefs.editing=true;fillSetup();major.focus();});
  form.addEventListener('input',()=>{welcomeRefs.dirty=true;setError(err,'');});
  form.addEventListener('submit',async event=>{
    event.preventDefault(); if (button.disabled || !sid() || !form.reportValidity()) return;
    const requestSid = sid(); setError(err,''); markBusy(button,true);
    if (!major.value.trim()) { setError(err,'请填写报考方向。'); markBusy(button,false); return; }
    try {
      await runWrite(async()=>{
        await platformRequest('PATCH',`/api/v1/sessions/${requestSid}/profile`,{
          exam_year:Number(year.value), major:major.value.trim(), target_major:major.value.trim(), target_score:Number(score.value),daily_minutes:Number(minutes.value),
          preferred_regions:regions.value.split(/[,，、\s]+/).map(v=>v.trim()).filter(Boolean)
        });
        if (sid() !== requestSid) throw new Error('档案已切换，请核对当前档案。');
        await platformRequest('POST',`/api/v1/sessions/${requestSid}/recommendations`,{});
        await readState();
      });
      if (sid() !== requestSid) return;
      welcomeRefs.dirty=false;welcomeRefs.editing=false;fillSetup();
      document.getElementById('welcome-school-slot')?.scrollIntoView({block:'start',behavior:'auto'});
    } catch (error) {
      if (sid()===requestSid) setError(err,(error.message || '保存失败')+' 已保存的内容会保留，请核对后继续。');
    } finally { markBusy(button,false); }
  });
}
function buildTargetSettings() {
  const mount=mountEl('target-settings'); if (!mount) return;
  const form=el('form','portal-form target-settings'); const school=schoolSelect();
  const button=lockable(el('button','btn btn-ghost','更新目标院校')); button.type='submit';
  const err=makeError(); form.append(fieldRow('目标院校',school),button,err);mount.appendChild(form);
  Object.assign(targetRefs,{form,school,button,err,dirty:false});
  school.addEventListener('change',()=>{targetRefs.dirty=true;setError(err,'');});
  form.addEventListener('submit',async event=>{
    event.preventDefault();if(button.disabled||!sid()||!form.reportValidity())return;
    const requestSid=sid();markBusy(button,true);setError(err,'');
    try {
      await runWrite(()=>platformRequest('PUT',`/api/v1/sessions/${requestSid}/target`,{school_id:school.value,user_note:'在档案设置中由用户调整'}));
      if(sid()===requestSid){targetRefs.dirty=false;notify('目标院校已更新。');}
    }catch(error){if(sid()===requestSid)setError(err,error.message||'暂时无法保存，请刷新核对。');}
    finally{markBusy(button,false);}
  });
}
function fillSetup() {
  const p=sessionOf().profile||{}, f=welcomeRefs;
  if(f.form && !f.dirty){
    f.year.value=p.exam_year||new Date().getFullYear()+1;
    f.major.value=p.major||p.target_major||'图书情报';f.score.value=p.target_score??'';f.minutes.value=p.daily_minutes||60;
    f.regions.value=Array.isArray(p.preferred_regions)?p.preferred_regions.join('、'):'';
  }
  fillSchools(targetRefs.school,targetRefs.dirty?undefined:sessionOf().selected_school_id);
  const firstVisit=!readyForStudy();
  if (recRefs.btn) recRefs.btn.hidden=firstVisit;
  if (recRefs.coverage) recRefs.coverage.hidden=firstVisit;
  const hasRecommendations=!!sessionOf().recommendations;
  const showRecommendations=firstVisit&&hasRecommendations&&!f.editing;
  const slot=document.getElementById('welcome-school-slot');
  const referenceSlot=document.getElementById('reference-school-slot');
  const recSection=document.getElementById('school-recommendations');
  if(slot)slot.hidden=!showRecommendations;
  if(f.form)f.form.closest('.welcome-card').hidden=showRecommendations;
  const recParent=firstVisit?slot:referenceSlot;
  if(recParent&&recSection&&recSection.parentNode!==recParent)recParent.appendChild(recSection);
  const target=document.getElementById('workspace-target');
  if(target) target.textContent=state?(sessionOf().selected_school_id?`${nameOf(sessionOf().selected_school_id)}${p.target_score?' · '+p.target_score+' 分目标':''}`:'填写情况，找到合适方向'):'正在读取备考档案…';
  if(f.button)f.button.disabled=!!(writing||activity.busy||activity.pending||!state||!sources);
  showPage(hashPage(),false);
}

/* ================= 来源覆盖 ================= */
async function loadSources() {
  sourcesError = '';
  renderCoverage();
  try { sources = await platformRequest('GET', '/api/v1/meta/sources'); }
  catch (e) { sources = null; sourcesError = e.message || '读取失败'; }
  renderCoverage();
  fillSetup();
}
const recRefs = {};
function renderCoverage() {
  const c = recRefs.coverage;
  if (!c) return;
  clear(c);
  if (sourcesError) {
    c.appendChild(el('span', null, '来源读取失败：' + sourcesError + '。'));
    const retry = el('button', 'btn btn-ghost', '重新读取来源');
    retry.type = 'button';
    retry.addEventListener('click', loadSources);
    c.appendChild(retry);
    return;
  }
  if (!sources) { c.appendChild(el('span', null, '正在读取来源覆盖…')); return; }
  const parts = (sources.coverage || []).map((cv) => {
    const name = cv.school_name || nameOf(cv.school_id);
    return `${name}（${cv.latest_year || '年份未知'}，${cv.record_count != null ? cv.record_count : 0} 条）`;
  });
  c.appendChild(el('span', null, parts.length ? '来源覆盖：' + parts.join('；') : '来源覆盖：暂无记录。'));
}

/* ================= 档案 ================= */
const profileRefs = {};
function buildProfile() {
  const m = mountEl('profile');
  if (!m) return;
  const form = el('form', 'portal-form pf-profile');
  form.noValidate = true;
  const year = el('input', 'input'); year.type = 'number'; year.min = '2000'; year.max = '2100'; year.step = '1'; year.required = true;
  const major = el('input', 'input'); major.type = 'text'; major.required = true;
  const regions = el('input', 'input'); regions.type = 'text'; regions.placeholder = '如：吉林 黑龙江、北京';
  const score = el('input', 'input'); score.type = 'number'; score.min = '100'; score.max = '500'; score.step = '1';
  const minutes = el('input', 'input'); minutes.type = 'number'; minutes.min = '1'; minutes.max = '1440'; minutes.step = '1'; minutes.required = true;
  form.append(fieldRow('考研年份', year), fieldRow('报考方向', major), fieldRow('期望地区', regions),
    fieldRow('目标总分', score), fieldRow('每日学习分钟', minutes));
  const err = makeError();
  const btn = lockable(el('button', 'btn btn-primary', '保存档案'));
  btn.type = 'submit';
  form.append(err, btn);
  m.appendChild(form);
  Object.assign(profileRefs, { form, year, major, regions, score, minutes, err, btn });
  form.addEventListener('input', () => markDraft('profile', form));
  form.addEventListener('submit', onProfileSubmit);
}
function fillProfile() {
  const f = profileRefs;
  if (!f.form || dirtyForms.has(f.form)) return; // 不覆盖未保存草稿
  const p = sessionOf().profile || {};
  f.year.value = p.exam_year != null ? p.exam_year : '';
  f.major.value = p.major || '';
  f.regions.value = Array.isArray(p.preferred_regions) ? p.preferred_regions.join(' ') : (p.preferred_regions || '');
  f.score.value = p.target_score != null ? p.target_score : '';
  f.minutes.value = p.daily_minutes != null ? p.daily_minutes : '';
}
async function onProfileSubmit(ev) {
  ev.preventDefault();
  const f = profileRefs;
  const requestSid = sid(); // 捕获发起会话：await 后仅当仍是该会话时回填 UI
  if (!requestSid || f.btn.disabled) return;
  setError(f.err, '');
  if (f.year.value.trim() === '') return setError(f.err, '请填写考研年份。');
  const year = Number(f.year.value);
  if (!Number.isInteger(year) || year < 2000 || year > 2100) return setError(f.err, '考研年份需为 2000–2100 的整数。');
  const major = f.major.value.trim();
  if (!major) return setError(f.err, '请填写报考方向。');
  if (f.minutes.value.trim() === '') return setError(f.err, '请填写每日学习分钟。');
  const minutes = Number(f.minutes.value);
  if (!Number.isInteger(minutes) || minutes < 1 || minutes > 1440) return setError(f.err, '每日学习分钟需为 1–1440 的整数。');
  const body = {
    exam_year: year, major, target_major: major, daily_minutes: minutes,
    preferred_regions: f.regions.value.split(/[,，、\s]+/).map((s) => s.trim()).filter(Boolean),
  };
  if (f.score.value.trim() !== '') {
    const sc = Number(f.score.value);
    if (!Number.isInteger(sc) || sc < 100 || sc > 500) return setError(f.err, '目标总分需为 100–500 的整数，可留空。');
    body.target_score = sc;
  }
  markBusy(f.btn, true);
  try {
    await runWrite(() => platformRequest('PATCH', `/api/v1/sessions/${requestSid}/profile`, body));
    clearDraft('profile', requestSid);
    if (sid() !== requestSid) return; // 会话已切换：只清原会话草稿，不动当前表单与提示
    dirtyForms.delete(f.form);
    notify('档案已保存。');
  } catch (e) {
    if (sid() !== requestSid) return; // 错误只回填发起会话
    setError(f.err, (e.message || '保存失败') + '，草稿已保留，请刷新核对后再试。');
  } finally { markBusy(f.btn, false); }
}

/* ================= 院校比较 ================= */
function buildRecommendations() {
  const m = mountEl('recommendations');
  if (!m) return;
  const coverage = el('div', 'portal-help');
  const targetLine = el('p', 'hint');
  const btn = lockable(el('button', 'btn btn-primary', '比较院校'));
  btn.type = 'button';
  const err = makeError();
  const result = el('div', 'portal-result');
  m.append(coverage, targetLine, btn, err, result);
  Object.assign(recRefs, { coverage, targetLine, btn, err, result });
  btn.addEventListener('click', onCompare);
  result.addEventListener('click', onRecAction); // 事件委托，重复渲染不重复绑定
}
function foldList(label, items) {
  if (!Array.isArray(items) || items.length === 0) return document.createTextNode('');
  const d = el('details');
  d.appendChild(el('summary', null, `${label}（${items.length}）`));
  const ul = el('ul', 'list');
  items.forEach((it) => ul.appendChild(el('li', null, textOf(it))));
  d.appendChild(ul);
  return d;
}
function sourceFold(items) {
  if (!Array.isArray(items) || items.length === 0) return document.createTextNode('');
  const d = el('details');
  d.appendChild(el('summary', null, '来源'));
  const ul = el('ul', 'list');
  items.forEach((src) => {
    const li = el('li');
    const url = typeof src.url === 'string' && /^https?:\/\//i.test(src.url) ? src.url : '';
    const meta = [src.applicable_year, src.level].filter(Boolean).join(' · ');
    if (url) {
      const a = el('a', null, src.title || url);
      a.href = url;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      li.appendChild(a);
      if (meta) li.appendChild(el('span', 'hint', `（${meta}）`));
    } else {
      li.textContent = (src.title || '未命名来源') + (meta ? `（${meta}）` : '');
    }
    ul.appendChild(li);
  });
  d.appendChild(ul);
  return d;
}
function recCard(rec, selectedId) {
  const card = el('section', 'card');
  card.appendChild(el('h3', 'card-title', rec.school_name || nameOf(rec.school_id)));
  const tierRaw = rec.metrics ? metricVal(rec.metrics.tier) : null;
  card.appendChild(el('p', 'hint',
    (tierRaw !== null && tierRaw !== undefined && tierRaw !== '') ? `分档：${textOf(tierRaw)}` : (rec.tier_note || '资料不足，未分档')));
  const reasons = Array.isArray(rec.fit_reasons) ? rec.fit_reasons : [];
  if (reasons.length) {
    const ul = el('ul', 'list');
    reasons.forEach((fr) => {
      const kind = fr && fr.kind === 'fact' ? '事实' : '判断';
      ul.appendChild(el('li', null, `【${kind}】${textOf(fr && fr.text !== undefined ? fr.text : fr)}`));
    });
    card.appendChild(ul);
  }
  card.append(foldList('风险', rec.risks), foldList('未知', rec.unknowns),
    foldList('资料冲突', rec.data_conflicts), sourceFold(rec.sources));
  const isCurrent = rec.school_id === selectedId;
  const btn = el('button', isCurrent ? 'btn' : 'btn btn-ghost', isCurrent ? '当前目标' : '确认为目标');
  btn.type = 'button';
  btn.dataset.schoolId = rec.school_id;
  if (isCurrent) btn.disabled = true; else lockable(btn);
  card.appendChild(btn);
  return card;
}
function renderRecommendations(dataOpt) {
  const r = recRefs;
  if (!r.result) return;
  const s = sessionOf();
  r.targetLine.textContent = s.selected_school_id ? `当前目标：${nameOf(s.selected_school_id)}` : '当前目标：未确认';
  clear(r.result);
  const data = dataOpt || s.recommendations;
  const list = data && Array.isArray(data.recommendations) ? data.recommendations : [];
  if (!list.length) {
    r.result.appendChild(el('p', 'portal-empty', data ? '当前条件下没有可推荐的院校。可查看未入选原因，再调整备考信息。' : '填写备考信息后，系统会先给出院校比较，再由你确认目标。'));
  }
  list.forEach((rec) => r.result.appendChild(recCard(rec, s.selected_school_id)));
  if (Array.isArray(data?.excluded) && data.excluded.length) {
    const ul = el('ul', 'list');
    data.excluded.forEach((x) => ul.appendChild(el('li', null,
      typeof x === 'string' ? x : (x.school_name || nameOf(x.school_id)) + (x.reason ? '：' + textOf(x.reason) : ''))));
    const d = el('details');
    d.appendChild(el('summary', null, `未入选（${data.excluded.length}）`));
    d.appendChild(ul);
    r.result.appendChild(d);
  }
  applyLock();
}
async function onCompare() {
  const r = recRefs;
  const requestSid = sid();
  if (!requestSid || r.btn.disabled || activity.busy || activity.pending) return;
  setError(r.err, '');
  markBusy(r.btn, true);
  try {
    await runWrite(() => platformRequest('POST', `/api/v1/sessions/${requestSid}/recommendations`, {}));
    if (sid() !== requestSid) return; // 旧会话结果不渲染到新会话
    renderRecommendations();
    notify('比较完成，请核对后确认目标。');
  } catch (e) {
    if (sid() !== requestSid) return;
    setError(r.err, (e.message || '比较失败') + '。已有内容未改动，请刷新核对后再试。');
  } finally {
    markBusy(r.btn, false);
  }
}
async function onRecAction(ev) {
  const btn = ev.target.closest('button[data-school-id]');
  const requestSid = sid();
  if (!btn || btn.disabled || !requestSid) return;
  markBusy(btn, true);
  try {
    await runWrite(async() => {
      await platformRequest('PUT', `/api/v1/sessions/${requestSid}/target`,
        { school_id: btn.dataset.schoolId, user_note: CONFIRM_NOTE });
      await readState();
    });
    if (sid() !== requestSid) return;
    notify(`已确认目标：${nameOf(btn.dataset.schoolId)}。`);
  } catch (e) {
    if (sid() !== requestSid) return;
    notify((e.message || '确认失败') + '，请刷新核对。', true);
  } finally { markBusy(btn, false); }
}

/* ================= 备考资料 ================= */
const matRefs = {};
function buildMaterials() {
  const m = mountEl('materials');
  if (!m) return;
  const form = el('form', 'portal-form');
  form.noValidate = true;
  const title = el('input', 'input'); title.type = 'text'; title.required = true; title.maxLength = 120;
  const text = el('textarea', 'input'); text.rows = 6; text.required = true;
  form.append(fieldRow('资料标题', title), fieldRow('资料正文', text));
  const err = makeError();
  const btn = lockable(el('button', 'btn btn-primary', '保存资料'));
  btn.type = 'submit';
  form.append(err, btn);
  const draftNote = el('p', 'hint', '未保存文字仅暂存在当前浏览器标签页');
  m.append(form, draftNote);
  const list = el('div', 'portal-result');
  m.appendChild(list);
  Object.assign(matRefs, { form, title, text, err, btn, list });
  form.addEventListener('input', () => markDraft('material', form));
  form.addEventListener('submit', onMaterialSave);
  list.addEventListener('click', onMaterialDelete);
}
async function onMaterialSave(ev) {
  ev.preventDefault();
  const r = matRefs;
  const requestSid = sid(); // 捕获发起会话
  if (!requestSid || r.btn.disabled) return;
  setError(r.err, '');
  const title = r.title.value.trim();
  const text = r.text.value.trim();
  if (!title || !text) return setError(r.err, '请填写资料标题与正文。');
  const body = { title, text, is_demo: false };
  const sel = sessionOf().selected_school_id;
  if (sel) body.school_id = sel;
  markBusy(r.btn, true);
  try {
    await runWrite(() => platformRequest('POST', `/api/v1/sessions/${requestSid}/materials`, body));
    clearDraft('material', requestSid);
    if (sid() !== requestSid) return; // 会话已切换：不清当前输入与提示
    dirtyForms.delete(r.form);
    r.title.value = '';
    r.text.value = '';
    notify('资料已保存。');
  } catch (e) {
    if (sid() !== requestSid) return;
    setError(r.err, (e.message || '保存失败') + '，草稿已保留，请刷新核对后再试。');
  } finally { markBusy(r.btn, false); }
}
function renderMaterials() {
  const r = matRefs;
  if (!r.list) return;
  clear(r.list);
  const items = sessionOf().materials || [];
  if (!items.length) { r.list.appendChild(el('p', 'portal-empty', '尚未保存资料。')); return; }
  const ul = el('ul', 'list');
  items.forEach((it) => {
    const li = el('li');
    li.appendChild(el('strong', null, it.title || '未命名'));
    li.appendChild(el('span', 'hint',
      `  ${it.character_count != null ? it.character_count : 0} 字 · ${it.paragraph_count != null ? it.paragraph_count : 0} 段${it.is_demo ? ' · 示例' : ''}  `));
    const del = lockable(el('button', 'btn btn-ghost', '删除'));
    del.type = 'button';
    del.dataset.materialId = it.material_id;
    del.dataset.title = it.title || '';
    li.appendChild(del);
    ul.appendChild(li);
  });
  r.list.appendChild(ul);
  applyLock();
}
async function onMaterialDelete(ev) {
  const btn = ev.target.closest('button[data-material-id]');
  const requestSid = sid();
  if (!btn || btn.disabled || !requestSid) return;
  if (!window.confirm(`确认删除资料「${btn.dataset.title}」？此操作仅删除该条记录。`)) return;
  markBusy(btn, true);
  try {
    await runWrite(() => platformRequest('DELETE',
      `/api/v1/sessions/${requestSid}/materials/${encodeURIComponent(btn.dataset.materialId)}`));
    if (sid() !== requestSid) return;
    notify('资料已删除。');
  } catch (e) {
    if (sid() !== requestSid) return;
    notify((e.message || '删除失败') + '，请刷新核对。', true);
  } finally { markBusy(btn, false); }
}

/* ================= 整体学习安排 ================= */
const planRefs = {};
function buildPlan() {
  const m = mountEl('global-plan');
  if (!m) return;
  const form = el('form', 'portal-form');
  form.noValidate = true;
  const minutes = el('input', 'input');
  minutes.type = 'number'; minutes.min = '1'; minutes.max = '1440'; minutes.step = '1'; minutes.required = true;
  form.appendChild(fieldRow('计划每日分钟', minutes));
  const err = makeError();
  const btn = lockable(el('button', 'btn btn-primary', '调整学习安排'));
  btn.type = 'submit';
  form.append(err, btn);
  const result = el('div', 'portal-result');
  m.append(form, result);
  Object.assign(planRefs, { form, minutes, err, btn, result });
  form.addEventListener('input', () => markDraft('plan', form));
  form.addEventListener('submit', onPlanSubmit);
}
async function onPlanSubmit(ev) {
  ev.preventDefault();
  const r = planRefs;
  const requestSid = sid(); // 捕获发起会话
  if (!requestSid || r.btn.disabled) return;
  setError(r.err, '');
  if (!sessionOf().selected_school_id) return setError(r.err, '请先在「准备」环节确认目标院校。');
  if (r.minutes.value.trim() === '') return setError(r.err, '请填写计划每日分钟。');
  const minutes = Number(r.minutes.value);
  if (!Number.isInteger(minutes) || minutes < 1 || minutes > 1440) return setError(r.err, '计划每日分钟需为 1–1440 的整数。');
  markBusy(r.btn, true);
  try {
    await runWrite(() => platformRequest('POST', `/api/v1/sessions/${requestSid}/plan/revise`, { daily_minutes: minutes }));
    clearDraft('plan', requestSid);
    if (sid() !== requestSid) return; // 会话已切换：不动当前表单与提示
    dirtyForms.delete(r.form);
    notify('学习安排已更新。');
  } catch (e) {
    if (sid() !== requestSid) return;
    setError(r.err, (e.message || '调整失败') + '，草稿已保留，请刷新核对后再试。');
  } finally { markBusy(r.btn, false); }
}
function renderPlan() {
  const r = planRefs;
  if (!r.result) return;
  clear(r.result);
  const s = sessionOf();
  if (!s.selected_school_id) {
    r.result.appendChild(el('p', 'portal-empty', '尚未确认目标院校，确认后才能生成整体安排。'));
    const go = el('button', 'btn btn-ghost', '前往准备环节确认目标');
    go.type = 'button';
    go.addEventListener('click', () => showPage('prepare', true));
    r.result.appendChild(go);
    return;
  }
  if (!dirtyForms.has(r.form)) {
    r.minutes.value = (s.profile && s.profile.daily_minutes) || (s.plan && s.plan.daily_minutes) || '';
  }
  const plan = s.plan;
  if (!plan || !Array.isArray(plan.items)) {
    r.result.appendChild(el('p', 'portal-empty', '尚未生成安排，填写每日分钟后点击「调整学习安排」。'));
  } else if (!plan.items.length) {
    r.result.appendChild(el('p', 'portal-empty', '当天可用时间不足最小任务时长，所有任务已暂缓。可调整每日分钟后重新点击「调整学习安排」。'));
    r.result.append(foldList('本次调整', plan.changes), foldList('已移除', plan.dropped));
  } else {
    const ul = el('ul', 'list');
    plan.items.forEach((it) => ul.appendChild(el('li', null,
      `${textOf(it.task)} · ${it.minutes != null ? it.minutes : '?'} 分钟${it.reason ? '（' + textOf(it.reason) + '）' : ''}`)));
    r.result.appendChild(ul);
    r.result.append(foldList('本次调整', plan.changes), foldList('已移除', plan.dropped));
  }
  if (state && state.plan) {
    const row = el('div', 'portal-row');
    row.appendChild(el('span', 'hint', '实验室有进行中的诊断安排：'));
    const go = el('button', 'btn btn-ghost', '查看实验室安排');
    go.type = 'button';
    go.addEventListener('click', () => showPage('lab', true));
    row.appendChild(go);
    r.result.appendChild(row);
  }
}

/* ================= 决定记录 ================= */
function renderTimeline() {
  const m = mountEl('timeline');
  if (!m) return;
  clear(m);
  const decisions = sessionOf().decisions || [];
  if (!decisions.length) { m.appendChild(el('p', 'portal-empty', '尚未记录任何决定。')); return; }
  const ul = el('ul', 'list');
  decisions.forEach((d) => {
    const t = (d && d.time) || d.timestamp || d.created_at;
    let timeText = '';
    if (typeof t === 'number' && Number.isFinite(t) && t > 0) {
      const dt = new Date(t * 1000); // 数字按秒级时间戳处理
      if (!Number.isNaN(dt.getTime())) timeText = dt.toLocaleString('zh-CN') + ' · ';
    } else if (typeof t === 'string' && t.trim()) {
      const dt = new Date(t); // ISO 字符串
      if (!Number.isNaN(dt.getTime())) timeText = dt.toLocaleString('zh-CN') + ' · ';
    } // 没有时间则不造日期
    ul.appendChild(el('li', null, `${timeText}${textOf(d.choice)}${d.reason ? '：' + textOf(d.reason) : ''}`));
  });
  m.appendChild(ul);
}

/* ================= 助手（Agent） ================= */
const agentRefs = { history: [] };
function clearAgentState() { // 切换会话时清空助手历史/聊天DOM/输入/错误，仅保留本会话
  agentRefs.history = [];
  if (agentRefs.log) clear(agentRefs.log);
  if (agentRefs.input) agentRefs.input.value = '';
  if (agentRefs.err) setError(agentRefs.err, '');
}
function buildAgent() {
  const m = mountEl('agent');
  if (!m) return;
  const log = el('div', 'portal-result');
  log.setAttribute('aria-live', 'polite');
  const form = el('form', 'portal-form');
  form.noValidate = true;
  const input = el('textarea', 'input');
  input.rows = 3;
  input.placeholder = '例如：我想考图书情报，每天能学两小时，先从哪里开始？';
  input.required = true;
  form.appendChild(fieldRow('说说你的打算', input));
  const err = makeError();
  const btn = lockable(el('button', 'btn btn-primary', '交给助手'));
  btn.type = 'submit';
  form.append(err, btn);
  m.append(log, form);
  Object.assign(agentRefs, { log, form, input, err, btn });
  form.addEventListener('submit', onAgentSubmit);
}
function agentExtras(data){
  // 只把实际执行结果交给公开流程展示，不包含 thought / 参数 / 内部ID。
  window.dispatchEvent(new CustomEvent('yt:agent-result',{detail:{session_id:sid(),trace:Array.isArray(data.trace)?data.trace:[]}}));
  return null;
}
function appendChat(role, content, extras) {
  const r = agentRefs;
  const row = el('div', 'portal-row');
  row.appendChild(el('strong', null, role === 'user' ? '你：' : '助手：'));
  row.appendChild(el('span', null, content));
  if (extras) row.appendChild(extras);
  r.log.appendChild(row);
  while (r.log.children.length > 24) r.log.removeChild(r.log.firstChild);
  row.scrollIntoView({ block: 'nearest' });
}
async function onAgentSubmit(ev) {
  ev.preventDefault();
  const r = agentRefs;
  const requestSid = sid(); // 捕获发起会话：await 后所有回填都只写给该 sid
  if (!requestSid || r.btn.disabled) return;
  setError(r.err, '');
  const msg = r.input.value.trim();
  if (!msg) return setError(r.err, '请先填写内容。');
  const hist = r.history.slice(-6); // 仅自己收发的最近 6 条，不含 state 快照，属于 requestSid
  r.history.push({ role: 'user', content: msg });
  appendChat('user', msg);
  r.input.value = '';
  markBusy(r.btn, true);
  try {
    const data = await runWrite(() => platformRequest('POST', '/api/v1/agent',
      { message: msg, session_id: requestSid, history: hist }));
    if (sid() !== requestSid) return; // 会话已切换：旧响应不得回填新会话的 history/DOM
    if (data.session_id && data.session_id !== requestSid) {
      throw new Error('助手返回了不同的会话标识');
    }
    r.history.push({ role: 'assistant', content: data.reply || '' });
    if (r.history.length > 12) r.history = r.history.slice(-12);
    appendChat('assistant', data.reply || '（无回复内容）', agentExtras(data));
  } catch (e) {
    if (sid() !== requestSid) return; // 错误提示与 history 回滚只作用于发起会话
    r.history.pop(); // 失败不造回复，也不残留待重发上下文
    setError(r.err, (e.message || '助手暂时不可用') + '，输入内容请自行核对后再试。');
  } finally {
    markBusy(r.btn, false);
  }
}

/* ================= 顶部摘要 ================= */
function renderSummary() {
  const m = mountEl('summary');
  if (!m) return;
  clear(m);
  const s = sessionOf();
  const p = s.profile || {};
  const target = s.selected_school_id ? `目标已确认：${nameOf(s.selected_school_id)}` : '目标未确认';
  const matCount = (s.materials || []).length;
  let stage = '填写考研档案';
  if (p.exam_year && p.major) {
    stage = !s.selected_school_id ? '比较院校并确认目标'
      : (!s.plan ? '设置每日分钟，生成学习安排' : '按安排学习，可进入实验室练习');
  }
  m.appendChild(el('p', 'summary-line', `${target} · 资料 ${matCount} 条 · 下一步：${stage}`));
}

/* ================= 汇总渲染与初始化 ================= */
function renderAll() {
  if (!state) return;
  fillProfile();            // 仅在无未保存草稿时回填
  renderCoverage();
  renderRecommendations();  // 从 session.recommendations 恢复
  renderMaterials();
  renderPlan();
  renderTimeline();
  renderSummary();
  applyLock();
  fillSetup();
}
let inited = false;
function init() {
  if (inited) return;
  inited = true;
  if (globalThis.ytWorkbenchState) state = globalThis.ytWorkbenchState; // 不自行发起首个 GET
  setupNav();
  buildWelcome();
  buildTargetSettings();
  buildProfile();
  buildRecommendations();
  buildMaterials();
  buildPlan();
  buildAgent();
  if (sid()) restoreDraftsFor(sid(), true); // yt:state 可能早于 platform 初始化，先吃 globalThis 快照
  renderAll();
  loadSources();
  window.addEventListener('yt:state', (e) => adoptState(e.detail));
  window.addEventListener('yt:activity', (e) => {
    activity = { busy: !!(e.detail && e.detail.busy), pending: !!(e.detail && e.detail.pending) };
    applyLock();
    if (activity.busy || activity.pending) {
      notify('正在保存你的学习进度…', false, true);
    } else if (noticeNode && noticeNode.dataset.busyNote === '1') {
      noticeNode.hidden = true;   // 临时忙碌提示完成后即隐藏，真实写入结果通知不受影响
      noticeNode.textContent = '';
      delete noticeNode.dataset.busyNote;
    }
  });
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
