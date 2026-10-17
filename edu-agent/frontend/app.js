import { render } from './components.js';
import { readState, sendAction, makeRequest } from './api.js';

const PENDING_KEY = 'yt.pending.v1';
const SESSION_KEY = 'yt.session.v1';
const GOAL_KEY = 'yt.localGoal.v1';
const FEEDBACK_KINDS = ['unclear', 'too_easy', 'guessed', 'new_example'];
const TABS = ['lab', 'evidence', 'plan'];

let state = null;
let pending = null;
let pendingSid = null;
let busy = false;

const ui = {
  busy: false, error: null, pending: false, localGoal: '', tab: 'lab',
  contextOpen: false, timeDraft: '', notice: '', selectedOption: ''
};

const sGet = (k) => { try { return sessionStorage.getItem(k); } catch (e) { return null; } };
const sSet = (k, v) => { try { sessionStorage.setItem(k, v); return true; } catch (e) { return false; } };
const sDel = (k) => { try { sessionStorage.removeItem(k); } catch (e) {} };
const lGet = (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } };
const lSet = (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} };

function validBody(b) {
  return !!b && typeof b === 'object' && !Array.isArray(b)
    && typeof b.action === 'string' && b.action.length > 0
    && typeof b.request_id === 'string' && b.request_id.length > 0
    && Number.isInteger(b.expected_revision) && b.expected_revision >= 0
    && typeof b.session_id === 'string' && b.session_id.length > 0;
}

function loadPending() {
  const raw = sGet(PENDING_KEY);
  if (!raw) return null;
  let rec = null;
  try { rec = JSON.parse(raw); } catch (e) { rec = null; }
  const body = rec && rec.body;
  if (!body || typeof body !== 'object' || Array.isArray(body)) { sDel(PENDING_KEY); return null; }
  const sid = rec && typeof rec.session_id === 'string' && rec.session_id ? rec.session_id : null;
  if (!sid) { sDel(PENDING_KEY); return null; }
  if (body.session_id !== undefined && body.session_id !== sid) { sDel(PENDING_KEY); return null; }
  if (body.session_id !== sid) {
    body.session_id = sid;
    try { sSet(PENDING_KEY, JSON.stringify(rec)); } catch (e) {}
  }
  if (!validBody(body)) { sDel(PENDING_KEY); return null; }
  return { body: body, session_id: sid };
}

function setPending(body) {
  pending = body;
  pendingSid = state && typeof state.session_id === 'string' && state.session_id ? state.session_id : null;
  let ok = false;
  try { ok = sSet(PENDING_KEY, JSON.stringify({ body: body, session_id: pendingSid })); } catch (e) { ok = false; }
  if (!ok) ui.notice = '浏览器存储不可用：刷新页面可能丢失本次重试标识，请勿刷新。';
}

function clearPending() { pending = null; pendingSid = null; sDel(PENDING_KEY); }

const labState = () => (state && state.lab_state && typeof state.lab_state === 'object' ? state.lab_state : null);
const cycle = () => { const l = labState(); return l && l.cycle && typeof l.cycle === 'object' ? l.cycle : null; };
const question = () => { const l = labState(); return l && l.question ? l.question : null; };
const canAnswer = () => { const c = cycle(); return !!c && c.can_answer === true; };
const canContinue = () => { const c = cycle(); return !!c && c.can_continue === true; };
const isStopped = () => { const c = cycle(); return !!c && (c.stage === 'stopped' || c.stop_reason === 'user_stop'); };
const questionId = () => { const q = question(); return q && typeof q.question_id === 'string' ? q.question_id : null; };

function renderNow() {
  ui.busy = busy || !!globalThis.ytPlatformBusy;
  ui.pending = !!pending;
  try {
    globalThis.ytLabActivity = { busy: busy, pending: !!pending };
    window.dispatchEvent(new CustomEvent('yt:activity', { detail: globalThis.ytLabActivity }));
  } catch (e) {}
  try { render(state, ui, handlers); } catch (e) {}
}

function applyState(next) {
  if (!next || typeof next !== 'object' || Array.isArray(next)) return;
  const curSid = state && typeof state.session_id === 'string' && state.session_id ? state.session_id : null;
  const inSid = typeof next.session_id === 'string' && next.session_id ? next.session_id : null;
  const curRev = state && Number.isInteger(state.revision) && state.revision >= 0 ? state.revision : null;
  const inRev = Number.isInteger(next.revision) && next.revision >= 0 ? next.revision : null;
  if (curSid && inSid && curSid === inSid && curRev !== null && inRev !== null && inRev < curRev) return; // 同 sid 低 revision 直接忽略
  let s = next;
  if (next.runtime_mode !== 'mock' && next.runtime_mode !== 'live') {
    const prevMode = state && state.runtime_mode;
    if (prevMode === 'mock' || prevMode === 'live') s = Object.assign({}, next, { runtime_mode: prevMode });
  }
  const prevQid = questionId();
  if (curSid && inSid && curSid !== inSid) ui.selectedOption = '';
  if (s.lab_state === null || s.lab_state === undefined) {
    s = Object.assign({}, s, { lab_state: null, plan: null });
    ui.selectedOption = '';
  }
  state = s;
  if (s.lab_state) sSet(SESSION_KEY, '1');
  else sDel(SESSION_KEY);
  if (questionId() !== prevQid) ui.selectedOption = '';
  const c = s.lab_state && s.lab_state.cycle;
  if (c && Number.isInteger(c.budget_minutes) && c.budget_minutes >= 1) ui.timeDraft = String(c.budget_minutes);
}

