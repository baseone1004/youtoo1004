# -*- coding: utf-8 -*-
"""
공통_api.py — AI 호출 + 무료 웹 검색 (두 채널 폴더가 같은 파일을 복사해 씀)

설정.json 예)
  "AI": "deepseek"          ← deepseek / gemini / claude 중 하나
  "API_키": "sk-..."        ← 고른 서비스의 키 하나만
  "모델": ""                ← 비워 두면 기본 모델
"""
import os, sys, json, time, re

PROVIDERS = {
    # 가격은 1백만 토큰당 달러 (입력, 출력) — 대략적인 비용 표시용
    "deepseek": dict(base_url="https://api.deepseek.com", model="deepseek-chat",
                     env="DEEPSEEK_API_KEY", price=(0.27, 1.10), max_out=8000,
                     발급="https://platform.deepseek.com"),
    "gemini":   dict(base_url="https://generativelanguage.googleapis.com/v1beta/openai/", model="gemini-3.6-flash",
                     env="GEMINI_API_KEY", price=(0.0, 0.0), max_out=60000,
                     발급="https://aistudio.google.com/apikey"),
    "claude":   dict(base_url=None, model="claude-opus-5",
                     env="ANTHROPIC_API_KEY", price=(5.0, 25.0), max_out=64000,
                     발급="https://console.anthropic.com"),
    # 딥시크 웹 채팅(chat.deepseek.com)을 크롬 확장으로 조종 — API 키·비용 없음 (대본선택.bat + 딥시크_확장 필요)
    "deepseek-web": dict(base_url=None, model="chat.deepseek.com", env="", price=(0.0, 0.0), max_out=8000,
                         발급="(키 불필요) 딥시크_확장 폴더를 크롬에 설치하고 chat.deepseek.com 에 로그인"),
}
환율 = 1400

예비_순서 = ("gemini", "deepseek", "claude")      # 주 AI 가 계속 실패하면 키가 저장된 순서대로 넘어간다 (무료·저렴한 순)


def fallback_candidates(cfg, current):
    """현재 AI 를 뺀, 키가 저장된 예비 AI 이름 목록."""
    out = []
    for name in 예비_순서:
        if name == current:
            continue
        if (cfg.get("API_키_" + name) or "").strip():
            out.append(name)
    return out


