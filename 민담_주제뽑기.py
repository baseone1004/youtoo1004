# -*- coding: utf-8 -*-
"""
민담_주제뽑기.py — 야담·민담·옛이야기 채널용 벤치마킹
  1) 야담·민담 계열 유튜브 채널을 찾아 "평소보다 크게 터진" 영상을 모은다.
  2) 내 민담 채널에 이미 올린 주제, 이미 재창조한 주제는 뺀다.
  3) 민담_후보.json + 민담_리포트.html 로 보여 준다. 카드의 [재창조 →] 를 누르면 대본선택 화면에서
     베리에이션 4개(A~D)를 만들고 그중 하나로 대본을 뽑는다 (v11.3 1-B 방식).

설정.json:  "민담_채널": 내 민담 채널 주소,  "민담_벤치_채널_추가": [참고할 채널 주소들]
"""
import sys, os, re, json, html, random, webbrowser, datetime, urllib.parse
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.abspath(__file__)); os.chdir(BASE)
import 주제뽑기 as T

검색어 = ["야담", "옛날이야기 야담", "조선 야담 이야기", "민담 설화 이야기", "전설 설화 옛이야기", "옛이야기 오디오북",
        "어른을 위한 전래동화", "야담 옛날이야기 민담 전설 설화", "조선시대 기담", "귀신 이야기 옛날", "암행어사 이야기", "구비문학 옛날이야기"]
제외_채널_단어 = ["뉴스", "News", "방송", "KBS", "MBC", "SBS", "JTBC", "YTN", "키즈", "Kids", "어린이", "동요"]
벤치_채널_수 = 10
채널당_영상_수 = 80
히트_수 = 30
사용한_주제_파일 = "민담_사용한_주제.txt"

장르_키워드 = [
    ("귀신·도깨비", ["귀신", "도깨비", "구미호", "혼령", "저승", "무덤", "원혼", "요괴", "산신", "처녀귀신"]),
    ("미스터리·추리", ["사건", "살인", "밀실", "범인", "추리", "포도청", "의문", "실종", "누명", "진범"]),
    ("사랑·비극", ["사랑", "기생", "혼인", "정혼", "부부", "이별", "첫사랑", "신부", "과부", "정인"]),
    ("해학·풍자", ["김선달", "골탕", "속인", "사기", "꾀", "바보", "구두쇠", "양반 망신", "웃음"]),
    ("역사인물", ["세종", "정조", "이순신", "정약용", "허준", "장영실", "김만덕", "박문수", "연산군", "숙종", "영조"]),
    ("가족·성장", ["효자", "효녀", "며느리", "시어머니", "아들", "딸", "형제", "부모", "유산", "가문"]),
]

def guess_genre(title):
    best, score = "권선징악", 0
    for g, kws in 장르_키워드:
        n = sum(1 for k in kws if k in title)
        if n > score:
            best, score = g, n
    return best

def load_used():
    if not os.path.exists(사용한_주제_파일):
        with open(사용한_주제_파일, "w", encoding="utf-8") as f:
            f.write("# 이미 재창조했거나 만들기로 한 민담 주제(레퍼런스 제목)를 한 줄에 하나씩. 다음부터 비슷한 건 안 나옵니다.\n")
        return []
    with open(사용한_주제_파일, encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip() and not l.startswith("#")]

def esc(s):
    return html.escape(str(s or ""))

