import React,{useEffect,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {createPortal} from 'react-dom';
import {useReducedMotion} from 'motion/react';
import {Check,ArrowUpRight,BookOpen,ChevronDown} from 'lucide-react';
import SpotlightCard from './components/SpotlightCard';
import {BorderBeam} from './components/BorderBeam';
import {Task,TaskTrigger,TaskContent,TaskItem} from './components/Task';
import {InlineCitationSource,InlineCitationQuote} from './components/InlineCitation';
import {resetToDefaults} from '../frontend/api.js';

declare global {var ytWorkbenchState:any;var ytLabActivity:any;var ytPlatformBusy:boolean;}
function useWorkbench(){
  const [state,setState]=useState(globalThis.ytWorkbenchState);
  const [busy,setBusy]=useState(!!(globalThis.ytPlatformBusy||globalThis.ytLabActivity?.busy));
  useEffect(()=>{
    const update=()=>{setState(globalThis.ytWorkbenchState);setBusy(!!(globalThis.ytPlatformBusy||globalThis.ytLabActivity?.busy));};
    const names=['yt:state','yt:activity','yt:platform-busy'];names.forEach(n=>window.addEventListener(n,update));update();
    return()=>names.forEach(n=>window.removeEventListener(n,update));
  },[]);return {state,busy};
}
function PaperCard({children,className=''}:{children:React.ReactNode,className?:string}){
  const reduced=useReducedMotion();
  if(reduced)return <section className={'paper-card '+className}>{children}</section>;
  return <SpotlightCard theme="light" grain={0} spotlightColor="#568183" intensity={.45} borderGlow={.6} ambient={false} flare={false} className={'paper-card '+className}>{children}</SpotlightCard>;
}
function AgentMark({busy,ready}:{busy:boolean,ready:boolean}){
  const reduced=useReducedMotion();
  return <div className={'agent-mark '+(busy?'is-working':'')} role="status" aria-live="polite"><span className="agent-symbol" aria-hidden="true"><i/><i/><i/><i/></span><span>{busy?'正在处理':ready?'研研 · 就绪':'正在连接'}</span>{busy&&!reduced&&<BorderBeam size={32} duration={5} colorFrom="#5d8e91" colorTo="#b83b28"/>}</div>;
}
const toolNames:Record<string,string>={create_session:'建立学习档案',update_profile:'更新备考档案',recommend:'比较院校条件',select_school:'确认目标院校',revise_plan:'调整学习安排',list_sources:'查看招生来源',lab_start:'开始诊断练习',lab_answer:'核对本次作答',get_session:'读取学习进度',get_plan:'查看学习安排',add_material:'保存学习资料',list_materials:'查看学习资料',get_material:'阅读学习资料'};
function ExecutedTasks({trace}:{trace:any[]}){
  const steps=trace.filter(t=>t&&t.tool&&typeof t.ok==='boolean');
  if(!steps.length)return null;
  return <Task className="executed-task" defaultOpen={false}><TaskTrigger title="这次做了什么"><button className="task-toggle" type="button">这次做了什么 <span>{steps.length} 项</span><ChevronDown size={14}/></button></TaskTrigger><TaskContent className="task-content">{steps.map((t,i)=><TaskItem key={i} className="executed-item"><span className={t.ok?'step-check':'step-failed'}>{t.ok?<Check size={12}/>: '!'}</span><span>{toolNames[t.tool]||'处理本次请求'}</span><small>{t.ok?'已完成':'未完成'}</small></TaskItem>)}</TaskContent></Task>;
}
function Workflow({lab,busy}:{lab:any,busy:boolean}){
  const stage=lab?.stage;const answers=(lab?.evidence||[]).filter((e:any)=>e.type==='answer');
  const verificationPending=stage==='verification'&&!!lab?.question;
  const verificationDone=!verificationPending&&!!lab?.summary&&typeof lab.summary.verification_correct==='boolean';
  const firstDone=answers.length>0;const distinguishDone=!!lab?.explanation;
  const items=[
    {name:'诊断',detail:'先了解你的判断',done:firstDone,active:stage==='diagnosis'&&lab?.substage!=='distinguish'},
    {name:'区分',detail:'换个角度，确认卡点',done:distinguishDone,active:stage==='diagnosis'&&lab?.substage==='distinguish'},
    {name:'讲解',detail:'对照原文，想清原因',done:!!lab?.explanation,active:false},
    {name:'换题验证',detail:'用新题检验理解',done:verificationDone,active:stage==='verification'&&!verificationDone}
  ];
  return <div className="learning-flow" aria-label="实际学习进程">{items.map((it,i)=><div key={it.name} className={'learning-step '+(it.done?'is-done':it.active?'is-current':'')} aria-current={it.active?'step':undefined}><span className="step-no">{it.done?<Check size={13}/>:String(i+1).padStart(2,'0')}</span><span><strong>{it.name}</strong><small>{it.done?'已完成':it.active?(busy?'正在处理…':'进行中'):it.detail}</small></span>{it.active&&<span className="current-dot"/>}</div>)}</div>;
}
function materialTitle(ev:any,state:any){
  if(ev.material_title)return ev.material_title;
  const material=state?.session?.materials?.find((m:any)=>m.material_id===ev.material_id);
  if(material?.title)return material.title;
  // 本地固定练习材料的已核对标题；仅省去演示性质的括注，绝不伪造教材/外部来源。
  return ev.material_id==='demo_logic_v1'?'条件方向判断':'参考原文';
}
function EvidenceCard({ev,state}:{ev:any,state:any}){
  if(!['material','rule_source','answer','feedback'].includes(ev.type))return null;
  const isMaterial=ev.type==='material'||ev.type==='rule_source';
  const answerOption=ev.question?.options?.find((o:any)=>o.id===ev.answer);
  return <article tabIndex={-1} data-evidence-id={ev.id} className={'citation-card '+(isMaterial?'is-quote':'')}>
    {isMaterial?<><div className="citation-kind"><BookOpen size={13}/>原文依据</div><InlineCitationQuote className="citation-quote">{ev.quote}</InlineCitationQuote><InlineCitationSource className="citation-source" title={'《'+materialTitle(ev,state)+'》'} description={Number.isInteger(ev.locator?.line)?'第 '+ev.locator.line+' 行':undefined}/></>:ev.type==='answer'?<><p className="citation-kind">作答记录 <span className={ev.is_correct?'answer-correct':'answer-practice'}>{ev.is_correct?'回答正确':'待巩固'}</span></p><p className="answer-stem">{ev.question?.stem}</p><p className="answer-choice">选择 {ev.answer}{answerOption?' · '+answerOption.text:''}</p></>:<><p className="citation-kind">学习感受</p><p>{ev.label||'已记录本次感受'}</p></>}
  </article>;
}
function LabSidebar({state,busy}:{state:any,busy:boolean}){
  const lab=state?.lab_state;const evidence=lab?.evidence||[];
  const quotes=evidence.filter((e:any)=>e.type==='material'||e.type==='rule_source');
  const latest=evidence.filter((e:any)=>e.type==='answer').slice(-1);
  const latestQuestionId=latest[0]?.question_id;
  const latestQuotes=quotes.filter((e:any)=>e.question_id===latestQuestionId);
  const primary=[...(latestQuotes.length?latestQuotes:quotes).slice(-1),...latest];const prior=evidence.filter((e:any)=>!primary.includes(e));
  const latestHyp=lab?.hypotheses?.at(-1);
  return <><PaperCard className="workflow-card"><div className="section-kicker">每一步，都看得见</div><h2>学习进程</h2><Workflow lab={lab} busy={busy}/></PaperCard>
  <PaperCard className="evidence-panel"><div className="section-kicker">言之有据</div><h2>为什么这样讲？</h2>{latestHyp&&<p className="learning-conclusion">{latestHyp.label}<small>{({tentative:'还需要进一步确认',supported:'本次作答提供了支持',revised:'已随新作答调整',inconclusive:'暂时无法确定'} as any)[latestHyp.status]||''}</small></p>}{!primary.length?<p className="quiet-empty">完成作答后，你的选择与参考原文会留在这里。</p>:primary.map((ev:any)=><EvidenceCard key={ev.id} ev={ev} state={state}/>)}{prior.length>0&&<details className="earlier-evidence"><summary>更多参考与记录 <span>{prior.length}</span></summary>{prior.map((ev:any)=><EvidenceCard key={ev.id} ev={ev} state={state}/>)}</details>}</PaperCard></>;
}
function App(){
  const {state,busy}=useWorkbench();const [agent,setAgent]=useState<{sid?:string,trace:any[]}>({trace:[]});
  useEffect(()=>{const listen=(event:Event)=>{const d=(event as CustomEvent).detail;if(d?.session_id===globalThis.ytWorkbenchState?.session_id)setAgent({sid:d.session_id,trace:d.trace||[]});};window.addEventListener('yt:agent-result',listen);return()=>window.removeEventListener('yt:agent-result',listen);},[]);
  return <>{createPortal(<AgentMark busy={busy} ready={!!state}/>,document.getElementById('yt-agent-island')!)}{createPortal(<ExecutedTasks trace={agent.sid===state?.session_id?agent.trace:[]}/>,document.getElementById('yt-agent-task-island')!)}{createPortal(<LabSidebar state={state} busy={busy}/>,document.getElementById('yt-lab-sidebar-island')!)}</>;
}
const root=document.createElement('div');root.id='presentation-root';document.body.append(root);createRoot(root).render(<App/>);
const resetButton=document.getElementById('reset-demo') as HTMLButtonElement;
const status=document.getElementById('reset-status')!;
function resetAvailability(){resetButton.disabled=!globalThis.ytWorkbenchState||!!(globalThis.ytPlatformBusy||globalThis.ytLabActivity?.busy||globalThis.ytLabActivity?.pending);}
['yt:state','yt:activity','yt:platform-busy'].forEach(n=>window.addEventListener(n,resetAvailability));resetAvailability();
resetButton.addEventListener('click',async()=>{if(resetButton.disabled)return;status.hidden=true;try{await resetToDefaults();document.querySelectorAll('details').forEach(d=>d.open=false);location.hash='#prepare';status.textContent='已恢复默认，可以重新开始。';status.hidden=false;}catch(error:any){status.textContent=error.message||'暂时无法恢复默认，请刷新核对。';status.hidden=false;}finally{resetAvailability();}});
