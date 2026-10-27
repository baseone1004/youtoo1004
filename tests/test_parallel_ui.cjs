const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('화면/app.js','utf8');
const start=source.indexOf('function setChannel(ch)');
const fn=source.slice(start,source.indexOf('async function saveQuickLanguage()',start));
const nodes={chHint:{},genreChips:{classList:{toggle(){}}},customTitle:{},quickLanguage:{}};
const context={channel:'mindam',STATE:{
  profiles:{person:{이름:'心のしくみ便り',언어:'ja',영상_길이:'20~30분',유형:'정보형'},mindam:{이름:'설루온',언어:'ko',영상_길이:'1~2시간',유형:'이야기형'}},
  channels:{person:{},mindam:{}},
},document:{querySelectorAll:()=>[]},$:id=>nodes[id],loadAnalysis(){},renderChannels(){},renderTopics(){},renderChannelAccountSelectors(){}};
vm.createContext(context);vm.runInContext(fn,context);
context.setChannel('person');
assert.equal(context.channel,'person');
assert.equal(nodes.quickLanguage.value,'ja');
assert(nodes.chHint.textContent.includes('心のしくみ便り'));
context.setChannel('mindam');
assert.equal(context.channel,'mindam');
assert.equal(nodes.quickLanguage.value,'ko');
assert(nodes.chHint.textContent.includes('설루온'));
assert(!source.includes('async function openChannelWindow('));
console.log('Single-app channel selection checks passed (Japanese psychology and Korean folk tale)');
