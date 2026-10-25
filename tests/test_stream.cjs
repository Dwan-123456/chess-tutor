const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('webapp/static/index.html', 'utf8');
const code = html.slice(html.indexOf('async function stream()'), html.indexOf('\nfunction onEvent(ev)'));
function setup(fetch) {
  const game = {raw: '1. e4 *'};
  const button = {};
  const state = {game, ev: {}, cm: {}};
  const events = [];
  const context = {S: state, API: '', AbortController, TextDecoder, TypeError, fetch,
    $: () => button, renderGameHead() {}, renderPanel() {}, renderMoveMarks() {}, tick() {},
    onEvent(event) { events.push(event); if (event.type === 'done') state.done = event; }};
  vm.createContext(context); vm.runInContext(code, context);
  return {context, state, events, button};
}
function response(chunks, failure) {
  return {ok: true, body: {getReader() {return {async read() {
    if (chunks.length) return {value: new TextEncoder().encode(chunks.shift()), done: false};
    if (failure) throw new TypeError('Error in input stream');
    return {done: true};
  }}}}};
}
(async () => {
  for (const failure of [false, true]) {
    const test = setup(async () => response(['{"type":"meta"}\n'], failure));
    await test.context.stream();
    assert.match(test.state.streamErr, /连接已中断/);
    assert.equal(test.state.streaming, false);
    assert.equal(test.button.hidden, false);
  }
  const complete = setup(async () => response(['{"type":"done"}']));
  await complete.context.stream();
  assert.equal(complete.state.streamErr, null);
  assert.equal(complete.events.length, 1);
  let finishOld;
  let calls = 0;
  const race = setup(async () => ++calls === 1 ? new Promise(resolve => finishOld = resolve) : response(['{"type":"done"}\n']));
  const old = race.context.stream();
  await race.context.stream();
  finishOld(response(['{"type":"error","message":"stale"}\n']));
  await old;
  assert.equal(race.state.streamErr, null);
  assert.equal(race.events.length, 1);
  console.log('PASS: dropped stream, premature EOF, final buffered event, superseded request');
})().catch(error => {console.error(error); process.exitCode = 1;});
