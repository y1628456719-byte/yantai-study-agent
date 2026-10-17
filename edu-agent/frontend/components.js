/* 砚台 YANTAI — components.js
 * 无框架纯函数组件：唯一导出 render(state, ui, handlers)。
 * state 为同源 API 原始对象或 null；所有远端文本经 textContent 创建，不使用 innerHTML。
 */
const TABS=[['lab','做题'],['evidence','证据'],['plan','计划']];
const FB_DEFS=[['unclear','没懂'],['too_easy','太简单'],['guessed','我是猜的'],['new_example','换个例子']];
const STATUS_ZH={tentative:'暂定',supported:'已有支持',revised:'已修正',inconclusive:'仍不确定'};
const RESULT_ZH={needs_practice:'需再练',needs_confirmation:'待确认',tentative_transfer:'本次换题验证暂时通过，不代表长期掌握'};
const STOP_ZH={time_budget:'可用时间不足，已暂停本轮：已答记录保留；调大「今日可用分钟」后点「继续」恢复同一轮。',round_limit:'已到本轮上限（最多三轮）：可结束本轮并查看小结与计划。',bank_exhausted:'本次练习的题目已完成：本轮到此为止，已答记录保留。',user_stop:'你已手动结束本轮：已答记录保留，可查看小结与计划。',answer_pending:'有一道作答尚未提交：请先提交或刷新状态，本轮才能继续。'};
const STOP_SHORT={time_budget:'时间暂停',round_limit:'到三轮上限',bank_exhausted:'题库用尽',user_stop:'手动停止',answer_pending:'有待提交作答'};
const ACTION_ZH={start:'开始',submit:'提交答案',feedback:'写反馈',time:'调整时间',continue:'继续',stop:'结束本轮'};
const EVI_ZH={material:'资料引用',answer:'作答',feedback:'反馈'};
const PHASES=[['diagnosis','首题诊断'],['distinguish','区分题'],['verification','换题验证'],['summary','本轮小结']];

function el(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=text;return n}
function mount(name){return document.querySelector('[data-mount="'+name+'"]')}
function zh(map,key){if(key==null)return'—';return map[key]||String(key)}
function stat(label,value){const d=el('div','stat');d.appendChild(el('div','stat-num',String(value)));d.appendChild(el('div','stat-label',label));return d}
function stageKey(lab){
  if(lab.stage==='diagnosis'&&lab.substage==='distinguish')return'distinguish';
  return lab.stage;
}
function stageLabel(lab){
  const k=stageKey(lab);
  return{diagnosis:'首题诊断',distinguish:'区分题',verification:'换题验证',summary:'本轮小结',stopped:'已停止'}[k]||String(k);
}
function snapshotFocus(){
  const a=document.activeElement;
  if(!a||!a.dataset||!a.dataset.focusKey)return null;
  const s={key:a.dataset.focusKey};
  if(typeof a.selectionStart==='number'){s.start=a.selectionStart;s.end=a.selectionEnd}
  return s;
}
function restoreFocus(snap){
  if(!snap)return;
  const n=document.querySelector('[data-focus-key="'+snap.key+'"]');
  if(!n)return;
  n.focus();
  if(snap.start!=null&&n.setSelectionRange){try{n.setSelectionRange(snap.start,snap.end)}catch(e){}}
}
function idButton(id,h){const b=el('button','text-link','查看作答依据');b.type='button';b.addEventListener('click',()=>h.openEvidence(id));return b}
function evidenceIdRow(ids,h){const row=el('div','tactions');if(ids&&ids.length){const b=el('button','text-link','查看依据 ↗');b.type='button';b.addEventListener('click',()=>h.openEvidence(ids[0]));row.appendChild(b)}return row}
/* 旧顶栏入口保留兼容；演示只显示公开学习状态。 */
function renderMode(state){}
function renderStatus(state,ui,h){}
function renderTabs(ui,h){
  const bar=mount('tabs');if(!bar)return;bar.replaceChildren();
  bar.setAttribute('role','tablist');
  for(const [key,label] of TABS){
    const b=el('button','tabbtn',label);
    b.type='button';b.setAttribute('role','tab');b.setAttribute('aria-selected',String(ui.tab===key));
    b.addEventListener('click',()=>h.setTab(key));
    bar.appendChild(b);
  }
  document.body.dataset.tab=ui.tab;
  document.body.dataset.contextOpen=String(!!ui.contextOpen);
  if(ui.tab==='evidence'){document.querySelectorAll('details.drawer').forEach(d=>{if(d.querySelector('[data-mount="evidence"]'))d.open=true})}
}
function renderMobileEntry(ui,h){
  const box=mount('mobileEntry');if(!box)return;box.replaceChildren();
  const row=el('div','tactions');
  const b=el('button','btn btn-ghost ctx-btn',ui.contextOpen?'收起目标与资料':'目标与资料');
  b.type='button';b.setAttribute('aria-expanded',String(!!ui.contextOpen));
  b.addEventListener('click',()=>h.toggleContext());
  row.appendChild(b);
  box.appendChild(row);
}

