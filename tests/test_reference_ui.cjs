const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const source = fs.readFileSync('화면/app.js', 'utf8');
const functions = source.slice(source.indexOf('async function genBodyFromUI()'), source.indexOf('async function galStart()')) + source.slice(source.indexOf('function referencePathFor('), source.indexOf('function renderReference()'));
(async () => {
  const selection = {value: 'script.txt'};
  const context = {galDir:'images', galPromptsPath:'prompts.txt', styleValue:'test', STATE:{style_prefixes:{}, profiles:{person:{마스코트:{이미지:'person.png', 레퍼런스_사용:true}}, mindam:{마스코트:{이미지:'story.png', 레퍼런스_사용:true}}}}, $: () => selection, get8765:async()=>({kie_key_saved:true})};
  vm.createContext(context); vm.runInContext(functions, context);
  assert.equal((await context.genBodyFromUI()).reference_image, 'person.png');
  assert.equal((await context.genBodyFromUI()).reference_model, 'bytedance/seedream-v4-edit');
  context.STATE.profiles.person.마스코트.생성_모델='seedream/4.5-edit';
  assert.equal((await context.genBodyFromUI()).reference_model, 'seedream/4.5-edit');
  selection.value='folder/final.txt';
  assert.equal((await context.genBodyFromUI()).reference_image, 'story.png');
  context.STATE.profiles.mindam.마스코트.레퍼런스_사용=false;
  assert.equal((await context.genBodyFromUI()).reference_image, '');
  console.log('Reference UI checks passed (5 cases)');
})().catch(e => { console.error(e); process.exitCode=1; });
