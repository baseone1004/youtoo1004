const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('화면/app.js','utf8');
const fn=source.slice(source.indexOf('async function openChannelWindow('),source.indexOf('async function selectReferenceAccount('));
(async()=>{
  for(const allowed of [true,false]){
    let destination='',refreshed=false;
    const link={classList:{remove:()=>{link.visible=true;}}};
    const popup={location:{replace:url=>destination=url},close:()=>{}};
    const context={window:{open:()=>allowed?popup:null},api:async(path,body)=>{assert.equal(path,'/api/workspaces/open');assert.equal(body.id,'ja');return {url:'http://127.0.0.1:18802/'};},$:()=>link,toast:()=>{},refreshParallelChannels:async()=>{refreshed=true;}};
    vm.createContext(context);vm.runInContext(fn,context);
    const button={disabled:false,textContent:'일본 채널 · 별도 창 열기'};
    await context.openChannelWindow('ja',button);
    assert.equal(button.disabled,false);
    assert(refreshed && link.visible);
    assert.equal(link.href,'http://127.0.0.1:18802/');
    assert.equal(destination,allowed?link.href:'');
  }
  console.log('Parallel channel UI checks passed (popup allowed/blocked)');
})().catch(e=>{console.error(e);process.exitCode=1;});