/* ---------- 左栏：目标 / 资料 / 预算 ---------- */
function renderGoal(state,ui,h){
  const box=mount('goal');if(!box)return;box.replaceChildren();
  const c=el('section','card');
  const head=el('div','row');
  head.style.justifyContent='space-between';
  head.style.alignItems='center';
  head.appendChild(el('h2','card-title','目标'));
  const closeBtn=el('button','btn','关闭目标与资料');
  closeBtn.type='button';
  closeBtn.style.minHeight='44px';
  closeBtn.addEventListener('click',()=>h.toggleContext());
  
  c.appendChild(head);
  const SCHOOL_ZH={jlu:'吉林大学',heu:'哈尔滨工程大学',neu:'东北大学'};
  const sid=state&&state.session&&state.session.selected_school_id;
  const school=sid&&SCHOOL_ZH[sid]?SCHOOL_ZH[sid]:null;
  const prof=state&&state.profile;
  const dir=prof&&(prof.direction||prof.target_direction||prof.major);
  if(school){
    const chips=el('div','row');
    chips.appendChild(el('span','badge','目标院校：'+school));
    if(dir)chips.appendChild(el('span','badge','报考方向：'+dir));
    c.appendChild(chips);
  }else{
    c.appendChild(el('p','hint','目标未确认：请在档案中选择报考院校与方向。'));
  }
  const row=el('div','tactions');
  const gb=el('button','btn btn-ghost btn-sm',school?'修改目标档案':'前往档案确认目标');
  gb.type='button';
  gb.addEventListener('click',()=>{location.hash='#profile'});
  row.appendChild(gb);
  c.appendChild(row);
  c.appendChild(el('p','hint','从条件推理开始练习，作答与反馈会用于安排下一步。'));
  box.appendChild(c);
}
function renderMaterial(state,h){
  const box=mount('material');if(!box)return;box.replaceChildren();
  const c=el('section','card');
  c.appendChild(el('h2','card-title','资料'));
  c.appendChild(el('p','hint','练习依据与原文'));
  const lab=state&&state.lab_state;
  const mats=lab?(lab.evidence||[]).filter(e=>e&&['material','rule_source'].includes(e.type)):[];
  if(!mats.length){
    c.appendChild(el('p','hint',lab?'作答后出现引用行，可回查原文。':'开始诊断后，在此回查原文引用。'));
  }else{
    const ul=el('ul','list');
    mats.forEach((m,i)=>{
      const li=el('li');
      const b=el('button','btn btn-ghost btn-sm','引用'+(i+1)+(m.locator?' · 第'+m.locator.line+'行':''));
      b.type='button';b.title=String(m.id);b.setAttribute('aria-label','查看资料引用'+(i+1));
      b.addEventListener('click',()=>h.openEvidence(m.id));
      li.appendChild(b);ul.appendChild(li);
    });
    c.appendChild(ul);
  }
  const sessMats=state&&state.session&&state.session.materials;
  const mCount=Array.isArray(sessMats)?sessMats.length:(sessMats&&typeof sessMats==='object'?Object.keys(sessMats).length:0);
  c.appendChild(el('p','hint',mCount>0?('档案资料已存 '+mCount+' 条；当前练习使用的依据以引用卡片为准。'):'档案尚未保存资料；可在备考参考页收存备考笔记。'));
  const mrow=el('div','tactions');
  const mb=el('button','btn btn-ghost btn-sm',mCount>0?'管理档案资料':'前往档案添加资料');
  mb.type='button';
  mb.addEventListener('click',()=>{location.hash='#reference'});
  mrow.appendChild(mb);
  c.appendChild(mrow);
  box.appendChild(c);
}
function renderBudget(state){
  const box=mount('budget');if(!box)return;box.replaceChildren();
  const lab=state&&state.lab_state,plan=state&&state.plan;
  const cyc=lab&&lab.cycle;
  const s=el('span','tb-budget-line');
  if(!cyc&&!plan){s.textContent='尚未开始 · 初始预算待开始';box.appendChild(s);return}
  const budget=cyc?cyc.budget_minutes:plan.daily_minutes;
  const spent=cyc?cyc.estimated_spent_minutes:plan.estimated_spent_minutes;
  const avail=cyc?Math.max(0,budget-spent):(plan.available_minutes!=null?plan.available_minutes:Math.max(0,budget-spent));
  s.textContent='今日可用'+budget+'分 / 余'+avail+'分';
  s.title=spent!=null?('已用约'+spent+'分 · 演示估算'):'演示估算';
  box.appendChild(s);
}

