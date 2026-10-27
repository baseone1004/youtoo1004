const fs = require('fs'), vm = require('vm'), assert = require('assert');
const code = fs.readFileSync('화면/app.js', 'utf8');
const fn = code.slice(code.indexOf('async function prepareVideoEditor()'), code.indexOf("window.addEventListener('message'"));
async function check(ready) {
  const nodes = {frVideo:{dataset:{},style:{},src:'about:blank'},videoStatus:{},galFile:{value:''}};
  const ctx = {$: id => nodes[id], api:async () => ({ready,url:'http://127.0.0.1:8767/'}), toast:()=>{}};
  vm.createContext(ctx); vm.runInContext(fn,ctx); await ctx.prepareVideoEditor();
  if (ready) {
    assert(nodes.frVideo.src.startsWith('http://127.0.0.1:8767/?settings='));
    assert.equal(nodes.frVideo.style.display, '');
  } else {
    assert.equal(nodes.frVideo.src,'about:blank');
    assert.equal(nodes.frVideo.style.display, 'none');
    assert(nodes.videoStatus.textContent.includes('내장 FFmpeg 렌더링 사용'));
  }
}
(async () => {await check(true); await check(false); console.log('Editor frame checks passed (external editor and built-in renderer)');})().catch(e=>{console.error(e);process.exitCode=1;});
