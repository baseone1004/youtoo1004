/* 대본 만들기 · 딥시크 웹 연결 (content script)
 *
 * chat.deepseek.com 탭에서 돌아가며, 대본선택 서버(127.0.0.1:8766)의 작업 큐를 가져와
 *   새 대화 열기 → 지침+요청 입력 → 전송 → 답변이 끝날 때까지 대기 → 답변 본문 수집 → 서버로 전달
 * 을 반복합니다. 한 탭만 담당 탭이 됩니다(여러 탭이 같은 작업을 가져가지 않도록).
 */
(() => {
  if (window.__DAEBON_DS_BRIDGE__) return;
  window.__DAEBON_DS_BRIDGE__ = true;

  const SERVER = 'http://127.0.0.1:8766';
  const VERSION = '1.0.0';
  const PENDING_KEY = 'DAEBON_PENDING_JOB';          // 새 대화를 위해 페이지를 새로 열 때 작업을 이어받는 용도
  const DRIVER_KEY = 'DAEBON_DRIVER';                // 담당 탭 (localStorage: 탭끼리 공유)
  const DRIVER_TTL = 25000;
  const DRIVER_ID = Math.random().toString(36).slice(2);
  const FIRST_REPLY_MS = 4 * 60 * 1000;             // 답변이 시작조차 안 되면 실패
  const REPLY_TIMEOUT_MS = 20 * 60 * 1000;
  const QUIET_MS = 4000;                             // 글자 변화가 이 시간 동안 없고 중지 버튼도 없으면 완료
  const BLOCKED = /서버가 바쁩니다|서버가 혼잡|잠시 후 다시 시도|다시 시도해 주세요|사용량이 많아|로그인이 만료|다시 로그인|服务器繁忙|系统繁忙|登录已过期|server is busy|too many requests|rate limit|try again later|something went wrong/i;

  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const norm = s => String(s || '').replace(/\s+/g, ' ').trim();

  /* ───────── 상태 표시 ───────── */
  const badge = document.createElement('div');
  badge.style.cssText = 'position:fixed;right:14px;bottom:14px;z-index:2147483647;background:#111827;color:#e5e9f2;border:1px solid #4f7cff;border-radius:10px;padding:8px 12px;font:12.5px/1.4 "Malgun Gothic",sans-serif;max-width:340px;box-shadow:0 8px 24px rgba(0,0,0,.4);pointer-events:none;opacity:.92';
  const setBadge = (msg, color) => { badge.textContent = '대본 만들기 · ' + msg; badge.style.borderColor = color || '#4f7cff'; if (!badge.isConnected) document.body.appendChild(badge); };

  /* ───────── 서버 통신 ───────── */
  async function api(path, method = 'GET', body) {
    const r = await fetch(SERVER + path, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }

  /* ───────── 담당 탭 ───────── */
  const readDriver = () => { try { return JSON.parse(localStorage.getItem(DRIVER_KEY) || 'null'); } catch (_) { return null; } };
  const writeDriver = () => localStorage.setItem(DRIVER_KEY, JSON.stringify({ id: DRIVER_ID, at: Date.now() }));
  function iAmDriver() {
    const d = readDriver();
    if (!d || Date.now() - d.at > DRIVER_TTL || d.id === DRIVER_ID) { writeDriver(); return true; }
    return false;
  }

  /* ───────── DOM 헬퍼 (DeepSeek 화면) ───────── */
  const visible = el => { if (!el || !el.isConnected) return false; const r = el.getBoundingClientRect(), s = getComputedStyle(el); return r.width > 6 && r.height > 6 && s.display !== 'none' && s.visibility !== 'hidden'; };

  function assistantNodes() {
    // 실측(2026-09): 답변 본문은 div.ds-markdown.ds-assistant-message-main-content 하나. 문단(.ds-markdown-paragraph)은 잡지 않는다.
    const sel = ['[data-role=assistant]', '[data-message-author-role=assistant]', '.ds-markdown'];
    let nodes = [...new Set(sel.flatMap(s => [...document.querySelectorAll(s)]))].filter(n => n.isConnected && !n.closest('form,[contenteditable=true]')
      && !(n.parentElement && n.parentElement.closest('.ds-markdown')));
    if (!nodes.length) {   // 스킨이 바뀐 경우: 본문에 [제목]/===NNN=== 이 보이는 영역을 답변으로 간주
      nodes = [...document.querySelectorAll('main div, #root div')].filter(n => visible(n) && !n.querySelector('textarea,[contenteditable=true]') &&
        /\[\s*(제목|대본|A|원본|제목 후보|챕터 계획)\s*\]|===\s*\d{3,4}\s*===/.test(n.innerText || ''));
    }
    // 서로 포함 관계면 안쪽(작은) 것만
    nodes = nodes.filter(n => !nodes.some(o => o !== n && n.contains(o) && norm(o.innerText).length >= norm(n.innerText).length * 0.7));
    nodes = nodes.filter(n => { const r = n.closest('[data-role],[data-message-author-role]'); const who = r && (r.getAttribute('data-role') || r.getAttribute('data-message-author-role') || ''); return !/user/i.test(who); });
    return nodes;
  }
  const lastAssistantText = () => { const n = assistantNodes(); return n.length ? String(n[n.length - 1].innerText || '').trim() : ''; };
  const conversationEmpty = () => assistantNodes().filter(n => norm(n.innerText).length > 40).length === 0;
  const conversationId = () => (String(location.pathname).match(/\/chat\/s\/([^/?#]+)/i) || [])[1] || '';

  function findComposer() {
    const list = [...document.querySelectorAll('textarea'), ...document.querySelectorAll('[contenteditable=true]')].filter(visible);
    return list.sort((a, b) => b.getBoundingClientRect().bottom - a.getBoundingClientRect().bottom)[0] || null;
  }
  async function waitComposer(timeout = 30000) {
    const end = Date.now() + timeout; let same = null, stable = 0;
    while (Date.now() < end) {
      const c = findComposer();
      if (c && !c.disabled) { if (c === same) stable++; else { same = c; stable = 1; } if (stable >= 3) return c; } else { same = null; stable = 0; }
      await sleep(400);
    }
    throw new Error('입력칸을 찾지 못했습니다 (로그인 상태인지 확인)');
  }
  function setComposerText(el, text) {
    el.focus();
    if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
      if (setter) setter.call(el, text); else el.value = text;
      el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true }));
    } else {
      el.textContent = '';
      el.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, inputType: 'insertText', data: text }));
      el.textContent = text;
      el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: text }));
    }
  }
  const composerText = el => el ? String(el.value !== undefined && el.value !== null ? el.value : (el.textContent || '')) : '';

  let idleSendHtml = '';
  function sendButton() {
    const ta = findComposer(); const bottom = ta ? ta.getBoundingClientRect().bottom : 1e9;
    const list = [...document.querySelectorAll('[role=button],button')].filter(visible).filter(el =>
      (/ds-button--primary/.test(el.className) && /ds-button--filled/.test(el.className)) ||
      /보내기|전송|send/i.test([el.getAttribute('aria-label'), el.title, el.getAttribute('data-testid')].filter(Boolean).join(' ')));
    return list.sort((a, b) => Math.abs(a.getBoundingClientRect().top - bottom) - Math.abs(b.getBoundingClientRect().top - bottom))[0] || null;
  }
  function rememberIdleSend() { const b = sendButton(); if (b && !idleSendHtml) idleSendHtml = b.innerHTML; }
  function stopButton() {
    // DeepSeek 는 라벨 없이 전송 버튼 아이콘이 '중지'로 바뀐다. 대기 상태 아이콘과 다르면 생성 중으로 본다.
    const b = sendButton();
    if (b && idleSendHtml && b.innerHTML !== idleSendHtml) return b;
    const re = /(?:^|[-_\s])(?:생성\s*중지|답변\s*중지|중지|stop(?:\s*(?:generating|generation|response))?)(?:$|[-_\s])/i;
    return [...document.querySelectorAll('button,[role=button]')].filter(visible).find(el =>
      [el.getAttribute('aria-label'), el.title, el.getAttribute('data-testid'), el.getAttribute('data-state')].filter(Boolean).some(v => re.test(norm(v)))) || null;
  }
  function clickSend(composer) {
    const send = sendButton();
    if (send) { try { send.click(); return true; } catch (_) { } }
    const init = { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, composed: true };
    composer.focus(); ['keydown', 'keypress', 'keyup'].forEach(t => composer.dispatchEvent(new KeyboardEvent(t, init)));
    return false;
  }
  function findNewChat() {
    const re = /새\s*대화|새\s*채팅|새로운\s*대화|new\s*chat|new\s*conversation/i;
    const el = [...document.querySelectorAll('button,[role=button],a')].filter(visible).find(el =>
      re.test([el.getAttribute('aria-label'), el.title, el.getAttribute('data-testid'), el.textContent].filter(Boolean).join(' ')));
    return el || [...document.querySelectorAll('a[href="/"], a[href="/chat"], a[href^="/a/chat"]')].filter(visible)[0] || null;
  }
  async function ensureFreshChat(job) {
    // 주소에 대화 번호(/a/chat/s/…)가 없고 답변 말풍선도 없으면 새 대화
    const okNow = async () => { for (let i = 0; i < 4; i++) { if (conversationId() || !conversationEmpty()) return false; await sleep(350); } return true; };
    if (await okNow()) return true;
    const ctl = findNewChat();
    if (ctl) { try { ctl.click(); } catch (_) { } for (let i = 0; i < 12; i++) { await sleep(400); if (await okNow()) { await sleep(500); return true; } } }
    // 버튼으로 안 되면 페이지를 새로 연다 (작업은 세션에 보관했다가 이어받음)
    sessionStorage.setItem(PENDING_KEY, JSON.stringify(job));
    location.href = 'https://chat.deepseek.com/';
    await sleep(30000);
    return false;
  }

  /* ───────── 작업 실행 ───────── */
  async function runJob(job) {
    setBadge('새 대화 준비 중…');
    sessionStorage.setItem(PENDING_KEY, JSON.stringify(job));
    if (!(await ensureFreshChat(job))) return;                      // 새로고침 → 다시 이어받음
    let composer = await waitComposer();
    await sleep(600); rememberIdleSend();
    const before = assistantNodes().length;
    for (let attempt = 1; attempt <= 3; attempt++) {
      setComposerText(composer, job.text); await sleep(700);
      if (!composerText(findComposer()).includes(job.text.slice(0, 40))) { composer = await waitComposer(10000); setComposerText(composer, job.text); await sleep(900); }
      clickSend(composer);
      const sentDeadline = Date.now() + 20000; let sent = false;
      while (Date.now() < sentDeadline) { await sleep(500); if (stopButton() || assistantNodes().length > before || !composerText(findComposer()).trim()) { sent = true; break; } }
      if (sent) break;
      if (attempt === 3) throw new Error('메시지를 보내지 못했습니다');
      composer = await waitComposer();
    }
    setBadge('답변 기다리는 중…');
    const start = Date.now(); let lastText = '', lastChange = Date.now(), lastBeat = 0, started = false;
    while (true) {
      await sleep(1000);
      const now = Date.now();
      const text = lastAssistantText();
      if (assistantNodes().length > before || (text && text !== lastText)) started = true;
      if (text !== lastText) { lastText = text; lastChange = now; }
      if (now - lastBeat > 8000) { lastBeat = now; try { await api('/api/web/beat', 'POST', { id: job.id, progress: (document.hidden ? '⚠ 딥시크 창이 가려져 있어 답변이 그려지지 않음 · ' : '') + `${text.length}자`, hidden: !!document.hidden, v: VERSION }); } catch (_) { } setBadge(`답변 받는 중 · ${text.length.toLocaleString()}자`); }
      const generating = !!stopButton();
      if (!text && now - start > 20000 && (now - start) % 10000 < 1100) {   // 답변 영역이 안 그려지면 목록을 아래로 밀어 렌더를 유도
        const vl = document.querySelector('.ds-virtual-list'); if (vl) vl.scrollTop = vl.scrollHeight;
      }
      if (!started && now - start > FIRST_REPLY_MS) throw new Error('답변이 시작되지 않았습니다');
      if (now - start > REPLY_TIMEOUT_MS) throw new Error('답변 대기 시간 초과');
      if (started && !generating && text.length > 0 && now - lastChange > QUIET_MS) break;
      if (started && !generating && text.length === 0 && now - lastChange > 15000) throw new Error('답변 본문을 읽지 못했습니다');
    }
    // 답변 본문에 "서버 혼잡" 같은 안내만 있으면 실패 처리 → 서버가 재시도
    const final = lastAssistantText();
    if (final.length < 40 && BLOCKED.test(final)) throw new Error('딥시크 안내문: ' + final.slice(0, 80));
    await api('/api/web/result', 'POST', { id: job.id, text: final });
    sessionStorage.removeItem(PENDING_KEY);
    setBadge(`전달 완료 · ${final.length.toLocaleString()}자`, '#22c55e');
  }

  async function mainLoop() {
    // 새로고침 전에 남겨둔 작업 이어받기
    let pending = null;
    try { pending = JSON.parse(sessionStorage.getItem(PENDING_KEY) || 'null'); } catch (_) { }
    if (pending && pending.id) { writeDriver(); try { await runJob(pending); } catch (e) { try { await api('/api/web/result', 'POST', { id: pending.id, error: String(e.message || e) }); } catch (_) { } sessionStorage.removeItem(PENDING_KEY); } }
    while (true) {
      try {
        if (!iAmDriver()) { setBadge('다른 딥시크 탭이 담당 중 (이 탭은 대기)', '#8b95ad'); await sleep(8000); continue; }
        writeDriver();
        setBadge('연결됨 · 작업 대기 중');
        const job = await api(`/api/web/next?wait=20&v=${VERSION}&hidden=${document.hidden ? 1 : 0}`);
        if (job && job.id) {
          writeDriver();
          try { await runJob(job); }
          catch (e) {
            const msg = String(e.message || e);
            setBadge('실패: ' + msg, '#ef4444');
            try { await api('/api/web/result', 'POST', { id: job.id, error: msg }); } catch (_) { }
            sessionStorage.removeItem(PENDING_KEY);
            await sleep(5000);
          }
        }
      } catch (e) {
        setBadge('대본선택 서버(8766)에 연결 안 됨 — 대본선택.bat 을 켜 두세요', '#f59e0b');
        await sleep(6000);
      }
    }
  }
  setInterval(() => { if (readDriver()?.id === DRIVER_ID) writeDriver(); }, 8000);
  mainLoop();
})();
