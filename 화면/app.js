// 유튜브 영상 자동 제작 — 화면 동작 (백엔드: 대본선택.py 8766, 편집프로그램 8765)
'use strict';
const $ = id => document.getElementById(id);
const EDITOR = 'http://127.0.0.1:8765';
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const js = s => String(s ?? '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
const pad3 = n => String(n).padStart(3, '0');

let STATE = null, WORK = null, pollTimer = null;
let channel = 'person';                       // 1단계 채널 탭
const selection = new Map();                  // key "channel|title" → {channel,title,topic}
const custom = {person: [], mindam: []};      // 직접 적은 주제
let toolChannel = 'person', chosenVar = null, varRef = '', bench = null;
let galDir = '', galPromptsPath = '', galKey = '', galBusy = false, galPrompts = {path: '', count: 0};
let kieJobId = sessionStorage.getItem('kieJobId') || '', kieTimer = null;
let guideEditing = '';

// ── 공통 ─────────────────────────────────────────────
async function api(p, body, method) {
  const r = await fetch(p, {method: method || (body ? 'POST' : 'GET'), headers: {'Content-Type': 'application/json'}, body: body ? JSON.stringify(body) : undefined});
  const j = await r.json().catch(() => ({}));
  if (r.status === 404 && p.startsWith('/api/')) { $('restartBtn').classList.remove('hidden'); throw new Error('프로그램이 예전 코드로 실행 중입니다. 위의 [🔁 다시 시작]을 눌러 주세요.'); }
  if (!r.ok) throw new Error(j.detail || r.statusText);
  return j;
}
// 코드가 바뀌었는지 확인해 [다시 시작] 버튼을 보여 준다
async function checkVersion() {
  try { const v = await api('/api/version'); $('restartBtn').classList.toggle('hidden', v.version === v.current); } catch (e) {}
}
async function restartProgram() {
  if (STATE && STATE.job && STATE.job.status === 'running') return toast('진행 중인 작업이 끝난 뒤 다시 시작하세요.', true);
  if (!confirm('프로그램을 다시 시작할까요? (5초 정도 걸립니다)')) return;
  try { await api('/api/restart', {}); } catch (e) { return toast(e.message, true); }
  toast('다시 시작하는 중…');
  await new Promise(r => setTimeout(r, 2500));
  for (let i = 0; i < 30; i++) { try { const r = await fetch('/api/version', {cache: 'no-store'}); if (r.ok) { location.reload(); return; } } catch (e) {} await new Promise(r => setTimeout(r, 1000)); }
  toast('다시 시작이 확인되지 않습니다. 유튜브_자동화_시작 파일로 실행해 주세요.', true);
}
async function post8765(path, body) {
  const r = await fetch(EDITOR + path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body || {})});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || r.statusText);
  return j;
}
const get8765 = path => fetch(EDITOR + path).then(r => r.json());
// 이미지 목록·파일은 이 서버(8766)가 직접 읽는다 → 편집프로그램이 바쁘거나 꺼져 있어도 다운로드된 그림이 바로 보인다.
const listImages = dir => api('/api/images?dir=' + encodeURIComponent(dir)).then(j => j.images || []);
const imageUrl = it => `/api/image?path=${encodeURIComponent(it.path)}&t=${it.mtime}`;
const IDLE = {status: 'idle', current: 0, total: 0, failed: []};
const norm = p => String(p || '').replace(/\//g, '\\').replace(/\\+$/, '').toLowerCase();
// 편집프로그램의 생성 상태 + 지금 어느 폴더에 만드는지 (다른 폴더의 상태를 이 갤러리에 잘못 표시하지 않도록)
async function genStatus() {
  try { const [st, info] = await Promise.all([get8765('/api/gen/status'), get8765('/api/info')]); const gen = (info.config || {}).gen || {}; return {...st, output_dir: gen.output_dir || '', prompts_file: gen.prompts_file || ''}; }
  catch (e) { return {...IDLE, output_dir: ''}; }
}
// 대본 파일 → 이미지 폴더 (서버 assets_dir 규칙과 동일)
const imagesDirOf = f => (f.endsWith('final.txt') ? f.slice(0, f.lastIndexOf('\\')) : f.replace(/\.txt$/, '') + '_자료') + '\\images';
function toast(msg, err) {
  const t = document.createElement('div'); t.className = 'toast' + (err ? ' err' : ''); t.textContent = msg;
  $('toastBox').appendChild(t); setTimeout(() => t.remove(), err ? 6000 : 3500);
}
async function openPath(p) { if (!p) return toast('열 폴더가 없습니다.', true); await api('/api/open', {path: p}); }
async function showFile(p) { const j = await api('/api/file?path=' + encodeURIComponent(p)); const v = $('pgPreview'); v.textContent = j.text; v.classList.remove('hidden'); }
function showBig(src) { const lb = document.createElement('div'); lb.className = 'lightbox'; lb.innerHTML = `<img src="${src}">`; lb.onclick = () => lb.remove(); document.body.appendChild(lb); }
function fill(sel, items, val) { sel.innerHTML = items.map(x => `<option value="${esc(x)}">${esc(x)}</option>`).join(''); if (val && items.includes(val)) sel.value = val; }
async function exitProgram() {
  if (!confirm('진행 중인 작업을 멈추고 프로그램을 종료할까요?')) return;
  try { await api('/api/shutdown', {}); document.body.innerHTML = '<main style="max-width:560px;margin:14vh auto;padding:36px;font:18px/1.7 sans-serif;text-align:center"><h1>프로그램을 종료했습니다</h1><p>이 창은 닫아도 됩니다. 다시 쓰려면 유튜브_자동화_시작 파일을 실행하세요.</p></main>'; }
  catch (e) { toast('종료 요청 실패: ' + e.message, true); }
}

// ── 화면 전환 ──────────────────────────────────────────
let currentStep = 1;
function showView(name) {
  if (name === 'advanced') name = 'wizard';
  for (const v of ['wizard', 'settings', 'onboard']) $('view-' + v).classList.toggle('hidden', v !== name);
  document.querySelectorAll('.top-nav button[data-view]').forEach(b => b.classList.toggle('on', b.dataset.view === name));
  if (name === 'settings') { loadEditorSettings(); loadAnalysis(); window.scrollTo({top: 0, behavior: 'smooth'}); }
  if (name === 'onboard') { renderOnboard(); window.scrollTo({top: 0, behavior: 'smooth'}); }
}
// 한 페이지에 모두 펼쳐 두고, 단계 표시줄은 해당 위치로 스크롤한다.
function goStep(n, instant) {
  currentStep = n; renderStepBar();
  if (n === 2) renderSelection();
  if (n === 4 && $('workFile').value && !WORK) loadWorkspace(false);
  showView('wizard');
  if (instant) return;
  const el = $('step-' + n); if (el) setTimeout(() => el.scrollIntoView({behavior: 'smooth', block: 'start'}), 30);
}
// 화면에 보이는 단계에 맞춰 표시줄을 자동으로 갱신한다.
const stepWatcher = new IntersectionObserver(entries => {
  const visible = entries.filter(e => e.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
  if (visible) { currentStep = +visible.target.id.split('-')[1]; renderStepBar(); }
}, {rootMargin: '-35% 0px -55% 0px', threshold: [0, 0.2, 0.5, 1]});
for (let i = 1; i <= 4; i++) if ($('step-' + i)) stepWatcher.observe($('step-' + i));
let galleryVisible = false, videoLoaded = false;
new IntersectionObserver(entries => { galleryVisible = entries[0].isIntersecting; if (galleryVisible) refreshGallery(true); }, {threshold: 0.02}).observe($('galleryBlock'));
$('adv-video').addEventListener('toggle', () => { if ($('adv-video').open && !videoLoaded) { videoLoaded = true; prepareVideoEditor(); } });
function renderStepBar() {
  if (!$('stepBar')) return;
  const busy = STATE && ((STATE.job && STATE.job.status === 'running') || (STATE.queue && STATE.queue.status === 'running'));
  document.querySelectorAll('#stepBar li').forEach(li => {
    const n = +li.dataset.step;
    li.classList.toggle('on', n === currentStep);
    li.classList.toggle('done', n < currentStep && !(n === 3 && busy));
    li.classList.toggle('busy', n === 3 && busy && currentStep !== 3);
  });
}
function goAdvanced(name) {
  if (name === 'gallery') return goStep(3);
  showView('wizard');
  if (name === 'video') { videoLoaded = true; prepareVideoEditor(); }
  if (name === 'gallery') refreshGallery(true);
  setTimeout(() => $('adv-' + name).scrollIntoView({behavior: 'smooth', block: 'start'}), 30);
}

// ── 준비 상태 ─────────────────────────────────────────
async function checkReady() {
  const items = [];
  try {
    const info = STATE || await api('/api/state');
    const v = info.config.인월드키있음 && (info.config.인월드_목소리_사람 || info.config.인월드_목소리);
    if (info.config.AI === 'deepseek-web') items.push([!!info.web_alive, info.web_alive ? '딥시크 웹 연결' : '딥시크 창 안 보임', 'settings']);
    else items.push([!!info.config.키있음, info.config.키있음 ? info.config.AI + ' 키' : info.config.AI + ' 키 없음', 'settings']);
    items.push([!!v, v ? '나레이션 목소리' : '나레이션 목소리 없음', 'settings']);
  } catch (e) {}
  try {
    const j = await get8765('/api/info'); const xy = ((j.config || {}).gen_ui || {}).XY || {}; const ok = !!(xy.prompt && xy.download);
    items.push([ok, ok ? '드롭샷 좌표' : '드롭샷 좌표 없음', 'settings']);
    items.push([true, '편집프로그램 연결', null]);
  } catch (e) { items.push([false, '편집프로그램 꺼짐 — 시작 파일을 다시 실행하세요', null]); }
  const html = '<span class="lbl">준비 상태</span>' + items.map(([ok, label, tab]) => `<span class="pill ${ok ? 'ok' : 'bad'}" ${(!ok && tab) ? `onclick="showView('${tab}')"` : ''}>${ok ? '✓' : '!'} ${esc(label)}${(!ok && tab) ? ' → 고치기' : ''}</span>`).join('');
  $('readyBar').innerHTML = html; $('setupReady').innerHTML = html;
}

// ── 상태 불러오기 ──────────────────────────────────────
async function refresh() {
  STATE = await api('/api/state');
  const c = STATE.config, g = STATE.guidelines;
  // 1단계
  renderTopics();
  // 2단계
  renderProfileNames();
  fill($('optGuide'), g.script, (STATE.profiles.person.지침 || {}).대본 || '정보형_대본지침.txt');
  fill($('optImgGuide'), g.image, (STATE.profiles[channel] && STATE.profiles[channel].지침 || {}).이미지 || '이미지지침_정보형.txt');
  $('optChunk').value = c.프롬프트_묶음 || 30; $('optHook').value = c.후킹_장면수 ?? 7;
  const lenOpts = Object.entries(STATE.lengths).map(([k, v]) => `<option value="${k}" ${k === '2' ? 'selected' : ''}>${esc(v)}</option>`).join('');
  if (!$('mindamLen').options.length) { $('mindamLen').innerHTML = lenOpts; $('toolLen').innerHTML = lenOpts; }
  renderStyles(STATE.styles, c.화풍 || '실사');
  renderLenButtons(c.분당_글자수 || 270, c.대본_글자수);
  const scriptOpts = STATE.scripts.map(s => `<option value="${esc(s.path)}">${esc(s.name)}</option>`).join('');
  const keep = (sel, html, empty) => { const old = sel.value; sel.innerHTML = html || `<option value="">${empty}</option>`; if (old && [...sel.options].some(o => o.value === old)) sel.value = old; };
  keep($('contFile'), scriptOpts, '대본 없음'); keep($('toolFile'), scriptOpts, '대본 없음');
  // 4단계
  const prevWork = $('workFile').value || localStorage.getItem('workScript') || '';
  $('workFile').innerHTML = scriptOpts || '<option value="">완성된 대본 없음</option>';
  if (STATE.scripts.some(s => s.path === prevWork)) $('workFile').value = prevWork;
  if ($('workFile').value && (!WORK || WORK.script_file !== $('workFile').value)) { $('galFile').value = $('workFile').value; loadWorkspace(false); }
  // 고급
  const prevGal = $('galFile').value || localStorage.getItem('selectedScript') || $('workFile').value || '';
  $('galFile').innerHTML = scriptOpts || '<option value="">대본 없음</option>';
  if (STATE.scripts.some(s => s.path === prevGal)) $('galFile').value = prevGal;
  keep($('resetFile'), (STATE.reset_items || []).map(x => `<option value="${esc(x.id)}">${esc(x.kind === 'mindam' ? '민담 · ' : '대본 · ')}${esc(x.label)}</option>`).join(''), '초기화할 작업 없음');
  fill($('toolMGuide'), g.mindam, '01_기획_지침.txt');
  const guides = [...g.script, ...g.image, ...g.mindam.map(x => '민담/' + x)];
  keep($('guideSel'), guides.map(x => `<option value="${esc(x)}">${esc(x)}</option>`).join(''), '지침 없음');
  // 설정
  $('sAI').value = c.AI; $('sModel').value = c.모델 || '';
  const isWeb = c.AI === 'deepseek-web';
  $('aiStat').textContent = isWeb ? (STATE.web_alive ? '웹 연결됨' : '웹 연결 안 됨') : (c.키있음 ? '키 저장됨' : '키 없음');
  $('aiStat').className = 'stat ' + ((isWeb ? STATE.web_alive : c.키있음) ? 'ok' : 'bad');
  $('sStatus').textContent = isWeb ? '지금은 딥시크 웹(무료, 키 불필요)을 씁니다.' : (c.키있음 ? `지금은 ${c.AI} · 키 저장됨` : `지금은 ${c.AI} · API 키가 없습니다. 아래에 키를 저장하세요.`);
  $('webHelp').classList.toggle('hidden', !isWeb);
  $('sWeb').textContent = isWeb ? (STATE.web_alive ? '🟢 딥시크 확장 연결됨 (chat.deepseek.com 탭 감지)' : '🔴 확장이 연결되지 않음 — 크롬에 딥시크_확장을 설치하고 chat.deepseek.com 탭을 열어 두세요') : '';
  for (const [k, v] of Object.entries(STATE.keys || {})) { const el = $('k_' + k); if (!el) continue; el.textContent = v ? '저장됨 ' + v : '없음'; el.className = 'stat ' + (v ? 'ok' : 'bad'); }
  $('sInworldModel').value = c.인월드_모델 || 'inworld-tts-1.5-max';
  $('sVoiceP').value = c.인월드_목소리_사람 || ''; $('sSpeedP').value = c.인월드_속도_사람 || 1.0;
  $('sVoiceM').value = c.인월드_목소리_민담 || ''; $('sSpeedM').value = c.인월드_속도_민담 || 1.0; $('sCpm').value = c.분당_글자수 || 270;
  $('vPerson').textContent = c.인월드_목소리_사람 ? '저장됨' : '없음'; $('vPerson').className = 'stat ' + (c.인월드_목소리_사람 ? 'ok' : 'bad');
  $('vMindam').textContent = c.인월드_목소리_민담 ? '저장됨 (따로 씀)' : '없음 → ' + pname('person') + ' 목소리 사용'; $('vMindam').className = 'stat ' + (c.인월드_목소리_민담 ? 'ok' : '');
  $('vCpm').textContent = (c.분당_글자수 || 270) + '자/분';
  $('tgTokenStat').textContent = c.텔레그램_토큰 ? '저장됨 ' + c.텔레그램_토큰 : '없음'; $('tgTokenStat').className = 'stat ' + (c.텔레그램_토큰 ? 'ok' : '');
  $('tgChatStat').textContent = c.텔레그램_채팅_ID ? '연결된 채팅: ' + c.텔레그램_채팅_ID : '연결된 채팅 없음';
  $('tgEnabled').checked = c.텔레그램_알림 !== false;
  renderChannels(STATE.channels || {}); loadTrash(); renderProfileForm();
  $('ytKeyStat').textContent = c.유튜브_API_키 ? '저장됨 ' + c.유튜브_API_키 : '없음'; $('ytKeyStat').className = 'stat ' + (c.유튜브_API_키 ? 'ok' : '');
  loadAnalysis(channel); loadBench();
  // 경고
  const warn = $('envwarn');
  if (isWeb && !STATE.web_alive) { warn.classList.remove('hidden'); warn.textContent = '딥시크 웹 확장이 연결되지 않았습니다. 크롬에서 chat.deepseek.com 탭을 열어 두거나, [설정]에서 "딥시크 웹 쓰는 법"을 보세요.'; }
  else if (!isWeb && !c.키있음) { warn.classList.remove('hidden'); warn.textContent = 'AI 키가 없습니다. [설정]에서 키를 넣거나 AI를 딥시크 웹(무료)으로 바꾸세요.'; }
  else warn.classList.add('hidden');
  refreshKieStatus();
  renderOverview(STATE.job); renderQueue(STATE.queue); renderStepBar();
  if (STATE.job && STATE.job.status === 'running') startPolling(false);
  checkReady();
}

// ── 1단계: 주제 고르기 ─────────────────────────────────
function setChannel(ch) {
  channel = ch;
  document.querySelectorAll('#chSeg button').forEach(b => b.classList.toggle('on', b.dataset.ch === ch));
  const pf = (STATE && STATE.profiles && STATE.profiles[ch]) || {};
  $('chHint').textContent = `${pf.이름 || ''} · ${pf.영상_길이 || ''} · ${pf.유형 || ''}`;
  $('genreChips').classList.toggle('hidden', ch !== 'mindam');
  if (STATE) { renderChannels(STATE.channels || {}); loadAnalysis(ch); }
  $('customTitle').placeholder = ch === 'mindam' ? '예) 장터에서 아기를 백 냥에 사온 과부, 그 아이의 정체는' : '예) 나이 들수록 친구가 줄어드는 진짜 이유';
  renderTopics();
}
function topicSource(ch) {
  const t = (STATE && STATE.topics) || {};
  const list = ch === 'mindam' ? (t.mindam || []) : [...(t.plan || []), ...(t.candidates || [])];
  return [...custom[ch].map(title => ({제목: title, _custom: true})), ...list];
}
function renderTopics() {
  if (!STATE) return;
  const list = topicSource(channel);
  const rows = list.map((t, i) => {
    const key = channel + '|' + t.제목, on = selection.has(key);
    const near = t.비슷한_내_영상 ? ` · ⚠ 내 채널의 "${t.비슷한_내_영상}"과 비슷함` : '';
    if (!t._custom && t.생성) { const key = channel + '|' + t.제목, on = selection.has(key);
      return `<li class="${on ? 'sel' : ''}" onclick="toggleTopic('${channel}',${i},event)"><input type="checkbox" ${on ? 'checked' : ''} tabindex="-1"><span class="t">${esc(t.제목)}<span class="m">${esc([t.카테고리 || t.장르, t.한줄].filter(Boolean).join(' · '))}${near}</span></span><span class="tag" style="color:#F5B942;background:#3A2E12">AI 추천</span></li>`; }
    const meta = (t._custom ? '' : channel === 'mindam' ? `${t.장르 || ''} · ${t.채널 || ''} · 조회수 ${((t.조회수 || 0) / 10000).toFixed(1)}만 (평소의 ${t.배수}배)` : (t.카테고리 || '') + (t.날짜 ? ` · ${t.날짜} 계획` : ' · 추천 후보')) + near;
    return `<li class="${on ? 'sel' : ''}" onclick="toggleTopic('${channel}',${i},event)"><input type="checkbox" ${on ? 'checked' : ''} tabindex="-1"><span class="t">${esc(t.제목)}${meta ? `<span class="m">${esc(meta)}</span>` : ''}</span>${t._custom ? `<span class="tag custom">직접 입력</span><button class="x" onclick="removeCustom('${channel}',${i},event)" title="목록에서 지우기">×</button>` : '<span class="tag">추천</span>'}</li>`;
  });
  $('topicList').innerHTML = rows.join('') || `<li class="empty">추천 주제가 아직 없습니다. ${channel === 'mindam' ? '민담_주제뽑기.bat' : '실행.bat'} 으로 주제를 먼저 뽑거나, 아래에 직접 적어 추가하세요.</li>`;
  renderSelection();
}
function toggleTopic(ch, i, ev) {
  if (ev && ev.target.tagName === 'BUTTON') return;
  const t = topicSource(ch)[i]; if (!t) return;
  const key = ch + '|' + t.제목;
  if (selection.has(key)) selection.delete(key); else selection.set(key, {channel: ch, title: t.제목, topic: t._custom ? null : t});
  renderTopics();
}
function selectAllVisible(on) {
  for (const t of topicSource(channel)) { const key = channel + '|' + t.제목; if (on) selection.set(key, {channel, title: t.제목, topic: t._custom ? null : t}); else selection.delete(key); }
  renderTopics();
}
async function addCustomTopic(silent) {
  const lines = $('customTitle').value.split(/\n/).map(x => x.trim()).filter(Boolean);
  if (!lines.length) { if (!silent) toast('주제를 먼저 적어 주세요.', true); return 0; }
  let added = 0;
  for (const title of lines) {
    try {
      const r = await api('/api/channel/check', {channel, title});
      if (r.status === 'dup' && !confirm(`내 채널에 이미 비슷한 영상이 있습니다:\n「${r.near}」\n그래도 추가할까요?`)) continue;
      if (r.status === 'similar') toast(`참고: 내 채널의 「${r.near}」과 조금 비슷합니다.`);
    } catch (e) {}
    if (!custom[channel].includes(title)) custom[channel].unshift(title);
    selection.set(channel + '|' + title, {channel, title, topic: null}); added++;
  }
  $('customTitle').value = ''; renderTopics(); if (!silent) toast(`${added}개를 목록에 추가하고 선택했습니다.`);
  return added;
}
function removeCustom(ch, i, ev) {
  ev.stopPropagation(); const t = topicSource(ch)[i]; if (!t || !t._custom) return;
  custom[ch] = custom[ch].filter(x => x !== t.제목); selection.delete(ch + '|' + t.제목); renderTopics();
}
function removeSel(key) { selection.delete(key); renderTopics(); if (!selection.size && currentStep === 2) goStep(1); }
function renderSelection() {
  const n = selection.size, items = [...selection.entries()];
  $('selCount').textContent = n;
  $('selText').textContent = n ? `편 제작 예정 — 그림체를 확인하고 시작하세요` : '개 선택 — 위에서 주제를 체크하거나 적으세요';
  $('toStep2').disabled = !n && !$('customTitle').value.trim();
  $('orderCount').textContent = n ? `${n}편 · 위에서부터 순서대로` : '';
  $('orderList').innerHTML = items.map(([key, x], i) => `<li><span class="n">${i + 1}</span><span class="t">${esc(x.title)}</span><span class="ch">${pname(x.channel)}</span><button class="mini ghost" onclick="removeSel('${js(key)}')">빼기</button></li>`).join('') || '<li class="hint">선택한 주제가 없습니다. 1단계에서 체크하세요.</li>';
  const hasP = items.some(([, x]) => x.channel === 'person'), hasM = items.some(([, x]) => x.channel === 'mindam');
  $('personLenRow').classList.toggle('hidden', !hasP); $('mindamLenRow').classList.toggle('hidden', !hasM);
  const est = [];
  if (hasP) est.push(`${pname('person')} ${items.filter(([, x]) => x.channel === 'person').length}편 (편당 30~60분)`);
  if (hasM) est.push(`민담 ${items.filter(([, x]) => x.channel === 'mindam').length}편 (편당 1~2시간)`);
  $('startHint').textContent = est.length ? est.join(' + ') + ' 정도 걸립니다. 이미지 생성 중에는 마우스·키보드를 쓰지 마세요.' : '';
  $('startBtn').disabled = !n && !$('customTitle').value.trim();
}
// 민담 장르 칩
(function () {
  const box = $('genreChips');
  for (const g of ['권선징악', '귀신·도깨비', '해학·풍자', '사랑·비극', '역사인물', '미스터리·추리', '가족·성장']) {
    const b = document.createElement('button'); b.textContent = g; b.onclick = () => { $('customTitle').value = g; addCustomTopic(); }; box.appendChild(b);
  }
})();

// ── 2단계: 그림체·길이·시작 ─────────────────────────────
let styleValue = '실사';
function renderStyles(list, cur) {
  const desc = STATE.style_info || {}, groups = STATE.style_groups || {'그림체': list};
  $('styleBox').innerHTML = Object.entries(groups).map(([g, names]) => `<div class="stylegrp"><div class="stylegrp-t">${esc(g)}</div><div class="styles">${names.map(s => `<button type="button" data-val="${esc(s)}" onclick="pickStyle('${js(s)}')">${esc(s)}<small>${esc(desc[s] || '')}</small></button>`).join('')}</div></div>`).join('');
  pickStyle(cur, true);
}
function renderQuickStyles() {
  const names = ['애니', '파스텔', '실사']; if (!names.includes(styleValue)) names.push(styleValue);
  $('quickStyles').innerHTML = names.map(n => `<button type="button" data-val="${esc(n)}" class="${n === styleValue ? 'on' : ''}" onclick="pickStyle('${js(n)}')">${esc(n)}</button>`).join('');
}
function pickStyle(v, silent) {
  styleValue = v; document.querySelectorAll('.styles button').forEach(b => b.classList.toggle('on', b.dataset.val === v)); renderQuickStyles();
  if (!silent) { api('/api/config', {화풍: v}).catch(() => {}); toast('그림체: ' + v); }
}
let cpm = 270, targetChars = 6750;
function renderLenButtons(cpmValue, chars) {
  cpm = cpmValue; targetChars = chars || Math.round(25 * cpm);
  $('personLen').innerHTML = [20, 25, 30].map(m => `<button type="button" data-min="${m}" onclick="setLen(${m})">${m}분</button>`).join('');
  markLen();
}
function markLen() { document.querySelectorAll('#personLen button').forEach(b => b.classList.toggle('on', Math.abs(targetChars - Math.round(+b.dataset.min * cpm)) < 50)); $('personLenHint').textContent = `약 ${targetChars.toLocaleString()}자`; }
function setLen(min) { targetChars = Math.round(min * cpm); markLen(); api('/api/config', {대본_글자수: targetChars}).catch(() => {}); }
function productionOptions() {
  return {guideline: $('optGuide').value, target: targetChars, mark_used: true, length: $('mindamLen').value, img_guideline: $('optImgGuide').value, style: styleValue, chunk: +$('optChunk').value, thumb_position: 'auto',
    steps: {optimize: $('optOptimize').checked, prompts: true, tts: true, images: true, hook: +$('optHook').value, render: true, thumbnail: $('optThumb').checked}};
}
async function startProduction() {
  await addCustomTopic(true);                       // 입력칸에 적어 둔 주제도 함께
  if (!selection.size) { $('customTitle').focus(); return toast('주제를 체크하거나 적어 주세요.', true); }
  const options = productionOptions();
  if (options.steps.hook > 0 && !await refreshKieStatus()) toast('KIE 키가 없어 움직이는 영상은 건너뜁니다. 나머지는 모두 자동으로 만듭니다.');
  if (!confirm(`선택한 ${selection.size}편을 순서대로 만들까요?\n대본 → 나레이션 → 이미지 → 영상변환 → 썸네일 → 최종 영상 → 제목·설명·태그 저장까지 자동으로 하고, 끝나면 다음 편으로 넘어갑니다.`)) return;
  try {
    const q = await api('/api/queue/start', {items: [...selection.values()], options});
    selection.clear(); custom.person = []; custom.mindam = []; renderTopics(); renderQueue(q); startPolling(true); window.scrollTo({top: 0, behavior: 'smooth'}); toast('제작을 시작했습니다.');
  } catch (e) { toast(e.message, true); }
}
async function continuePipeline() {
  const file = $('contFile').value; if (!file) return toast('이어서 만들 대본을 고르세요.', true);
  const o = productionOptions(); const steps = {...o.steps, optimize: false};
  try {
    await api('/api/pipeline', {reuse_prompts: true, script_file: file, channel: file.endsWith('final.txt') ? 'mindam' : 'person', style: o.style, img_guideline: o.img_guideline, chunk: o.chunk, steps, thumb_position: 'auto'});
    goStep(3); startPolling(true);
  } catch (e) { toast(e.message, true); }
}

// ── 3단계: 진행 ─────────────────────────────────────────
const PIPE_STEPS = ['① 대본', '② 이미지 프롬프트', '③ 나레이션', '④ 이미지 생성', '⑤ 움직이는 영상', "⑤' 썸네일", '⑥ 최종 영상', '완료'];
const STEP_OUT = [['script', 'file'], ['prompts', 'file'], ['narration', 'folder'], ['images', 'folder'], ['hook', 'folder'], ['thumbnails', 'folder'], ['video', 'folder'], ['assets', 'folder']];
const KIND_LABEL = {bench: '채널 벤치마킹', script: '대본 만들기', mindam: '이야기 대본 만들기', images: '이미지 프롬프트', variations: '제목 변형', optimize: '제목·설명·태그', tts: '나레이션', pipeline: '한 편 자동 제작', queue_pipeline: '연속 제작 중', thumbnail: '썸네일'};
function startPolling(scroll) {
  clearInterval(pollTimer); $('pgResult').classList.add('hidden'); $('pgErr').classList.add('hidden'); $('pgPreview').classList.add('hidden');
  poll(); pollTimer = setInterval(poll, 1500);
  if (scroll) goStep(3);
}
function renderOverview(job) {
  const steps = [...document.querySelectorAll('#pipe div')];
  steps.forEach(x => { x.classList.remove('now', 'done'); x.querySelector('small').textContent = '대기'; });
  if (!job || job.status === 'none') return;
  const r = job.result || {}, done = {script: !!(r.script || r.file), prompt: !!r.prompts, image: !!r.images, motion: !!r.hook, audio: !!(r.narration || r.mp3), render: !!r.video};
  for (const x of steps) if (done[x.dataset.stage]) { x.classList.add('done'); x.querySelector('small').textContent = '완료'; }
  const s = String(job.stage || '');
  const current = s.includes('최종') ? 'render' : s.includes('후킹') ? 'motion' : s.includes('이미지 자동') ? 'image' : s.includes('나레이션') ? 'audio' : s.includes('프롬프트') ? 'prompt' : s.includes('대본') ? 'script' : '';
  if (job.status === 'done') steps.forEach(x => { x.classList.add('done'); x.classList.remove('now'); x.querySelector('small').textContent = '완료'; });
  else if (job.status === 'running' && current) { const x = steps.find(v => v.dataset.stage === current); if (x) { x.classList.add('now'); x.querySelector('small').textContent = '진행 중'; } }
}
function stageIndex(st) {
  st = st || ''; const m = st.match(/^([①②③④⑤⑥]'?)/); if (m) return PIPE_STEPS.findIndex(s => s.startsWith(m[1]));
  if (/최종 렌더|렌더링/.test(st)) return 6; if (/썸네일/.test(st)) return 5; if (/후킹/.test(st)) return 4; if (/이미지 생성/.test(st)) return 3;
  if (/나레이션/.test(st)) return 2; if (/프롬프트|변환/.test(st)) return 1; if (/완료/.test(st)) return 7; return 0;
}
function renderSteps(j) {
  const box = $('pgSteps'); if (!['pipeline', 'queue_pipeline'].includes(j.kind)) { box.innerHTML = ''; return; }
  let idx = stageIndex(j.stage); if (j.status === 'done') idx = PIPE_STEPS.length - 1;
  const fin = j.status === 'done', r = j.result || {};
  const cnt = (j.stage || '').match(/이미지 생성\s*(\d+)\/(\d+)/); const extra = cnt ? ` ${cnt[1]}/${cnt[2]}` : '';
  box.innerHTML = PIPE_STEPS.map((s, i) => { const [key, how] = STEP_OUT[i]; const v = r[key]; const path = Array.isArray(v) ? (v[0] || '') : (v || ''); s = s + (i === 3 ? extra : '');
    const cls = ((i < idx || fin) ? 'done' : (i === idx ? 'now' : '')) + (path ? ' has' : '');
    return `<span class="${cls}" ${path ? `onclick="openStep('${js(path)}','${how}')" title="클릭: ${how === 'file' ? '내용 보기' : '폴더 열기'}"` : ''}>${(i < idx || fin) ? '✓ ' : ''}${esc(s)}${path ? ' ↗' : ''}</span>`; }).join('');
}
function openStep(path, how) { if (how === 'file') showFile(path); else openPath(path); }
function humanStage(j) {
  const s = j.stage || '';
  const map = [[/대본/, '대본을 쓰고 있습니다'], [/프롬프트/, '장면별 이미지 설명을 만들고 있습니다'], [/나레이션/, '나레이션 음성과 자막을 만들고 있습니다'], [/이미지 자동|이미지 생성/, '드롭샷에서 이미지를 만들고 있습니다 — 마우스·키보드를 쓰지 마세요'], [/후킹/, '앞부분 움직이는 영상을 만들고 있습니다'], [/썸네일/, '썸네일을 만들고 있습니다'], [/최종|렌더/, '최종 영상을 합치고 있습니다']];
  const hit = map.find(([re]) => re.test(s));
  return (hit ? hit[1] : (s || '준비 중')) + (s ? ` (${s})` : '');
}
async function poll() {
  let j; try { j = await api('/api/job'); } catch (e) { return; }
  if (!j || j.status === 'none') { $('pgStage').textContent = '지금은 진행 중인 작업이 없습니다.'; $('cancelJob').classList.add('hidden'); $('liveDot').classList.remove('on'); return; }
  if (STATE) STATE.job = j;
  renderOverview(j); renderStepBar();
  $('cancelJob').classList.toggle('hidden', j.status !== 'running');
  $('pgKind').textContent = (KIND_LABEL[j.kind] || '작업 중') + ' · ' + j.started + ' 시작';
  $('pgKind').className = 'stat ' + (j.status === 'running' ? 'now' : j.status === 'done' ? 'ok' : 'bad');
  $('liveDot').classList.toggle('on', j.status === 'running');
  const log = $('pgLog'); const txt = j.log.join('\n') + (j.partial ? '\n' + j.partial : '');
  if (log.textContent !== txt) { log.textContent = txt; if ($('pgFollow').checked) log.scrollTop = log.scrollHeight; }
  $('pgLines').textContent = `(${j.log.length}줄)`;
  $('pgStage').textContent = j.status === 'running' ? humanStage(j) : j.status === 'done' ? '✅ 완료' : '❌ 중단됨';
  const cur = ((STATE && STATE.queue && STATE.queue.items) || []).find(x => x.status === 'working');
  $('pgSub').textContent = cur ? `지금 만드는 편: ${cur.title}` : (j.result && j.result.title ? `작업: ${j.result.title}` : '');
  renderSteps(j); refreshGallery(false);
  if (j.status === 'running' && (j.result || {}).script) {
    const sc = j.result.script; const opt = [...$('workFile').options].find(o => o.value === sc || sc.endsWith(o.value));
    if (opt && $('workFile').value !== opt.value) { $('workFile').value = opt.value; onWorkChange(); }
    else if (Date.now() - lastWorkLoad > 15000) loadWorkspace(false);
  }
  renderStageCards();
  if (j.status === 'running' && STATE && STATE.config.AI === 'deepseek-web') {
    try { const w = await api('/api/web/status'); const t = (w.taken || [])[0]; $('pgWeb').classList.remove('hidden');
      $('pgWeb').textContent = !w.alive ? '🔴 딥시크 확장이 끊겼습니다 — chat.deepseek.com 창을 열어 두세요' : (t ? `🌐 딥시크 웹 응답 중 · ${t.progress || '전송 중'} · ${t.since}초` : '🌐 딥시크 웹 연결됨'); } catch (e) {}
  } else $('pgWeb').classList.add('hidden');
  let p = 0.1; const m = [...j.log.join('\n').matchAll(/(\d+)\/(\d+)\s*(묶음|부분)|챕터\s*(\d+)/g)];
  if (m.length) { const last = m[m.length - 1]; p = last[2] ? (+last[1]) / (+last[2]) * 0.9 : Math.min(0.9, (+last[4]) / 9); }
  if (j.progress) p = j.progress;
  if (j.status !== 'running') { clearInterval(pollTimer); pollTimer = null; p = 1; }
  $('pgBar').style.width = (p * 100) + '%';
  if (j.status === 'done' && j.kind === 'variations') { if ($('varBox')._shown !== j.started) { $('varBox')._shown = j.started; renderVariations(j.result); } return; }
  if (j.status === 'done') {
    const r = j.result, box = $('pgResult'); box.classList.remove('hidden'); loadWorkspace(false);
    if (j.kind === 'thumbnail' && $('workFile').value) loadWorkspace(false);
    if (j.kind === 'bench') { loadBench(); }
    if (r.script || r.file) { localStorage.setItem('workScript', r.script || r.file); WORK = null; }
    const line = (label, v, btn) => v ? `<div>${label}: <code>${esc(v)}</code> ${btn || ''}</div>` : '';
    box.innerHTML = `<b>✅ 완료</b> ${r.title ? esc(r.title) : ''} ${r.chars ? `(${r.chars.toLocaleString()}자)` : ''} ${r.scenes ? `(장면 ${r.scenes}개)` : ''}`
      + line('대본', r.file || r.script, `<button class="mini" onclick="showFile('${js(r.file || r.script)}')">내용 보기</button>`)
      + line('유튜브 제목·설명·태그', r.opt, `<button class="mini" onclick="showFile('${js(r.opt)}')">보기</button>`)
      + line('나레이션', r.mp3 || r.narration) + line('이미지 폴더', r.images, `<button class="mini" onclick="openPath('${js(r.images)}')">열기</button>`)
      + line('최종 영상', r.video, `<button class="mini" onclick="openPath('${js(r.video)}')">열기</button>`)
      + (r.thumbnails && r.thumbnails.length ? line(`썸네일 ${r.thumbnails.length}장`, r.thumbnails[0], `<button class="mini" onclick="openPath('${js(r.thumbnails[0])}')">열기</button>`) : '')
      + line('업로드 폴더', r.upload_dir, `<button class="mini" onclick="openPath('${js(r.upload_dir)}')">열기</button>`)
      + line('결과 폴더', r.assets, `<button class="mini" onclick="openPath('${js(r.assets)}')">열기</button>`)
      + ((j.kind === 'script' || j.kind === 'mindam') && r.file ? `<div style="margin-top:8px"><button class="primary" onclick="$('contFile').value='${js(r.file)}';continuePipeline()">🎬 이 대본으로 나레이션 → 이미지 → 영상까지 이어서 만들기</button></div>` : '')
      + ((r.issues || []).length ? `<div class="hint" style="margin-top:6px">확인: ${r.issues.map(esc).join(' · ')}</div>` : '')
      + `<div class="hint">${esc(r.cost || '')}</div>`;
    refresh();
  } else if (j.status === 'error') { const e = $('pgErr'); e.classList.remove('hidden'); e.className = 'errwrap'; e.innerHTML = explainError(j.error); }
}
// ── 친절한 오류 안내: 원인별 제목·설명·해결 버튼 ──
function explainError(message, opts) {
  const raw = String(message || '알 수 없는 오류'); opts = opts || {};
  const retry = opts.retry !== false && STATE && STATE.queue && (STATE.queue.items || []).some(x => ['error', 'pending'].includes(x.status));
  const R = retry ? [['▶ 다시 시도', "queueControl('resume')"]] : [];
  const gemini = STATE && STATE.keys && STATE.keys.gemini;
  let t;
  if (/중단|취소|cancel/i.test(raw) && !/편집프로그램|이미지 생성/.test(raw)) t = {title: '사용자가 중단했습니다', why: '이어서 만들려면 다시 시도를 누르세요.', acts: R};
  else if (/확장|chat\.deepseek|웹 대기|딥시크 웹|답변이 시작되지|deepseek-web/i.test(raw)) t = {title: '딥시크 창이 응답하지 않았어요', why: '크롬의 chat.deepseek.com 탭이 닫혔거나, 확장 연결이 끊겼거나, 딥시크가 느린 상태입니다. 탭이 열려 있고 로그인돼 있는지 확인한 뒤 다시 시도하세요.', acts: [['확장 상태 확인', "obOpen(2)"], ...R, ...(gemini ? [['제미나이로 바꿔서 시도', "switchAI('gemini')"]] : [])]};
  else if (/8765|편집프로그램|연결할 수 없|Failed to fetch|ECONNREFUSED|WinError 10061/i.test(raw)) t = {title: '편집프로그램이 꺼져 있어요', why: '이미지·영상을 만드는 편집프로그램(8765)에 연결되지 않았습니다. 바탕화면의 시작 파일(유튜브_자동화_시작)을 다시 실행해 두 프로그램을 모두 켠 뒤 다시 시도하세요.', acts: [['연결 상태 확인', "obOpen(4)"], ...R]};
  else if (/좌표|드롭샷|창을 찾|window|생성 버튼|다운로드 버튼|이미지 생성이 끝나지|실패 \d+장/i.test(raw)) t = {title: '드롭샷 창을 못 찾았거나 좌표가 안 맞아요', why: '드롭샷 창이 닫혔거나 위치·크기가 바뀌면 자동 클릭이 빗나갑니다. 드롭샷 창을 열고 좌표 세 개를 다시 잡은 뒤 [위치 확인]으로 점검하세요.', acts: [['좌표 다시 잡기', "obOpen(4)"], ...R]};
  else if (/인월드|목소리|voice|tts/i.test(raw)) t = {title: '나레이션(인월드) 설정을 확인하세요', why: '인월드 키가 없거나 목소리 ID가 틀렸거나 사용량이 다 됐을 수 있습니다.', acts: [['나레이션 설정', "obOpen(3)"], ...R]};
  else if (/KIE|kie/.test(raw)) t = {title: '움직이는 영상(KIE) 단계에서 멈췄어요', why: 'KIE 키가 없거나 잔액이 부족하면 이 단계만 건너뛰고 나머지는 계속 만들 수 있습니다.', acts: [['KIE 설정', "showView('settings')"], ...R]};
  else if (/API_키|api key|401|403|429|quota|insufficient|한도|잔액|rate limit/i.test(raw)) t = {title: 'AI 키 또는 사용량 문제예요', why: '키가 틀렸거나, 무료 한도를 다 썼거나, 잔액이 부족합니다. 키를 확인하거나 다른 AI로 바꿔 보세요.', acts: [['AI 키 설정', "obOpen(2)"], ...R, ...(gemini ? [['제미나이로 바꿔서 시도', "switchAI('gemini')"]] : [])]};
  else if (/형식|읽지 못했|파싱|블록/i.test(raw)) t = {title: 'AI 답변 형식이 어긋났어요', why: 'AI가 정해진 형식으로 답하지 않았습니다. 대개 다시 시도하면 해결됩니다. 반복되면 다른 AI로 바꿔 보세요.', acts: [...R, ...(gemini ? [['제미나이로 바꿔서 시도', "switchAI('gemini')"]] : [])]};
  else t = {title: '작업이 멈췄어요', why: '아래 상세 내용과 로그의 마지막 줄을 확인하세요. 대개 다시 시도하면 이어서 진행됩니다.', acts: [...R, ['로그 보기', "$('pgLog').scrollIntoView({behavior:'smooth'})"]]};
  return `<div class="errbox"><b>⚠ ${esc(t.title)}</b><div class="why">${esc(t.why)}</div><div class="acts">${t.acts.map(([l, fn]) => `<button class="mini" onclick="${fn.replace(/"/g, '&quot;')}">${esc(l)}</button>`).join('')}</div><details><summary>상세 오류</summary><pre>${esc(raw)}</pre></details></div>`;
}
function errorHelp(message) { return explainError(message); }
async function switchAI(name) {
  try { await api('/api/config', {AI: name}); toast(`대본 AI를 ${name} 로 바꿨습니다. 다시 시도합니다…`); await refresh(); if (STATE.queue && (STATE.queue.items || []).some(x => ['error', 'pending'].includes(x.status))) await queueControl('resume'); }
  catch (e) { toast(e.message, true); }
}
async function cancelJob() { if (!confirm('지금 만들고 있는 작업을 중단할까요?')) return; try { await api('/api/cancel', {}); toast('중단을 요청했습니다.'); } catch (e) { toast(e.message, true); } }
async function queueControl(action) {
  const labels = {pause: '지금 만드는 편이 끝나면 잠시 멈춥니다.', resume: '연속 제작을 계속합니다.', cancel: '지금 작업과 남은 주제를 모두 중단할까요?'};
  if (action === 'cancel' && !confirm(labels.cancel)) return;
  try { renderQueue(await api('/api/queue/' + action, {})); toast(labels[action]); } catch (e) { toast(e.message, true); }
}
async function removeQueueItem(id) { try { renderQueue(await api('/api/queue/remove', {id})); } catch (e) { toast(e.message, true); } }
function renderQueue(q) {
  if (!q) return; const items = q.items || [], done = items.filter(x => x.status === 'done').length, failed = items.filter(x => x.status === 'error').length;
  $('queueManage').classList.toggle('hidden', !items.length || !['running', 'paused'].includes(q.status));
  $('queueSummary').textContent = items.length ? `${q.status_text || ''} · 전체 ${items.length}편 · 완료 ${done}편${failed ? ` · 실패 ${failed}편` : ''}` : '대기열 없음';
  $('queueList').innerHTML = items.map((x, i) => `<div class="qitem ${x.status}"><span class="n">${i + 1}편</span><span class="t">${esc(x.title)}</span><span class="s">${esc(x.status_text || '대기 중')}${x.stage ? ' · ' + esc(x.stage) : ''}</span>${x.status === 'working' ? `<progress max="1" value="${x.progress || 0}"></progress>` : ''}${x.result && x.result.upload_dir ? `<button class="mini" onclick="openPath('${js(x.result.upload_dir)}')">업로드 폴더</button>` : (x.result && x.result.assets ? `<button class="mini" onclick="openPath('${js(x.result.assets)}')">결과 폴더</button>` : '')}${x.status === 'pending' ? `<button class="mini ghost" onclick="removeQueueItem('${x.id}')">빼기</button>` : ''}${x.error ? explainError(x.error, {retry: x.status === 'error'}) : ''}</div>`).join('');
}
async function refreshQueue() { try { const q = await api('/api/queue'); if (STATE) STATE.queue = q; renderQueue(q); renderStepBar(); } catch (e) {} }

// ── 4단계: 완성 확인 ────────────────────────────────────
function onWorkChange() { const f = $('workFile').value; if (f) { $('galFile').value = f; localStorage.setItem('selectedScript', f); refreshGallery(true); } loadWorkspace(true); }
let lastWorkLoad = 0;
async function loadWorkspace(showToast) {
  const file = $('workFile').value;
  if (!file) { WORK = null; $('workBody').classList.add('hidden'); $('workEmpty').classList.remove('hidden'); renderStageCards(); renderFiles(); return; }
  lastWorkLoad = Date.now();
  try {
    WORK = await api('/api/workspace?script=' + encodeURIComponent(file)); localStorage.setItem('workScript', file);
    $('workScript').value = WORK.script || ''; $('workPrompts').value = WORK.prompts || ''; $('workSrt').value = WORK.srt || '';
    $('workTitle').value = WORK.title || ''; $('workDesc').value = WORK.description || ''; $('workSources').value = WORK.sources || ''; $('workTags').value = WORK.tags || '';
    renderThumbs();
    $('workEmpty').classList.add('hidden'); $('workBody').classList.remove('hidden'); if (showToast) toast('작업을 불러왔습니다.');
    try { WORK.files = await api('/api/assets?script=' + encodeURIComponent(file)); } catch (e) { WORK.files = {}; }
    renderStageCards(); renderFiles();
  } catch (e) { toast(e.message, true); }
}
// 단계 카드(1~6)의 상태 배지·진행 막대: 지금 작업의 결과물 + 실행 중인 단계
function renderStageCards() {
  const j = STATE && STATE.job, active = j && j.status === 'running' && ['pipeline', 'queue_pipeline'].includes(j.kind);
  const s = String((j && j.stage) || '');
  const now = active ? (s.includes('최종') ? 'render' : s.includes('후킹') ? 'motion' : s.includes('이미지 자동') || s.includes('이미지 생성') ? 'image' : s.includes('나레이션') ? 'audio' : s.includes('프롬프트') ? 'prompt' : s.includes('대본') ? 'script' : '') : '';
  const w = WORK || {}, f = w.files || {};
  const done = {script: !!(w.script || '').trim(), prompt: !!(w.prompts || '').trim(), audio: !!(w.srt || '').trim() || !!f.narration, motion: false, render: !!f.video, image: false};
  for (const el of document.querySelectorAll('[data-stage-stat]')) {
    const k = el.dataset.stageStat; const isNow = now === k;
    el.textContent = isNow ? '진행 중' : done[k] ? '완료' : '대기';
    el.className = 'stat ' + (isNow ? 'now' : done[k] ? 'ok' : '');
    const bar = document.querySelector(`[data-stage-bar="${k}"]`); if (bar) bar.style.width = isNow ? (Math.max(8, (j.progress || 0.1) * 100)) + '%' : done[k] ? '100%' : '0%';
  }
  const ib = document.querySelector('[data-stage-bar="image"]'); if (ib && !now.includes('image')) ib.style.width = galFilled ? galFilled + '%' : '0%';
}
let galFilled = 0;
function renderFiles() {
  if (!WORK) renderThumbs();
  const box = $('fileList'); if (!WORK) { box.innerHTML = '<div class="hint">작업을 고르면 만들어진 파일이 여기에 나옵니다.</div>'; return; }
  const f = WORK.files || {};
  const items = [['대본', WORK.script_file, true], ['이미지 프롬프트', WORK.prompts_file, !!(WORK.prompts || '').trim()], ['나레이션 (mp3)', f.narration, !!f.narration], ['자막 (srt)', WORK.srt_file, !!(WORK.srt || '').trim()],
    ...(WORK.thumbnails || []).map((p, i) => [`썸네일 ${i + 1}`, p, true]), ['최종 영상 (mp4)', f.video, !!f.video], ['결과 폴더', WORK.assets, true]];
  box.innerHTML = items.map(([label, path, ok]) => `<div class="file ${ok ? '' : 'off'}"><span class="fl">${esc(label)}</span><span class="fp">${esc((path || '').split(/[\\/]/).pop())}</span><span class="spacer"></span>${ok ? `<button class="mini" onclick="openPath('${js(path)}')">열기</button>` : '<span class="hint">아직 없음</span>'}</div>`).join('');
}
function openWork(what) {
  if (!WORK) return toast('작업을 먼저 고르세요.', true);
  const p = {assets: WORK.assets, video: WORK.assets + '\\최종.mp4', thumbs: WORK.thumbnail_dir, narration: WORK.narration, script: WORK.script_file}[what];
  openPath(p);
}
// 썸네일: 완성본(문구 있음)이 있으면 그것, 없으면 raw 원본을 "문구 넣기 전"으로 보여 준다. 위 제작 현황에도 같이 표시.
function renderThumbs() {
  const done = (WORK && WORK.thumbnails) || [], raw = (WORK && WORK.thumbnail_raw) || [];
  const card = (p, label, cls) => { const src = `/api/image?path=${encodeURIComponent(p)}&t=${Date.now()}`; return `<div class="g ${cls}"><div class="no">${esc(label)}</div><div class="pic"><img src="${src}" loading="lazy" onclick="showBig('${src}')"></div></div>`; };
  let html = '', note = '';
  if (done.length) { html = done.map((p, i) => card(p, `썸네일 ${i + 1}`, 'done')).join(''); note = `완성 ${done.length}장 · 유튜브에 올릴 때 이 파일을 쓰세요`; }
  else if (raw.length) { html = raw.map((p, i) => card(p, `원본 ${i + 1} (문구 전)`, 'now')).join(''); note = '이미지는 받았는데 문구가 아직 안 얹혔습니다 → 아래 [원본에 문구 넣어 완성하기]'; }
  else { html = '<div class="hint">아직 썸네일이 없습니다. 제작이 끝나면 자동으로 생기고, 아래 버튼으로 따로 만들 수도 있습니다.</div>'; note = ''; }
  $('workThumbs').innerHTML = html; $('thumbNote').textContent = note;
  $('composeBtn').classList.toggle('hidden', !raw.length);
  $('topThumbs').innerHTML = (done.length || raw.length) ? html : ''; $('topThumbsRow').classList.toggle('hidden', !(done.length || raw.length));
}
async function composeThumbnails() {
  if (!WORK) return toast('작업을 먼저 고르세요.', true);
  try { const r = await api('/api/thumbnail/compose', {script_file: WORK.script_file}); toast(`썸네일 ${r.thumbnails.length}장 완성`); await loadWorkspace(false); } catch (e) { toast(e.message, true); }
}
async function makeWorkspaceThumbnails() {
  if (!WORK) return toast('작업을 먼저 고르세요.', true);
  try { await api('/api/thumbnail', {script_file: WORK.script_file, style: styleValue, position: 'auto', regenerate: true}); startPolling(true); toast('썸네일 3장을 다시 만듭니다.'); } catch (e) { toast(e.message, true); }
}
async function saveWorkspaceText(kind) {
  if (!WORK) return toast('작업을 먼저 고르세요.', true); const ids = {script: 'workScript', prompts: 'workPrompts', srt: 'workSrt'};
  try { await api('/api/workspace/save', {script_file: WORK.script_file, kind, text: $(ids[kind]).value}); toast({script: '대본', prompts: '이미지 프롬프트', srt: '자막'}[kind] + ' 저장 완료'); } catch (e) { toast(e.message, true); }
}
async function saveWorkspaceMeta() {
  if (!WORK) return toast('작업을 먼저 고르세요.', true);
  try { await api('/api/workspace/save', {script_file: WORK.script_file, kind: 'metadata', title: $('workTitle').value, description: $('workDesc').value, sources: $('workSources').value, tags: $('workTags').value}); toast('제목·설명·태그 저장 완료'); } catch (e) { toast(e.message, true); }
}
async function copyField(id) {
  const el = $(id), text = el.value || ''; if (!text) return toast('복사할 내용이 없습니다.', true);
  try { await navigator.clipboard.writeText(text); } catch (e) { el.focus(); el.select(); document.execCommand('copy'); }
  toast('복사했습니다. 유튜브에 붙여 넣으세요.');
}
async function rerunTTS() {
  if (!WORK) return toast('작업을 먼저 고르세요.', true);
  try { await saveWorkspaceText('script'); await api('/api/tts', {script_file: WORK.script_file}); startPolling(true); toast('고친 대본으로 나레이션을 다시 만듭니다.'); } catch (e) { toast(e.message, true); }
}
async function loadTrash() {
  try { const t = await api('/api/trash'); $('trashStat').textContent = t.items ? `휴지통 ${t.items}개 · ${t.gb} GB (${t.keep_days}일 지나면 자동 삭제)` : '휴지통 비어 있음'; } catch (e) {}
}
async function emptyTrash() {
  if (!confirm('휴지통(대본/_휴지통)을 완전히 비울까요? 되돌릴 수 없습니다.')) return;
  try { const r = await api('/api/trash/empty', {}); toast(`휴지통 ${r.removed}개 항목을 지웠습니다.`); loadTrash(); } catch (e) { toast(e.message, true); }
}
async function resetEverything() {
  if (STATE && STATE.job && STATE.job.status === 'running') return toast('진행 중인 작업을 먼저 중단하세요.', true);
  if (!confirm('작업했던 것을 전부 지울까요?\n대본·이미지·나레이션·썸네일·최종 영상·업로드 폴더·대기열이 모두 휴지통(대본/_휴지통)으로 옮겨집니다.\n설정(API 키·목소리·좌표)은 남습니다.')) return;
  if (!confirm('정말 전체 초기화할까요? (되돌리려면 _휴지통 폴더에서 꺼내야 합니다)')) return;
  try { const r = await api('/api/reset-all', {}); WORK = null; selection.clear(); localStorage.removeItem('workScript'); localStorage.removeItem('selectedScript'); await refresh(); loadWorkspace(false); renderQueue({items: [], status: 'idle'}); toast(`${r.count}개 항목을 휴지통으로 옮겼습니다. 새로 시작할 수 있습니다.`); window.scrollTo({top: 0, behavior: 'smooth'}); }
  catch (e) { toast(e.message, true); }
}
async function deleteCurrentWork() {
  if (!WORK) return toast('작업을 먼저 고르세요.', true);
  const name = $('workFile').options[$('workFile').selectedIndex]?.textContent || WORK.script_file;
  if (!confirm(`「${name}」의 대본·이미지·영상·음성·자막을 모두 휴지통으로 옮길까요?`)) return;
  try { const r = await api('/api/delete-script', {script_file: WORK.script_file}); WORK = null; localStorage.removeItem('workScript'); await refresh(); loadWorkspace(false); toast(`${r.count}개 항목을 휴지통으로 옮겼습니다.`); } catch (e) { toast(e.message, true); }
}

// ── 고급: 이미지 갤러리 ─────────────────────────────────
function onGalFileChange() { localStorage.setItem('selectedScript', $('galFile').value); refreshGallery(true); }
let galAssets = {script: '', images: '', prompts: ''};
async function assetsOf(script) {
  if (galAssets.script !== script) { galAssets = {script, images: '', prompts: ''}; try { const a = await api('/api/assets?script=' + encodeURIComponent(script)); galAssets.images = a.images; galAssets.prompts = a.prompts; } catch (e) {} }
  return galAssets;
}
async function refreshGallery(force) {
  const j = STATE && STATE.job; let dir = '', pr = '';
  const active = j && ['pipeline', 'queue_pipeline'].includes(j.kind) && j.status === 'running';
  if ((!galleryVisible || $('view-wizard').classList.contains('hidden')) && !force && !active) return;
  if (active && (j.result || {}).images) { dir = j.result.images; pr = j.result.prompts || ''; }
  else if (active && (j.result || {}).script) { const a = await assetsOf(j.result.script); dir = a.images; pr = a.prompts; }
  if (active && (j.result || {}).script && [...$('galFile').options].some(o => o.value === j.result.script) && $('galFile').value !== j.result.script) $('galFile').value = j.result.script;
  if (!active) {                                   // 여기 작업이 없어도 편집프로그램이 만드는 중이면 그 폴더를 따라간다
    const st = await genStatus();
    if (['running', 'paused'].includes(st.status) && st.output_dir) {
      const match = [...$('galFile').options].find(o => o.value && norm(st.output_dir).endsWith('\\' + norm(imagesDirOf(o.value))));
      if (match && $('galFile').value !== match.value) { $('galFile').value = match.value; localStorage.setItem('selectedScript', match.value); }
      if (match && $('workFile').value !== match.value) { $('workFile').value = match.value; loadWorkspace(false); }
      if (!match) { dir = st.output_dir; pr = st.prompts_file; }
    }
  }
  if (!dir) { const f = $('galFile').value; if (f) { const a = await assetsOf(f); dir = a.images; pr = a.prompts; } }
  galDir = dir; galPromptsPath = pr; if (force) galKey = '';
  await updateGallery(dir, pr); await refreshKieFiles();
}
async function updateGallery(dir, pr) {
  if (!dir) { $('advGal').innerHTML = '<div class="hint">제작이 시작되면 여기에 이미지가 나타납니다. 위에서 작업을 고르면 그 작업의 이미지를 보여 줍니다.</div>'; $('galStat').textContent = ''; $('topImgsRow').classList.add('hidden'); return; }
  if (galBusy) return; galBusy = true;
  try {
    let [st, all] = await Promise.all([genStatus(), listImages(dir)]);
    if (norm(st.output_dir) !== norm(dir)) st = IDLE;      // 다른 폴더를 만드는 중이면 이 폴더는 '대기'로 표시
    const imgs = {}; all.forEach(i => { if (!i.video) imgs[i.no] = i; });
    if (pr && galPrompts.path !== pr) { try { const pj = await post8765('/api/gen/prompts', {path: pr}); galPrompts = {path: pr, count: pj.count || 0}; } catch (e) { galPrompts = {path: pr, count: 0}; } }
    const total = Math.max(st.total || 0, galPrompts.count || 0, ...Object.keys(imgs).map(Number), 0);
    const key = JSON.stringify([st.status, st.current, Object.values(imgs).map(i => i.mtime)]);
    galFilled = total ? Math.round(Object.keys(imgs).length / total * 100) : 0; renderStageCards();
    $('galStat').textContent = `${Object.keys(imgs).length}/${total} · ${({running: '생성 중', paused: '잠시 멈춤', done: '완료', stopped: '중단', error: '오류', idle: '대기'})[st.status] || st.status}${st.current ? ' · 지금 ' + pad3(st.current) + '번' : ''}${st.failed && st.failed.length ? ' · 실패 ' + st.failed.join(',') : ''}`;
    if (key === galKey) return; galKey = key;
    const busy = st.status === 'running' || st.status === 'paused', failed = new Set(st.failed || []); const out = [];
    for (let i = 1; i <= total; i++) {
      const it = imgs[i], now = busy && st.current === i, fail = !it && failed.has(i);
      const src = it ? imageUrl(it) : '';
      const stTxt = it ? '완료' : (now ? '만드는 중' : (fail ? '실패' : '대기'));
      const btns = it ? `<button class="re" onclick="regenScene(${i})">다시 만들기</button><button class="vi" onclick="hookScene(${i})">움직이기</button>` : `<button class="re" onclick="regenScene(${i})">${fail ? '다시 시도' : '만들기'}</button>`;
      out.push(`<div class="g${it ? ' done' : ''}${now ? ' now' : ''}${fail ? ' fail' : ''}"><div class="no">${pad3(i)}</div><div class="pic">${it ? `<img src="${src}" loading="lazy" onclick="showBig('${src}')">` : (fail ? '실패' : (now ? '…' : '대기'))}</div><div class="st">${stTxt}</div><div class="bt">${btns}</div></div>`);
    }
    $('advGal').innerHTML = out.join('') || '<div class="hint">아직 이미지 프롬프트가 없습니다.</div>';
    // 상단 제작 현황에도 방금 받은 이미지 8장을 바로 보여 준다
    const latest = Object.values(imgs).sort((a, b) => b.mtime - a.mtime).slice(0, 8).sort((a, b) => a.no - b.no);
    $('topImgsRow').classList.toggle('hidden', !latest.length);
    $('topImgsStat').textContent = `${Object.keys(imgs).length}/${total}장 받음${st.current ? ' · 지금 ' + pad3(st.current) + '번' : ''}`;
    $('topImgs').innerHTML = latest.map(it => { const src = imageUrl(it); return `<div class="g done"><div class="no">${pad3(it.no)}</div><div class="pic"><img src="${src}" loading="lazy" onclick="showBig('${src}')"></div></div>`; }).join('');
  } catch (e) { $('galStat').textContent = '이미지 목록을 읽지 못했습니다'; galKey = ''; } finally { galBusy = false; }
}
async function genBodyFromUI() {
  const info = await get8765('/api/info'); const ui = (info.config || {}).gen_ui || {}; const XY = ui.XY || {};
  if (!galDir) throw new Error('대본을 먼저 고르세요'); if (!galPromptsPath) throw new Error('이미지 프롬프트 파일이 없습니다. 먼저 이미지 프롬프트를 만드세요.');
  if (!XY.prompt || !XY.download) throw new Error('설정에서 프롬프트 입력창과 이미지 다운로드 좌표를 먼저 저장하세요');
  return {prompts_file: galPromptsPath, output_dir: galDir, download_dir: (ui.P || {}).download || info.downloads_dir, prompt_xy: XY.prompt, generate_xy: XY.generate || XY.prompt, download_xy: XY.download,
    wait_generate: +(ui.wait_generate || 60), wait_download: +(ui.wait_download || 120), wait_next: 0.5, start_no: 1, end_no: 0, skip_existing: true,
    style_prefix: (STATE.style_prefixes || {})[styleValue] || ui.style_prefix || '', retries: 1, window_keyword: ui.window_keyword || '드롭샷', auto_generate: ui.auto_generate !== false};
}
async function galStart(fromScratch) {
  try {
    const body = await genBodyFromUI();
    if (fromScratch) { if (!confirm('지금 있는 그림을 전부 이전/ 폴더로 옮기고 1번부터 다시 만들까요?')) return; const r = await post8765('/api/gen/reset', {output_dir: galDir}); body.skip_existing = false; toast(`그림 ${r.moved}장을 이전/ 으로 옮겼습니다`); }
    await post8765('/api/gen/start', body); galKey = ''; toast((fromScratch ? '1번부터 다시 만듭니다' : '빠진 장면부터 이어서 만듭니다') + ' — 드롭샷 창을 가리지 마세요');
  } catch (e) { toast(e.message, true); }
}
async function galCtl(what) { try { await post8765('/api/gen/' + what, {}); toast({pause: '잠시 멈춤', resume: '계속', stop: '중단 요청'}[what]); galKey = ''; } catch (e) { toast(e.message, true); } }
async function regenScene(no) {
  try { const st = await get8765('/api/gen/status'); if (st.status === 'running' || st.status === 'paused') return toast('지금 이미지 생성이 돌아가는 중입니다. [■ 중단] 뒤에 다시 누르거나 끝날 때까지 기다리세요.', true); } catch (e) {}
  if (!confirm(pad3(no) + '번 장면을 다시 만들까요? (드롭샷 창을 가리지 마세요)')) return;
  try {
    const body = await genBodyFromUI(); Object.assign(body, {scene: no, start_no: no, end_no: no, skip_existing: false}); delete body.auto_generate;
    await post8765('/api/gen/regen', body); toast(pad3(no) + '번 다시 만들기 시작'); galKey = '';
  } catch (e) { toast(e.message, true); }
}
async function hookScene(no) {
  if (!await ensureKieReady()) return;
  if (!confirm(pad3(no) + '번 장면을 움직이는 영상으로 만들까요? (KIE 크레딧 사용, 1~4분)')) return;
  try {
    const info = await get8765('/api/info'); const ui = (info.config || {}).gen_ui || {};
    await post8765('/api/hook/start', {api_key: '', images_dir: galDir, prompts_file: galPromptsPath || '', scenes: [no], model: ui.kie_model || 'veo-3-1', aspect_ratio: ui.kie_ratio || '16:9', duration: 0, motion_prompt: ui.motion_prompt || 'Cinematic slow camera movement, subtle natural motion, keep the same style and composition.', use_scene_prompt: true, output_dir: ''});
    toast(pad3(no) + '번 영상 변환 시작');
  } catch (e) { toast(e.message, true); }
}
async function refreshKieStatus() {
  try { const info = await get8765('/api/info'); const ok = !!info.kie_key_saved; $('kieStatus').textContent = $('sKieStat').textContent = ok ? 'KIE 키 저장됨' : 'KIE 키 없음'; $('sKieStat').className = 'stat ' + (ok ? 'ok' : 'bad'); return ok; }
  catch (e) { $('kieStatus').textContent = $('sKieStat').textContent = '편집프로그램 연결 확인'; $('sKieStat').className = 'stat bad'; return false; }
}
async function saveKieKey() {
  const key = $('sKieKey').value.trim(); if (!key) return toast('KIE API 키를 입력하세요', true);
  try { await post8765('/api/config', {kie_api_key: key}); $('sKieKey').value = ''; await refreshKieStatus(); toast('KIE 키 저장됨'); } catch (e) { toast('KIE 키 저장 실패: ' + e.message, true); }
}
async function ensureKieReady() { if (await refreshKieStatus()) return true; showView('settings'); $('sKieKey').focus(); toast('움직이는 영상을 만들려면 KIE 키를 먼저 저장하세요. (세부 옵션에서 0으로 하면 건너뜁니다)', true); return false; }
async function refreshKieFiles() {
  if (!galDir) { $('kieScenes').textContent = ''; if (!kieJobId) $('kieProgress').textContent = ''; return; }
  try {
    const done = new Set((await listImages(galDir)).filter(x => x.video).map(x => x.no));
    const count = [1, 2, 3, 4, 5, 6, 7].filter(n => done.has(n)).length;
    $('kieScenes').textContent = [1, 2, 3, 4, 5, 6, 7].map(n => `${pad3(n)} ${done.has(n) ? '✓' : '대기'}`).join(' · ');
    if (!kieJobId) $('kieProgress').textContent = count === 7 ? '✅ 앞 7장 영상 변환 완료' : `영상 ${count}/7개 완료`;
  } catch (e) { $('kieScenes').textContent = '영상 파일 확인 실패: ' + e.message; }
}
async function pollKieJob() {
  if (!kieJobId) return;
  try { const r = await fetch(EDITOR + '/api/jobs/' + encodeURIComponent(kieJobId)); if (!r.ok) throw new Error('작업을 찾지 못했습니다'); const j = await r.json();
    $('kieProgress').textContent = `KIE ${j.stage || j.status || '진행 중'}${j.progress != null ? ' · ' + Math.round(j.progress * 100) + '%' : ''}${j.error ? ' · ' + j.error : ''}`;
    await refreshKieFiles();
    if (['done', 'error', 'cancelled'].includes(j.status)) { clearInterval(kieTimer); kieTimer = null; kieJobId = ''; sessionStorage.removeItem('kieJobId'); await refreshGallery(true); if (j.status === 'error') $('kieProgress').textContent = '❌ 영상화 오류: ' + (j.error || '로그를 확인하세요'); else if (j.status === 'cancelled') $('kieProgress').textContent = '■ 중단됨'; }
  } catch (e) { clearInterval(kieTimer); kieTimer = null; kieJobId = ''; sessionStorage.removeItem('kieJobId'); await refreshKieFiles(); }
}
async function startFirstSevenVideos() {
  if (kieJobId) return toast('앞 7장 영상화가 이미 진행 중입니다', true);
  if (!galDir || !galPromptsPath) return toast('대본과 이미지 프롬프트를 먼저 고르세요', true);
  if (!await ensureKieReady()) return;
  try {
    const ready = new Set((await listImages(galDir)).filter(x => !x.video).map(x => x.no));
    const missing = [1, 2, 3, 4, 5, 6, 7].filter(no => !ready.has(no));
    if (missing.length) return toast('앞 7장 이미지가 먼저 필요합니다. 없는 장면: ' + missing.map(pad3).join(', '), true);
    if (!confirm('앞 7장 이미지를 움직이는 영상으로 변환할까요? 장면마다 KIE 크레딧이 사용됩니다.')) return;
    const info = await get8765('/api/info'), ui = (info.config || {}).gen_ui || {};
    const j = await post8765('/api/hook/start', {api_key: '', images_dir: galDir, prompts_file: galPromptsPath, scenes: [1, 2, 3, 4, 5, 6, 7], model: ui.kie_model || 'veo-3-1', aspect_ratio: ui.kie_ratio || '16:9', duration: 0, motion_prompt: ui.motion_prompt || 'Subtle 2D motion, preserve characters and composition.', use_scene_prompt: true, output_dir: ''});
    kieJobId = j.job_id; sessionStorage.setItem('kieJobId', kieJobId); clearInterval(kieTimer); kieTimer = setInterval(pollKieJob, 2000); pollKieJob(); toast('앞 7장 영상 변환을 시작했습니다');
  } catch (e) { toast('영상화 실패: ' + e.message, true); }
}
async function cancelKieVideos() { if (!kieJobId) return toast('진행 중인 영상화가 없습니다', true); try { await post8765('/api/jobs/' + encodeURIComponent(kieJobId) + '/cancel', {}); $('kieProgress').textContent = '중단 요청'; } catch (e) { toast('중단 실패: ' + e.message, true); } }
if (kieJobId) { kieTimer = setInterval(pollKieJob, 2000); setTimeout(pollKieJob, 500); }

// ── 고급: 영상·자막 설정 (편집프로그램 iframe) ─────────────
async function prepareVideoEditor() {
  const fr = $('frVideo'), status = $('videoStatus'), script = $('galFile').value;
  fr.src = fr.dataset.src + '?settings=' + Date.now();
  if (!script) { status.textContent = '자막 글꼴·크기·색상을 설정할 수 있습니다. 이미지 수정 탭에서 대본을 고르면 이미지·음성·자막이 자동 연결됩니다.'; return; }
  status.textContent = '선택한 대본의 나레이션·자막·이미지를 연결하는 중…';
  try {
    const a = await api('/api/assets?script=' + encodeURIComponent(script));
    const scan = await post8765('/api/scan_folder', {path: a.assets});
    if (!scan.srt || !scan.narration || !scan.images) throw new Error('이 대본의 자막, 나레이션 또는 이미지가 없습니다. 먼저 제작을 완료하세요.');
    const kieCount = (await listImages(scan.images)).filter(x => x.video).length;
    const info = await get8765('/api/info');
    const ui = {...((info.config || {}).ui || {}), easy: a.assets, srt: scan.srt, flow: scan.flow || '', images: scan.images, narration: scan.narration, subtitle_mov: scan.subtitle_mov || '', output: a.assets + '\\최종.mp4'};
    await post8765('/api/config', {ui});
    status.textContent = '✓ 연결됨: ' + script.split(/[\\/]/).pop() + ' · 이미지 ' + scan.image_count + '장 · 움직이는 영상 ' + kieCount + '개 · 결과는 이 대본의 결과 폴더에 저장됩니다.';
    fr.src = fr.dataset.src + '?selected=' + Date.now();
  } catch (e) { status.textContent = '연결 실패: ' + e.message; toast(e.message, true); }
}
window.addEventListener('message', e => { if (e.data && e.data.aipHeight) { const f = $('frVideo'); if (f && f.contentWindow === e.source) f.style.height = (e.data.aipHeight + 40) + 'px'; } });

// ── 고급: 단계별 따로 실행 ───────────────────────────────
function setToolChannel(ch) { toolChannel = ch; document.querySelectorAll('#adv-tools .seg button').forEach(b => b.classList.toggle('on', b.dataset.ch === ch)); $('toolMindam').classList.toggle('hidden', ch !== 'mindam'); }
function findTopic(ch, title) { return topicSource(ch).find(t => t.제목 === title && !t._custom) || null; }
async function startScriptOnly() {
  const title = $('toolTitle').value.trim(); if (!title) return toast('주제를 적어 주세요.', true);
  try {
    if (toolChannel === 'mindam') { const b = findTopic('mindam', title); await api('/api/mindam', {title: chosenVar ? chosenVar.title : title, reference: chosenVar ? varRef : '', variation: chosenVar ? chosenVar.text : '', length: $('toolLen').value, mark_used: true, resume_dir: $('toolResume').value, bench: b, optimize: true}); }
    else await api('/api/script', {topic: findTopic('person', title), title, guideline: $('optGuide').value, target: targetChars, mark_used: true, optimize: true});
    startPolling(true);
  } catch (e) { toast(e.message, true); }
}
async function startVariations() { const title = $('toolTitle').value.trim(); if (!title) return toast('제목을 적어 주세요.', true); try { await api('/api/variations', {title, bench: findTopic('mindam', title)}); $('varBox')._shown = null; startPolling(true); } catch (e) { toast(e.message, true); } }
function renderVariations(r) {
  varRef = r.reference; chosenVar = null; const box = $('varBox'); box.classList.remove('hidden');
  box.innerHTML = `<p class="hint">원본: ${esc(r.reference)} · AI 추천: ${esc(r.recommend || '')}</p>` + r.options.map(o => `<label class="row" style="align-items:flex-start;border:1px solid var(--line);border-radius:10px;padding:10px 12px;cursor:pointer" onclick="chooseVar('${o.key}')"><input type="radio" name="var" value="${o.key}" style="margin-top:6px"><div><b>[${o.key}] ${esc(o.title)}</b><pre style="margin:4px 0 0;white-space:pre-wrap;font:13px/1.5 var(--sans);color:var(--muted)">${esc(o.text.replace(/^-\s*제목.*\n?/m, ''))}</pre></div></label>`).join('');
  box._options = r.options; goAdvanced('tools'); setTimeout(() => box.scrollIntoView({behavior: 'smooth'}), 180);
}
function chooseVar(k) { const o = $('varBox')._options.find(x => x.key === k); chosenVar = o; document.querySelector(`input[name=var][value=${k}]`).checked = true; toast(`선택: [${k}] ${o.title} — 이제 [대본만 만들기]를 누르세요`); }
function toolFile() { const f = $('toolFile').value; if (!f) { toast('대본 파일을 고르세요.', true); return null; } return f; }
async function startImages() { const f = toolFile(); if (!f) return; try { await api('/api/images', {script_file: f, guideline: $('optImgGuide').value, style: styleValue, chunk: +$('optChunk').value}); startPolling(true); } catch (e) { toast(e.message, true); } }
async function startTTS() { const f = toolFile(); if (!f) return; try { await api('/api/tts', {script_file: f}); startPolling(true); } catch (e) { toast(e.message, true); } }
async function startOptimize() { const f = toolFile(); if (!f) return; try { await api('/api/optimize', {script_file: f}); startPolling(true); } catch (e) { toast(e.message, true); } }
async function startThumb() { const f = toolFile(); if (!f) return; try { await api('/api/thumbnail', {script_file: f, style: styleValue, position: 'auto'}); startPolling(true); } catch (e) { toast(e.message, true); } }
async function restyleTo2D() { const f = toolFile(); if (!f) return; if (!confirm('이 대본의 이미지 프롬프트를 2D·레퍼런스 지시로 바꿀까요? 기존 파일은 백업됩니다.')) return; try { const r = await api('/api/restyle-2d', {script_file: f}); pickStyle('2D 일러스트'); toast(`${r.count}개 장면을 2D로 바꿨습니다`); } catch (e) { toast(e.message, true); } }
async function makeTimestamps() { try { const j = await api('/api/timestamps', {dir: $('tsDir').value, srt: $('tsSrt').value}); $('tsOut').textContent = j.text + '\n→ ' + j.file; } catch (e) { toast(e.message, true); } }

// ── 고급: 지침 수정 / 초기화 ─────────────────────────────
async function openGuide() {
  const name = $('guideSel').value; if (!name) return;
  try { const j = await api('/api/guideline?name=' + encodeURIComponent(name)); guideEditing = name; $('guideName').textContent = name; $('guideText').value = j.text; $('guideEditor').classList.remove('hidden'); } catch (e) { toast(e.message, true); }
}
async function saveGuide() { if (!guideEditing) return; try { await api('/api/guideline', {name: guideEditing, text: $('guideText').value}); toast('지침 저장됨: ' + guideEditing); } catch (e) { toast(e.message, true); } }
async function resetSelected() {
  const id = $('resetFile').value, item = (STATE.reset_items || []).find(x => x.id === id);
  if (!item) return toast('초기화할 작업을 고르세요', true);
  if (!confirm(`「${item.label}」의 대본과 생성 자료를 모두 휴지통으로 옮길까요?`)) return;
  try { const r = await api('/api/reset', {id, scope: 'all'}); await refresh(); toast(`${r.count}개 항목을 휴지통으로 옮겼습니다`); } catch (e) { toast(e.message, true); }
}

// ── 벤치마킹: 비슷한 심리 채널의 히트 영상 (제목·썸네일 참고) ──
async function loadBench() {
  try {
    const b = await api('/api/bench'); const hits = b.히트 || [];
    $('benchBox').classList.toggle('hidden', !hits.length || channel !== 'person');
    if (document.activeElement !== $('benchChannels')) $('benchChannels').value = (b.추가_채널 || []).join('\n');
    if (!hits.length) return;
    $('benchStat').textContent = `벤치마킹 ${b.날짜} · 채널 ${(b.채널 || []).length}곳 · 평소보다 몇 배 터진 영상 ${hits.length}개 — 제목·썸네일 참고`;
    $('benchGal').innerHTML = hits.slice(0, 12).map(v => `<div class="g done" title="${esc(v.title)}"><div class="pic"><a href="${esc(v.url)}" target="_blank" rel="noopener"><img src="${esc(v.thumb)}" loading="lazy" referrerpolicy="no-referrer"></a></div><div class="st" style="white-space:normal;font-size:11.5px;line-height:1.3;color:var(--ink)">${esc(v.title.slice(0, 40))}</div><div class="hint" style="font-size:11px">${esc(v.channel)} · ${v.ratio}배</div></div>`).join('');
  } catch (e) {}
}
async function runBenchmark() {
  if (STATE && STATE.job && STATE.job.status === 'running') return toast('진행 중인 작업이 끝난 뒤 실행하세요.', true);
  if (!confirm(`${pname(channel)}와 비슷한 채널을 찾아 터진 영상을 모읍니다` + ' (1~3분). 지금 실행할까요?')) return;
  try { await api('/api/bench/run', {channel}); startPolling(true); toast('벤치마킹을 시작했습니다. 끝나면 추천 주제가 새로 채워집니다.'); } catch (e) { toast(e.message, true); }
}
async function saveBenchChannels() {
  const channels = $('benchChannels').value.split(/\n/).map(x => x.trim()).filter(Boolean);
  try { const r = await api('/api/bench/channels', {channels}); toast(`벤치 채널 ${r.count}개 저장`); } catch (e) { toast(e.message, true); }
}

// ── 새 주제 추천 (누를 때마다 안 본 후보 → 모자라면 AI 가 새로 만듦) ──
async function refreshTopics() {
  const btn = $('topicRefreshBtn'), stat = $('topicRefreshStat');
  const shown = topicSource(channel).filter(t => !t._custom).map(t => t.제목);
  btn.disabled = true; stat.textContent = '새 주제를 찾는 중… (AI가 만들 때는 30초 안팎)';
  try {
    const r = await api('/api/topics/refresh', {channel, shown});
    if (channel === 'mindam') STATE.topics.mindam = r.items; else { STATE.topics.plan = []; STATE.topics.candidates = r.items; }
    renderTopics();
    stat.textContent = r.generated ? `AI가 새 주제 ${r.generated}개를 만들었습니다` : '아직 안 본 추천 주제를 보여 줍니다';
    toast(r.generated ? `새 주제 ${r.items.length}개 (AI 생성 ${r.generated}개)` : `새 주제 ${r.items.length}개`);
  } catch (e) { stat.textContent = ''; toast('새 주제를 만들지 못했습니다: ' + e.message, true); }
  finally { btn.disabled = false; }
}
let analysisChannel = 'person';
const fmtN = n => n >= 10000 ? (n / 10000).toFixed(1).replace(/\.0$/, '') + '만' : String(n);
async function loadAnalysis(ch) {
  if (ch) analysisChannel = ch;
  document.querySelectorAll('[data-ach]').forEach(b => b.classList.toggle('on', b.dataset.ach === analysisChannel));
  let a; try { a = await api('/api/channel/analysis?channel=' + analysisChannel); } catch (e) { $('chAnalysis').textContent = e.message; return; }
  if (!a.ok) { $('chAnalysis').innerHTML = `<div class="hint">${esc(a.reason)}</div>`; $('chAnalysisLine').textContent = ''; return; }
  const row = v => `<div><span class="v">${fmtN(v.views)}회</span><span class="t">${esc(v.title)}</span>${v.published ? `<span class="hint">${esc(v.published)}</span>` : ''}</div>`;
  $('chAnalysis').innerHTML = `<div class="hint">${esc(a.name)} · ${a.source === 'api' ? '유튜브 API' : 'yt-dlp'} · ${esc(a.fetched)} 기준${a.first ? ` · ${esc(a.first)} ~ ${esc(a.last)}` : ''}</div>
    <div class="kpis"><div class="kpi"><b>${a.count}</b><small>영상 수</small></div><div class="kpi"><b>${fmtN(a.subs)}</b><small>구독자</small></div><div class="kpi"><b>${fmtN(a.avg)}</b><small>평균 조회수</small></div><div class="kpi"><b>${fmtN(a.median)}</b><small>중간 조회수</small></div><div class="kpi"><b>${a.above_avg}/${a.count}</b><small>평균 이상 영상</small></div>${a.avg_minutes ? `<div class="kpi"><b>${a.avg_minutes}분</b><small>평균 길이</small></div>` : ''}${a.best_weekday ? `<div class="kpi"><b>${a.best_weekday}요일</b><small>반응 좋은 게시 요일</small></div>` : ''}</div>
    <b>잘 된 영상 TOP 5</b><div class="rank">${a.top.map(row).join('')}</div>
    ${a.bottom.length ? `<b>반응이 약했던 영상</b><div class="rank">${a.bottom.map(row).join('')}</div>` : ''}
    <div style="margin-top:8px"><b>잘 되는 키워드</b> ${a.keywords.length ? a.keywords.map(k => `<span class="kw good">${esc(k.word)} · 평균 ${fmtN(Math.round(k.avg))}회</span>`).join('') : '<span class="hint">(아직 영상이 적어 뚜렷한 키워드가 없습니다)</span>'}</div>
    ${a.weak.length ? `<div style="margin-top:6px"><b>반응이 약한 키워드</b> ${a.weak.map(k => `<span class="kw bad">${esc(k.word)}</span>`).join('')}</div>` : ''}
    <p class="hint" style="margin-top:8px">[새 주제 추천]을 누르면 AI가 이 분석(잘 된 영상·키워드)을 참고해서 주제를 만듭니다.</p>`;
  if (analysisChannel === channel) $('chAnalysisLine').textContent = `내 채널 분석: 영상 ${a.count}편 · 평균 조회수 ${fmtN(a.avg)}회` + (a.keywords.length ? ` · 잘 되는 키워드: ${a.keywords.slice(0, 5).map(k => k.word).join(', ')}` : '');
}
async function saveYtKey() {
  const v = $('ytKey').value.trim(); if (!v) return toast('API 키를 입력하세요.', true);
  try { await api('/api/config', {유튜브_API_키: v}); $('ytKey').value = ''; toast('유튜브 API 키 저장. 연결을 확인합니다…'); await refresh(); await testYtKey(); pollChannel('person'); pollChannel('mindam'); } catch (e) { toast(e.message, true); }
}
async function testYtKey() {
  const st = $('ytKeyStat'), info = $('ytKeyInfo'); info.textContent = '연결 확인 중…';
  try { const r = await api('/api/youtube/test', {}); st.textContent = '연결됨'; st.className = 'stat ok'; info.textContent = `✓ 구글 API 정상 · ${r.name} · 구독자 ${fmtN(r.subs)}`; toast('유튜브 API 연결 확인 완료'); }
  catch (e) { st.textContent = '연결 실패'; st.className = 'stat bad'; info.textContent = e.message; toast('유튜브 API 연결 실패: ' + e.message, true); }
}

// ── 내 유튜브 채널 연동 ───────────────────────────────────
function renderChannels(ch) {
  const P = ch.person || {}, M = ch.mindam || {};
  const paint = (info, statId, infoId, urlId) => {
    const st = $(statId), inf = $(infoId);
    if (!info.url) { st.textContent = '연동 안 됨'; st.className = 'stat'; inf.textContent = ''; }
    else if (info.error) { st.textContent = '읽기 실패'; st.className = 'stat bad'; inf.textContent = info.error; }
    else if (info.busy && !info.fetched) { st.textContent = '읽는 중…'; st.className = 'stat'; inf.textContent = '채널 제목을 가져오는 중입니다. 잠시 뒤 새로고침하세요.'; }
    else if (info.fetched) {
      st.textContent = info.warning ? '연동됨 (제목만)' : '연동됨'; st.className = 'stat ok';
      inf.textContent = `${info.name} · 영상 ${info.count}편${info.count ? '' : ' (아직 올린 영상이 없어 뺄 주제도 없습니다)'} · ${info.fetched} 확인${info.busy ? ' · 다시 읽는 중…' : ''}${info.warning ? '\n⚠ ' + info.warning : ''}`;
    }
    else { st.textContent = '대기'; st.className = 'stat'; inf.textContent = '아직 읽지 않았습니다. [제목 다시 읽기]를 누르세요.'; }
    if (document.activeElement !== $(urlId)) $(urlId).value = info.url || '';
  };
  paint(P, 'chStatP', 'chInfoP', 'chUrlP'); paint(M, 'chStatM', 'chInfoM', 'chUrlM');
  const cur = channel === 'mindam' ? M : P;
  $('chLinkStat').textContent = cur.fetched && !cur.error ? `✓ 내 채널(${cur.name}) 영상 ${cur.count}편과 겹치는 주제는 자동으로 뺐습니다` : cur.url ? (cur.error || '내 채널 제목을 읽는 중…') : '내 유튜브 채널을 연동하면 이미 올린 주제를 자동으로 뺍니다';
}
async function saveChannel(ch) {
  const url = $(ch === 'mindam' ? 'chUrlM' : 'chUrlP').value.trim();
  try { await api('/api/config', ch === 'mindam' ? {민담_채널: url} : {내_채널: url}); toast(url ? '채널 주소 저장. 제목을 읽어 옵니다…' : '채널 연동을 해제했습니다.'); await refresh(); if (url) pollChannel(ch); } catch (e) { toast(e.message, true); }
}
async function refreshChannel(ch) {
  const st = $(ch === 'mindam' ? 'chStatM' : 'chStatP'); st.textContent = '읽는 중…'; st.className = 'stat';
  try { await api('/api/channel/refresh', {channel: ch}); toast('채널 제목을 다시 읽었습니다.'); await refresh(); } catch (e) { toast(e.message, true); await refresh(); }
}
async function pollChannel(ch, tries) {
  tries = tries || 0; if (tries > 20) return;
  try { const s = await api('/api/channel'); renderChannels(s); const info = s[ch] || {}; if (info.busy || (!info.fetched && !info.error)) return setTimeout(() => pollChannel(ch, tries + 1), 2000); await refresh(); } catch (e) {}
}

// ── 설정 ─────────────────────────────────────────────
async function saveAI() { await api('/api/config', {AI: $('sAI').value, 모델: $('sModel').value || ''}); toast('AI 저장: ' + $('sAI').options[$('sAI').selectedIndex].textContent); refresh(); }
async function saveKey(k) {
  const v = $('key_' + k).value.trim(); if (!v) return toast('새 키를 입력한 뒤 저장을 누르세요. (이미 저장된 키는 그대로 유지됩니다)', true);
  const body = k === 'inworld' ? {인월드_API_키: v} : {['API_키_' + k]: v};
  const ai = STATE.config.AI === 'deepseek-web' ? 'deepseek' : STATE.config.AI; if (k === ai) body.API_키 = v;
  await api('/api/config', body); $('key_' + k).value = ''; toast({deepseek: '딥시크', gemini: '제미나이', claude: '클로드', inworld: '인월드'}[k] + ' 키 저장됨'); refresh();
}
async function saveVoice(ch) {
  const body = ch === 'mindam' ? {인월드_목소리_민담: $('sVoiceM').value.trim(), 인월드_속도_민담: +$('sSpeedM').value}
    : {인월드_목소리_사람: $('sVoiceP').value.trim(), 인월드_속도_사람: +$('sSpeedP').value, 인월드_목소리: $('sVoiceP').value.trim(), 인월드_속도: +$('sSpeedP').value, 인월드_모델: $('sInworldModel').value};
  if (ch !== 'mindam' && !body.인월드_목소리_사람) return toast('목소리 ID를 입력하세요', true);
  await api('/api/config', body); toast(pname(ch) + (ch === 'mindam' && !body.인월드_목소리_민담 ? ' 목소리 비움 → ' + pname('person') + ' 목소리를 같이 씁니다' : ' 목소리 저장됨')); refresh();
}
async function saveCpm() { await api('/api/config', {분당_글자수: +$('sCpm').value, 인월드_모델: $('sInworldModel').value}); toast('저장됨'); refresh(); }
async function saveTelegramToken() { const token = $('tgToken').value.trim(); if (!token) return toast('BotFather에서 받은 봇 토큰을 입력하세요.', true); await api('/api/config', {텔레그램_봇_토큰: token}); $('tgToken').value = ''; toast('토큰 저장. 이제 봇에게 메시지를 보내고 채팅 자동 찾기를 누르세요.'); refresh(); }
async function findTelegramChats() { try { const r = await api('/api/telegram/chats', {}); $('tgChats').innerHTML = (r.chats || []).map(x => `<option value="${esc(x.id)}">${esc(x.name)} · ${esc(x.id)}</option>`).join('') || '<option value="">찾은 채팅이 없습니다</option>'; if (!r.chats.length) toast('텔레그램에서 봇에게 메시지를 먼저 보낸 뒤 다시 눌러주세요.', true); } catch (e) { toast(e.message, true); } }
async function saveTelegramChat() { const id = $('tgChats').value; if (!id) return toast('저장할 채팅을 선택하세요.', true); await api('/api/config', {텔레그램_채팅_ID: id}); toast('텔레그램 채팅 저장됨'); refresh(); }
async function saveTelegramEnabled() { await api('/api/config', {텔레그램_알림: $('tgEnabled').checked}); toast($('tgEnabled').checked ? '텔레그램 알림 켬' : '텔레그램 알림 끔'); }
async function testTelegram() { try { await api('/api/telegram/test', {}); toast('텔레그램으로 테스트 메시지를 보냈습니다.'); } catch (e) { toast(e.message, true); } }
async function loadMascot() {
  const path = ((STATE.profiles || {}).person || {}).마스코트 && STATE.profiles.person.마스코트.이미지 || 'assets/캐릭터/해.png';
  if (!$('mascotImg')) return;
  try { const r = await fetch('/api/image?path=' + encodeURIComponent(path), {cache: 'no-store'}); if (!r.ok) throw 0;
    $('mascotImg').src = '/api/image?path=' + encodeURIComponent(path) + '&t=' + Date.now(); $('mascotImg').classList.remove('hidden'); $('mascotStat').textContent = '저장됨'; $('mascotStat').className = 'stat ok'; }
  catch (e) { $('mascotImg').classList.add('hidden'); $('mascotStat').textContent = '파일 없음'; $('mascotStat').className = 'stat'; }
}
async function loadEditorSettings() {
  loadMascot();
  try {
    const info = await get8765('/api/info'); if (!info.config) throw new Error('편집프로그램 응답 없음');
    const ui = info.config.gen_ui || {}, xy = ui.XY || {};
    for (const name of ['prompt', 'generate', 'download']) { const pair = xy[name] || []; $('s_' + name + '_x').value = pair[0] ?? ''; $('s_' + name + '_y').value = pair[1] ?? ''; }
    $('sWindowKeyword').value = ui.window_keyword || '드롭샷';
    const nm = {prompt: '입력창', generate: '생성', download: '다운로드'};
    $('sXyStatus').textContent = '저장된 좌표: ' + ['prompt', 'generate', 'download'].map(n => xy[n] ? `${nm[n]} (${xy[n].join(', ')})` : `${nm[n]} 없음`).join(' · ');
    await refreshKieStatus();
  } catch (e) { $('sXyStatus').textContent = '좌표를 불러오지 못했습니다 (편집프로그램이 꺼져 있나요?): ' + e.message; }
}
async function saveEditorXY() {
  try {
    const info = await get8765('/api/info'), ui = (info.config || {}).gen_ui || {}, XY = {...(ui.XY || {})};
    for (const name of ['prompt', 'generate', 'download']) { const x = $('s_' + name + '_x').value.trim(), y = $('s_' + name + '_y').value.trim(); if ((x && !y) || (!x && y)) throw new Error('X와 Y를 모두 입력하세요: ' + name); if (x && y) XY[name] = [Number(x), Number(y)]; }
    await post8765('/api/config', {gen_ui: {...ui, XY, window_keyword: $('sWindowKeyword').value.trim() || '드롭샷'}});
    $('sXyStatus').textContent = '✓ 좌표가 저장됐습니다'; toast('좌표 저장됨'); checkReady(); return true;
  } catch (e) { $('sXyStatus').textContent = '좌표 저장 실패: ' + e.message; toast(e.message, true); return false; }
}
async function captureEditorXY(name) {
  const label = {prompt: '프롬프트 입력창', generate: '생성 버튼', download: '다운로드 버튼'}[name]; const counters = [...document.querySelectorAll('.xy-countdown')];
  const setC = (t, on) => counters.forEach(c => { c.textContent = t; c.classList.toggle('active', !!on); });
  xyStatus(`마우스를 드롭샷의 ${label} 위에 올려 두세요`); setC('6', true);
  try {
    const capture = post8765('/api/gen/capture', {seconds: 6});
    for (let s = 6; s >= 1; s--) { setC(String(s), true); await new Promise(r => setTimeout(r, 1000)); }
    const j = await capture; $('s_' + name + '_x').value = j.x; $('s_' + name + '_y').value = j.y; await saveEditorXY(); xyStatus(`✓ ${label} 좌표 저장 (${j.x}, ${j.y})`);
  } catch (e) { xyStatus('좌표 잡기 실패: ' + e.message); }
  finally { setC('6', false); if (obStep === 4 && !$('view-onboard').classList.contains('hidden')) renderOnboard(); }
}
async function testEditorXY(name) { const x = $('s_' + name + '_x').value, y = $('s_' + name + '_y').value; if (x === '' || y === '') return toast('좌표를 먼저 입력하거나 잡으세요', true); try { await post8765('/api/gen/test', {x: Number(x), y: Number(y)}); toast('마우스를 해당 좌표로 옮겼습니다'); } catch (e) { toast('좌표 테스트 실패: ' + e.message, true); } }
async function detectGenerateButton() {
  $('sXyStatus').textContent = "드롭샷에서 파란 '이미지 생성하기' 버튼을 찾는 중…";
  try { const j = await post8765('/api/gen/detect', {window_keyword: $('sWindowKeyword').value.trim() || '드롭샷'}); $('s_generate_x').value = j.x; $('s_generate_y').value = j.y; await saveEditorXY(); $('sXyStatus').textContent = `✓ 생성하기 버튼을 찾았습니다: X=${j.x}, Y=${j.y}`; }
  catch (e) { $('sXyStatus').textContent = '자동 찾기 실패: 드롭샷 창을 열고 프롬프트를 입력해 파란 생성 버튼이 보이게 한 뒤 다시 누르세요. (' + e.message + ')'; }
}

// ── 시작 ─────────────────────────────────────────────
(async function init() {
  setChannel('person');
  try { await refresh(); } catch (e) { toast('서버 상태를 불러오지 못했습니다: ' + e.message, true); }
  const busy = STATE && ((STATE.job && STATE.job.status === 'running') || (STATE.queue && STATE.queue.status === 'running'));
  renderQuickStyles(); renderStageCards(); renderFiles();
  if (STATE && !busy && !(STATE.config || {}).온보딩_완료 && !localStorage.getItem('ob_done') && !sessionStorage.getItem('ob_skipped')) showView('onboard');
  $('customTitle').addEventListener('input', renderSelection);
  for (const ch of ['person', 'mindam']) { const info = (STATE && STATE.channels || {})[ch] || {}; if (info.url && (info.busy || (!info.fetched && !info.error))) pollChannel(ch); }
  if (busy) startPolling(false);
  setInterval(refreshQueue, 3000); setInterval(checkReady, 20000); checkVersion(); setInterval(checkVersion, 30000); setInterval(() => refreshGallery(false), 4000);
  refreshGallery(true);
})();


// ── 채널 프로필 ─────────────────────────────────────
let profileSlot = 'person';
function pname(slot) { const p = (STATE && STATE.profiles && STATE.profiles[slot]) || {}; return p.이름 || (slot === 'mindam' ? '이야기형' : '정보형'); }
function renderProfileNames() {
  const P = STATE.profiles || {}; if (!P.person) return;
  $('brandSub').textContent = `${pname('person')} · ${pname('mindam')}`;
  document.querySelectorAll('#chSeg button, #adv-tools .seg button[data-ch], [data-ach]').forEach(b => { const k = b.dataset.ch || b.dataset.ach; b.textContent = pname(k); });
  document.querySelectorAll('.pname-person').forEach(el => el.textContent = pname('person'));
  document.querySelectorAll('.pname-mindam').forEach(el => el.textContent = pname('mindam'));
  document.querySelectorAll('#profSeg button').forEach(b => { b.textContent = `${pname(b.dataset.slot)} (${(P[b.dataset.slot] || {}).유형 || ''})`; });
  const pf = P[channel] || {}; $('chHint').textContent = `${pf.이름 || ''} · ${pf.영상_길이 || ''} · ${pf.유형 || ''}`;
}
function setProfileSlot(slot) { profileSlot = slot; document.querySelectorAll('#profSeg button').forEach(b => b.classList.toggle('on', b.dataset.slot === slot)); renderProfileForm(); }
function renderProfileForm() {
  const P = STATE.profiles || {}, p = P[profileSlot]; if (!p || !$('pf_이름')) return;
  const g = STATE.guidelines || {script: [], image: []};
  const opt = (list, cur) => `<option value="">(기본)</option>` + list.map(x => `<option value="${esc(x)}" ${x === cur ? 'selected' : ''}>${esc(x)}</option>`).join('');
  $('pf_지침_대본').innerHTML = opt(g.script, (p.지침 || {}).대본 || ''); $('pf_지침_이미지').innerHTML = opt(g.image, (p.지침 || {}).이미지 || '');
  $('pf_썸네일_레이아웃').innerHTML = Object.entries(STATE.layouts || {}).map(([k, v]) => `<option value="${k}" ${(p.썸네일 || {}).레이아웃 === k ? 'selected' : ''}>${esc(v)}</option>`).join('');
  for (const k of ['이름', '대상_시청자', '영상_길이', '해시태그', '설명', '카테고리', '면책', '업로드_폴더']) $('pf_' + k).value = p[k] || '';
  for (const k of ['검색어', '기본_태그']) $('pf_' + k).value = (p[k] || []).join(', ');
  const m = p.마스코트 || {}; for (const k of ['이름', '이미지', '설명', '프롬프트']) $('pf_마스코트_' + k).value = m[k] || '';
  const t = p.썸네일 || {}; for (const k of ['띠_문구', '화풍', '구도']) $('pf_썸네일_' + k).value = t[k] || '';
  $('profTypeHint').textContent = profileSlot === 'mindam' ? '이야기형: 기획 → 챕터로 창작 이야기를 씁니다 (야담·민담·전설 등)' : '정보형: 주제 하나를 9구간 나레이션으로 풀어 씁니다 (심리·건강·역사·상식 등)';
  $('profStat').textContent = p.이름 || '';
  profileSlot === 'person' ? loadMascot() : ($('mascotImg').classList.add('hidden'), $('mascotStat').textContent = '');
}
async function saveProfile() {
  const data = {};
  for (const k of ['이름', '대상_시청자', '영상_길이', '해시태그', '설명', '카테고리', '면책', '업로드_폴더', '검색어', '기본_태그']) data[k] = $('pf_' + k).value;
  data.지침 = {대본: $('pf_지침_대본').value, 이미지: $('pf_지침_이미지').value};
  data.마스코트 = {}; for (const k of ['이름', '이미지', '설명', '프롬프트']) data.마스코트[k] = $('pf_마스코트_' + k).value;
  data.썸네일 = {레이아웃: $('pf_썸네일_레이아웃').value}; for (const k of ['띠_문구', '화풍', '구도']) data.썸네일[k] = $('pf_썸네일_' + k).value;
  if (!data.이름.trim()) return toast('채널 이름을 넣으세요.', true);
  try { await api('/api/profile', {slot: profileSlot, data}); toast(`${data.이름} 프로필 저장됨 — 다음 제작부터 반영됩니다`); await refresh(); } catch (e) { toast(e.message, true); }
}


// ── 처음 설정(온보딩) ─────────────────────────────────
let obStep = 1, obEditor = null, obTimer = null;
const OB_STEPS = [[1, '채널'], [2, '대본 AI'], [3, '나레이션'], [4, '이미지(드롭샷)'], [5, '선택 사항'], [6, '완료']];
function xyStatus(t) { if ($('sXyStatus')) $('sXyStatus').textContent = t; if ($('obXyStatus')) $('obXyStatus').textContent = t; }
async function obProbeEditor() { try { const j = await get8765('/api/info'); obEditor = j && j.config ? j : null; } catch (e) { obEditor = null; } return obEditor; }
function obStatus() {
  const c = (STATE && STATE.config) || {}, P = (STATE && STATE.profiles) || {};
  const xy = ((obEditor && obEditor.config && obEditor.config.gen_ui) || {}).XY || {};
  return {
    1: !!(P.person && P.person.이름) && localStorage.getItem('ob_profile_ok') === '1',
    2: c.AI === 'deepseek-web' ? !!(STATE && STATE.web_alive) : !!c.키있음,
    3: !!(c.인월드키있음 && (c.인월드_목소리_사람 || c.인월드_목소리)),
    4: !!(obEditor && xy.prompt && xy.download),
    5: true,
  };
}
function obOpen(step) { showView('onboard'); obGo(step); }
function obGo(step) { obStep = Math.max(1, Math.min(6, step)); renderOnboard(); }
async function obSkip() { sessionStorage.setItem('ob_skipped', '1'); showView('wizard'); }
async function obFinish() { try { await api('/api/config', {온보딩_완료: true}); } catch (e) {} localStorage.setItem('ob_done', '1'); toast('설정이 끝났습니다. 주제를 고르고 제작을 시작하세요!'); await refresh(); showView('wizard'); }
async function obProfileOk(save) {
  if (save) { const 이름 = $('ob_name').value.trim(), 설명 = $('ob_desc').value.trim(), 대상 = $('ob_aud').value.trim(); if (!이름) return toast('채널 이름을 넣으세요.', true);
    try { await api('/api/profile', {slot: 'person', data: {이름, 설명, 대상_시청자: 대상, 해시태그: '#' + 이름.replace(/\s+/g, '')}}); await refresh(); } catch (e) { return toast(e.message, true); } }
  localStorage.setItem('ob_profile_ok', '1'); toast('채널 정보 확인'); obGo(2);
}
async function obSaveAI() {
  const mode = $('ob_aiMode').value;
  try {
    if (mode === 'deepseek-web') { await api('/api/config', {AI: 'deepseek-web'}); }
    else { const key = $('ob_aiKey').value.trim(); const body = {AI: mode}; if (key) { body['API_키_' + mode] = key; body.API_키 = key; } await api('/api/config', body); $('ob_aiKey').value = ''; }
    toast('대본 AI 저장'); await refresh(); renderOnboard();
  } catch (e) { toast(e.message, true); }
}
async function obSaveVoice() {
  const key = $('ob_inKey').value.trim(), voice = $('ob_voice').value.trim(), voiceM = $('ob_voiceM').value.trim();
  const body = {인월드_목소리_민담: voiceM, 인월드_속도_민담: +$('ob_speedM').value || 1.0, 인월드_속도_사람: +$('ob_speedP').value || 1.0};
  if (key) body.인월드_API_키 = key;
  if (voice) { body.인월드_목소리_사람 = voice; body.인월드_목소리 = voice; body.인월드_속도 = body.인월드_속도_사람; }
  if (!key && !voice && !voiceM) return toast('키 또는 목소리 ID를 입력하세요.', true);
  try { await api('/api/config', body); $('ob_inKey').value = ''; toast('나레이션 설정 저장'); await refresh(); renderOnboard(); } catch (e) { toast(e.message, true); }
}
async function renderOnboard() {
  if (!STATE) return;
  if (obStep === 4 || obStep === 6) await obProbeEditor(); else if (obEditor === null) obProbeEditor().then(() => { if (obStep === 6) renderOnboard(); });
  const st = obStatus(), c = STATE.config || {}, P = STATE.profiles || {}, pf = P.person || {};
  $('obSteps').innerHTML = OB_STEPS.map(([n, l]) => `<li class="${n === obStep ? 'on' : ''} ${st[n] ? 'done' : ''}" onclick="obGo(${n})"><span class="n">${st[n] ? '✓' : n}</span>${l}</li>`).join('');
  $('obPrev').disabled = obStep === 1; $('obNext').classList.toggle('hidden', obStep === 6);
  const S = (ok, okText, badText, wait) => `<div class="ob-status ${ok ? 'ok' : (wait ? 'wait' : 'bad')}">${ok ? '✓ ' + okText : (wait ? '… ' : '! ') + badText}</div>`;
  let h = '';
  if (obStep === 1) {
    h = `<h3>1. 내 채널은 어떤 채널인가요?</h3><p class="why">여기 적은 이름과 소개가 대본·제목·설명·썸네일 프롬프트에 그대로 들어갑니다. 지금은 기본값(${esc(pf.이름 || '')})이 들어 있으니, 내 채널에 맞게 고치고 저장하세요. 마스코트·태그 같은 세부 항목은 나중에 [설정 → 채널 프로필]에서 바꿀 수 있습니다.</p>`
      + S(st[1], '채널 정보를 확인했습니다', '아직 확인하지 않았습니다')
      + `<div class="keyrow"><b>채널 이름</b><input type="text" id="ob_name" value="${esc(pf.이름 || '')}"></div>
         <div class="keyrow"><b>시청자</b><input type="text" id="ob_aud" value="${esc(pf.대상_시청자 || '')}" placeholder="예: 관계·심리에 관심 있는 40~60대"></div>
         <div class="keyrow"><b>채널 소개</b></div><textarea id="ob_desc" class="short" style="font-size:15px">${esc(pf.설명 || '')}</textarea>
         <div class="row"><button class="primary" onclick="obProfileOk(true)">저장하고 다음</button><button onclick="obProfileOk(false)">이대로 쓰기</button></div>`;
  } else if (obStep === 2) {
    const isWeb = c.AI === 'deepseek-web', keys = STATE.keys || {};
    h = `<h3>2. 대본을 쓰는 AI</h3><p class="why">두 가지 방법 중 하나만 있으면 됩니다. <b>딥시크 웹</b>은 무료지만 크롬 확장 설치가 필요하고, <b>API 키</b> 방식은 키만 넣으면 됩니다. 둘 다 넣어 두면 하나가 막힐 때 자동으로 다른 쪽으로 넘어갑니다.</p>`
      + S(st[2], isWeb ? '딥시크 웹 연결됨 (chat.deepseek.com 탭 감지)' : `${c.AI} 키 저장됨`, isWeb ? '딥시크 확장이 아직 연결되지 않았습니다' : `${c.AI} 키가 없습니다`)
      + `<div class="keyrow"><b>방법</b><select id="ob_aiMode" onchange="renderOnboardAI()"><option value="deepseek-web" ${isWeb ? 'selected' : ''}>딥시크 웹 (무료 · 크롬 확장)</option><option value="gemini" ${c.AI === 'gemini' ? 'selected' : ''}>제미나이 API 키 (무료 한도 있음)</option><option value="deepseek" ${c.AI === 'deepseek' ? 'selected' : ''}>딥시크 API 키</option><option value="claude" ${c.AI === 'claude' ? 'selected' : ''}>클로드 API 키</option></select></div>
         <div id="ob_aiWeb" class="${isWeb ? '' : 'hidden'}"><ol class="ob-guide">
           <li>크롬 주소창에 <code>chrome://extensions</code> 를 입력해 엽니다.</li>
           <li>오른쪽 위 <b>개발자 모드</b>를 켭니다.</li>
           <li><b>압축해제된 확장 프로그램을 로드</b> → 아래 [확장 폴더 열기]로 열리는 <code>딥시크_확장</code> 폴더를 고릅니다.</li>
           <li>크롬에서 <a href="https://chat.deepseek.com" target="_blank" rel="noopener">chat.deepseek.com</a> 에 로그인한 탭을 하나 열어 둡니다 (창을 닫지 마세요).</li>
           <li>위 상태가 <b>연결됨</b>으로 바뀌면 끝입니다 (몇 초마다 자동 확인).</li></ol>
           <div class="row"><button onclick="openPath('${js(STATE.extension_dir || '딥시크_확장')}')">📁 확장 폴더 열기</button><button class="primary" onclick="obSaveAI()">딥시크 웹으로 저장</button></div></div>
         <div id="ob_aiKeyBox" class="${isWeb ? 'hidden' : ''}"><div class="keyrow"><b>API 키</b><input type="password" id="ob_aiKey" placeholder="${keys[c.AI] ? '저장됨 ' + keys[c.AI] + ' (바꿀 때만 입력)' : '키를 붙여 넣으세요'}"><button class="primary mini" onclick="obSaveAI()">저장</button></div>
           <p class="hint">제미나이 키: <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener">aistudio.google.com/apikey</a> · 딥시크 키: <a href="https://platform.deepseek.com" target="_blank" rel="noopener">platform.deepseek.com</a> · 클로드 키: <a href="https://console.anthropic.com" target="_blank" rel="noopener">console.anthropic.com</a></p></div>`;
  } else if (obStep === 3) {
    const keys = STATE.keys || {};
    h = `<h3>3. 나레이션 목소리 (인월드)</h3><p class="why">대본을 읽어 주는 음성입니다. 인월드(inworld.ai)에서 API 키를 받고, 채널마다 어울리는 목소리의 ID를 넣으세요. 두 채널은 목소리를 따로 씁니다 (이야기형을 비우면 정보형 목소리를 같이 씁니다).</p>`
      + S(st[3], `인월드 키 · ${pname('person')} ${c.인월드_목소리_사람 || c.인월드_목소리}${c.인월드_목소리_민담 ? ` · ${pname('mindam')} ${c.인월드_목소리_민담}` : ''}`, c.인월드키있음 ? '정보형 채널 목소리 ID가 없습니다' : '인월드 키가 없습니다')
      + `<ol class="ob-guide"><li><a href="https://inworld.ai" target="_blank" rel="noopener">inworld.ai</a> 가입 → API Keys 에서 키 발급</li><li>Voices 목록에서 한국어 목소리를 골라 ID(예: Sarah)를 복사. 정보형은 차분한 목소리, 이야기형은 구수한 이야기꾼 목소리가 어울립니다.</li><li>아래에 넣고 저장</li></ol>
         <div class="keyrow"><b>인월드 키</b><input type="password" id="ob_inKey" placeholder="${keys.inworld ? '저장됨 ' + keys.inworld + ' (바꿀 때만 입력)' : '키를 붙여 넣으세요'}"></div>
         <div class="keyrow"><b>${esc(pname('person'))}</b><input type="text" id="ob_voice" value="${esc(c.인월드_목소리_사람 || c.인월드_목소리 || '')}" placeholder="목소리 ID (예: Sarah)" style="max-width:220px"><label>속도 <input type="number" id="ob_speedP" value="${c.인월드_속도_사람 || c.인월드_속도 || 1.0}" step="0.05" min="0.5" max="1.5" style="width:80px"></label></div>
         <div class="keyrow"><b>${esc(pname('mindam'))}</b><input type="text" id="ob_voiceM" value="${esc(c.인월드_목소리_민담 || '')}" placeholder="비우면 ${esc(pname('person'))} 목소리 사용" style="max-width:220px"><label>속도 <input type="number" id="ob_speedM" value="${c.인월드_속도_민담 || c.인월드_속도 || 1.0}" step="0.05" min="0.5" max="1.5" style="width:80px"></label></div>
         <div class="row"><button class="primary" onclick="obSaveVoice()">저장</button><span class="hint">속도 1.0이 보통, 0.9는 조금 느리게 (시니어 시청자는 0.9~1.0 권장).</span></div>`;
  } else if (obStep === 4) {
    const xy = ((obEditor && obEditor.config && obEditor.config.gen_ui) || {}).XY || {};
    const nm = {prompt: '프롬프트 입력창', generate: '생성하기 버튼', download: '이미지 다운로드'};
    h = `<h3>4. 이미지 만들기 (편집프로그램 + 드롭샷 좌표)</h3><p class="why">이미지는 드롭샷 AI 화면을 자동으로 클릭해서 만듭니다. 편집프로그램이 켜져 있어야 하고, 드롭샷 창에서 세 곳의 위치를 한 번만 잡아 두면 됩니다. 창 크기나 모니터가 바뀌면 다시 잡으세요.</p>`
      + S(!!obEditor, '편집프로그램 연결됨', '편집프로그램이 꺼져 있습니다 — 바탕화면의 유튜브_자동화_시작 파일을 다시 실행하세요')
      + (obEditor ? S(st[4], '드롭샷 좌표 저장됨', '좌표가 아직 없습니다') : '')
      + `<ol class="ob-guide"><li>[드롭샷 AI 열기]로 드롭샷을 열고 로그인합니다. 이미지 생성 화면(보드)이 보이게 둡니다.</li><li>항목마다 [6초 좌표 잡기]를 누른 뒤 <b>6초 안에 마우스를 그 위치에 올려 두세요</b>. 숫자가 1이 될 때까지 그대로.</li><li>생성 버튼은 [자동 찾기]를 먼저 시도해 보세요.</li><li>[위치 확인]을 누르면 마우스가 그 자리로 이동합니다. 맞으면 끝.</li></ol>
         <div class="row"><button class="primary" onclick="window.open('https://aistudio.dropshot.io/ko/workspace/board','_blank','noopener')">↗ 드롭샷 AI 열기</button><span class="xy-countdown" >6</span><span class="hint" id="obXyStatus"></span></div>`
      + ['prompt', 'generate', 'download'].map(n => `<div class="keyrow"><b>${nm[n]}</b><span class="stat ${xy[n] ? 'ok' : 'bad'}">${xy[n] ? `(${xy[n].join(', ')})` : '없음'}</span>${n === 'generate' ? `<button class="mini primary" onclick="detectGenerateButton().then(renderOnboard)">자동 찾기</button>` : ''}<button class="mini ${n === 'generate' ? '' : 'primary'}" onclick="captureEditorXY('${n}')">6초 좌표 잡기</button><button class="mini" onclick="testEditorXY('${n}')">위치 확인</button></div>`).join('')
      + `<div class="row"><button class="mini" onclick="renderOnboard()">상태 다시 확인</button></div>`;
  } else if (obStep === 5) {
    const ch = STATE.channels || {}, keys = STATE.keys || {};
    h = `<h3>5. 있으면 좋은 것들 (건너뛰어도 됩니다)</h3><p class="why">지금 안 해도 영상은 만들어집니다. 나중에 [설정]에서 언제든 넣을 수 있습니다.</p>
         <ul class="ob-guide">
           <li><b>내 유튜브 채널 연동</b> — 이미 올린 제목과 겹치는 주제를 자동으로 뺍니다. ${ch.person && ch.person.url ? '✓ 연동됨' : '<button class="mini" onclick="showView(\'settings\');setTimeout(()=>$(\'chanCard\').scrollIntoView({behavior:\'smooth\'}),50)">설정에서 넣기</button>'}</li>
           <li><b>유튜브 API 키</b> — 조회수·게시일까지 읽어 채널 분석에 씁니다. ${c.유튜브_API_키 ? '✓ 저장됨' : '(선택)'}</li>
           <li><b>KIE 키</b> — 앞 7장을 움직이는 영상으로 만듭니다. 없으면 이 단계만 건너뜁니다.</li>
           <li><b>텔레그램 알림</b> — 한 편이 끝날 때 휴대폰으로 알려 줍니다. ${c.텔레그램_토큰 ? '✓ 저장됨' : '(선택)'}</li>
           <li><b>마스코트 그림</b> — 채널 캐릭터가 있으면 [설정 → 채널 프로필]에 넣고 드롭샷 References에 올려 두세요.</li>
         </ul>`;
  } else {
    const all = [1, 2, 3, 4].every(n => st[n]);
    h = `<div class="ob-done"><div class="big">${all ? '🎉' : '🧭'}</div><h3>${all ? '준비가 끝났습니다!' : '아직 남은 항목이 있어요'}</h3><p class="why">${all ? '이제 [만들기] 화면에서 주제를 고르고 제작 시작을 누르면 대본부터 최종 영상까지 자동으로 만듭니다.' : '위 단계 중 ✓ 가 없는 항목을 마저 끝내야 제작이 실패하지 않습니다. 지금 시작해도 되지만 그 단계에서 멈춥니다.'}</p>
         <div class="row" style="justify-content:center"><button class="primary" onclick="obFinish()">${all ? '🚀 만들기 시작' : '그래도 시작하기'}</button>${all ? '' : `<button onclick="obGo(${[1, 2, 3, 4].find(n => !st[n]) || 1})">남은 항목으로</button>`}</div></div>`;
  }
  $('obBody').innerHTML = h;
  clearTimeout(obTimer);
  if (obStep === 2 && (c.AI === 'deepseek-web') && !st[2]) obTimer = setTimeout(async () => { if ($('view-onboard').classList.contains('hidden') || obStep !== 2) return; try { STATE = await api('/api/state'); } catch (e) {} renderOnboard(); }, 4000);
}
function renderOnboardAI() { const web = $('ob_aiMode').value === 'deepseek-web'; $('ob_aiWeb').classList.toggle('hidden', !web); $('ob_aiKeyBox').classList.toggle('hidden', web); }
