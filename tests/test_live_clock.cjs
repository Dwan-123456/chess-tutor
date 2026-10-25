const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('webapp/static/live.js','utf8');
const L = {running:true,position:{turn:'white'},ended:null,busy:false,time:{white:10000,black:10000},stamp:1000};
let cancelled = 0;
const context={L,Date,cancelLiveAnalysis(){cancelled++;}};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function settleClock('),source.indexOf('function paintClock(')),context);
context.settleClock(3500);
assert.equal(L.time.white,7500); assert.equal(L.time.black,10000);
L.running=false;context.settleClock(8000);assert.equal(L.time.white,7500);
L.running=true;L.position.turn='black';context.settleClock(9500);
assert.equal(L.time.black,8500);assert.equal(L.time.white,7500);
// Background suspension counts wall time; it never relies on timer tick counts.
context.settleClock(30000);assert.equal(L.time.black,0);assert.equal(L.running,false);
assert.match(L.ended,/黑方超时/);assert.equal(cancelled,1);
assert.equal(context.clockText(59999),'01:00');assert.equal(context.clockText(0),'00:00');
// Position requests do not consume either side's time until the committed turn is installed.
L.busy=true;L.stamp=30000;context.settleClock(31000);assert.equal(L.stamp,30000);
assert.match(source,/\.live-square::before\{content:attr\(data-coordinate\)/);
assert.doesNotMatch(source,/<small>\$\{Rules.name\(sq\)\}/);
console.log('PASS: active clock, pause/resume, side switching, background elapsed time, timeout, nonselectable coordinates');
