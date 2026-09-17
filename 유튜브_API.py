# -*- coding: utf-8 -*-
"""유튜브 공식 API(Data API v3, API 키만 필요)로 내 채널의 영상 목록·조회수·좋아요·게시일을 읽는다.

키 발급: console.cloud.google.com → 프로젝트 만들기 → 'YouTube Data API v3' 사용 설정 → 사용자 인증 정보 → API 키.
하루 무료 할당량(10,000)으로 채널 하나를 수백 번 읽을 수 있다."""
import re
import urllib.parse

import requests

BASE = "https://www.googleapis.com/youtube/v3/"


KEY_HELP = "유튜브 API 키는 'AIza' 로 시작하는 39자입니다 (console.cloud.google.com → API 및 서비스 → 사용자 인증 정보 → API 키). 제미나이(AI Studio) 키와는 다릅니다."


def check_key_format(key):
    """키 모양이 구글 클라우드 API 키가 아니면 이유 문자열, 맞으면 ''."""
    key = (key or "").strip()
    if not key:
        return "유튜브 API 키가 비어 있습니다."
    if not re.fullmatch(r"AIza[\w-]{35}", key):
        return f"저장된 키({key[:4]}…{key[-3:]}, {len(key)}자)는 유튜브 API 키 모양이 아닙니다. " + KEY_HELP
    return ""


def _get(key, path, **params):
    params["key"] = key
    r = requests.get(BASE + path, params=params, timeout=30)
    if r.status_code != 200:
        try:
            msg = r.json()["error"]["message"]
        except Exception:  # noqa: BLE001
            msg = r.text[:200]
        if r.status_code == 401 and "API keys are not supported" in msg:   # 제미나이 키 등 다른 종류의 키를 넣었을 때
            msg = "이 키는 유튜브 API 키가 아닙니다. " + KEY_HELP
        elif r.status_code == 400 and "API key not valid" in msg:
            msg = "키가 올바르지 않습니다(오타이거나 삭제된 키). " + KEY_HELP
        elif r.status_code == 403 and ("not been used" in msg or "disabled" in msg):
            msg = "이 프로젝트에서 'YouTube Data API v3' 가 사용 설정되지 않았습니다. 구글 클라우드 콘솔에서 사용 설정하세요."
        raise RuntimeError(f"유튜브 API 오류 ({r.status_code}): {msg}")
    return r.json()


def _channel_query(url_or_id):
    """채널 주소 → channels.list 에 넘길 조건."""
    s = (url_or_id or "").strip()
    if re.fullmatch(r"UC[\w-]{20,}", s):
        return {"id": s}
    path = urllib.parse.urlparse(s if s.startswith("http") else "https://www.youtube.com/" + s.lstrip("/")).path
    m = re.search(r"/channel/(UC[\w-]+)", path)
    if m:
        return {"id": m.group(1)}
    m = re.search(r"/@([^/]+)", path)
    if m:
        return {"forHandle": "@" + urllib.parse.unquote(m.group(1))}
    m = re.search(r"/(?:c|user)/([^/]+)", path)
    if m:
        return {"forUsername": urllib.parse.unquote(m.group(1))}
    raise ValueError("채널 주소 형식을 알 수 없습니다: " + s)


def _duration_seconds(iso):
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def fetch_channel(key, url_or_id, limit=300):
    """{name, id, subs, videos:[{id,title,views,likes,comments,duration,published,url}]}"""
    ch = _get(key, "channels", part="snippet,statistics,contentDetails", **_channel_query(url_or_id))
    items = ch.get("items") or []
    if not items:
        raise ValueError("채널을 찾지 못했습니다. 주소를 확인하세요.")
    c = items[0]
    uploads = c["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, titles, published, token = [], {}, {}, None
    if int((c.get("statistics") or {}).get("videoCount") or 0) == 0:     # 아직 영상이 없는 채널: 업로드 목록 조회가 오류를 내므로 건너뛴다
        return dict(name=c["snippet"]["title"], id=c["id"], subs=int((c.get("statistics") or {}).get("subscriberCount") or 0), videos=[])
    while len(ids) < limit:
        try:
            pl = _get(key, "playlistItems", part="snippet,contentDetails", playlistId=uploads, maxResults=50, **({"pageToken": token} if token else {}))
        except RuntimeError as exc:                     # 업로드 목록이 아직 없는 새 채널도 여기로 온다
            if ids:
                raise
            if "404" in str(exc) or "401" in str(exc) or "playlistNotFound" in str(exc):
                break
            raise
        for it in pl.get("items") or []:
            vid = it["contentDetails"]["videoId"]
            ids.append(vid)
            titles[vid] = it["snippet"]["title"]
            published[vid] = (it["contentDetails"].get("videoPublishedAt") or it["snippet"].get("publishedAt") or "")[:10]
        token = pl.get("nextPageToken")
        if not token:
            break
    videos = []
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        vs = _get(key, "videos", part="statistics,contentDetails", id=",".join(chunk))
        for v in vs.get("items") or []:
            st = v.get("statistics") or {}
            videos.append(dict(id=v["id"], title=titles.get(v["id"], ""), views=int(st.get("viewCount") or 0),
                               likes=int(st.get("likeCount") or 0), comments=int(st.get("commentCount") or 0),
                               duration=_duration_seconds((v.get("contentDetails") or {}).get("duration")),
                               published=published.get(v["id"], ""), url="https://www.youtube.com/watch?v=" + v["id"]))
    videos.sort(key=lambda v: v["published"], reverse=True)
    return dict(name=c["snippet"]["title"], id=c["id"], subs=int((c.get("statistics") or {}).get("subscriberCount") or 0), videos=videos)
