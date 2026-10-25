const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('webapp/static/index.html','utf8');
const nodes=new Map();
const $=id=>{if(!nodes.has(id))nodes.set(id,{value:'0',style:{setProperty(){}},closest(){return $('depthLabel')}});return nodes.get(id)};
const context={$};vm.createContext(context);
vm.runInContext(html.slice(html.indexOf('let engineDefaults ='),html.indexOf('const engineReady =')),context);
const limits={threads:{min:1,max:20,step:1},hash_mb:{min:256,max:32768,step:256},depth:{min:1,max:40,step:1},time_limit_s:{min:0.1,max:60,step:0.1},effort:{min:0,max:5,step:1}};
for(const mode of ['manual','adaptive','stockfish']){
 context.applyEngineControls({engine:{mode,threads:10,hash_mb:10240,depth:21,effort:'high',time_limit_s:0.1},limits,effort_policies:Array(6).fill({max_time_s:5,min_nodes:250000,min_depth:12,stable_iterations:3,score_tolerance_cp:18})});
 assert.equal($('#timeLimitGroup').hidden,mode!=='stockfish');
 assert.equal($('#effortGroup').hidden,mode!=='adaptive');
 assert.equal($('depthLabel').hidden,mode!=='manual');
 assert.equal($('#timeLimitValue').textContent,'0.1 秒');
 assert.equal($('#engineTimeLimit').min,0.1);
 assert.equal($('#engineTimeLimit').max,60);
}
console.log('PASS: third mode, slider range and units, independent mode controls');
