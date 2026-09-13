fetch('http://127.0.0.1:8766/api/web/status').then(r => r.json()).then(j => {
  const el = document.getElementById('st');
  el.textContent = (j.alive ? '🟢 chat.deepseek.com 탭이 서버에 연결됨' : '🟠 서버는 켜져 있지만 chat.deepseek.com 탭이 아직 연결되지 않음')
    + ` · 대기 ${j.pending}개 · 진행 ${j.taken.length}개`;
}).catch(() => { document.getElementById('st').textContent = '🔴 대본선택 서버(127.0.0.1:8766)에 연결할 수 없습니다. 대본선택.bat 을 켜 주세요.'; });

document.getElementById('open').onclick = () => {
  const w = 600, h = 860;
  chrome.windows.create({ url: 'https://chat.deepseek.com/', type: 'popup', width: w, height: h, left: Math.max(0, screen.availWidth - w - 10), top: 30, focused: false });
};
