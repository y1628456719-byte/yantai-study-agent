import { prepareJourney, returnToMyJourney } from './api.js';

const TRACK_LABELS = {
  direction: '方向判断',
  reading: '必要条件表述',
  transfer: '条件链推导'
};
const STATUS_LABELS = {needs_check:'需要再验证',pending:'待观察',showing_strength:'近期较稳定',needs_practice:'优先练习',provisional:'继续观察'};

function formatTrack(track) {
  if (!track) return '逻辑判断';
  return TRACK_LABELS[track] || String(track);
}
function displayReason(text) {
  return String(text || '').replace(/\s*本计划按每日 \d+ 分钟预算安排。/g, '')
    .replace(/；反馈不会改判分。/g, '。')
    .replace(/；这是自报意图，不是掌握证据。/g, '，先练一轮再看表现。');
}
function nextSuggestion(journey) {
  if (['我是猜的', '我还没理解'].includes(journey?.feedback_text)) {
    return '先对照讲解想清原因，再换一道题确认。选对一次，还要看看能不能讲明白。';
  }
  return journey?.next_step || '';
}

function detectMinutesFromText(text) {
  const raw = String(text || '');
  const chars = '零〇一二两三四五六七八九十百千万';
  const number = String.raw`(?:\d+(?:\.\d+)?|[${chars}]+|半)`;
  const whole = String.raw`(?:\d+|[${chars}]+)`;
  const tokenChars = String.raw`0-9A-Za-z_+.\-−－负負${chars}半个点~～`;
  const boundary = tokenChars;
  const suffix = `0-9A-Za-z_${chars}半几多左右上下以上以下以内不到余来到至后`;
  const unit = '(?:个小时|小时|分钟|分)';
  const modifier = '(?:半|几|多|左右|上下|以上|以下|以内|不到|余|来|到|至|后)';
  const expression = new RegExp(String.raw`(?<![${boundary}])(?:(?<mixed>${whole}(?:个)?半小时)|(?<hourAndHalf>${whole}(?:个)?小时半)|(?<number>${number})\s*(?<unit>${unit}))(?![${suffix}])`, 'g');
  const unsupported = new RegExp(String.raw`(?<![${boundary}])(?:${whole}(?:个)?(?:半小时|小时半)${modifier}|${number}(?:个)?(?:几|多)${unit}|${number}\s*${unit}${modifier}|${number}\s*(?:到|至|~|～|-)\s*${number}\s*${unit})`, 'g');
  const matches = [...raw.matchAll(expression)];
  const spans = new Set(matches.map(m => `${m.index}:${m.index+m[0].length}`));
  const candidates = new RegExp(String.raw`(?<![${tokenChars}])[${tokenChars}]+(?:\s+[${tokenChars}]+)*\s*${unit}(?:半)?`, 'g');
  if ([...raw.matchAll(candidates)].some(m => !spans.has(`${m.index}:${m.index+m[0].length}`))) return null;
  if (matches.length !== 1 || [...raw.matchAll(unsupported)].some(m => !spans.has(`${m.index}:${m.index+m[0].length}`))) return null;
  const digits = {零:0,〇:0,一:1,二:2,两:2,三:3,四:4,五:5,六:6,七:7,八:8,九:9};
  function chineseInteger(value) {
    if ([...value].every(c => c in digits)) return Number([...value].map(c => digits[c]).join(''));
    let total=0, section=0, current=null, lastUnit=10000, seenWan=false, previousDigit=null;
    for (const char of value) {
      if (char in digits) {
        if (previousDigit!==null && !'零〇'.includes(previousDigit)) throw new Error('invalid numeral');
        current=digits[char];previousDigit=char;
      } else if ('十百千'.includes(char)) {
        const scale={十:10,百:100,千:1000}[char];
        if (scale>=lastUnit || current===0) throw new Error('invalid unit');
        section+=(current??1)*scale;current=null;lastUnit=scale;previousDigit=null;
      } else if (char==='万') {
        if (seenWan) throw new Error('repeated unit');
        total+=(section+(current||0)||1)*10000;section=0;current=null;lastUnit=10000;seenWan=true;previousDigit=null;
      } else throw new Error('invalid numeral');
    }
    return total+section+(current||0);
  }
  function fraction(value) {
    if (value==='半') return [1n,2n];
    if ([...value].some(c => chars.includes(c))) return [BigInt(chineseInteger(value)),1n];
    const [integer,decimal='']=value.split('.');
    return [BigInt(integer+decimal),10n**BigInt(decimal.length)];
  }
  try {
    const groups=matches[0].groups;
    const mixed=groups.mixed||groups.hourAndHalf;
    const [value,scale]=fraction(mixed?mixed.slice(0,-3).replace(/个$/,''):groups.number);
    const numerator=value*(mixed||groups.unit.includes('小时')?60n:1n)+(mixed?30n*scale:0n);
    return numerator%scale===0n && numerator>=scale && numerator<=1440n*scale ? Number(numerator/scale) : null;
  } catch { return null; }
}