/* ---------- 中栏：通知 / 起步 / 题 / 动作 / 反馈 / 时间 / 讲解 / 小结 ---------- */
function renderStarter(state,ui,h){
  const box=mount('starter');if(!box)return;box.replaceChildren();
  if(ui.notice){const n=el('div','notice');n.setAttribute('role','status');n.appendChild(el('span',null,ui.notice));box.appendChild(n)}
  if(ui.busy){const n=el('div','notice notice-wait');n.setAttribute('role','status');n.appendChild(el('span',null,'正在等待服务响应…'));box.appendChild(n)}
  if(ui.error){
    const n=el('div','notice notice-error');n.setAttribute('role','alert');
    const wrap=el('div');
    wrap.appendChild(el('strong',null,'出错了：'));
    wrap.appendChild(document.createTextNode(ui.error.message||'未知错误'));
    if(!ui.pending)wrap.appendChild(el('p','hint','该请求已被拒绝，没有未完成的操作；请核对最新状态后主动重新提交，不会自动重复执行。'));
    const row=el('div','tactions');
    if(ui.pending){
      const rb=el('button','btn btn-ghost btn-sm','重试');rb.type='button';rb.disabled=!!ui.busy;rb.addEventListener('click',()=>h.retry());
      row.appendChild(rb);
    }
    const fb=el('button','btn btn-ghost btn-sm','刷新');fb.type='button';fb.disabled=!!ui.busy;fb.addEventListener('click',()=>h.refresh());
    row.appendChild(fb);wrap.appendChild(row);n.appendChild(wrap);box.appendChild(n);
  }
  if(ui.pending&&!ui.error){
    const n=el('div','notice');n.setAttribute('role','status');
    n.appendChild(el('span',null,'上一步操作未完成；可在网络恢复后重试，重试前不会自动继续。'));
    const row=el('div','tactions');
    const rb=el('button','btn btn-ghost btn-sm','重试上一次操作');rb.type='button';rb.disabled=!!ui.busy;rb.addEventListener('click',()=>h.retry());
    row.appendChild(rb);n.appendChild(row);box.appendChild(n);
  }
  if(!state){
    if(ui.busy){
      const w=el('section','card empty');
      w.appendChild(el('h1','empty-title','正在读取工作台'));
      w.appendChild(el('p','empty-body','正在等待服务返回状态，请稍候；成功读取前不会开始诊断。'));
      box.appendChild(w);return;
    }
    const c=el('section','card empty');
    c.appendChild(el('h1','empty-title','无法读取工作台状态'));
    c.appendChild(el('p','empty-body','首次加载失败或服务不可达。可刷新重试；在成功读取前不能开始诊断。'));
    const b=el('button','btn btn-primary','刷新状态');b.type='button';b.disabled=!!ui.busy;b.addEventListener('click',()=>h.refresh());
    c.appendChild(b);box.appendChild(c);return;
  }
  if(state.lab_state)return;
  const c=el('section','card empty');
  c.appendChild(el('p','section-kicker','从一道题开始'));
  c.appendChild(el('h2','empty-title','把盲点，磨到见底。'));
  c.appendChild(el('p','empty-body','有时不是不会，而是还没分清。先做一个判断，再换个角度，看清自己卡在哪里。'));
  const flow=el('p','intro-flow','诊断  →  区分  →  讲解  →  换题验证');c.appendChild(flow);
  const start=el('button','btn btn-primary','开始诊断');start.type='button';start.disabled=!!(ui.busy||ui.pending);start.addEventListener('click',()=>h.start());c.appendChild(start);
  box.appendChild(c);
}
function syncSubmitButton(){
  const b=document.querySelector('[data-action="submit"]');
  if(!b)return;
  const picked=document.querySelector('[data-mount="question"] input[type=radio]:checked');
  b.disabled=b.dataset.lock==='true'||b.dataset.canAnswer!=='true'||!picked;
}
function renderQuestion(state,ui,h){
  const box=mount('question');if(!box)return;box.replaceChildren();
  const lab=state&&state.lab_state;if(!lab||!lab.question)return;
  const q=lab.question,cyc=lab.cycle||{};
  const lock=!!(ui.busy||ui.pending)||cyc.can_answer===false;
  const c=el('section','card');
  c.appendChild(el('p','q-meta',stageLabel(lab)));
  c.appendChild(el('h1','q-stem',q.stem));
  const ul=el('ul','opts');
  for(const o of q.options){
    const li=el('li');
    const label=el('label','opt'+(ui.selectedOption===o.id?' is-selected':'')+(lock?' is-disabled':''));
    const input=el('input');
    input.type='radio';input.name='q-'+q.question_id;input.value=o.id;
    input.checked=ui.selectedOption===o.id;input.disabled=lock;
    input.dataset.focusKey='opt-'+q.question_id+'-'+o.id;
    input.addEventListener('change',()=>{
      h.selectOption(o.id);
      ul.querySelectorAll('label.opt').forEach(l=>{
        const r=l.querySelector('input[type=radio]');
        l.classList.toggle('is-selected',!!r&&r.value===o.id);
      });
      syncSubmitButton();
    });
    label.appendChild(input);
    label.appendChild(el('span','opt-key',o.id));
    label.appendChild(el('span','opt-text',o.text));
    li.appendChild(label);ul.appendChild(li);
  }
  c.appendChild(ul);
  box.appendChild(c);
}
function phaseStrip(lab){
  const wrap=el('div');
  const bar=el('div','phase');
  const steps=[['diagnosis','诊断'],['distinguish','追问'],['verification','验证'],['summary','复盘']];
  const k=stageKey(lab);
  const idx=steps.findIndex(p=>p[0]===k);
  const cur=idx<0?-1:idx;
  steps.forEach((p,i)=>{
    if(i){const ln=el('span','phase-link','—');ln.setAttribute('aria-hidden','true');bar.appendChild(ln)}
    const s=el('span',i<cur?'phase-step is-done':i===cur?'phase-step is-active':'phase-step',p[1]);
    if(i===cur)s.setAttribute('aria-current','step');
    bar.appendChild(s);
  });
  wrap.appendChild(bar);
  const cyc=lab.cycle||{};
  const routine=cyc.stop_reason==='answer_pending'&&cyc.can_answer!==false;
  wrap.appendChild(el('p','phase-text','当前阶段：'+stageLabel(lab)+(cyc.stop_reason&&!routine?'（'+(STOP_SHORT[cyc.stop_reason]||'已暂停')+'）':'')));
  return wrap;
}
function renderActions(state,ui,h){
  const box=mount('actions');if(!box)return;box.replaceChildren();
  const lab=state&&state.lab_state;if(!lab)return;
  const cyc=lab.cycle||{};
  const lock=!!(ui.busy||ui.pending);

  const routinePause=cyc.stop_reason==='answer_pending'&&cyc.can_answer!==false;
  if((cyc.stop_reason||lab.stage==='stopped')&&!routinePause){
    const n=el('div','notice');n.setAttribute('role','status');
    n.appendChild(el('span',null,(cyc.stop_reason&&STOP_ZH[cyc.stop_reason])||'本轮已停止。'));
    box.appendChild(n);
  }
  const row=el('div','tactions');
  if(lab.next_action==='answer'&&lab.question){
    const b=el('button','btn btn-primary','提交判断');
    b.type='button';
    b.dataset.action='submit';b.dataset.lock=String(lock);b.dataset.canAnswer=String(cyc.can_answer!==false);
    b.disabled=lock||!ui.selectedOption||cyc.can_answer===false;
    b.addEventListener('click',()=>{
      const picked=document.querySelector('[data-mount="question"] input[type=radio]:checked');
      h.submit(picked?picked.value:(ui.selectedOption||null));
    });
    row.appendChild(b);
  }
  if(lab.next_action==='continue'&&cyc.can_continue){
    const b=el('button','btn btn-primary',lab.stage==='summary'?'继续下一轮':'继续');
    b.type='button';b.disabled=lock;b.addEventListener('click',()=>h.next());
    row.appendChild(b);
  }
  if(lab.stage!=='stopped'){
    const s=el('button','btn btn-danger','结束本轮');
    s.type='button';s.disabled=lock;s.addEventListener('click',()=>h.stop());
    const more=el('details','secondary-actions');more.appendChild(el('summary',null,'更多'));more.appendChild(s);row.appendChild(more);
  }
  if(row.childNodes.length)box.appendChild(row);
}
function renderFeedback(state,ui,h){
  const box=mount('feedback');if(!box)return;box.replaceChildren();
  const lab=state&&state.lab_state;if(!lab)return;
  if(lab.stage==='verification'&&lab.question&&lab.cycle?.round>1)return;
  const fb=lab.feedback;
  if(fb){
    const n=el('div','fb '+(fb.is_correct?'fb-ok':'fb-bad'));
    n.appendChild(el('p','fb-title',fb.is_correct?'回答正确':'本题尚未答对'));
    if(fb.text)n.appendChild(el('p','para',fb.text));

    box.appendChild(n);
  }
  if(fb&&lab.stage!=='stopped'){
    const bar=el('details','feedback-options');
    bar.appendChild(el('summary',null,'想换一种讲法？'));
    const row=el('div','tactions');
    for(const [k,t] of FB_DEFS){
      const b=el('button','btn btn-ghost btn-sm',t);
      b.type='button';b.disabled=!!(ui.busy||ui.pending);
      b.addEventListener('click',()=>h.feedback(k));
      row.appendChild(b);
    }
    bar.appendChild(row);box.appendChild(bar);
  }
}
function renderTime(state,ui,h){
  const box=mount('time');if(!box)return;box.replaceChildren();
  const lab=state&&state.lab_state;if(!lab)return;
  const cyc=lab.cycle||{};
  const c=el('section','card');
  c.appendChild(el('h2','card-title','今日可用分钟'));
  const form=el('form');
  const wrap=el('div','tform');
  const f=el('div','field');
  const input=el('input','input');
  input.type='number';input.min='1';input.max='1440';input.step='1';input.required=true;
  input.inputMode='numeric';input.placeholder='1–1440 整数';
  input.value=ui.timeDraft||'';input.disabled=!!(ui.busy||ui.pending);
  input.dataset.focusKey='time';
  input.setAttribute('aria-label','今日可用分钟');
  input.addEventListener('input',()=>h.setTimeDraft(input.value));
  f.appendChild(input);
  f.appendChild(el('span','hint','当前 '+(cyc.budget_minutes!=null?cyc.budget_minutes:'—')+' 分钟 · 已用 '+(cyc.estimated_spent_minutes!=null?cyc.estimated_spent_minutes:'—')+' 分钟'));
  wrap.appendChild(f);form.appendChild(wrap);
  const row=el('div','tactions');
  const b=el('button','btn btn-primary','调整时间');
  b.type='submit';b.disabled=!!(ui.busy||ui.pending);
  row.appendChild(b);form.appendChild(row);
  form.addEventListener('submit',e=>{
    e.preventDefault();
    if(!input.checkValidity()){input.reportValidity();return}
    const v=Number(input.value);
    if(!Number.isInteger(v)||v<1||v>1440){input.setCustomValidity('需为 1–1440 的整数');input.reportValidity();input.setCustomValidity('');return}
    h.time(v);
  });
  c.appendChild(form);
  if(cyc.stop_reason==='time_budget')c.appendChild(el('p','hint','时间已不足，本轮暂停；调大「今日可用分钟」后点「继续」恢复同一轮，已答记录保留。'));
  box.appendChild(c);
}
function renderExplain(state,h){
  const box=mount('explain');if(!box)return;box.replaceChildren();
  const lab=state&&state.lab_state;if(!lab||!lab.explanation)return;
  const c=el('section','card');
  c.appendChild(el('h2','card-title','换个角度，想明白。'));
  String(lab.explanation.text||'').split(/\n+/).filter(Boolean).forEach(p=>c.appendChild(el('p','para',p)));
  c.appendChild(evidenceIdRow(lab.explanation.evidence_ids,h));
  box.appendChild(c);
}
function renderSummary(state,h){
  const box=mount('summary');if(!box)return;box.replaceChildren();
  const lab=state&&state.lab_state;if(!lab||!lab.summary)return;
  if(lab.stage==='verification'&&lab.question&&lab.cycle?.round>1)return;
  const s=lab.summary;
  const c=el('section','card');
  c.appendChild(el('h2','card-title','本轮小结'));
  const g=el('div','sum-grid');
  g.appendChild(stat('结果',s.result_status==null?'—':(RESULT_ZH[s.result_status]||String(s.result_status))));
  g.appendChild(stat('换题验证',s.verification_correct?'通过':'未通过'));
  c.appendChild(g);
  if(s.result)c.appendChild(el('p','para',s.result));
  if(s.uncertainty)c.appendChild(el('p','hint',String(s.uncertainty).replace('仅为自编样本中的本次表现','仅代表本次练习表现')));
  c.appendChild(evidenceIdRow(s.evidence_ids,h));
  box.appendChild(c);
}

