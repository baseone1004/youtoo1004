const fs = require('fs'), vm = require('vm'), assert = require('assert');
const code = fs.readFileSync('화면/app.js', 'utf8');
const fn = code.slice(code.indexOf('async function prepareVideoEditor()'), code.indexOf("window.addEventListener('message'"));
async function check(ready) {
  const nodes = {frVideo:{dataset:{},src:'about:blank'},videoStatus:{},galFile:{value:''}};
  const ctx = {$: id => nodes[id], api:async () => ({ready,url:'http://127.0.0.1:8767/'}), toast:()=>{}};
  vm.createContext(ctx); vm.runInContext(fn,ctx); await ctx.prepareVideoEditor();
  if (ready) assert(nodes.frVideo.src.startsWith('http://127.0.0.1:8767/?settings='));
  else { assert.equal(nodes.frVideo.src,'about:blank'); assert(nodes.videoStatus.textContent.includes('연결 실패')); }
}
(async () => {await check(true); await check(false); console.log('Editor frame checks passed (2 cases)');})().catch(e=>{console.error(e);process.exitCode=1;});