function buildContextualSuggestions(draft, journey) {
  const text = String(draft || '').trim();
  const hasTime = Boolean(detectMinutesFromText(text));
  const hasTopic = /必要|充分|只有|才|除非|条件|逆否|推导|箭头|传递/.test(text);

  if (!text) {
    return {
      hint: '不知道怎么开头？可以从这些打算里选一句：',
      items: [
        '遇到“只有…才…”总把箭头画反，今天有 30 分钟先抓薄弱点',
        '单句条件能看懂，但多步条件链推导经常断，今天能学 1 小时',
        '想先用 30 分钟排查充分与必要条件的盲点，看看到底卡在哪',
        '想用 60 分钟练习必要条件的不同说法'
      ]
    };
  }
  if (!hasTopic && !hasTime) {
    return {
      hint: '再补一句最容易犹豫的地方，或今天能学多久：',
      items: [
        '加上卡点：遇到“只有…才…”容易把推导方向搞反',
        '加上卡点：多步条件链串联时经常找错落脚点',
        '加上时间：今天有 30 分钟集中练习',
        '加上时间：今天预留 1 小时完整复盘'
      ]
    };
  }
  if (hasTopic && !hasTime) {
    return {
      hint: '已记下卡点，可点选补充今日可用时长（不补充沿用档案时间）：',
      items: [
        '，今天有 30 分钟针对性突破',
        '，今天安排 1 小时系统训练',
        '，做完先看证据再排下一步计划'
      ]
    };
  }
  if (!hasTopic && hasTime) {
    return {
      hint: '已明确今日时间预算，可补充你想优先排查的薄弱方向：',
      items: [
        '，重点排查“只有…才…”的方向判断',
        '，重点练必要条件的不同中文表述',
        '，重点突破多步条件链的传递推导'
      ]
    };
  }
  const focusLabel = journey?.focus?.label;
  return {
    hint: focusLabel ? `当前记录聚焦「${focusLabel}」，可直接开始或微调描述：` : '目标、卡点与时间已清晰，可直接开始盲点诊断：',
    items: [
      '，先做诊断题验证是不是真懂',
      '，结合错因讲解再练一道变形题'
    ]
  };
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function bootJourney() {
  const prepareRoot = document.getElementById('journey-prepare-root');
  const labSidebar = document.getElementById('journey-lab-sidebar');
  const planRoot = document.getElementById('journey-plan-root');
  const legacyGplan = document.getElementById('legacy-gplan-wrap');
  if (!prepareRoot) return;

  let userDirty = false;
  let lastSessionId = null;
  let errorText = '';
  let statusInfo = '';
  function returnButton() {
    const button = el('button', 'text-link journey-return-btn', '返回我的学习记录');
    button.type = 'button';
    button.disabled = isSystemBusy();
    button.addEventListener('click', async () => {
      if (isSystemBusy()) return;
      try {
        const restored = await returnToMyJourney();
        window.location.hash = restored?.lab_state ? '#lab' : '#prepare';
      } catch (err) {
        errorText = err?.message || '暂时无法返回，请刷新核对。';
        syncBusyControls();
        const notice = document.getElementById('reset-status');
        if (notice) { notice.textContent = errorText; notice.hidden = false; }
      }
    });
    return button;
  }

  const head = el('div', 'journey-desk-head');
  const label = el('label', 'journey-label', '今天想先解决什么？');
  label.setAttribute('for', 'journey-goal-input');
  label.id = 'journey-input-label';
  const budgetPill = el('span', 'journey-budget-pill', '今日预算：默认 60 分钟');
  head.append(label, budgetPill);

  const desc = el('p', 'journey-Hint', '可以说：“总把只有…才…画反，今天有30分钟。”');
  const textarea = el('textarea', 'journey-textarea');
  textarea.id = 'journey-goal-input';
  textarea.name = 'goal';
  textarea.rows = 3;
  textarea.maxLength = 2000;
  textarea.placeholder = '最近哪类题容易犹豫？今天有多少时间？';

  const suggestWrap = el('div', 'journey-suggest-wrap');
  const suggestCaption = el('div', 'journey-suggest-caption');
  suggestCaption.setAttribute('aria-live', 'polite');
  const chipList = el('div', 'journey-chips');
  chipList.setAttribute('role', 'group');
  chipList.setAttribute('aria-label', '上下文填写建议（点击填入输入框，不会自动提交）');
  suggestWrap.append(suggestCaption, chipList);

  const actionsBar = el('div', 'journey-actions-bar');
  const submitBtn = el('button', 'journey-primary-btn', '进入盲点实验室 →');
  submitBtn.type = 'button';

  const demoBox = el('div', 'journey-demo-box');
  const demoNote = el('span', '', '想看看学过一段时间后，系统会怎样安排？');
  const demoBtn = el('button', 'journey-demo-btn', '体验已有练习档案（24条预置）');
  demoBtn.type = 'button';
  demoBox.append(demoNote, demoBtn);
  const prepareReturn = returnButton();
  demoBox.appendChild(prepareReturn);
  actionsBar.append(submitBtn, demoBox);

  const statusLine = el('div', 'journey-status-line');
  statusLine.setAttribute('role', 'status');
  statusLine.setAttribute('aria-live', 'polite');

  prepareRoot.replaceChildren(head, desc, textarea, suggestWrap, actionsBar, statusLine);

  function isSystemBusy() {
    return Boolean(
      globalThis.ytPlatformBusy ||
      globalThis.ytLabActivity?.busy ||
      globalThis.ytLabActivity?.pending
    );
  }

  function updateBudgetPill(journey) {
    const draftMin = detectMinutesFromText(textarea.value);
    const savedMin = journey?.daily_minutes || globalThis.ytWorkbenchState?.session?.profile?.daily_minutes;
    if (draftMin) {
      budgetPill.textContent = `今日预算：已识别 ${draftMin} 分钟`;
    } else if (savedMin) {
      budgetPill.textContent = `今日预算：${savedMin} 分钟`;
    } else {
      budgetPill.textContent = '今日 60 分钟 · 未填写时默认';
    }
  }

  function renderSuggestions(journey) {
    const { hint, items } = buildContextualSuggestions(textarea.value, journey);
    suggestCaption.textContent = hint;
    chipList.replaceChildren();
    const busy = isSystemBusy();
    items.forEach((phrase) => {
      const btn = el('button', 'journey-chip', phrase.replace(/^，/, ''));
      btn.type = 'button';
      btn.disabled = busy;
      btn.addEventListener('click', () => {
        userDirty = true;
        const current = textarea.value.trim();
        if (!current) {
          textarea.value = phrase.replace(/^，/, '');
        } else if (phrase.startsWith('，')) {
          textarea.value = current.replace(/[。；，,.]+$/, '') + phrase;
        } else if (phrase.startsWith('加上')) {
          const clean = phrase.replace(/^加上[^：]+：/, '');
          textarea.value = `${current}，${clean}`;
        } else {
          textarea.value = phrase;
        }
        textarea.focus();
        updateBudgetPill(globalThis.ytWorkbenchState?.journey);
        renderSuggestions(globalThis.ytWorkbenchState?.journey);
      });
      chipList.appendChild(btn);
    });
  }

  function syncBusyControls() {
    const busy = isSystemBusy();
    submitBtn.disabled = busy || !globalThis.ytWorkbenchState;
    demoBtn.disabled = busy || !globalThis.ytWorkbenchState;
    textarea.readOnly = busy;
    chipList.querySelectorAll('button').forEach((b) => { b.disabled = busy; });
    document.querySelectorAll('.journey-return-btn').forEach((b) => { b.disabled = busy; });
    if (busy && statusInfo) {
      statusLine.dataset.tone = 'info';
      statusLine.textContent = statusInfo;
    } else if (errorText) {
      statusLine.dataset.tone = 'error';
      statusLine.textContent = errorText;
    } else {
      statusLine.textContent = '';
    }
  }

  async function handlePrepare(useDemo) {
    if (isSystemBusy()) return;
    errorText = '';
    const rawGoal = textarea.value.trim();
    const effectiveGoal = rawGoal || globalThis.ytWorkbenchState?.journey?.goal || '排查充分与必要条件的理解盲点';
    statusInfo = useDemo ? '正在载入 24 条预置演示档案并生成今日诊断焦点…' : '正在保存学习目标并准备盲点实验室…';
    syncBusyControls();
    try {
      const nextState = await prepareJourney(effectiveGoal, useDemo);
      userDirty = false;
      statusInfo = '';
      errorText = '';
      syncBusyControls();
      window.location.hash = '#lab';
      if (!nextState?.lab_state) {
        window.dispatchEvent(new CustomEvent('yt:begin'));
      }
    } catch (err) {
      statusInfo = '';
      errorText = err?.message || '准备失败，请稍后重试';
      syncBusyControls();
    }
  }

  textarea.addEventListener('input', () => {
    userDirty = true;
    errorText = '';
    updateBudgetPill(globalThis.ytWorkbenchState?.journey);
    renderSuggestions(globalThis.ytWorkbenchState?.journey);
    syncBusyControls();
  });

  textarea.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      handlePrepare(false);
    }
  });

  submitBtn.addEventListener('click', () => handlePrepare(false));
  demoBtn.addEventListener('click', () => handlePrepare(true));

  function renderRecordItem(rec) {
    const li = el('li', 'journey-record-item');
    const originText = rec.origin === 'preset' ? '预置档案' : '本次实作';
    const gradeText = rec.grade === 'correct' ? '回答正确' : '回答错误';
    const left = el('span', '', `${formatTrack(rec.track)} · ${rec.choice || '已作答'}`);
    const right = el('span', '', `${gradeText}（${originText}）`);
    li.append(left, right);
    return li;
  }

  function renderLabAndPlan(state) {
    const journey = state?.journey || null;
    const lessonLabel = document.querySelector('.lesson-label');
    if (lessonLabel) lessonLabel.textContent = `本次练习 · ${state?.lab_state?.question?.topic_label || '充分与必要条件'}`;
    prepareReturn.hidden = !journey?.can_return;
    demoBtn.hidden = !!journey?.can_return;
    demoNote.textContent = journey?.can_return ? '正在体验独立演示档案，你的学习记录已保留。' : '想看看学过一段时间后，系统会怎样安排？';
    if (!userDirty && journey?.goal && document.activeElement !== textarea) {
      textarea.value = journey.goal;
    }
    updateBudgetPill(journey);
    renderSuggestions(journey);

    if (labSidebar) {
      labSidebar.hidden = !journey;
      labSidebar.replaceChildren();
      const title = el('h3', 'journey-focus-title', '接下来这样学');
      labSidebar.appendChild(title);
      if (!journey) {
        labSidebar.appendChild(el('p', 'block-hint', '完成准备或载入 24 条预置档案后，这里将分列展示「过去薄弱处 → 今日先练 → 本次作答证据 → 下一步计划」。'));
      } else {
        const history = el('details', 'journey-history');
        history.appendChild(el('summary', '', `查看学习记录（${Number(journey.seed_count || 0) + Number(journey.actual_count || 0)} 次）`));
        history.appendChild(el('p', 'block-hint', '预置档案用于演示，本次作答单独统计。短测结果仍需后续练习确认。'));
        if (Array.isArray(journey.skills) && journey.skills.length) {
          const ul = el('ul', 'journey-skill-list');
          journey.skills.forEach((sk) => {
            const li = el('li', 'journey-skill-item');
            li.append(
              el('span', '', sk.label || formatTrack(sk.id)),
              el('span', '', `答对 ${sk.correct ?? 0}/${sk.total ?? 0} · ${STATUS_LABELS[sk.status] || '待观察'}`)
            );
            ul.appendChild(li);
          });
          history.appendChild(ul);
        }
        if (journey.feedback_text || journey.next_step) {
          const fbBox = el('div', 'journey-focus-block');
          if (journey.feedback_text) fbBox.appendChild(el('p', '', `最新反馈：${journey.feedback_text}`));
          if (journey.next_step) fbBox.appendChild(el('p', 'block-hint', nextSuggestion(journey)));
          const planLink = el('a', 'text-link', '查看同步更新的今日安排 →');
          planLink.href = '#plan';
          fbBox.appendChild(planLink);
          labSidebar.appendChild(fbBox);
        }
        const records = Array.isArray(journey.records) ? [...journey.records].reverse() : [];
        if (records.length) {
          const recent = records.slice(0, 3);
          const rest = records.slice(3);
          history.appendChild(el('p', 'block-hint', '最近三次练习：'));
          const rList = el('ul', 'journey-record-list');
          recent.forEach((r) => rList.appendChild(renderRecordItem(r)));
          history.appendChild(rList);
          if (rest.length) {
            const more = el('details', '');
            more.style.marginTop = '0.45rem';
            more.appendChild(el('summary', 'block-hint', `展开其余 ${rest.length} 条记录`));
            const moreList = el('ul', 'journey-record-list');
            rest.forEach((r) => moreList.appendChild(renderRecordItem(r)));
            more.appendChild(moreList);
            history.appendChild(more);
          }
        }
        labSidebar.appendChild(history);
        if (journey.can_return) labSidebar.appendChild(returnButton());
      }
    }

    if (planRoot) {
      planRoot.replaceChildren();
      if (journey && Array.isArray(journey.plan) && journey.plan.length) {
        if (legacyGplan) legacyGplan.open = false;
        const h2 = el('h2', 'block-title', `今日练习安排（总预算 ${journey.daily_minutes || 60} 分钟）`);
        const sub = el('p', 'block-hint', `围绕目标「${journey.goal || '充分与必要条件'}」生成 · 预置 ${journey.seed_count || 0} 次 / 本次实作 ${journey.actual_count || 0} 次`);
        planRoot.append(h2, sub);
        if (journey.feedback_text || journey.next_step) {
          const note = el('p', 'journey-Hint', `${journey.feedback_text ? `你说：“${journey.feedback_text}”。` : ''}${nextSuggestion(journey)}`);
          planRoot.appendChild(note);
        }
        const steps = el('ol', 'journey-plan-steps');
        journey.plan.forEach((item, idx) => {
          const li = el('li', 'journey-plan-step');
          const headLine = el('div', 'journey-focus-title', `${idx + 1}. ${item.task || formatTrack(item.track)} · ${item.minutes ?? 0} 分钟`);
          const reasonLine = el('p', 'block-hint', displayReason(item.reason) || '依据诊断记录安排');
          li.append(headLine, reasonLine);
          steps.appendChild(li);
        });
        const backLab = el('a', 'text-link', '回到盲点实验室继续验证 →');
        backLab.href = '#lab';
        planRoot.append(steps, backLab);
        if (journey.can_return) planRoot.appendChild(returnButton());
      } else {
        const h2 = el('h2', 'block-title', '本次练习安排');
        const emptyHint = el('p', 'block-hint', '完成备考设置后，在准备页写下一句今日打算，即可按时间预算开始练习。也可以体验已有练习档案。');
        const goPrep = el('a', 'text-link', '去准备页设定目标或载入演示档案 →');
        goPrep.href = '#prepare';
        planRoot.append(h2, emptyHint, goPrep);
      }
    }
  }

  window.addEventListener('yt:state', (e) => {
    const next = e?.detail || globalThis.ytWorkbenchState || {};
    const sid = next.session_id ?? next.session?.id ?? null;
    if (lastSessionId && sid && sid !== lastSessionId) {
      userDirty = false;
      errorText = '';
      statusInfo = '';
      textarea.value = next.journey?.goal || '';
    }
    if (sid) lastSessionId = sid;
    renderLabAndPlan(next);
    syncBusyControls();
  });

  window.addEventListener('yt:reset', () => {
    userDirty = false;
    errorText = '';
    statusInfo = '';
    textarea.value = '';
    renderLabAndPlan(globalThis.ytWorkbenchState || {});
    syncBusyControls();
  });

  window.addEventListener('yt:activity', syncBusyControls);
  window.addEventListener('yt:platform-busy', syncBusyControls);

  const init = globalThis.ytWorkbenchState || {};
  lastSessionId = init.session_id ?? init.session?.id ?? null;
  renderLabAndPlan(init);
  syncBusyControls();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', bootJourney, { once: true });
} else {
  bootJourney();
}
