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
    if not url or T is None:
        return {}
    with _lock:
        if channel in _busy:
            return cached(channel)
        _busy.add(channel)
    try:
        info = T.fetch_channel(url, 300)
        data = _load()
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        if info and info.get("videos") is not None:
            data[channel] = dict(url=url, name=info.get("name") or url, titles=[v["title"] for v in info["videos"] if v.get("title")],
                                 fetched=now, error="")
        else:
            previous = data.get(channel) or {}
            data[channel] = dict(previous, url=url, fetched=previous.get("fetched", ""), error=f"채널을 읽지 못했습니다 ({now}). 주소를 확인하세요.")
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
                            busy=channel in _busy)
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