class AI:
    def __init__(self, cfg, _is_fallback=False):
        name = (cfg.get("AI") or "deepseek").strip().lower()
        if name not in PROVIDERS:
            raise SystemExit(f"설정.json 의 AI 값은 deepseek / deepseek-web / gemini / claude 중 하나여야 합니다. (지금: {name})")
        p = PROVIDERS[name]
        self.name, self.p = name, p
        self.cfg, self.fallback, self._is_fallback = cfg, None, _is_fallback
        self.model = (cfg.get("모델") or "").strip() or p["model"]
        self.usage = {"in": 0, "out": 0}
        self.cancel_check = None
        if name == "deepseek-web":
            import 웹큐
            self.key, self.client = "", None
            if not 웹큐.extension_alive():
                spare = self._make_fallback("딥시크 확장이 연결되어 있지 않음")
                if spare is None:
                    raise SystemExit("딥시크 확장이 연결되어 있지 않습니다. 크롬에서 chat.deepseek.com 탭을 열고(로그인) 확장이 '연결됨'인지 확인하세요. "
                                     "확장 설치: 딥시크_확장 폴더 → chrome://extensions → 개발자 모드 → '압축해제된 확장 프로그램을 로드'")
                self.__dict__.update(spare.__dict__)      # 예비 AI 로 통째로 바꿔치기
            return
        self.key = (cfg.get("API_키") or "").strip() or os.environ.get(p["env"], "")
        if not self.key:
            raise SystemExit(f"설정.json 의 API_키 가 비어 있습니다. {name} 키는 {p['발급']} 에서 발급받아 붙여 넣으세요.")
        if name == "claude":
            import anthropic
            self.client = anthropic.Anthropic(api_key=self.key)
        else:
            from openai import OpenAI
            self.client = OpenAI(api_key=self.key, base_url=p["base_url"])

    # ── 한 번 묻고 전체 답을 받는다 (스트리밍, 진행 표시) ──────────────
    def _make_fallback(self, reason):
        """키가 저장된 예비 AI 를 만든다. 없으면 None."""
        if self._is_fallback:
            return None
        for name in fallback_candidates(self.cfg, self.name):
            try:
                spare_cfg = dict(self.cfg, AI=name, API_키=self.cfg.get("API_키_" + name, ""), 모델="")
                spare = AI(spare_cfg, _is_fallback=True)
                spare.cancel_check = self.cancel_check
                print(f"\n   ↻ {reason} → 예비 AI {name} 로 전환합니다", flush=True)
                return spare
            except Exception as exc:  # noqa: BLE001
                print(f"\n   ! 예비 AI {name} 준비 실패: {str(exc)[:100]}", flush=True)
        return None

    def ask(self, system, user, max_tokens=None, retries=2):
        if self.fallback is not None:                 # 이미 예비로 넘어갔으면 계속 예비를 쓴다
            return self.fallback.ask(system, user, max_tokens)
        max_tokens = min(max_tokens or self.p["max_out"], self.p["max_out"])
        for attempt in range(retries + 1):
            try:
                if self.name == "deepseek-web":
                    return self._ask_web(system, user)
                if self.name == "claude":
                    return self._ask_claude(system, user, max_tokens)
                return self._ask_openai(system, user, max_tokens)
            except Exception as e:
                if attempt >= retries:
                    spare = self._make_fallback(f"{self.name} 가 {retries + 1}번 실패 ({str(e)[:80]})")
                    if spare is None:
                        raise
                    self.fallback = spare
                    return spare.ask(system, user, max_tokens)
                wait = 8 * (attempt + 1)
                print(f"\n   ! 호출 실패({e.__class__.__name__}: {str(e)[:120]}) — {wait}초 뒤 다시 시도")
                time.sleep(wait)

    def _ask_web(self, system, user):
        """딥시크 웹: 지침 + 요청을 한 메시지로 새 대화에 보내고, 확장이 답변을 가져올 때까지 기다린다."""
        import 웹큐
        text = (system.strip() + "\n\n" + "=" * 30 + "\n[요청]\n" + user.strip()
                + "\n\n(위 지침을 그대로 따른다. 설명·확인 질문·머리말 없이 결과물만 출력한다.)")
        jid = 웹큐.submit(text, {"new_chat": True})
        print(" [딥시크 웹 대기]", end="", flush=True)
        out = 웹큐.wait(jid, cancel_check=self.cancel_check)
        self.usage["in"] += len(text) // 2; self.usage["out"] += len(out) // 2
        print()
        return out

    def _progress(self, n):
        if n and n % 500 == 0:
            print(".", end="", flush=True)

    def _ask_openai(self, system, user, max_tokens):
        kwargs = dict(model=self.model, max_tokens=max_tokens, stream=True,
                      messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        try:
            kwargs["stream_options"] = {"include_usage": True}
            stream = self.client.chat.completions.create(**kwargs)
        except Exception:
            kwargs.pop("stream_options", None)
            stream = self.client.chat.completions.create(**kwargs)
        parts, n, usage = [], 0, None
        for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = chunk.usage
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                t = chunk.choices[0].delta.content
                parts.append(t); n += len(t); self._progress(n)
        text = "".join(parts)
        if usage:
            self.usage["in"] += usage.prompt_tokens or 0; self.usage["out"] += usage.completion_tokens or 0
        else:   # 사용량을 안 주는 서비스는 글자 수로 어림
            self.usage["in"] += (len(system) + len(user)) // 2; self.usage["out"] += len(text) // 2
        print()
        return text

    def _ask_claude(self, system, user, max_tokens):
        n, texts = 0, []
        with self.client.messages.stream(
            model=self.model, max_tokens=max_tokens, system=system,
            thinking={"type": "adaptive"}, output_config={"effort": "high"},
            messages=[{"role": "user", "content": user}],
        ) as stream:
            for t in stream.text_stream:
                texts.append(t); n += len(t); self._progress(n)
            msg = stream.get_final_message()
        print()
        if msg.stop_reason == "refusal":
            raise RuntimeError("모델이 이 주제의 작성을 거절했습니다: " + str(getattr(msg, "stop_details", "")))
        self.usage["in"] += msg.usage.input_tokens; self.usage["out"] += msg.usage.output_tokens
        return "".join(texts)

    # ── 이번 실행 비용 ─────────────────────────────────────────────
    def cost_text(self):
        i, o = self.p["price"]
        usd = self.usage["in"] / 1e6 * i + self.usage["out"] / 1e6 * o
        extra = (" + 예비 " + self.fallback.cost_text()) if self.fallback is not None else ""
        if self.name == "deepseek-web":
            return f"deepseek-web · 입력 {self.usage['in']:,} / 출력 {self.usage['out']:,} 토큰(어림) · 0원 (웹 채팅)" + extra
        return f"{self.name} · 입력 {self.usage['in']:,} / 출력 {self.usage['out']:,} 토큰 · 약 {usd*환율:,.0f}원" + extra


# ── 무료 웹 검색 (DuckDuckGo) — 모델에게 실제 URL을 쥐여 주기 위해 ─────
def web_search(queries, per_query=5, max_total=8):
    """검색어 여러 개 → [{title, url, snippet}] (중복 URL 제거). 실패해도 빈 목록으로 조용히 넘어감."""
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            return []
    out, seen = [], set()
    믿을만한 = ("go.kr", "ac.kr", "or.kr", "re.kr", "kci.go.kr", "dbpia", "riss", "news", "yna.co.kr", "hani.co.kr", "khan.co.kr",
              "joongang", "chosun", "donga", "kbs", "mbc", "sbs", "ytn", "sciencetimes", "dongascience", "hankyung", "mk.co.kr", "edu", "wikipedia")
    for q in queries:
        try:
            with DDGS() as d:
                for r in d.text(q, region="kr-kr", max_results=per_query):
                    url = r.get("href") or r.get("url") or ""
                    if not url or url in seen:
                        continue
                    seen.add(url)
                    out.append({"title": r.get("title", ""), "url": url, "snippet": (r.get("body") or "")[:300]})
        except Exception:
            continue
        time.sleep(1)
    out.sort(key=lambda r: 0 if any(d in r["url"] for d in 믿을만한) else 1)   # 기관·언론·논문을 앞으로
    return out[:max_total]

def format_sources(items):
    if not items:
        return ""
    lines = ["[참고 자료] (아래 URL만 출처로 쓴다. 목록에 없는 URL은 만들지 않는다)"]
    for i, r in enumerate(items, 1):
        lines.append(f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}")
    return "\n".join(lines)


# ── 대본 후처리 (DINO 이미지 단계 호환: 문장 = 마침표) ─────────────────
def strip_next_teaser(text):
    """맨 끝부분의 '다음 이야기에서는 ~', '다음 영상에서는 ~' 같은 다음 편 예고 문장을 지운다 (마지막 1,500자 안에서만)."""
    head, tail = text[:-1500], text[-1500:]
    verbs = r"(들려|다루|다룹|만나|이어|소개|알아|살펴|전해|찾아|보여|말씀|함께|준비)"
    # 문장 단위(마침표·줄바꿈 안)로만 지운다 — 앞뒤 문장은 건드리지 않는다
    tail = re.sub(r"[^.。\n]*다음\s*(이야기|영상|편)(에서는|에서|엔|은|는|부터)[^.。\n]*" + verbs + r"[^.。\n]*[.。]\s*", "", tail)
    tail = re.sub(r"\.(?=[가-힣])", ". ", tail)
    return head + tail

def fix_script_sentences(text):
    """[대본] 구간의 ? ! … 을 마침표로. 따옴표 안도 동일하게 처리한다."""
    text = re.sub(r"[?!？！]+", ".", text)
    text = re.sub(r"(\.{2,}|…+)", ".", text)
    text = re.sub(r"\.\s*\.", ".", text)
    return text

def find_issues(script):
    """수정하지 않고 알려만 주는 항목."""
    issues = []
    hanja = re.findall(r"[一-鿿]", script)
    if hanja:
        issues.append(f"한자 {len(hanja)}자 남아 있음 (예: {''.join(hanja[:5])})")
    digits = re.findall(r"\d+", script)
    if digits:
        issues.append(f"아라비아 숫자 {len(digits)}곳 (예: {', '.join(digits[:5])}) — TTS 읽기 확인")
    long_lines = [s for s in re.split(r"(?<=\.)\s+", script) if len(s) > 90]
    if len(long_lines) > 10:
        issues.append(f"90자 넘는 긴 문장 {len(long_lines)}개")
    return issues
