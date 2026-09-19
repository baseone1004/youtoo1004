# -*- coding: utf-8 -*-
"""자막 '글자 두께' 조절을 편집프로그램에 붙인다.
글꼴에 굵은 두께가 없어도(예: 학교안심 날개 R) 두꺼운 자막이 되도록, 자막을 두 겹으로 그린다:
1겹 = 테두리색 글자 + (두께 + 외곽선) 만큼의 외곽선, 2겹 = 글자색 글자 + 글자색 외곽선(두께). 두께 0 이면 예전과 같다."""
from pathlib import Path


def _replace_once(text, old, new, what):
    if new in text:
        return text, False
    if old not in text:
        raise ValueError(f"편집프로그램에서 {what}을(를) 찾지 못했습니다.")
    return text.replace(old, new, 1), True


def apply(editor_dir):
    editor = Path(editor_dir)
    changed = False
    renderer = editor / "core" / "renderer.py"
    app_file = editor / "app.py"
    html = editor / "static" / "index.html"
    if not renderer.is_file() or not app_file.is_file() or not html.is_file():
        return False

    # ── core/renderer.py ──
    text = renderer.read_text(encoding="utf-8")
    text, c = _replace_once(text, "    srt_bold: bool = True\n    srt_shadow: float = 0.0\n",
                            "    srt_bold: bool = True\n    srt_weight: float = 0.0        # 글자 두께(px): 0 = 글꼴 그대로, 1~6 = 글자색 외곽선을 덧그려 두껍게\n    srt_shadow: float = 0.0\n",
                            "RenderOptions 의 srt_bold"); changed |= c
    old = '''            style = (f"FontName={opts.srt_font},FontSize={opts.srt_font_size},Bold={-1 if opts.srt_bold else 0},"
                     f"Outline={opts.srt_outline:g},Shadow={opts.srt_shadow:g},MarginV={opts.srt_margin_v},Alignment={align},"
                     f"PrimaryColour={hex_to_ass(opts.srt_color)},OutlineColour={hex_to_ass(opts.srt_outline_color)}")
            if opts.srt_box:
                style += f",BorderStyle=4,BackColour={hex_to_ass(opts.srt_box_color, int((1 - opts.srt_box_alpha) * 255))}"
            # 자막 폰트 크기는 PlayRes 기준이므로 1080p 로 고정해 해상도와 무관하게 같은 크기가 되게 함
            sub = f"subtitles=filename='{_ff_escape_path(str(srt_copy))}':original_size={W}x{H}:force_style='{style}'"
            if opts.srt_fonts_dir and Path(opts.srt_fonts_dir).is_dir():
                sub += f":fontsdir='{_ff_escape_path(opts.srt_fonts_dir)}'"
            filters.append(f"{vlabel}{sub}[vsub]")
            vlabel = "[vsub]"
'''
    new = '''            # 자막 폰트 크기는 PlayRes 기준이므로 1080p 로 고정해 해상도와 무관하게 같은 크기가 되게 함
            fontsdir = f":fontsdir='{_ff_escape_path(opts.srt_fonts_dir)}'" if opts.srt_fonts_dir and Path(opts.srt_fonts_dir).is_dir() else ""
            for i, style in enumerate(subtitle_style_layers(opts, align, hex_to_ass)):
                sub = f"subtitles=filename='{_ff_escape_path(str(srt_copy))}':original_size={W}x{H}:force_style='{style}'" + fontsdir
                filters.append(f"{vlabel}{sub}[vsub{i}]")
                vlabel = f"[vsub{i}]"
'''
    text, c = _replace_once(text, old, new, "자막 굽기(subtitles 필터)"); changed |= c
    helper = '''

def subtitle_style_layers(o, align, hex_to_ass):
    """자막 force_style 목록. 글자 두께(srt_weight)가 0 이면 한 겹, 아니면 두 겹(테두리 겹 → 글자 겹)으로 두껍게 그린다."""
    weight = float(getattr(o, "srt_weight", 0) or 0)
    base = (f"FontName={o.srt_font},FontSize={o.srt_font_size},Bold={-1 if o.srt_bold else 0},"
            f"Shadow={o.srt_shadow:g},MarginV={o.srt_margin_v},Alignment={align}")
    box = f",BorderStyle=4,BackColour={hex_to_ass(o.srt_box_color, int((1 - o.srt_box_alpha) * 255))}" if o.srt_box else ""
    if weight <= 0:
        return [base + f",Outline={o.srt_outline:g},PrimaryColour={hex_to_ass(o.srt_color)},OutlineColour={hex_to_ass(o.srt_outline_color)}" + box]
    edge = base + (f",Outline={weight + o.srt_outline:g},PrimaryColour={hex_to_ass(o.srt_outline_color)},"
                   f"OutlineColour={hex_to_ass(o.srt_outline_color)}") + box
    body = base + f",Outline={weight:g},PrimaryColour={hex_to_ass(o.srt_color)},OutlineColour={hex_to_ass(o.srt_color)}"
    return [edge, body]
'''
    if "def subtitle_style_layers(" not in text:
        anchor = "\ndef render("
        if anchor not in text:
            raise ValueError("편집프로그램 renderer.py 에서 render() 를 찾지 못했습니다.")
        text = text.replace(anchor, helper + anchor, 1); changed = True
    if changed:
        renderer.write_text(text, encoding="utf-8")

    # ── app.py: 요청 모델·렌더 옵션·미리보기 ──
    text = app_file.read_text(encoding="utf-8")
    c_any = False
    n = text.count("    srt_bold: bool = True\n    srt_shadow: float = 0.0\n")
    if n and "    srt_weight: float = 0.0\n" not in text:
        text = text.replace("    srt_bold: bool = True\n    srt_shadow: float = 0.0\n",
                            "    srt_bold: bool = True\n    srt_weight: float = 0.0\n    srt_shadow: float = 0.0\n"); c_any = True
    text, c = _replace_once(text, "srt_bold=req.srt_bold, srt_shadow=req.srt_shadow,",
                            "srt_bold=req.srt_bold, srt_weight=req.srt_weight, srt_shadow=req.srt_shadow,", "RenderOptions 만들기"); c_any |= c
    old = '''    style = (f"FontName={req.srt_font},FontSize={req.srt_font_size},Bold={-1 if req.srt_bold else 0},Outline={req.srt_outline:g},"
             f"Shadow={req.srt_shadow:g},MarginV={req.srt_margin_v},Alignment={align},PrimaryColour={hex_to_ass(req.srt_color)},"
             f"OutlineColour={hex_to_ass(req.srt_outline_color)}")
    if req.srt_box:
        style += f",BorderStyle=4,BackColour={hex_to_ass(req.srt_box_color, int((1 - req.srt_box_alpha) * 255))}"
    sub = f"subtitles=filename='{_ff_escape_path(str(srt))}':original_size={req.width}x{req.height}:force_style='{style}'"
    if req.srt_fonts_dir and Path(req.srt_fonts_dir).is_dir():
        sub += f":fontsdir='{_ff_escape_path(req.srt_fonts_dir)}'"
'''
    new = '''    from core.renderer import subtitle_style_layers
    fontsdir = f":fontsdir='{_ff_escape_path(req.srt_fonts_dir)}'" if req.srt_fonts_dir and Path(req.srt_fonts_dir).is_dir() else ""
    sub = ",".join(f"subtitles=filename='{_ff_escape_path(str(srt))}':original_size={req.width}x{req.height}:force_style='{style}'" + fontsdir
                   for style in subtitle_style_layers(req, align, hex_to_ass))
'''
    text, c = _replace_once(text, old, new, "자막 미리보기 스타일"); c_any |= c
    if c_any:
        app_file.write_text(text, encoding="utf-8"); changed = True

    # ── static/index.html: 두께 입력칸 + 저장/불러오기/미리보기 ──
    text = html.read_text(encoding="utf-8")
    c_any = False
    text, c = _replace_once(text,
                            '''<label class="f"><span><input type="checkbox" id="srt_bold" checked>굵게</span>''',
                            '''<label class="f">글자 두께 <input type="number" id="srt_weight" value="0" min="0" max="6" step="0.5" style="width:70px" title="0 = 글꼴 그대로. 올릴수록 글자가 두꺼워집니다 (글꼴에 굵은 두께가 없어도 됨)"></label>
      <label class="f"><span><input type="checkbox" id="srt_bold" checked>굵게</span>''', "자막 굵게 체크"); c_any |= c
    text, c = _replace_once(text, "srt_outline: +$('srt_outline').value, srt_bold: $('srt_bold').checked,",
                            "srt_outline: +$('srt_outline').value, srt_weight: +$('srt_weight').value, srt_bold: $('srt_bold').checked,", "subStyle()"); c_any |= c
    text, c = _replace_once(text, "for (const k of ['srt_align', 'srt_color', 'srt_outline_color', 'srt_outline', 'srt_margin_v'])",
                            "for (const k of ['srt_align', 'srt_color', 'srt_outline_color', 'srt_outline', 'srt_weight', 'srt_margin_v'])", "설정 불러오기"); c_any |= c
    text, c = _replace_once(text, "for (const id of ['srt_font','e_font_size','srt_font_size','srt_align','srt_color','srt_outline_color','srt_outline','srt_bold',",
                            "for (const id of ['srt_font','e_font_size','srt_font_size','srt_align','srt_color','srt_outline_color','srt_outline','srt_weight','srt_bold',", "미리보기 갱신 목록"); c_any |= c
    if c_any:
        html.write_text(text, encoding="utf-8"); changed = True
    return changed