function clearRound() {
  if (state) state = Object.assign({}, state, { plan: null, lab_state: null });
}

function setBusy(v) { busy = v; ui.busy = v; }

async function runRefresh(preserveError = false) {
  if (busy) return;
  const prevError = preserveError ? ui.error : null;
  setBusy(true); renderNow();
  try {
    const s = await readState();
    const wasActive = !!(state && state.lab_state) || sGet(SESSION_KEY) === '1';
    if (!s || typeof s !== 'object' || Array.isArray(s)) {
      ui.error = prevError;
      ui.notice = '读取状态失败：返回数据无效，请稍后重试。';
    } else {
      applyState(s);
      const realSid = typeof s.session_id === 'string' && s.session_id ? s.session_id : null;
      const sidChanged = !!(pending && realSid && pendingSid !== realSid);
      if (sidChanged) {
        clearPending();
        ui.notice = '会话已变化，请先核对当前记录后重新操作';
      } else if (pending) {
        ui.notice = '上次请求仍未确认，请同请求重试';
      } else {
        ui.notice = null;
      }
      const active = !!(s.lab_state && typeof s.lab_state === 'object');
      if (active) {
        sSet(SESSION_KEY, '1');
      } else {
        sDel(SESSION_KEY);
        if (wasActive && !sidChanged) ui.notice = '本轮未找到：可能服务已重启或会话已失效。';
      }
      ui.error = prevError;
    }
  } catch (e) {
    ui.error = prevError;
    ui.notice = '刷新读取失败（' + (e && e.message ? String(e.message) : '读取状态失败') + '），请稍后重试。';
  } finally {
    setBusy(false); renderNow();
  }
}

function onActionError(e) {
  const msg = e && e.message ? String(e.message) : '请求失败';
  const code = e && e.code != null ? e.code : null;
  if (e && e.definitive === true) {
    clearPending();
    ui.error = { message: msg, code: code };
    return true;
  }
  ui.error = null;
  ui.notice = '请求结果未确认（' + msg + '），请点击「重试」核对本次操作。';
  return false;
}

async function dispatch(body) {
  const requestSid = state && typeof state.session_id === 'string' && state.session_id ? state.session_id : null; // 捕获发起会话
  const sidSwitched = () => { // 除本地state外也核对共享状态：platform已切B而lab仍A时，旧A响应/错误不得回填
    const cur = state && typeof state.session_id === 'string' && state.session_id ? state.session_id : null;
    const shared = globalThis.ytWorkbenchState && typeof globalThis.ytWorkbenchState.session_id === 'string' && globalThis.ytWorkbenchState.session_id ? globalThis.ytWorkbenchState.session_id : null;
    return !!(requestSid && ((cur && cur !== requestSid) || (shared && shared !== requestSid)));
  };
  setBusy(true); renderNow();
  let needRefresh = false;
  try {
    const s = await sendAction(body);
    if (sidSwitched()) { // 已切到新会话：旧A响应不得覆盖B，原pending可清理
      clearPending();
      return;
    }
    clearPending();
    const known = state && Number.isInteger(state.revision) && state.revision >= 0 ? state.revision : null;
    const got = s && typeof s === 'object' && !Array.isArray(s) && Number.isInteger(s.revision) && s.revision >= 0 ? s.revision : null;
    const gotActive = !!(s && typeof s === 'object' && !Array.isArray(s) && s.lab_state && typeof s.lab_state === 'object');
    if (known !== null && got !== null && got < known && gotActive) {
      needRefresh = true;
    } else {
      applyState(s);
    }
  } catch (e) {
    if (sidSwitched()) { // 旧A错误不写进新会话
      clearPending();
      return;
    }
    needRefresh = onActionError(e);
  } finally {
    setBusy(false); renderNow();
  }
  if (needRefresh) await runRefresh(true);
}

function currentRevision() {
  return state && Number.isInteger(state.revision) && state.revision >= 0 ? state.revision : 0;
}

