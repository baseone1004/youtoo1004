# -*- coding: utf-8 -*-
"""편집프로그램에 한글 글꼴 목록과 개인 글꼴 등록 기능을 설치한다."""
from pathlib import Path


def _replace(source, old, new, label):
    if new in source:
        return source
    if old not in source:
        raise ValueError(f"편집프로그램 {label} 구조가 바뀌었습니다.")
    return source.replace(old, new, 1)


def apply(editor_dir):
    editor = Path(editor_dir)
    template = Path(__file__).with_name("편집프로그램_글꼴_저장소.py")
    module = editor / "core" / "user_fonts.py"
    changed = False
    if not module.is_file() or module.read_bytes() != template.read_bytes():
        module.write_bytes(template.read_bytes())
        changed = True

    app_file = editor / "app.py"
    source = app_file.read_text(encoding="utf-8")
    old = '''@app.get("/api/fonts")
def api_fonts():
    from core.fonts import installed_fonts
    return {"fonts": installed_fonts()}
'''
    new = '''@app.get("/api/fonts")
def api_fonts():
    from core.fonts import installed_fonts
    from core.user_fonts import list_fonts
    return {"fonts": installed_fonts() + list_fonts()}


class FontImportRequest(BaseModel):
    file_name: str
    data: str
    label: str = ""


@app.post("/api/fonts/import")
def api_import_font(req: FontImportRequest):
    from core.user_fonts import import_font
    try:
        return import_font(req.file_name, req.data, req.label)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
'''
    updated = _replace(source, old, new, "글꼴 API")
    if updated != source:
        app_file.write_text(updated, encoding="utf-8")
        changed = True

    html_file = editor / "static" / "index.html"
    source = html_file.read_text(encoding="utf-8")
    updated = source
    old = '''      <label class="f">추가 글꼴 폴더(.ttf) <span class="inline"><span class="path" id="p_fonts_dir" style="flex:1">-</span><button class="mini" onclick="pick('fonts_dir','folder','글꼴 폴더')">선택</button></span></label>'''
    new = '''      <label class="f">내 글꼴 이름 <input type="text" id="custom_font_label" placeholder="예: 영상 제목 글꼴"></label>
      <label class="f">내려받은 글꼴 등록 (.ttf · .otf · .ttc) <input type="file" id="custom_font_file" accept=".ttf,.otf,.ttc" onchange="importCustomFont()"></label>
      <label class="f">등록 글꼴 폴더 <span class="path" id="p_fonts_dir">-</span></label>'''
    updated = _replace(updated, old, new, "글꼴 입력")
    anchor = "let subtitlePreviewTimer = null;"
    functions = '''// youtoo-korean-custom-fonts
async function loadFontChoices(selected) {
  const fl = await api('/api/fonts', null, 'GET');
  const fonts = (fl.fonts || []).filter(f => f.custom || /[가-힣]/.test(f.name) || koreanFontNames[f.name]);
  $('srt_font').innerHTML = fonts.map(f => `<option value="${esc(f.name)}">${esc(f.custom ? (f.label || '내 글꼴') : (koreanFontNames[f.name] || f.name))}</option>`).join('');
  $('srt_font').value = selected || 'Malgun Gothic';
  if (!$('srt_font').value && fonts.length) $('srt_font').selectedIndex = 0;
}
async function importCustomFont() {
  const file = $('custom_font_file').files[0]; if (!file) return;
  if (file.size > 20 * 1024 * 1024) { toast('글꼴 파일은 20MB 이하여야 합니다', true); return; }
  try {
    const encoded = await new Promise((resolve, reject) => {
      const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',')[1]);
      reader.onerror = () => reject(new Error('글꼴 파일을 읽지 못했습니다')); reader.readAsDataURL(file);
    });
    const added = await api('/api/fonts/import', {file_name:file.name, data:encoded, label:$('custom_font_label').value.trim()});
    setPath('fonts_dir', added.fonts_dir); await loadFontChoices(added.name);
    $('custom_font_file').value = ''; $('custom_font_label').value = '';
    saveCfg(); scheduleSubtitlePreview(); toast('내 글꼴이 등록됐습니다. 다음 실행에도 사용할 수 있습니다.');
  } catch (e) { toast('글꼴 등록 실패: ' + e.message, true); }
}
'''
    if "// youtoo-korean-custom-fonts" not in updated:
        updated = _replace(updated, anchor, functions + anchor, "글꼴 목록")
    old = "    try { const fl = await api('/api/fonts', null, 'GET'); $('srt_font').innerHTML = fl.fonts.map(f => `<option value=\"${esc(f.name)}\">${esc(koreanFontNames[f.name] || f.name)}</option>`).join(''); $('srt_font').value = 'Malgun Gothic'; } catch (e) {}"
    new = "    try { await loadFontChoices('Malgun Gothic'); } catch (e) {}"
    updated = _replace(updated, old, new, "글꼴 초기화")
    if updated != source:
        html_file.write_text(updated, encoding="utf-8")
        changed = True
    return changed
