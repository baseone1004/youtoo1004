# -*- coding: utf-8 -*-
"""내 유튜브 채널 연동 — 채널에 이미 올린 영상 제목을 받아 두고, 추천 주제에서 겹치는 것을 걸러 낸다.

설정.json 의 내_채널(사람의 이유) · 민담_채널(민담) 주소를 쓴다. yt-dlp 로 최근 영상 제목만 읽으므로 API 키가 필요 없다.
받은 제목은 대본/_상태/채널_제목.json 에 저장해 두고, 화면은 저장본만 읽는다 (가져오기는 백그라운드 스레드)."""
import datetime
import json
import os
import threading

try:
    import 주제뽑기 as T          # yt-dlp 가 없으면 이 모듈이 sys.exit 하므로 막는다
except (ImportError, SystemExit):
    T = None
import 유튜브_API

CACHE = os.path.join("대본", "_상태", "채널_제목.json")
CONFIG_KEY = {"person": "내_채널", "mindam": "민담_채널"}
REFRESH_AFTER = datetime.timedelta(hours=6)
_lock = threading.Lock()
_busy = set()


def _load():
    try:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(data):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    tmp = CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CACHE)


def channel_url(cfg, channel):
    return str(cfg.get(CONFIG_KEY[channel], "") or "").strip()


def cached(channel):
    """저장된 채널 정보. {url, name, titles, fetched, error} 또는 {}."""
    return _load().get(channel) or {}


def titles(cfg, channel):
    """설정된 채널 주소와 맞는 저장본의 제목 목록. 주소가 비었거나 바뀌었으면 빈 목록."""
    url = channel_url(cfg, channel)
    info = cached(channel)
    if not url or info.get("url") != url:
        return []
    return list(info.get("titles") or [])


def fetch(cfg, channel):
    """채널 제목을 지금 가져와 저장한다. 결과 dict 를 돌려준다."""
    url = channel_url(cfg, channel)
    api_key = str(cfg.get("유튜브_API_키", "") or "").strip()
    if not url or (T is None and not api_key):
        return {}
    with _lock:
        if channel in _busy:
            return cached(channel)
        _busy.add(channel)
    try:
        data = _load()
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        info, source, error = None, "", ""
        if api_key:                                   # 공식 API: 조회수·좋아요·게시일까지 정확
            try:
                info = 유튜브_API.fetch_channel(api_key, url, 300); source = "api"
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
        if info is None and T is not None:            # 없으면 yt-dlp (제목·길이만 믿을 수 있음)
            info = T.fetch_channel(url, 300); source = "ytdlp"
        if info and info.get("videos") is not None:
            videos = [dict(title=v["title"], views=int(v.get("views") or 0), likes=int(v.get("likes") or 0), duration=int(v.get("duration") or 0),
                           published=v.get("published", ""), url=v.get("url", ""))
                      for v in info["videos"] if v.get("title")]
            data[channel] = dict(url=url, name=info.get("name") or url, titles=[v["title"] for v in videos], videos=videos,
                                 subs=int(info.get("subs") or 0), fetched=now, source=source,
                                 error=(f"유튜브 API 실패, yt-dlp로 대신 읽음: {error}" if error else ""))
        else:
            previous = data.get(channel) or {}
            data[channel] = dict(previous, url=url, fetched=previous.get("fetched", ""), error=error or f"채널을 읽지 못했습니다 ({now}). 주소를 확인하세요.")
        _save(data)
        return data[channel]
    finally:
        with _lock:
            _busy.discard(channel)


def fetch_in_background(cfg, channel, force=False):
    """저장본이 없거나 오래됐으면 백그라운드에서 다시 받는다."""
    url = channel_url(cfg, channel)
    if not url or T is None:
        return False
    info = cached(channel)
    if not force and info.get("url") == url and info.get("fetched"):
        try:
            age = datetime.datetime.now() - datetime.datetime.strptime(info["fetched"], "%Y-%m-%d %H:%M")
            if age < REFRESH_AFTER:
                return False
        except ValueError:
            pass
    threading.Thread(target=fetch, args=(cfg, channel), daemon=True, name=f"channel-sync-{channel}").start()
    return True