function runAction(action, params) {
  if (busy || pending) return;
  let body = null;
  try { body = makeRequest(action, params || {}, currentRevision()); } catch (e) { body = null; }
  if (!validBody(body)) {
    ui.error = { message: '无法构造请求', code: null };
    renderNow();
    return;
  }
  ui.error = null; ui.notice = '';
  setPending(body);
  dispatch(body);
}

function runRetry() {
  if (busy || !pending) return;
  if (!validBody(pending)) {
    clearPending();
    ui.error = { message: '重试信息无效，已清除', code: null };
    renderNow();
    return;
  }
  ui.error = null; ui.notice = '';
  dispatch(pending);
}

const handlers = {
  start() { if (!busy && !pending && state) runAction('start', {}); },
  submit(optionId) {
    if (busy || pending) return;
    const q = question();
    if (!q || !canAnswer() || typeof optionId !== 'string' || !optionId) return;
    const opts = Array.isArray(q.options) ? q.options : [];
    if (!opts.some((o) => o && o.id === optionId)) return;
    ui.selectedOption = optionId;
    runAction('submit', { question_id: q.question_id, answer: optionId });
  },
  feedback(kind) {
    if (busy || pending || FEEDBACK_KINDS.indexOf(kind) === -1) return;
    runAction('feedback', { feedback: kind });
  },
  time(minutes) {
    if (busy || pending || !labState()) return;
    const m = typeof minutes === 'number' ? minutes : Number(minutes);
    if (!Number.isInteger(m) || m < 1 || m > 1440) return;
    runAction('time', { minutes: m });
  },
  next() { if (!busy && !pending && canContinue()) runAction('continue', {}); },
  stop() { if (!busy && !pending && labState() && !isStopped()) runAction('stop', {}); },
  retry() { runRetry(); },
  refresh() { runRefresh(); },
  setLocalGoal(text) { const t = typeof text === 'string' ? text.slice(0, 200) : ''; ui.localGoal = t; lSet(GOAL_KEY, t); },
  setTab(t) { if (TABS.indexOf(t) === -1) return; ui.tab = t; renderNow(); },
  toggleContext() { ui.contextOpen = !ui.contextOpen; renderNow(); },
  openEvidence(id) {
    ui.tab = 'evidence';
    renderNow();
    if (id === undefined || id === null) return;
    const key = String(id);
    document.querySelectorAll('details.drawer').forEach(function(d){if(d.querySelector('[data-mount="evidence"]'))d.open=true;});
    const nodes = document.querySelectorAll('[data-evidence-id]');
    for (let i = 0; i < nodes.length; i++) {
      if (nodes[i].getAttribute('data-evidence-id') !== key) continue;
      const el = nodes[i];
      let ancestor=el.parentElement;while(ancestor){if(ancestor.tagName==='DETAILS')ancestor.open=true;ancestor=ancestor.parentElement;}
      try { el.scrollIntoView({ block: 'center' }); } catch (e) { try { el.scrollIntoView(); } catch (e2) {} }
      try { if (typeof el.focus === 'function') el.focus(); } catch (e) {}
      try { el.classList.add('is-hit'); } catch (e) {}
      break;
    }
  },
  selectOption(optionId) { ui.selectedOption = typeof optionId === 'string' ? optionId : ''; },
  setTimeDraft(text) { ui.timeDraft = typeof text === 'string' ? text : ''; }
};

function boot() {
  window.addEventListener('yt:begin', () => handlers.start());
  window.addEventListener('yt:reset',()=>{clearPending();ui.localGoal='';ui.timeDraft='';ui.notice='';ui.error=null;ui.selectedOption='';ui.tab='lab';ui.contextOpen=false;renderNow();});
  window.addEventListener('yt:refresh', function () { runRefresh(); });
  window.addEventListener('yt:platform-busy', function () { renderNow(); });
  window.addEventListener('yt:state', function (e) { // 共享状态桥：外部connector已拿到有效状态时同步lab，不发请求避免循环
    const d = e && e.detail;
    if (!d || typeof d !== 'object' || Array.isArray(d)) return;
    const prevSid = state && typeof state.session_id === 'string' && state.session_id ? state.session_id : null;
    const inSid = typeof d.session_id === 'string' && d.session_id ? d.session_id : null;
    applyState(d); // 同sid低revision仍由applyState守卫忽略
    if (inSid && inSid !== prevSid) { ui.error = null; ui.notice = null; ui.selectedOption = ''; } // 不动pending，留给runRefresh比较pendingSid
    renderNow();
  });
  const g = lGet(GOAL_KEY);
  if (typeof g === 'string') ui.localGoal = g.slice(0, 200);
  const p = loadPending();
  if (p) { pending = p.body; pendingSid = p.session_id; ui.notice = '检测到未完成的操作，可点击「重试」核对结果。'; }
  renderNow();
  runRefresh();
}

boot();
