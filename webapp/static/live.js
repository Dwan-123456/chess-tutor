'use strict';
let reviewDefaults = {deepseek_depth:'instant'}, reviewSave = Promise.resolve();
const reviewReady = fetch(API + '/api/settings/tutor/review').then(async r => {
  if (!r.ok) throw Error('复盘设置读取失败');
  reviewDefaults = await r.json(); $('#reviewDepth').value = reviewDefaults.deepseek_depth;
}).catch(e => { $('#reviewDepthState').textContent = e.message; });
$('#reviewDepth').onchange = () => {
  const depth = $('#reviewDepth').value;
  reviewSave = reviewSave.then(async () => {
    const r = await fetch(API + '/api/settings/tutor/review', {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({...reviewDefaults,deepseek_depth:depth})});
    if (!r.ok) throw Error('复盘设置保存失败');
    reviewDefaults = await r.json(); $('#reviewDepthState').textContent = '已保存 · 下一局复盘生效';
  }).catch(e => { $('#reviewDepthState').textContent = e.message; });
};

document.head.insertAdjacentHTML('beforeend', `<style>
#live{max-width:1200px;margin:28px auto;padding:0 24px;display:grid;grid-template-columns:minmax(280px,600px) minmax(260px,1fr);gap:26px}
#liveBoard{display:grid;grid-template-columns:repeat(8,1fr);aspect-ratio:1;touch-action:none;user-select:none;border:8px solid var(--frame);border-radius:10px;overflow:hidden}
.live-square{position:relative;display:grid;place-items:center;background:var(--sq-l);padding:0;border:0;aspect-ratio:1;color:#33271c;font-size:11px}
.live-square.dark{background:var(--sq-d)}.live-square.selected{box-shadow:inset 0 0 0 4px #e7bc56}
.live-square.legal::after{content:'';position:absolute;width:22%;height:22%;border-radius:50%;background:#38795688;pointer-events:none}
.live-square,.live-square *,.live-square::before{-webkit-user-select:none;user-select:none;-webkit-user-drag:none}
.live-square svg{width:96%;height:96%;pointer-events:none}.live-square::before{content:attr(data-coordinate);position:absolute;bottom:0;left:3px;pointer-events:none}
.live-square .w{fill:#fff5df;stroke:#302b24;stroke-width:1.6}.live-square .b{fill:#302b24;stroke:#fff5df;stroke-width:1.3}
.clockbar{display:flex;justify-content:space-between;align-items:center;margin:12px 0;padding:12px 18px;background:var(--surface2);border:1px solid var(--line2);border-radius:12px}
.clockbar.active{border-color:var(--accent)}.clockbar output{font:32px var(--mono)}
.live-card{padding:22px;background:var(--surface);border:1px solid var(--line2);border-radius:14px;margin-bottom:16px}
.live-card h2{font:22px var(--serif);margin:0 0 14px}.live-card select,.live-card input[type=number]{padding:7px;color:var(--text);background:var(--surface3);border:1px solid var(--line2);border-radius:6px;max-width:100%}
.live-card label{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:14px 0}.live-card input[type=checkbox]{accent-color:var(--accent);width:20px;height:20px}
#liveMoves{font:14px/1.9 var(--mono);max-height:220px;overflow:auto;white-space:pre-wrap}#liveComment{white-space:pre-wrap;overflow-wrap:anywhere}
#liveTrace table{width:100%;border-collapse:collapse;font:12px var(--mono)}#liveTrace td,#liveTrace th{padding:7px;text-align:left;border-bottom:1px solid var(--line)}#liveTrace summary{cursor:pointer;color:var(--muted)}#liveTrace .trace-line{font:12px/1.8 var(--mono);overflow-wrap:anywhere}#liveTrace h3{font-size:15px;margin:18px 0 6px}
#liveBest{color:var(--accent);overflow-wrap:anywhere}#liveStatus{min-height:24px;margin-top:12px;color:var(--accent)}
.live-ghost{position:fixed;width:70px;height:70px;z-index:100;pointer-events:none;filter:drop-shadow(0 8px 8px #0009)}
@media(max-width:800px){#live{grid-template-columns:1fr;padding:0 14px;gap:16px}}
</style>`);
$('#viewer').insertAdjacentHTML('afterend', `<main id="live" hidden>
 <section><div class="row"><button class="btn" id="liveBack">返回复盘</button><button class="btn" id="liveFlip">翻转棋盘</button></div>
 <div class="clockbar" id="blackClock"><span>黑方</span><output>10:00</output></div>
 <div id="liveBoard" aria-label="双人对局棋盘"></div>
 <div class="clockbar" id="whiteClock"><span>白方</span><output>10:00</output></div>
 <div class="row"><button class="btn primary" id="livePause">开始棋钟</button><button class="btn" id="liveExport">导出 PGN</button><button class="btn" id="liveDraw">申请和棋</button></div>
 <div id="liveStatus" role="status"></div></section>
 <aside><div class="live-card"><h2>双人实时对局</h2><p class="note">两人在此电脑轮流走棋。拖动棋子，或依次点击起点和终点。先启动棋钟再落子。</p>
 <label>每方时间（分钟）<input id="liveMinutes" type="number" min="1" max="180" value="10"></label>
 <label>每步加秒<input id="liveIncrement" type="number" min="0" max="120" value="0"></label>
 <button class="btn" id="liveReset">新对局 / 应用棋钟</button></div>
 <div class="live-card"><h2>实时辅助</h2>
 <label>Stockfish 最佳着法<input type="checkbox" id="liveSF"></label>
 <label>DeepSeek 解说<input type="checkbox" id="liveDS"></label>
 <label>Stockfish Effort<select id="liveEffort">${['low','mid','high','xhigh','max','ultra'].map(v=>`<option>${v}</option>`).join('')}</select></label>
 <label>DeepSeek 思考深度<select id="liveDepth"><option>instant</option><option>high</option><option>max</option></select></label>
 <p class="note" id="liveSettingsStatus">自动保存 · 参数调整从下一步生效；开关即时生效。</p>
 <div class="row"><button class="btn" id="liveRetry">重新分析当前局面</button><button class="btn" id="liveExplain" title="动态解释1至3个主要候选，需要开启Stockfish">Explain Search</button></div>
 <p id="liveBest"></p><p id="liveComment" aria-live="polite">辅助默认关闭，可在对局中开启。</p></div>
 <div class="live-card" id="liveTrace" hidden></div>
 <div class="live-card"><h2>棋步</h2><div id="liveMoves"></div></div></aside>
</main>
<dialog id="livePromotion"><form method="dialog"><p>选择升变棋子</p>${[['q','后'],['r','车'],['b','象'],['n','马']].map(([v,t])=>`<button class="btn" value="${v}">${t}</button>`).join('')}<button class="btn" value="">取消</button></form></dialog>`);

