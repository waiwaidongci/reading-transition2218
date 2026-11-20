import {research} from '../stores/research.js';import {session} from '../stores/session.js';import {pruneApi} from '../api/research.js';import {download} from '../api/client.js';import {metrics,sourceOptions} from '../components/common/business.js';import {table} from '../components/common/table.js';import {esc,localTime,formObject} from '../utils/format.js';import {bind,action,toast} from '../hooks/actions.js';import {pruneStates,pruneResults} from '../constants/enums.js';
function badge(t){let b='<span class="status '+esc(t.state)+'">'+esc(pruneStates[t.state]||t.state)+'</span>';if(t.state==='completed'&&t.result)b+=' <span class="status approved">'+esc(pruneResults[t.result.status]||t.result.status)+'</span>';return b}
function ledger(tasks,id){if(!tasks.length)return '<p class="empty">还没有剔除搜索任务。</p>';return '<div class="ledger">'+tasks.slice(0,8).map(t=>'<a href="#prune" data-prune="'+t.id+'"><small>#'+t.id+'</small><strong>'+esc(t.name)+'</strong><span class="status '+esc(t.state)+'">'+esc(pruneStates[t.state]||t.state)+'</span><time title="北京时间 · Asia/Shanghai">'+esc(localTime(t.created_at))+'</time></a>').join('')+'</div>'}
function liftRow(m){return m?[{label:'接续次数',value:m.count},{label:'条件概率',value:m.conditional_probability},{label:'目标章节基率',value:m.target_base_rate},{label:'提升度',value:m.lift}]:[]}
function detail(t){
 const p=t.progress||{},r=t.result;
 let html='<div class="run-title"><h2>'+esc(t.name)+'</h2>'+badge(t)+'<small>#'+t.id+' · 来源 #'+t.source_id+'</small></div>'
 +'<p class="fine">目标接续 <b>'+esc(t.target_from)+' → '+esc(t.target_to)+'</b> · 受理时冻结 '+t.input_count+' 条完整会话（原样本不动）· 输入 SHA256 <code>'+esc((t.input_sha256||'').slice(0,16))+'…</code><br>参数：'+esc(JSON.stringify(t.parameters))+' · 创建 '+esc(localTime(t.created_at))+'</p>';
 if(t.state==='failed')return html+'<div class="failure"><h2>本次搜索未完成</h2><p>'+esc(t.error)+'</p></div>';
 if(t.state==='queued'||t.state==='running'){
   return html+'<div class="pending"><span class="spinner"></span><h2>'+(p.phase==='scan'?'正在分批扫描冻结文集':'正在搜索与证明')+'</h2><p>已证下界 '+(p.lb??'—')+' · 当前最佳方案 '+(p.ub??'—')+' · 已探索节点 '+(p.nodes??0)+'。页面自动刷新，队列计算会继续。</p></div>';
 }
 if(!r)return html;
 const head=r.status==='optimal'?'已证最少：剔除 '+r.removed_count+' 个完整会话，提升度降到 1 或以下'
   :r.status==='infeasible'?'已证无解：在不消除该接续的前提下，剔除任何完整会话都无法使提升度 ≤ 1'
   :'已达约定时限：以下为已证下界与当前最佳可复算方案，可继续搜索';
 html+='<h2>'+esc(head)+'</h2>'
 +metrics([{label:'已证下界',value:r.bounds.lower},{label:'最佳方案（上界）',value:r.bounds.upper??'—'},{label:'剔除会话数',value:r.removed_count},{label:'搜索节点',value:r.proof.nodes}])
 +'<div class="result-grid"><section><h2>剔除前</h2>'+metrics(liftRow(r.before))+'</section><section><h2>剔除后（按剩余路径重算）</h2>'+(r.after?metrics(liftRow(r.after)):'<p class="empty">无可行方案</p>')+'</section></div>'
 +'<p class="fine">'+esc(r.explanation)+' 复算：下载剔除清单，用冻结输入（SHA256 '+esc(r.input_sha256.slice(0,16))+'…）重算接续次数与全部分母。</p>';
 if(r.removed_sessions&&r.removed_sessions.length){
   html+='<h2>剔除会话 <small>共 '+r.removed_sessions.length+' 条，预览前30条；CSV含全部行</small></h2>'
   +table(r.removed_sessions,[{key:'session',label:'匿名会话编号'},{key:'path',label:'章节路径'}],{limit:30})
   +'<p><button id="prune-export" class="secondary">下载剔除清单 CSV ↗</button>'+(r.status==='timeout'?' <button id="prune-resume">继续搜索 →</button>':'')+'</p>';
 }else if(r.status==='timeout'){html+='<p><button id="prune-resume">继续搜索 →</button></p>'}
 return html;
}
export async function prunePage(){
 const t=research.pruneId?await pruneApi.get(research.pruneId):null;research.activePrune=t;
 return '<section class="page-heading"><p class="eyebrow">05 / PRUNE &amp; PROVE</p><h1>剔除最少的完整会话，让这条接续回到基率。</h1><p>对提升度大于 1 的接续，搜索使提升度降到 1 或以下的最少剔除方案。受理时冻结文集，原样本不动；按剩余路径重算接续次数和全部分母，接续消失不算翻转。约定时限到了给出已证下界与可复算方案，证完才报最少或无解。</p></section>'
 +'<div class="split config-split"><section class="config-sheet">'
 +(session.writable?'<form id="prune-form"><label>分析来源<select name="source_id">'+sourceOptions(research.sources,research.sourceId)+'</select></label><label>研究名称<input name="name" required value="接续剔除搜索"></label><label>起点章节<input name="target_from" required placeholder="如：序章"></label><label>终点章节<input name="target_to" required placeholder="如：材料"></label><div class="parameter-fields"><label>约定时限（秒）<input type="number" name="time_limit_sec" required value="60"></label><label>分批大小<input type="number" name="batch_size" required value="5000"></label><label>剩余接续下限<input type="number" name="min_remaining" required value="1"></label></div><p class="fine">冻结后进入 Redis 队列，由独立 worker 分批处理；中断沿原输入与已证进度恢复，并发重试不覆盖较新结果。</p><button type="submit">冻结文集并开始搜索 →</button></form>':'<p>阅读者可以查看搜索任务与结果；新建剔除搜索由分析师或负责人执行。</p>')
 +'</section><aside><h2>搜索任务</h2>'+ledger(research.pruneTasks,research.pruneId)+'<div class="method-note"><b>口径</b><p>提升度 = 接续次数 × 总边数 ÷（起点出边 × 终点入边），全程整数重算。</p><p>时限到报已证下界与最佳方案；只有证明完成才报告最少或无解。</p></div></aside></div>'
 +(t?detail(t):'');
}
export function bindPrune(refresh){
 action('#prune-form',async form=>{const d=formObject(form);const r=await pruneApi.create({source_id:Number(d.source_id),name:d.name,target_from:d.target_from,target_to:d.target_to,parameters:{time_limit_sec:Number(d.time_limit_sec),batch_size:Number(d.batch_size),min_remaining:Number(d.min_remaining)}});research.pruneId=r.id;toast('已冻结文集并进入搜索队列');await refresh()});
 for(const a of document.querySelectorAll('[data-prune]'))a.addEventListener('click',async e=>{research.pruneId=Number(a.dataset.prune);await refresh()});
 bind('#prune-export','click',async()=>{try{await download('/prune-tasks/'+research.pruneId+'/export','prune-'+research.pruneId+'-removal.csv')}catch(e){toast(e.message,true)}});
 bind('#prune-resume','click',async()=>{try{await pruneApi.resume(research.pruneId,{});toast('已重新入队，沿已证进度继续搜索');await refresh()}catch(e){toast(e.message,true)}});
}