/* ---------- 右栏：判断 / 证据 / 计划 ---------- */
function baCell(title,list){
  const d=el('div','ba-cell');
  d.appendChild(el('p','label',title));
  if(!list||!list.length){d.appendChild(el('p','hint','无'));return d}
  list.forEach(x=>d.appendChild(el('p',null,x.label+'：'+zh(STATUS_ZH,x.status))));
  return d;
}
function renderHypotheses(state,h){}
function eviCard(ev,h){
  const d=el('article','evi');d.dataset.evidenceId=ev.id;d.tabIndex=-1;
  if(ev.type==='material'){d.appendChild(el('p','label','原文引文'));d.appendChild(el('blockquote','evi-text',ev.quote||''));if(ev.locator)d.appendChild(el('p','hint','第 '+ev.locator.line+' 行'));}
  else if(ev.type==='answer'){d.appendChild(el('p','label','作答记录'));if(ev.question?.stem)d.appendChild(el('p',null,ev.question.stem));d.appendChild(el('p','hint','选项 '+ev.answer+' · '+(ev.is_correct?'回答正确':'待巩固')));}
  else if(ev.type==='feedback')d.appendChild(el('p',null,ev.label||'学习感受已记录'));
  return d;
}
function renderEvidence(state,h){
  const box=mount('evidence');if(!box)return;box.replaceChildren();
  const c=el('section','card');
  c.appendChild(el('h2','card-title','证据流'));
  const lab=state&&state.lab_state;
  const list=lab?(lab.evidence||[]):[];
  if(!list.length){c.appendChild(el('p','hint','暂无证据，作答后逐条记录。'));box.appendChild(c);return}
  list.forEach(ev=>c.appendChild(eviCard(ev,h)));
  box.appendChild(c);
}
function taskCard(t,h,deferred){
  const d=el('div','task'+(deferred?' is-deferred':''));
  d.appendChild(el('span','task-min',(t.minutes!=null?t.minutes+'分':'—')));
  const body=el('div','task-body');
  const title=el('p',null,t.task);
  body.appendChild(title);
  if(t.reason)body.appendChild(el('p','task-why',t.reason));
  body.appendChild(evidenceIdRow(t.evidence_ids,h));
  d.appendChild(body);
  return d;
}
function renderPlan(state,ui,h){
  const box=mount('plan');if(!box)return;box.replaceChildren();
  const c=el('section','card');
  c.appendChild(el('h2','card-title','下一步计划'));
  const p=state&&state.plan;
  if(!p){c.appendChild(el('p','hint','待开始'));box.appendChild(c);return}
  const lab=state.lab_state;
  if(lab&&lab.stage!=='stopped'&&stageKey(lab)!=='summary')c.appendChild(el('p','hint','本轮进行中：计划依据当前证据生成，仍可能调整，非长期学习记录。'));
  else c.appendChild(el('p','hint','依据本轮证据生成，非长期学习记录。'));
  const tasks=p.tasks||[];
  const first=tasks[0];
  if(first){
    c.appendChild(el('p','label','下一步'));
    c.appendChild(taskCard(first,h,false));
  }else{
    c.appendChild(el('p','hint','暂无已排任务。'));
  }
  const g=el('div','sum-grid');
  g.appendChild(stat('今日可用',p.daily_minutes+' 分'));
  g.appendChild(stat('已用',p.estimated_spent_minutes+' 分'));
  g.appendChild(stat('剩余',p.available_minutes+' 分'));
  c.appendChild(g);
  const more=tasks.slice(1);
  const hasMore=more.length||(p.deferred&&p.deferred.length)||p.note;
  if(hasMore){
    const det=el('details');
    det.appendChild(el('summary',null,'全部安排与说明'));
    more.forEach(t=>det.appendChild(taskCard(t,h,false)));
    if(p.deferred&&p.deferred.length){
      det.appendChild(el('p','label','本次未安排（预算内暂不排）'));
      const du=el('ul','list');
      p.deferred.forEach(t=>{
        if(t&&typeof t==='object')du.appendChild(el('li',null,((t.minutes!=null?t.minutes+'分 · ':'')+(t.task||t.label||'未命名任务'))));
        else du.appendChild(el('li',null,String(t)));
      });
      det.appendChild(du);
    }
    det.appendChild(el('p','para','合计 '+p.total_minutes+' 分钟'));
    if(p.note)det.appendChild(el('p','hint',p.note));
    c.appendChild(det);
  }else{
    c.appendChild(el('p','hint','合计 '+p.total_minutes+' 分钟'));
  }
  box.appendChild(c);
}

/* ---------- 主入口 ---------- */
export function render(state,ui,handlers){
  const snap=snapshotFocus();
  const _lab=state&&state.lab_state;
  document.body.dataset.phase=_lab?stageKey(_lab):'none';
  document.body.dataset.hasLab=String(!!_lab);
  document.body.dataset.hasExplanation=String(!!(_lab&&_lab.explanation));
  renderMode(state);
  renderStatus(state,ui,handlers);
  renderTabs(ui,handlers);
  renderMobileEntry(ui,handlers);
  renderGoal(state,ui,handlers);
  renderMaterial(state,handlers);
  renderBudget(state);
  renderStarter(state,ui,handlers);
  renderQuestion(state,ui,handlers);
  renderActions(state,ui,handlers);
  renderFeedback(state,ui,handlers);
  renderTime(state,ui,handlers);
  renderExplain(state,handlers);
  renderSummary(state,handlers);
  renderHypotheses(state,handlers);
  renderEvidence(state,handlers);
  renderPlan(state,ui,handlers);
  restoreFocus(snap);
}
