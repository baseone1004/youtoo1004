const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const source = fs.readFileSync('화면/app.js', 'utf8');
const start = source.indexOf('function accountSlot(account)');
const end = source.indexOf('async function selectReferenceAccount', start);
const elements = {productionAccount: {}, referenceAccount: {}};
const context = {
  channel: 'person',
  savedYtAccounts: [{id:'korean', name:'마음등불심리노트', slot:'person',
    selected_slots:['person'], language:'ko'}],
  STATE: {dedicated:false, queue:{status:'paused'}},
  $: id => elements[id],
  esc: value => String(value),
};
vm.createContext(context);
vm.runInContext(source.slice(start, end), context);
context.renderChannelAccountSelectors();
assert.equal(elements.productionAccount.disabled, true);
assert.equal(elements.referenceAccount.disabled, true);
context.STATE.queue.status = 'idle';
context.renderChannelAccountSelectors();
assert.equal(elements.productionAccount.disabled, false);
assert.equal(elements.referenceAccount.disabled, false);
console.log('Channel selector locks while a queue is running or paused');
