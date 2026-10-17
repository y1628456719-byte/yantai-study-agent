// GPT-authored minimal same-origin connector; UI/design/controller are Kimi's.
const uncertain = (message, code = 'RESULT_UNCONFIRMED') => Object.assign(new Error(message), {code, definitive: false});
let sessionGeneration = 0;

export function validateState(value) {
  const object = v => v !== null && typeof v === 'object' && !Array.isArray(v);
  const invalid = () => { throw uncertain('返回状态不完整，请刷新核对。', 'INVALID_RESPONSE'); };
  if (!object(value) || !Number.isInteger(value.revision) || value.revision < 0 ||
      !['mock', 'live'].includes(value.runtime_mode) || !('lab_state' in value) || !('plan' in value)) invalid();
  if (typeof value.session_id !== 'string' || !value.session_id || !object(value.session) ||
      value.session.session_id !== value.session_id) invalid();
  if (value.lab_state === null) {
    if (value.plan !== null) invalid();
    return value;
  }
  const v = value.lab_state, plan = value.plan;
  if (!object(v) || typeof v.stage !== 'string' || !('question' in v) || !Array.isArray(v.evidence) ||
      !Array.isArray(v.hypotheses) || !Array.isArray(v.history) || !object(v.adaptation) || !object(v.cycle) ||
      typeof v.cycle.can_answer !== 'boolean' || typeof v.cycle.can_continue !== 'boolean' ||
      !object(plan) || !Array.isArray(plan.tasks) || !Array.isArray(plan.deferred)) invalid();
  if (v.question !== null && (!object(v.question) || typeof v.question.question_id !== 'string' ||
      typeof v.question.stem !== 'string' || !Array.isArray(v.question.options) ||
      !v.question.options.length || v.question.options.some(o => !object(o) || typeof o.id !== 'string' || typeof o.text !== 'string'))) invalid();
  return value;
}

async function request(path, body, timeout) {
  const startedGeneration = sessionGeneration;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(path, {
      method: body ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
      ...(body ? {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {})
    });
    let value;
    try { value = await response.json(); }
    catch { throw uncertain('无法确认返回结果，请刷新核对或用原请求重试。', 'INVALID_RESPONSE'); }
    if (!response.ok) {
      if (response.status >= 400 && response.status < 500 &&
          typeof value?.error?.code === 'string' && typeof value?.error?.message === 'string') {
        throw Object.assign(new Error(value.error.message), {code: value.error.code, definitive: true});
      }
      throw uncertain('服务未返回可确认的操作结果，请刷新核对。');
    }
    const state = validateState(value);
    const current = globalThis.ytWorkbenchState;
    // Any session adopted since this request began supersedes a different late SID.
    if (startedGeneration !== sessionGeneration && current && state.session_id !== current.session_id)
      return current;
    // A saved request result confirms that action, but is not the newest page state.
    if (state.session_id === current?.session_id && state.revision < current.revision) {
      if (body) return request('/api/state', null, 15000);
      return current;
    }
    if (state.session_id !== current?.session_id) sessionGeneration += 1;
    globalThis.ytWorkbenchState = state;
    window.dispatchEvent(new CustomEvent("yt:state", {detail: state}));
    return state;
  } catch (error) {
    if (typeof error.definitive === 'boolean') throw error;
    throw uncertain(error.name === 'AbortError' ? '等待超时，操作结果尚未确认。请刷新核对或用原请求重试。' : '连接中断，操作结果尚未确认。请刷新核对或用原请求重试。');
  } finally { clearTimeout(timer); }
}

export const readState = () => request('/api/state', null, 15000);
export const sendAction = body => {
  if (globalThis.ytPlatformBusy) return Promise.reject(Object.assign(new Error('正在保存准备信息，请稍候再作答。'), {code:'BUSY',definitive:true}));
  if (typeof body?.session_id !== 'string' || !body.session_id)
    return Promise.reject(Object.assign(new Error('请求缺少发起会话，请刷新核对。'), {code:'INVALID_INPUT',definitive:true}));
  return request('/api/action', body, 190000);
};
export function makeRequest(action, params, revision) {
  const sessionId = globalThis.ytWorkbenchState?.session_id;
  if (typeof sessionId !== 'string' || !sessionId || !Number.isInteger(revision) || revision < 0 || !['start', 'submit', 'feedback', 'time', 'continue', 'stop'].includes(action))
    throw uncertain('请求参数无效，请刷新核对。', 'INVALID_INPUT');
  const id = globalThis.crypto?.randomUUID?.() || ('req_' + Date.now() + '_' + Math.random().toString(36).slice(2));
  return {...params, action, session_id: sessionId, request_id: id, expected_revision: revision};
}

