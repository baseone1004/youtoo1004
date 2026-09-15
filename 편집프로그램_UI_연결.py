"""Apply small, idempotent usability updates to the separately installed editor UI."""

from pathlib import Path
import shutil
import re


def move_preview_below_controls(source):
    image = re.search(r'^    <img id="subPrevImg"[^\n]+\n', source, flags=re.M)
    anchor = '<div class="row" style="margin-top:8px"><div class="meta"><b>미리보기</b><small>지금 설정으로 자막이 어떻게 보이는지</small></div><button onclick="subPreview()">자막 미리보기</button></div>'
    if not image or anchor not in source:
        return source
    if source.index(image.group()) < source.index('🎵 배경음악'):
        return source
    source = source.replace(image.group(), '', 1)
    return source.replace(anchor, anchor + '\n' + image.group().rstrip('\n'), 1)


def warn_before_sample_render(source):
    marker = "// youtoo-sample-render-warning"
    if marker in source:
        return source
    old = "async function startRender() {\n  const req = collect();"
    new = ("async function startRender() {\n"
           "  // youtoo-sample-render-warning\n"
           "  const req = collect();\n"
           "  if ([req.srt, req.images, req.narration].some(p => /[\\\\/]samples[\\\\/]/i.test(p || ''))) {\n"
           "    if (!confirm('현재 샘플 자료가 선택돼 있습니다. 이 샘플로 영상을 만들까요?')) return;\n"
           "  }")
    if old not in source:
        raise ValueError("편집프로그램 영상 만들기 버튼 구조가 바뀌었습니다.")
    return source.replace(old, new, 1)


def apply(editor_dir):
    page = Path(editor_dir) / "static" / "index.html"
    if not page.is_file():
        return False
    original = page.read_text(encoding="utf-8")
    source = original
    source = source.replace('id="srt_outline" value="2"', 'id="srt_outline" value="3.5"')
    if "// youtoo-live-subtitle-preview" in source:
        updated = warn_before_sample_render(move_preview_below_controls(source))
        if updated != original:
            page.write_text(updated, encoding="utf-8")
            return True
        return False

    old_preview = """async function subPreview() {
  const body = { ...subStyle(), width: +$('e_res').value.split('x')[0], height: +$('e_res').value.split('x')[1], text: '자막 미리보기입니다. 이렇게 보입니다.' };
  try { const r = await fetch('/api/subtitle_preview', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
    if (!r.ok) throw new Error((await r.json()).detail); const img = $('subPrevImg'); img.src = URL.createObjectURL(await r.blob()); img.classList.remove('hidden'); }
  catch (e) { toast('미리보기 실패: ' + e.message, true); }
}"""
    new_preview = """let subtitlePreviewVersion = 0;
async function subPreview() {
  const version = ++subtitlePreviewVersion;
  const body = { ...subStyle(), width: +$('e_res').value.split('x')[0], height: +$('e_res').value.split('x')[1], text: '자막 미리보기입니다. 이렇게 보입니다.' };
  try { const r = await fetch('/api/subtitle_preview', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
    if (!r.ok) throw new Error((await r.json()).detail);
    const blob = await r.blob(); if (version !== subtitlePreviewVersion) return;
    const img = $('subPrevImg'); if (img.dataset.objectUrl) URL.revokeObjectURL(img.dataset.objectUrl);
    img.dataset.objectUrl = URL.createObjectURL(blob); img.src = img.dataset.objectUrl; img.classList.remove('hidden'); }
  catch (e) { if (version === subtitlePreviewVersion) toast('미리보기 실패: ' + e.message, true); }
}"""
    old_font = "$('srt_font').innerHTML = fl.fonts.map(f => `<option value=\"${esc(f.name)}\">${esc(f.name)}</option>`).join('');"
    new_font = "$('srt_font').innerHTML = fl.fonts.map(f => `<option value=\"${esc(f.name)}\">${esc(koreanFontNames[f.name] || f.name)}</option>`).join('');"
    marker = "document.querySelectorAll('input, select').forEach(el => el.addEventListener('change', () => { syncBoxes(); saveCfg(); }));"
    extra = """// youtoo-live-subtitle-preview
const koreanFontNames = {'Malgun Gothic':'맑은 고딕','Malgun Gothic SemiLight':'맑은 고딕 라이트','Gulim':'굴림','Dotum':'돋움','Batang':'바탕','Gungsuh':'궁서','Noto Sans KR':'노토 산스 한글'};
let subtitlePreviewTimer = null;
function scheduleSubtitlePreview() { clearTimeout(subtitlePreviewTimer); subtitlePreviewTimer = setTimeout(subPreview, 450); }
for (const id of ['srt_font','e_font_size','srt_font_size','srt_align','srt_color','srt_outline_color','srt_outline','srt_bold','srt_box','srt_margin_v','e_res']) {
  $(id).addEventListener('input', () => {
    if (id === 'e_font_size') $('srt_font_size').value = $('e_font_size').value;
    if (id === 'srt_font_size') $('e_font_size').value = $('srt_font_size').value;
    scheduleSubtitlePreview();
  });
  $(id).addEventListener('change', scheduleSubtitlePreview);
}
"""
    for old in (old_preview, old_font, marker, "    applyCfg(info.config && info.config.ui);"):
        if old not in source:
            raise ValueError("편집프로그램 화면 구조가 바뀌어 자동 미리보기 적용을 건너뜁니다.")
    updated = source.replace(old_preview, new_preview, 1)
    updated = updated.replace(old_font, new_font, 1)
    updated = updated.replace(marker, marker + "\n" + extra, 1)
    updated = updated.replace("    applyCfg(info.config && info.config.ui);",
                              "    applyCfg(info.config && info.config.ui);\n    scheduleSubtitlePreview();", 1)
    updated = warn_before_sample_render(move_preview_below_controls(updated))
    backup = page.with_name("index.before-live-preview.html")
    if not backup.exists():
        shutil.copy2(page, backup)
    page.write_text(updated, encoding="utf-8")
    return True