const L = {moves:[], position:null, selected:null, flipped:false, active:false, running:false,
  time:{white:600000,black:600000}, stamp:0, increment:0, ended:null, busy:false, serial:0, ctl:null,
  options:{stockfish:false,deepseek:false,deepseek_depth:'instant',engine:null}};
let liveSave = Promise.resolve(), liveCny = 0;
const liveReady = Promise.all([engineReady, fetch(API + '/api/settings/tutor/live').then(async r => {
  if(!r.ok) throw Error('对局设置读取失败'); return r.json();
})]).then(([,options])=>{
  L.options = options;
  if (!options.engine) options.engine = {...engineDefaults,mode:'adaptive',effort:'high'};
  $('#liveSF').checked=options.stockfish; $('#liveDS').checked=options.deepseek;
  $('#liveEffort').value=options.engine.effort; $('#liveDepth').value=options.deepseek_depth;
}).catch(e=>{$('#liveSettingsStatus').textContent=e.message;});
async function livePost(path, body, signal) {
  const r=await fetch(API+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal});
  const data=await r.json(); if(!r.ok) throw Error(data.error || data.detail || '请求失败'); return data;
}
function settleClock(now=Date.now()) {
  if(L.busy)return;
  if(L.running && L.position && !L.ended) {
    const side=L.position.turn;
    L.time[side]=Math.max(0,L.time[side]-(now-L.stamp));
    if(!L.time[side]) {
      L.ended=`${side==='white'?'白方':'黑方'}超时`;
      L.running=false; cancelLiveAnalysis();
    }
  }
  L.stamp=now;
}
function clockText(ms) {const sec=Math.ceil(ms/1000);return `${Math.floor(sec/60).toString().padStart(2,'0')}:${(sec%60).toString().padStart(2,'0')}`;}
function paintClock() {
  $('#liveExplain').disabled=!L.options.stockfish || !L.position;
  for(const side of ['white','black']) {
    const box=$('#'+side+'Clock'); $('output',box).textContent=clockText(L.time[side]);
    box.classList.toggle('active',L.running && L.position?.turn===side && !L.ended);
  }
  $('#livePause').textContent=L.running?'暂停棋钟':L.moves.length?'继续棋钟':'开始棋钟';
  $('#livePause').disabled=!!L.ended || !L.position || L.busy;
  $('#liveStatus').textContent=L.ended || (L.position ? `${L.position.turn==='white'?'白方':'黑方'}走棋${L.position.check?' · 将军':''}${L.running?'':' · 棋钟已暂停'}` : '准备棋盘…');
  $('#liveDraw').disabled=!L.position?.can_claim_draw || !!L.ended;
}
setInterval(()=>{if(L.active){settleClock();paintClock();}},100);
function paintLive() {
  if(!L.position) return;
  const pos=Rules.parseFEN(L.position.fen), legal=L.selected===null?[]:L.position.legal.filter(u=>u.startsWith(Rules.name(L.selected)));
  $('#liveBoard').innerHTML=Array.from({length:64},(_,i)=>{
    const file=L.flipped?7-i%8:i%8,rank=L.flipped?Math.floor(i/8):7-Math.floor(i/8),sq=rank*8+file,p=pos.b[sq];
    return `<button class="live-square ${((file+rank)%2===0)?'dark':''} ${sq===L.selected?'selected':''} ${legal.some(u=>u.slice(2,4)===Rules.name(sq))?'legal':''}" data-sq="${sq}" data-coordinate="${Rules.name(sq)}" aria-label="${Rules.name(sq)} ${p||'空格'}">${p?`<svg class="${Rules.isW(p)?'w':'b'}" viewBox="0 0 100 100"><use href="#p-${p.toUpperCase()}"/></svg>`:''}</button>`;
  }).join('');
  $('#liveMoves').textContent=L.position.sans.map((v,i)=>`${i%2===0?Math.floor(i/2)+1+'. ':''}${v}${i%2?'\n':' '}`).join('');
  $('#liveMoves').scrollTop=$('#liveMoves').scrollHeight;
  paintClock();
}
function cancelLiveAnalysis() {L.serial++;L.ctl?.abort();L.ctl=null;}
function searchScore(score) {
  if(score.mate!=null)return score.mate>0?`将杀 ${score.mate}`:`被将杀 ${Math.abs(score.mate)}`;
  return `${score.cp>=0?'+':''}${(score.cp/100).toFixed(2)}`;
}
function renderSearchTrace(trace) {
  const box=$('#liveTrace');box.hidden=false;
  if(!trace.candidates?.length){box.textContent=trace.error||'暂无完整搜索记录';return;}
  const reasons={only_legal_move:'仅有一种合法着法',clear_stable_lead:'首选优势明显且排名稳定，简要解释',close_candidates:'主要候选接近，展开比较',ranking_unstable:'搜索排名仍有变化，展开比较',stopped_during_iteration:'停止时首选发生变化，以下保留最后完整层的候选比较，优劣尚未确定'};
  const names={root_probe:'根节点候选搜索',beta_cutoff:'达到搜索阈值后停止分支',tt_cutoff:'复用已存搜索结果',null_cutoff:'空着试探触发剪枝',futility_cutoff:'静态界限触发剪枝'};
  const rows=trace.candidates.map(c=>`<section><h3>${c.rank}. ${esc(c.san)} · ${esc(searchScore(c.score_for_turn))}${c.bound==='exact'?'':'（评分界限）'}</h3><div class="trace-line">${esc(c.pv.join(' '))}</div></section>`).join('');
  const history=trace.iterations.slice(-12).map(i=>`<tr><td>${i.depth}</td><td>${i.candidates.map(c=>`${esc(c.san)} ${esc(searchScore(c.score_for_turn))}${c.bound==='exact'?'':' 界限'}`).join(' / ')}</td></tr>`).join('');
  const cutoffs=(trace.events||[]).filter(e=>e.kind!=='root_probe').slice(-6).map(e=>`<p>${esc(names[e.kind]||e.kind)} · 深度 ${e.iteration}<br><span class="trace-line">${esc(e.path_san.join(' '))}${e.move_san?' → '+esc(e.move_san):''}</span></p>`).join('');
  box.innerHTML=`<h2>搜索解释 · 实验版</h2><p>${esc(reasons[trace.selection_reason]||'候选比较')}</p><p class="note">本次解释 ${trace.candidate_count} 种 · 完成深度 ${trace.depth} · ${trace.elapsed_s} 秒 · 评分为当前行棋方视角</p>${rows}
    <details><summary>查看搜索记录</summary><p class="note">${trace.source==='native'?'来自本机记录版 Stockfish 的搜索事件。':'当前引擎仅提供公开搜索输出，未记录内部剪枝。'}</p><table><thead><tr><th>深度</th><th>候选评分记录</th></tr></thead><tbody>${history}</tbody></table>${cutoffs||'<p class="note">本次没有可展示的剪枝采样。</p>'}<p class="note">剪枝是缩减搜索的方法，不代表该候选一定差。仅采样浅层主搜索，不含完整搜索树或神经网络归因。</p><button class="btn" id="downloadSearch">导出搜索记录</button></details>`;
  $('#downloadSearch').onclick=()=>{
    const url=URL.createObjectURL(new Blob([JSON.stringify(trace,null,2)],{type:'application/json'}));
    const link=document.createElement('a');link.href=url;link.download='chess-tutor-search.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
}
async function analyzeLive(explain=false) {
  $('#liveTrace').hidden=true; L.trace=null;
  cancelLiveAnalysis(); $('#liveBest').textContent='';
  const options=structuredClone(L.options);
  if(!options.stockfish && !options.deepseek){$('#liveComment').textContent='辅助已关闭。';return;}
  const serial=L.serial,ctl=L.ctl=new AbortController();
  $('#liveComment').textContent='正在分析当前局面…';
  try {
    const result=await livePost('/api/live/analyze',{moves:[...L.moves],settings:options,explain_search:explain},ctl.signal);
    if(serial!==L.serial) return;
    if(result.trace){L.trace=result.trace;renderSearchTrace(result.trace);}
    if(result.best) $('#liveBest').textContent=result.best.san?`最佳着法：${result.best.san} · 变化：${result.best.pv.join(' ')} · ${result.best.mate!=null?'M'+result.best.mate:(result.best.cp/100).toFixed(2)+'（白方视角）'}`:'终局，无可走着法。';
    $('#liveComment').textContent=[result.comment,result.error].filter(Boolean).join('\n') || (options.stockfish?'Stockfish 分析已完成。':'暂无解说。');
    if(result.billing) { liveCny += result.billing.cost_cny; $('#totalCost').textContent=liveCny.toFixed(4)+' CNY'; }
  } catch(e) {if(serial===L.serial && e.name!=='AbortError') $('#liveComment').textContent=e.message;}
}
async function resetLive() {
  cancelLiveAnalysis();L.running=false;
  const minutes=Number($('#liveMinutes').value),increment=Number($('#liveIncrement').value);
  if(!Number.isFinite(minutes)||minutes<1||minutes>180||!Number.isFinite(increment)||increment<0||increment>120){toast('时间范围：1–180 分钟；加秒：0–120。',true);return;}
  L.busy=true;
  try {
    L.position=await livePost('/api/live/position',{moves:[]});
    L.moves=[];L.selected=null;L.ended=null;L.time={white:minutes*60000,black:minutes*60000};L.increment=increment*1000;L.stamp=Date.now();
    $('#liveTrace').hidden=true;L.trace=null;
    $('#liveBest').textContent='';$('#liveComment').textContent='辅助可在对局中随时开启。';
  }catch(e){toast(e.message,true);}finally{L.busy=false;paintLive();}
}
async function attemptLiveMove(from,to) {
  if(L.busy||!L.running||L.ended||!L.position) return;
  let choices=L.position.legal.filter(u=>u.startsWith(Rules.name(from)+Rules.name(to)));
  if(!choices.length){L.selected=null;paintLive();return;}
  if(choices.length>1) {
    const dialog=$('#livePromotion');dialog.returnValue='';dialog.showModal();
    const promotion=await new Promise(resolve=>dialog.addEventListener('close',()=>resolve(dialog.returnValue),{once:true}));
    choices=choices.filter(u=>u[4]===promotion);if(!choices.length)return;
  }
  settleClock();if(L.ended)return;
  // The analysis options are frozen at the moment the player commits this move.
  const moveOptions=structuredClone(L.options),side=L.position.turn,now=Date.now();
  L.busy=true;paintClock();cancelLiveAnalysis();
  try {
    const moves=[...L.moves,choices[0]],position=await livePost('/api/live/position',{moves});
    L.moves=moves;L.position=position;L.time[side]+=L.increment;L.stamp=now;
    L.selected=null;
    if(position.result!=='*'){L.ended=`${position.result} · ${position.termination}`;L.running=false;}
    const latest=L.options;L.options=moveOptions;analyzeLive();L.options=latest;
  }catch(e){toast(e.message,true);}finally{L.busy=false;settleClock();paintLive();}
}
let drag=null;
$('#liveBoard').addEventListener('pointerdown',e=>{
  const square=e.target.closest('[data-sq]');if(!square||!L.running||L.busy||L.ended)return;
  const sq=+square.dataset.sq,pos=Rules.parseFEN(L.position.fen),p=pos.b[sq];
  const own=p && Rules.isW(p)===(pos.turn==='w');
  if(L.selected!==null && !own){attemptLiveMove(L.selected,sq);return;}
  if(!own)return;
  L.selected=sq;paintLive();
  drag={from:sq,x:e.clientX,y:e.clientY,ghost:null};e.preventDefault();
});
document.addEventListener('pointermove',e=>{
  if(!drag)return;
  if(!drag.ghost && Math.hypot(e.clientX-drag.x,e.clientY-drag.y)>6) {
    drag.ghost=document.createElement('div');drag.ghost.className='live-ghost';
    drag.ghost.innerHTML=$(`[data-sq="${drag.from}"] svg`,$('#liveBoard')).outerHTML;document.body.appendChild(drag.ghost);
  }
  if(drag.ghost){drag.ghost.style.left=e.clientX-35+'px';drag.ghost.style.top=e.clientY-35+'px';}
});
document.addEventListener('pointerup',e=>{
  if(!drag)return;const d=drag;drag=null;d.ghost?.remove();
  const target=document.elementFromPoint(e.clientX,e.clientY)?.closest('#liveBoard [data-sq]');
  if(target && +target.dataset.sq!==d.from)attemptLiveMove(d.from,+target.dataset.sq);
});
document.addEventListener('pointercancel',()=>{drag?.ghost?.remove();drag=null;});
function saveLiveOptions(toggle) {
  const base=L.options.engine || engineDefaults || {threads:1,hash_mb:256,depth:20};
  L.options={stockfish:$('#liveSF').checked,deepseek:$('#liveDS').checked,deepseek_depth:$('#liveDepth').value,
    engine:{...base,mode:'adaptive',effort:$('#liveEffort').value}};
  const snapshot=structuredClone(L.options);
  $('#liveSettingsStatus').textContent='保存中…';
  liveSave=liveSave.then(async()=>{
    const r=await fetch(API+'/api/settings/tutor/live',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(snapshot)});
    if(!r.ok)throw Error('对局设置保存失败');$('#liveSettingsStatus').textContent='已保存 · 参数下一步生效；开关即时生效';
  }).catch(e=>{$('#liveSettingsStatus').textContent=e.message;});
  if(toggle)analyzeLive();
}
for(const id of ['liveSF','liveDS','liveEffort','liveDepth'])$('#'+id).onchange=()=>saveLiveOptions(id==='liveSF'||id==='liveDS');
$('#openLive').onclick=async()=>{
  await liveReady;stopPlaying();S.abort?.abort();L.active=true;
  $('#landing').hidden=true;$('#viewer').hidden=true;$('#live').hidden=false;$('#newGame').hidden=true;
  if(!L.position)await resetLive();else paintLive();
};
$('#liveBack').onclick=()=>{settleClock();L.running=false;L.active=false;cancelLiveAnalysis();$('#live').hidden=true;$('#landing').hidden=false;};
$('#livePause').onclick=()=>{settleClock();if(!L.ended){L.running=!L.running;L.stamp=Date.now();}paintClock();};
$('#liveReset').onclick=()=>{
  if(L.busy)return;
  if(L.moves.length && !confirm('开始新对局会清空当前棋局。继续？'))return;
  resetLive();
};
$('#liveFlip').onclick=()=>{L.flipped=!L.flipped;paintLive();};
$('#liveRetry').onclick=()=>analyzeLive();
$('#liveExplain').onclick=()=>analyzeLive(true);
$('#liveDraw').onclick=()=>{if(L.position?.can_claim_draw){settleClock();L.ended='1/2-1/2 · 申请和棋';L.running=false;cancelLiveAnalysis();paintClock();}};
$('#liveExport').onclick=()=>{
  if(!L.position)return;
  const result=L.ended?.startsWith('1/2-1/2')?'1/2-1/2':L.ended?.includes('超时')?(L.time.white===0?'0-1':'1-0'):L.position.result;
  let pgn=L.position.pgn.replace(/\[Result "[^"]*"\]/,`[Result "${result}"]`).replace(/(?:\*|1-0|0-1|1\/2-1\/2)\s*$/,result);
  const url=URL.createObjectURL(new Blob([pgn],{type:'application/x-chess-pgn'}));
  const a=document.createElement('a');a.href=url;a.download='chess-tutor-live.pgn';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
};