def status(cfg):
    """화면용 요약: 채널별 {url, name, count, fetched, error, busy}."""
    out = {}
    for channel in CONFIG_KEY:
        url = channel_url(cfg, channel)
        info = cached(channel)
        fresh = bool(url) and info.get("url") == url
        out[channel] = dict(url=url, name=info.get("name", "") if fresh else "", count=len(info.get("titles") or []) if fresh else 0,
                            fetched=info.get("fetched", "") if fresh else "", error=info.get("error", "") if fresh else "",
                            source=info.get("source", "") if fresh else "", busy=channel in _busy)
    return out


def check(cfg, channel, title):
    """제목이 내 채널 영상과 겹치는지. {status: new|similar|dup, sim, near}"""
    mine = titles(cfg, channel)
    if not mine or T is None:
        return dict(status="new", sim=0.0, near="")
    st, sim, near = T.dup_status(title, mine)
    return dict(status=st, sim=round(sim, 2), near=near)


def filter_topics(cfg, channel, items, key="제목"):
    """추천 목록에서 내 채널에 이미 있는(dup) 주제를 빼고, 비슷한(similar) 주제에는 표시를 남긴다."""
    mine = titles(cfg, channel)
    if not mine or T is None:
        return items
    kept = []
    for item in items:
        st, sim, near = T.dup_status(str(item.get(key, "")), mine)
        if st == "dup":
            continue
        if st == "similar":
            item = dict(item, 비슷한_내_영상=near)
        kept.append(item)
    return kept


def analysis(cfg, channel):
    """내 채널 분석: 영상 수·조회수 평균/중앙값·상위 영상·잘 되는/약한 키워드. 저장본(videos) 기준."""
    url = channel_url(cfg, channel)
    info = cached(channel)
    videos = info.get("videos") or []
    if not url or info.get("url") != url or not videos:
        return dict(ok=False, count=len(info.get("titles") or []), reason="채널을 아직 읽지 않았습니다. [제목 다시 읽기]를 눌러 주세요.")
    if not any(v.get("views") for v in videos):
        return dict(ok=False, count=len(videos), reason="조회수를 읽지 못했습니다. 유튜브 API 키를 저장하면 조회수·좋아요·게시일까지 분석합니다.")
    views = sorted((v["views"] for v in videos), reverse=True)
    avg = sum(views) / len(views)
    median = views[len(views) // 2]
    ranked = sorted(videos, key=lambda v: -v["views"])
    keywords = []
    if T is not None:
        agg = {}
        for v in videos:
            for w in T.tokens(v["title"]):
                if T.HANGUL.fullmatch(w):
                    agg.setdefault(w, []).append(v["views"])
        rows = [dict(word=w, n=len(vs), avg=sum(vs) / len(vs)) for w, vs in agg.items() if len(vs) >= 2]
        keywords = sorted(rows, key=lambda r: -r["avg"])
    strong = [r for r in keywords if r["avg"] >= avg * 1.2][:10]
    weak = [r for r in sorted(keywords, key=lambda r: r["avg"]) if r["avg"] <= avg * 0.6][:6]
    long_videos = [v for v in videos if v["duration"] >= 600]
    dated = [v for v in videos if v.get("published")]
    recent = sorted(dated, key=lambda v: v["published"], reverse=True)[:5]
    weekday = {}
    for v in dated:
        try:
            d = datetime.date.fromisoformat(v["published"]).weekday()
            weekday.setdefault(d, []).append(v["views"])
        except ValueError:
            pass
    best_day = max(weekday.items(), key=lambda kv: sum(kv[1]) / len(kv[1]))[0] if weekday else None
    return dict(ok=True, name=info.get("name", ""), subs=info.get("subs", 0), count=len(videos), fetched=info.get("fetched", ""), source=info.get("source", ""),
                avg=round(avg), median=median, total=sum(views),
                top=ranked[:5], bottom=ranked[-3:][::-1] if len(ranked) > 5 else [], recent=recent,
                keywords=strong, weak=weak,
                avg_minutes=round(sum(v["duration"] for v in long_videos) / len(long_videos) / 60, 1) if long_videos else 0,
                above_avg=sum(1 for v in videos if v["views"] >= avg),
                best_weekday=["월", "화", "수", "목", "금", "토", "일"][best_day] if best_day is not None else "",
                first=min(v["published"] for v in dated) if dated else "", last=max(v["published"] for v in dated) if dated else "")
