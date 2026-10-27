# -*- coding: utf-8 -*-
"""내 유튜브 채널 연동 — 채널에 이미 올린 영상 제목을 받아 두고, 추천 주제에서 겹치는 것을 걸러 낸다.

설정.json 의 내_채널(심리해독소) · 민담_채널(민담) 주소를 쓴다. yt-dlp 로 최근 영상 제목만 읽으므로 API 키가 필요 없다.
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
REFRESH_AFTER = datetime.timedelta(hours=1)          # 새로 올린 영상이 추천에서 빨리 빠지도록 자주 확인한다
_lock = threading.Lock()
_save_lock = threading.Lock()
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


def _account_sources(cfg, channel):
    """Return saved channels whose published topics belong to this production slot.

    The person slot may serve both Korean and Japanese psychology channels. The
    story account is deliberately excluded even when its language is Korean.
    """
    accounts = cfg.get("유튜브_계정") or []
    if channel == "person" and accounts:
        story_id = cfg.get("유튜브_선택_mindam")
        selected_id = cfg.get("유튜브_선택_person")
        selected_url = channel_url(cfg, channel)
        selected = []
        for account in accounts:
            if not account.get("url") or account.get("id") == story_id:
                continue
            language = account.get("language")
            if language in {"ko", "ja"} or account.get("id") == selected_id or account.get("url") == selected_url:
                selected.append(account)
        return selected
    if channel == "mindam" and accounts:
        selected_id = cfg.get("유튜브_선택_mindam")
        selected_url = channel_url(cfg, channel)
        return [a for a in accounts if a.get("url") and (a.get("id") == selected_id or a.get("url") == selected_url)]
    return []


def _titles_for_account(account):
    info = _load().get("account:" + str(account.get("id", "")), {})
    if info.get("url") != account.get("url"):
        return []
    return list(info.get("titles") or [])


def cached(channel):
    """저장된 채널 정보. {url, name, titles, fetched, error} 또는 {}."""
    return _load().get(channel) or {}


def titles(cfg, channel):
    """설정된 채널 주소와 맞는 저장본의 제목 목록. 주소가 비었거나 바뀌었으면 빈 목록."""
    sources = _account_sources(cfg, channel)
    if sources:
        return list(dict.fromkeys(title for account in sources for title in _titles_for_account(account)))
    url = channel_url(cfg, channel)
    info = cached(channel)
    if not url or info.get("url") != url:
        return []
    return list(info.get("titles") or [])


def _fetch_one(cfg, channel, url, api_key, cache_key):
    """Fetch one URL and save it under a slot or account cache key."""
    api_key = str(api_key or "").strip()
    if not url or (T is None and not api_key):
        return {}
    busy_key = str(cache_key)
    with _lock:
        if busy_key in _busy:
            return _load().get(busy_key, {})
        _busy.add(busy_key)
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
            try:
                info = T.fetch_channel(url, 300); source = "ytdlp"
            except Exception as exc:  # noqa: BLE001
                error = error or str(exc)
        if info and info.get("videos") is not None:
            videos = [dict(title=v["title"], views=int(v.get("views") or 0), likes=int(v.get("likes") or 0), duration=int(v.get("duration") or 0),
                           published=v.get("published", ""), url=v.get("url", ""))
                      for v in info["videos"] if v.get("title")]
            data[busy_key] = dict(url=url, name=info.get("name") or url, titles=[v["title"] for v in videos], videos=videos,
                                 subs=int(info.get("subs") or 0), fetched=now, source=source, error="",
                                 warning=(f"유튜브 API 는 실패해 제목만 읽었습니다(조회수 없음): {error}" if error else ""))
        else:
            previous = data.get(busy_key) or {}
            data[busy_key] = dict(previous, url=url, fetched=previous.get("fetched", ""), error=error or f"채널을 읽지 못했습니다 ({now}). 주소를 확인하세요.")
        # Several saved channels may refresh at once. Merge into the latest
        # cache snapshot so one thread cannot erase another channel's result.
        with _save_lock:
            latest = _load()
            latest[busy_key] = data[busy_key]
            _save(latest)
            return latest[busy_key]
    finally:
        with _lock:
            _busy.discard(busy_key)


def fetch(cfg, channel):
    """Refresh every saved channel represented by the slot, then return summary."""
    sources = _account_sources(cfg, channel)
    if sources:
        results = []
        selected_id = cfg.get("유튜브_선택_" + channel)
        for account in sources:
            key = account.get("api_key") or (cfg.get("유튜브_API_키") if account.get("id") == selected_id else "")
            result = _fetch_one(cfg, channel, account.get("url", ""), key, "account:" + str(account.get("id", "")))
            if result:
                results.append(result)
        return dict(url=channel_url(cfg, channel), name=" · ".join(r.get("name", "") for r in results if r.get("name")),
                    titles=list(dict.fromkeys(t for r in results for t in r.get("titles", []))),
                    videos=[v for r in results for v in r.get("videos", [])],
                    fetched=max((r.get("fetched", "") for r in results), default=""),
                    error=" · ".join(r.get("error", "") for r in results if r.get("error")),
                    warning=" · ".join(r.get("warning", "") for r in results if r.get("warning")),
                    source="accounts", account_count=len(sources))
    url = channel_url(cfg, channel)
    return _fetch_one(cfg, channel, url, cfg.get("유튜브_API_키", ""), channel)


def age_minutes(channel):
    """저장본을 받은 지 몇 분 지났는지 (없으면 아주 큰 값)."""
    info = cached(channel)
    try:
        return (datetime.datetime.now() - datetime.datetime.strptime(info["fetched"], "%Y-%m-%d %H:%M")).total_seconds() / 60
    except (KeyError, ValueError, TypeError):
        return 10 ** 9


def fetch_if_stale(cfg, channel, minutes=10):
    """저장본이 minutes 보다 오래됐으면 지금 바로(동기) 다시 받는다. 주제를 추천하기 직전에 써서 방금 올린 영상도 걸러 낸다."""
    sources = _account_sources(cfg, channel)
    if sources:
        stale = False
        for account in sources:
            info = _load().get("account:" + str(account.get("id", "")), {})
            if info.get("url") != account.get("url") or not info.get("fetched"):
                stale = True
                break
            try:
                age = datetime.datetime.now() - datetime.datetime.strptime(info["fetched"], "%Y-%m-%d %H:%M")
                if age.total_seconds() / 60 >= minutes:
                    stale = True
                    break
            except ValueError:
                stale = True
                break
        return fetch(cfg, channel) if stale else {}
    url = channel_url(cfg, channel)
    if not url or (T is None and not str(cfg.get("유튜브_API_키", "") or "").strip()):
        return cached(channel)
    if cached(channel).get("url") == url and age_minutes(channel) < minutes:
        return cached(channel)
    return fetch(cfg, channel)


def fetch_in_background(cfg, channel, force=False):
    """저장본이 없거나 오래됐으면 백그라운드에서 다시 받는다."""
    sources = _account_sources(cfg, channel)
    if sources:
        started = False
        selected_id = cfg.get("유튜브_선택_" + channel)
        for account in sources:
            cache_key = "account:" + str(account.get("id", ""))
            info = _load().get(cache_key, {})
            if info.get("url") == account.get("url") and info.get("fetched") and not force:
                try:
                    if datetime.datetime.now() - datetime.datetime.strptime(info["fetched"], "%Y-%m-%d %H:%M") < REFRESH_AFTER:
                        continue
                except ValueError:
                    pass
            api_key = account.get("api_key") or (cfg.get("유튜브_API_키") if account.get("id") == selected_id else "")
            thread = threading.Thread(target=_fetch_one, args=(cfg, channel, account.get("url", ""), api_key, cache_key), daemon=True,
                                      name=f"channel-sync-{str(account.get('id', ''))[:8]}")
            thread.start()
            started = True
        return started
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
        sources = _account_sources(cfg, channel)
        if sources:
            infos = [_load().get("account:" + str(a.get("id", "")), {}) for a in sources]
            fresh_infos = [i for a, i in zip(sources, infos) if i.get("url") == a.get("url")]
            successful = [i for i in fresh_infos if not i.get("error")]
            partial_errors = [i.get("error", "") for i in fresh_infos if i.get("error")]
            out[channel] = dict(url=channel_url(cfg, channel), name=" · ".join(i.get("name", "") for i in fresh_infos if i.get("name")),
                                count=len(titles(cfg, channel)), fetched=max((i.get("fetched", "") for i in fresh_infos), default=""),
                                error=(" · ".join(partial_errors) if partial_errors and not successful else ""),
                                warning=" · ".join([i.get("warning", "") for i in fresh_infos if i.get("warning")] +
                                                   (partial_errors if successful else [])),
                                source="accounts", busy=any(("account:" + str(a.get("id", ""))) in _busy for a in sources),
                                account_count=len(sources))
            continue
        url = channel_url(cfg, channel)
        info = cached(channel)
        fresh = bool(url) and info.get("url") == url
        out[channel] = dict(url=url, name=info.get("name", "") if fresh else "", count=len(info.get("titles") or []) if fresh else 0,
                            fetched=info.get("fetched", "") if fresh else "", error=info.get("error", "") if fresh else "",
                            warning=info.get("warning", "") if fresh else "",
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
        # 올릴 때 제목을 바꾸는 경우가 많으므로 주제 제목뿐 아니라 유튜브용 제목·썸네일 문구와도 비교해 가장 가까운 것을 쓴다
        names = [str(item.get(k, "") or "").strip() for k in (key, "유튜브제목", "썸네일")]
        results = [T.dup_status(n, mine) for n in names if n]
        st, sim, near = max(results, key=lambda r: r[1]) if results else ("new", 0.0, "")
        if st == "dup":
            continue
        if st == "similar":
            item = dict(item, 비슷한_내_영상=near)
        kept.append(item)
    return kept


def analysis(cfg, channel):
    """내 채널 분석: 영상 수·조회수 평균/중앙값·상위 영상·잘 되는/약한 키워드. 저장본(videos) 기준."""
    url = channel_url(cfg, channel)
    selected_id = cfg.get("유튜브_선택_" + channel)
    info = _load().get("account:" + str(selected_id), {}) if selected_id else {}
    if info.get("url") != url:
        info = cached(channel)
    videos = info.get("videos") or []
    if url and info.get("url") == url and info.get("fetched") and not info.get("error") and not videos:
        return dict(ok=False, connected=True, count=0, name=info.get("name", ""),
                    reason=f"{info.get('name', '')} · 유튜브 연결 완료. 공개 영상이 아직 없어 조회수 분석을 기다리고 있습니다. 영상을 공개한 뒤 다시 읽으면 분석이 표시됩니다.")
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
        import 채널_프로필
        japanese = 채널_프로필.language_code(channel) == "ja"
        for v in videos:
            words = ({w for w in ("人間関係", "心理", "心", "夫婦", "家族", "親", "子ども", "友達", "職場", "不安", "孤独", "疲れ", "本音", "言葉", "習慣", "会話", "人生", "自信", "後悔") if w in v["title"]}
                     if japanese else T.tokens(v["title"]))
            for w in words:
                if japanese or T.HANGUL.fullmatch(w):
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
