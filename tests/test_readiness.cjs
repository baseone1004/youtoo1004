const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const source = fs.readFileSync('화면/app.js', 'utf8');
const fn = source.slice(source.indexOf('async function checkReady()'), source.indexOf('// ── 상태 불러오기'));
async function check(channels, config, credit) {
  const nodes = {};
  const ctx = { STATE: { config }, selection: new Map(channels.map((ch, i) => [i, {channel: ch}])), channel: 'mindam',
    api: async () => credit, get8765: async () => ({kie_key_saved: true, image_model: 'z-image'}),
    $: id => nodes[id] || (nodes[id] = {}), esc: x => x };
  vm.createContext(ctx); vm.runInContext(fn, ctx);
  return await ctx.checkReady();
}
(async () => {
  const cfg = {AI:'test', 키있음:true, 인월드키있음:true, 인월드_목소리_민담:'story'};
  assert.equal(await check(['mindam'], cfg, {ok:true,credit:1}), true);
  assert.equal(await check(['person','mindam'], cfg, {ok:true,credit:1}), false);
  assert.equal(await check(['mindam'], cfg, {ok:false,credit:null}), false);
  assert.equal(await check(['mindam'], cfg, {ok:true,credit:0}), false);
  assert.equal(await check(['mindam'], {...cfg, 인월드_목소리:'common'}, {ok:true,credit:0.1}), true);
  console.log('Readiness checks passed (5 cases)');
})().catch(e => { console.error(e); process.exitCode = 1; });