def render(mine, bench, hits, warnings):
    today = datetime.date.today()
    cards = ""
    for i, h in enumerate(hits, 1):
        link = f"http://127.0.0.1:8766/?ch=mindam&t={urllib.parse.quote(h['title'])}"
        cards += f"""
<article class="pick">
  <div class="n">{i}</div>
  <div class="txt">
    <h3>{esc(h['title'])}</h3>
    <p class="ev"><b>{esc(h['channel'])}</b> · {T.fmt(h['views'])}회 · 평소의 <b>{h['ratio']}배</b> · {esc(h['genre'])}
      <a href="{esc(h['url'])}" target="_blank">영상 보기</a></p>
    {'<p class="warn">내 채널에 비슷한 영상: ' + esc(h['near']) + '</p>' if h.get('status') == 'similar' else ''}
    <div class="act"><a class="btn" href="{link}" target="_blank" title="대본선택.bat 이 켜져 있어야 합니다">재창조 → 베리에이션 4개 만들기</a>
      <button class="copy mini" data-copy="{esc(h['title'])}">제목 복사</button></div>
  </div>
</article>"""
    bench_rows = ""
    for ch in bench:
        hh = ch["hits"][0] if ch.get("hits") else None
        hit = (f'<a href="{esc(hh["url"])}" target="_blank">{esc(hh["title"])}</a><i>{T.fmt(hh["views"])}회 · 평소의 {hh["ratio"]}배</i>'
               if hh else '<i>크게 터진 영상 없음</i>')
        bench_rows += f'<li><a class="ch" href="{esc(ch["url"])}" target="_blank">{esc(ch["name"])}</a><span class="hit">{hit}</span></li>'
    my_html = (f'<p>{esc(mine["name"])} · 영상 {len(mine["videos"])}편과 겹치는 주제를 뺐습니다.</p>' if mine
               else '<p class="warn">설정.json 의 민담_채널 이 비어 있어 민담_사용한_주제.txt 만으로 걸렀습니다.</p>')
    warn_html = "".join(f"<p class='warn'>{esc(w)}</p>" for w in warnings)
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>민담·야담 · 벤치마킹 주제</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Gowun+Batang:wght@400;700&family=Noto+Sans+KR:wght@400;500;700&family=IBM+Plex+Mono:wght@500&display=swap">
<style>
:root{{--bg:#F5F1EA;--surface:#FFFDF9;--ink:#2B211A;--muted:#7C6E62;--line:#E6DCCF;--accent:#8A4B2B;--accent-soft:#F4E6DA;--gold:#C89B3C;--warn:#A85A1F;--warn-soft:#FBEBD9;
--serif:'Gowun Batang',serif;--sans:'Noto Sans KR',system-ui,sans-serif;--mono:'IBM Plex Mono',monospace}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1A1512;--surface:#241D18;--ink:#EFE6DC;--muted:#A69787;--line:#3A2F27;--accent:#D89A6A;--accent-soft:#3A2A1E;--gold:#F0B850;--warn:#E9A466;--warn-soft:#3A2A18}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);font-size:15px;line-height:1.65}}a{{color:inherit}}
.wrap{{max-width:960px;margin:0 auto;padding:40px 24px 80px}}
.eyebrow{{font-family:var(--mono);font-size:12px;letter-spacing:.12em;color:var(--accent)}}
h1{{font-family:var(--serif);font-size:36px;margin:6px 0 8px}}.top p{{margin:0;color:var(--muted)}}
h2{{font-family:var(--serif);font-size:22px;margin:44px 0 14px;padding-bottom:10px;border-bottom:2px solid var(--accent);display:flex;gap:12px;align-items:baseline}}
h2 small{{font-family:var(--sans);font-size:13px;font-weight:400;color:var(--muted)}}
.pick{{display:grid;grid-template-columns:44px 1fr;gap:14px;background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:18px 20px;margin-bottom:12px}}
.pick .n{{font-family:var(--mono);font-size:20px;color:var(--gold);padding-top:4px}}
.pick h3{{font-family:var(--serif);font-size:20px;line-height:1.35;margin:0 0 6px}}
.ev{{margin:0;font-size:13.5px;color:var(--muted)}}.ev a{{margin-left:8px;font-size:12.5px}}
.warn{{margin:8px 0 0;font-size:13px;color:var(--warn);background:var(--warn-soft);padding:6px 10px;border-radius:4px}}
.act{{margin-top:12px;display:flex;gap:8px;align-items:center}}
.btn{{text-decoration:none;background:var(--accent);color:#fff;padding:7px 14px;border-radius:6px;font-weight:700;font-size:13.5px}}
.copy{{border:1px solid var(--line);background:transparent;color:var(--ink);padding:6px 12px;border-radius:5px;font:500 13px var(--sans);cursor:pointer}}
.copy.mini{{padding:3px 9px;font-size:12px}}.copy.done{{background:var(--accent);color:#fff}}
.list{{list-style:none;margin:0;padding:0;background:var(--surface);border:1px solid var(--line);border-radius:8px}}
.list li{{display:flex;align-items:center;gap:12px;padding:10px 16px;border-bottom:1px solid var(--line);font-size:14px}}.list li:last-child{{border-bottom:0}}
.list .ch{{flex:0 0 180px;font-weight:700;text-decoration:none}}.list .hit{{flex:1;display:flex;flex-direction:column;min-width:0}}
.list .hit a{{font-size:13.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-decoration:none}}
.list i{{font-style:normal;font-family:var(--mono);font-size:12px;color:var(--muted)}}
.howto{{margin-top:56px;padding:18px 22px;background:var(--accent-soft);border-radius:8px;font-size:14px}}.howto b{{font-family:var(--serif);font-size:16px;display:block;margin-bottom:6px}}
.howto ol{{margin:0;padding-left:20px}}code{{font-family:var(--mono);font-size:12.5px}}
</style></head><body><div class="wrap">
<header class="top"><div class="eyebrow">민담·야담·옛이야기 · {today.month}월 {today.day}일</div>
<h1>요즘 터지는 야담, 다시 짓기</h1>
<p>야담·민담 채널 {len(bench)}곳에서 평소보다 크게 터진 영상 {len(hits)}편입니다. 제목 골격은 살리고 알맹이(주인공·관계·반전)를 바꿔 새 이야기로 만듭니다.</p>{warn_html}</header>
<h2>터진 제목 <small>평소 조회수 대비 배율 순</small></h2>
{cards}
<h2>참고한 채널</h2><ul class="list">{bench_rows}</ul>
<h2>내 채널</h2>{my_html}
<div class="howto"><b>쓰는 법</b><ol>
<li><code>대본선택.bat</code> 을 켜 두고 카드의 <em>재창조 →</em> 를 누르면 그 제목으로 베리에이션 A~D 가 만들어집니다. 하나를 고르면 기획→챕터→합본 대본이 나옵니다.</li>
<li>재창조한 레퍼런스 제목은 <code>민담_사용한_주제.txt</code> 에 자동으로 기록됩니다. 다음 실행부터는 빠집니다.</li>
<li>참고하고 싶은 채널이 있으면 <code>설정.json</code> 의 <code>민담_벤치_채널_추가</code> 에 주소를 넣으세요.</li></ol></div>
</div><script>
document.querySelectorAll('.copy').forEach(b=>b.addEventListener('click',async()=>{{try{{await navigator.clipboard.writeText(b.dataset.copy);}}catch(e){{}}const o=b.textContent;b.textContent='복사됨';b.classList.add('done');setTimeout(()=>{{b.textContent=o;b.classList.remove('done')}},1400);}}));
</script></body></html>"""

def main():
    cfg = T.load_config()
    cfg.setdefault("민담_채널", ""); cfg.setdefault("민담_벤치_채널_추가", [])
    with open(T.설정_파일, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    warnings = []
    random.seed(datetime.date.today().isocalendar()[1])

    print("① 내 민담 채널 확인 중...")
    mine = None
    if cfg["민담_채널"].strip():
        mine = T.fetch_channel(cfg["민담_채널"], 200)
        if mine:
            print(f"   {mine['name']} · {len(mine['videos'])}편")
        else:
            warnings.append("민담_채널 주소를 읽지 못했습니다. 설정.json 을 확인하세요.")
    else:
        print("   (설정.json 에 민담_채널 이 비어 있음)")
    used = load_used()

    print("② 야담·민담 채널 찾는 중...")
    exclude_ids = {mine["id"]} if mine and mine.get("id") else set()
    urls = [T.norm_channel_url(u) for u in cfg["민담_벤치_채널_추가"] if u.strip()]
    for cid, name, url, hits in T.discover_channels(검색어, exclude_ids, 제외_채널_단어, 벤치_채널_수):
        print(f"   {name}")
        if url not in urls:
            urls.append(url)

    print(f"③ 채널 {len(urls)}곳에서 터진 영상 찾는 중...")
    bench = []
    for u in urls:
        ch = T.fetch_channel(u, 채널당_영상_수)
        if not ch or not ch["videos"]:
            warnings.append(f"채널을 읽지 못했습니다: {u}")
            continue
        if mine and ch.get("id") == mine.get("id"):
            continue
        bench.append(T.analyze_channel(ch))

    print("④ 터진 제목 고르는 중...")
    trends = T.trend_keywords(bench)
    with open("민담_트렌드.json", "w", encoding="utf-8") as f:
        json.dump({"날짜": datetime.date.today().isoformat(), "단어": [t for t, _ in trends[:30]]}, f, ensure_ascii=False, indent=1)
    my_titles = [v["title"] for v in (mine["videos"] if mine else [])] + used
    seen, hits = [], []
    for ch in bench:
        for v in ch.get("hits", []):
            title = re.sub(r"\s*\|.*$", "", v["title"]).strip()      # "| 야담 옛날이야기…" 꼬리 제거
            title = re.sub(r"\s*#\S+", "", title).strip()
            if len(title) < 8 or any(T.similarity(title, s) >= 0.6 for s in seen):
                continue
            if re.search(r"모음|연속|몰아보기|합본|중간광고|시간\s*(연속|듣기)|재생목록|라이브", title):   # 모음집·라이브는 제목 재창조 대상이 아님
                continue
            st, sim, near = T.dup_status(title, my_titles)
            if st == "dup":
                continue
            seen.append(title)
            hits.append(dict(title=title, channel=ch["name"], channel_url=ch["url"], url=v["url"], views=v["views"],
                             ratio=v["ratio"], genre=guess_genre(title), status=st, near=near,
                             score=v["ratio"] * (1 + min(v["views"], 500000) / 500000)))
    hits.sort(key=lambda h: -h["score"])
    hits = hits[:히트_수]
    if not hits:
        warnings.append("터진 영상을 찾지 못했습니다. 설정.json 의 민담_벤치_채널_추가 에 채널을 넣어 보세요.")

    # 대본선택.py 가 읽는 형식 (민담_후보.json)
    with open("민담_후보.json", "w", encoding="utf-8") as f:
        json.dump([dict(제목=h["title"], 채널=h["channel"], 채널주소=h["channel_url"], 영상주소=h["url"], 조회수=h["views"],
                        배수=h["ratio"], 장르=h["genre"], 날짜=datetime.date.today().isoformat()) for h in hits],
                  f, ensure_ascii=False, indent=1)
    out = os.path.join(BASE, "민담_리포트.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(render(mine, bench, hits, warnings))
    print(f"\n완료! 터진 제목 {len(hits)}편 → 민담_리포트.html")
    webbrowser.open("file:///" + out.replace("\\", "/"))

if __name__ == "__main__":
    main()