// GPT-authored connector to the shared main backend; no UI or diagnostic logic.
export async function platformRequest(method, path, body) {
  if (!path.startsWith('/api/v1/')) throw new Error('仅允许同源业务接口。');
  const write = method !== 'GET';
  const activity = globalThis.ytLabActivity || {};
  if (write && (activity.busy || activity.pending || globalThis.ytPlatformBusy))
    throw Object.assign(new Error('请先完成或核对正在进行的学习操作，再保存准备信息。'), {definitive:true,code:'BUSY'});
  if (write) { globalThis.ytPlatformBusy = true; window.dispatchEvent(new Event("yt:platform-busy")); }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 240000);
  try {
    const response = await fetch(path, {method, credentials:'same-origin',cache:'no-store',signal:controller.signal,
      ...(body === undefined ? {} : {headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
    let value;
    try { value = await response.json(); } catch { throw uncertain('无法确认操作结果，请刷新核对，勿重复提交。'); }
    if (!response.ok || value.ok !== true) {
      if (response.status >= 400 && response.status < 500 && typeof value?.error?.message === 'string')
        throw Object.assign(new Error(value.error.message), {code:value.error.code,definitive:true});
      throw uncertain(value?.error?.message || '服务未确认本次结果，请刷新核对。');
    }
    return value.data;
  } catch (error) {
    if (typeof error.definitive === 'boolean') throw error;
    throw uncertain(error.name === 'AbortError' ? '等待超时，请刷新核对保存结果，勿连续提交。' : '连接中断，请刷新核对保存结果，勿连续提交。');
  } finally { clearTimeout(timer); if (write) { globalThis.ytPlatformBusy = false; window.dispatchEvent(new Event("yt:platform-busy")); } }
}

// 新会话恢复默认；沿用状态校验、会话代次和版本守卫。旧档案不删除。
export async function prepareJourney(goal, useDemo = false) {
  if (globalThis.ytPlatformBusy || globalThis.ytLabActivity?.busy || globalThis.ytLabActivity?.pending) {
    throw new Error('当前操作正在进行中，请稍候再试');
  }
  const current = globalThis.ytWorkbenchState || {};
  if (!current.session_id || !Number.isInteger(current.revision)) throw uncertain('页面还在读取，请稍后再试。');
  const sessionId = current.session_id ?? current.session?.id ?? null;
  const expectedRevision = typeof current.revision === 'number' ? current.revision : 0;
  const requestId = globalThis.crypto?.randomUUID ? globalThis.crypto.randomUUID() : `prep-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const body = {
    session_id: sessionId,
    expected_revision: expectedRevision,
    request_id: requestId,
    goal: String(goal || '').trim(),
    use_demo: Boolean(useDemo)
  };
  globalThis.ytPlatformBusy = true;
  window.dispatchEvent(new CustomEvent('yt:platform-busy', { detail: { busy: true } }));
  try {
    const next = await request('/api/prepare', body, 15000);
    if (!next || !next.journey) {
      throw new Error('服务未返回有效的学习准备档案，请重试');
    }
    return next;
  } finally {
    globalThis.ytPlatformBusy = false;
    window.dispatchEvent(new CustomEvent('yt:platform-busy', { detail: { busy: false } }));
  }
}

export async function returnToMyJourney() {
  const activity = globalThis.ytLabActivity || {};
  if (activity.busy || activity.pending || globalThis.ytPlatformBusy) throw uncertain('请先完成当前操作，再返回学习记录。');
  const current = globalThis.ytWorkbenchState;
  if (!current?.journey?.can_return) throw uncertain('当前不在体验档案中，请刷新核对。');
  const requestId = globalThis.crypto?.randomUUID?.() || `return-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  globalThis.ytPlatformBusy = true;
  window.dispatchEvent(new Event('yt:platform-busy'));
  try {
    return await request('/api/journey/return', {
      session_id: current.session_id, expected_revision: current.revision, request_id: requestId
    }, 15000);
  } finally {
    globalThis.ytPlatformBusy = false;
    window.dispatchEvent(new Event('yt:platform-busy'));
  }
}

export async function resetToDefaults(){
  const activity=globalThis.ytLabActivity||{};
  if(activity.busy||activity.pending||globalThis.ytPlatformBusy)throw Object.assign(new Error('请先完成当前操作，再恢复默认。'),{definitive:true,code:'BUSY'});
  const current=globalThis.ytWorkbenchState;
  if(!current)throw uncertain('请先等待页面读取完成。');
  globalThis.ytPlatformBusy=true;window.dispatchEvent(new Event('yt:platform-busy'));
  try{
    const next=await request('/api/reset',{session_id:current.session_id,expected_revision:current.revision},15000);
    if(next.session_id===current.session_id)throw uncertain('未确认恢复默认，请刷新核对。');
    window.dispatchEvent(new CustomEvent('yt:reset',{detail:next}));return next;
  }finally{globalThis.ytPlatformBusy=false;window.dispatchEvent(new Event('yt:platform-busy'));}
}
