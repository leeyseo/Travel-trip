"""
웹 검색 수집기 (Playwright 통합 버전)

fetch 전략 자동 선택:
  - JS 렌더링 필요한 사이트 (네이버, 트립어드바이저 등) → Playwright
  - 정적 페이지 → requests (빠름)

흐름:
  build_queries() → search_places() → collect_raw_text()
                                           ↓
                              _fetch_static() or _fetch_playwright()
"""

import os
import re
import time
import asyncio
import requests
from dataclasses import dataclass, field


SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
SERPER_ENDPOINT = "https://google.serper.dev/search"

# JS 렌더링이 필요한 도메인 목록
# 이 도메인이 URL에 포함되면 자동으로 Playwright 사용
JS_REQUIRED_DOMAINS = {
    "blog.naver.com",       # 네이버 블로그 — React 렌더링
    "post.naver.com",       # 네이버 포스트
    "tripadvisor.com",      # 트립어드바이저 — 무한스크롤
    "tripadvisor.co.kr",
    "yelp.com",
    "booking.com",
    "agoda.com",
    "instagram.com",
    "tiktok.com",
}


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    full_text: str = ""
    fetch_method: str = ""   # "static" | "playwright" | "snippet_only"


@dataclass
class FetchResult:
    url: str
    text: str
    method: str   # "static" | "playwright" | "failed"
    error: str = ""


# ──────────────────────────────────────────────
# 쿼리 생성
# ──────────────────────────────────────────────
def build_queries(
    destination: str,
    category: str,
    age_group: str,
    preferences: dict,
) -> list[str]:
    pref_keywords = {
        "attraction": {
            "culture":          "문화유산 역사",
            "activity":         "체험 액티비티",
            "nature":           "자연 공원",
            "food":             "감성 포토스팟",
            "nightlife":        "야경 야간",
            "shopping":         "쇼핑 거리",
        },
        "hotel": {
            "cleanliness":      "청결 깔끔한",
            "food":             "조식 맛있는",
            "walking_aversion": "역 근처 교통 편리",
        },
        "restaurant": {
            "food":             "맛집 현지 유명",
            "culture":          "로컬 정통",
            "cleanliness":      "위생 청결",
        },
    }

    top_prefs = sorted([(k,v) for k,v in preferences.items() if isinstance(v, (int, float))], key=lambda x: x[1], reverse=True)[:3]
    pref_map  = pref_keywords.get(category, {})
    pref_str  = " ".join(pref_map[k] for k, _ in top_prefs if k in pref_map)
    age_str   = {"20s":"20대","30s":"30대","40s":"40대",
                 "family":"가족여행","senior":"중장년"}.get(age_group, "")

    cat_kr = {"attraction": "관광지", "hotel": "숙소 호텔", "restaurant": "맛집"}[category]
    cat_kr2 = {"attraction": "명소",  "hotel": "호텔",      "restaurant": "레스토랑"}[category]

    return [
        f"{destination} {cat_kr} {pref_str} {age_str} 추천 2024",
        f"{destination} {cat_kr2} 한국인 후기 블로그",
        f"{destination} {pref_str} 베스트 top10",
    ]


# ──────────────────────────────────────────────
# Serper 검색
# ──────────────────────────────────────────────
def search_places(query: str, num: int = 5, gl: str = "kr", hl: str = "ko") -> list[SearchResult]:
    if not SERPER_API_KEY:
        print("[WARN] SERPER_API_KEY not set → mock 데이터 사용")
        return _mock_results(query)

    try:
        resp = requests.post(
            SERPER_ENDPOINT,
            json={"q": query, "num": num, "hl": hl, "gl": gl},
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        return [
            SearchResult(title=r.get("title",""), url=r.get("link",""), snippet=r.get("snippet",""))
            for r in resp.json().get("organic", [])[:num]
        ]
    except requests.RequestException as e:
        print(f"[ERROR] Serper 실패: {e}")
        return []


def search_places_en(query: str, num: int = 5) -> list[SearchResult]:
    """영어 검색 전용 — 구글 US 결과 (TripAdvisor, Eater, TimeOut 등)"""
    return search_places(query, num=num, gl="us", hl="en")


def search_naver_blog(place_name: str, destination: str, num: int = 5) -> list[SearchResult]:
    """네이버 블로그 전용 검색 — site:blog.naver.com 타겟"""
    query = f"site:blog.naver.com {destination} {place_name} 후기 방문"
    return search_places(query, num=num, gl="kr", hl="ko")


# ──────────────────────────────────────────────
# fetch 방식 판단
# ──────────────────────────────────────────────
def _needs_playwright(url: str) -> bool:
    """URL이 JS 렌더링이 필요한 도메인인지 확인"""
    return any(domain in url for domain in JS_REQUIRED_DOMAINS)


# ──────────────────────────────────────────────
# 방식 1 — requests (정적 페이지)
# ──────────────────────────────────────────────
def _fetch_static(url: str, max_chars: int = 4000) -> FetchResult:
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
            timeout=8,
        )
        resp.raise_for_status()
        # HTML 태그, 스크립트, 스타일 제거
        text = re.sub(r"<(script|style)[^>]*>.*?</(script|style)>", " ", resp.text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return FetchResult(url=url, text=text[:max_chars], method="static")
    except Exception as e:
        return FetchResult(url=url, text="", method="failed", error=str(e))


# ──────────────────────────────────────────────
# 방식 2 — Playwright (JS 렌더링 필요한 사이트)
# ──────────────────────────────────────────────
async def _fetch_playwright_async(url: str, max_chars: int = 4000) -> FetchResult:
    """
    실제 Chromium 브라우저를 띄워서 JS 실행 후 텍스트 추출.
    네이버 블로그처럼 React로 렌더링되는 사이트 대응.
    """
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,                          # 화면 없이 백그라운드 실행
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36",
                locale="ko-KR",                         # 한국어 페이지 요청
                viewport={"width": 1280, "height": 800},
            )
            page = await context.new_page()

            # 이미지·폰트·미디어 차단 → 속도 향상
            await page.route(
                "**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,mp4,mp3}",
                lambda route: route.abort(),
            )

            await page.goto(url, wait_until="domcontentloaded", timeout=15000)

            # 네이버 블로그: iframe 내부 본문 접근
            if "blog.naver.com" in url:
                text = await _extract_naver_blog(page)
            # 트립어드바이저: 리뷰 섹션 스크롤 후 추출
            elif "tripadvisor" in url:
                text = await _extract_tripadvisor(page)
            else:
                # 일반: networkidle 대기 후 body 텍스트
                await page.wait_for_load_state("networkidle", timeout=8000)
                text = await page.inner_text("body")

            await browser.close()

            text = re.sub(r"\s+", " ", text).strip()
            return FetchResult(url=url, text=text[:max_chars], method="playwright")

    except Exception as e:
        print(f"  [Playwright ERROR] {url[:60]}... → {e}")
        return FetchResult(url=url, text="", method="failed", error=str(e))


async def _extract_naver_blog(page) -> str:
    """
    네이버 블로그는 본문이 iframe 안에 있음.
    mainFrame → iframe#mainFrame → #postViewArea 순으로 접근.
    """
    try:
        # iframe이 로드될 때까지 대기
        frame = page.frame(name="mainFrame")
        if not frame:
            await page.wait_for_selector("iframe#mainFrame", timeout=5000)
            frame = page.frame(name="mainFrame")

        if frame:
            await frame.wait_for_selector("#postViewArea, .se-main-container", timeout=5000)
            return await frame.inner_text("#postViewArea, .se-main-container")
    except Exception:
        pass
    # fallback: 페이지 전체
    return await page.inner_text("body")


async def _extract_tripadvisor(page) -> str:
    """
    트립어드바이저: 리뷰 섹션까지 스크롤 후 추출.
    '더 보기' 버튼 클릭으로 리뷰 펼치기.
    """
    try:
        # 리뷰 섹션까지 스크롤
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        await page.wait_for_timeout(1500)

        # '더 보기' 버튼들 클릭
        expand_btns = await page.query_selector_all("[data-test-target='expand-review']")
        for btn in expand_btns[:5]:
            try:
                await btn.click()
                await page.wait_for_timeout(300)
            except Exception:
                pass

        # 리뷰 컨테이너 텍스트 추출
        review_container = await page.query_selector("#REVIEWS, [data-automation='WebPresentation_PoiReviewList']")
        if review_container:
            return await review_container.inner_text()
    except Exception:
        pass
    return await page.inner_text("body")


def _fetch_playwright_sync(url: str, max_chars: int = 4000) -> FetchResult:
    """동기 래퍼 — Windows asyncio 중첩 문제 해결"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 이미 실행 중인 루프가 있으면 nest_asyncio 사용
            import nest_asyncio
            nest_asyncio.apply()
            return loop.run_until_complete(_fetch_playwright_async(url, max_chars))
        else:
            return asyncio.run(_fetch_playwright_async(url, max_chars))
    except RuntimeError:
        return asyncio.run(_fetch_playwright_async(url, max_chars))


# ──────────────────────────────────────────────
# 통합 fetch — 자동으로 방식 선택
# ──────────────────────────────────────────────
def fetch_text(url: str, max_chars: int = 4000, force_playwright: bool = False) -> FetchResult:
    """
    URL을 보고 자동으로 fetch 방식 결정.

    force_playwright=True  → 무조건 Playwright
    JS_REQUIRED_DOMAINS    → 자동으로 Playwright
    그 외                  → requests (빠름)
    """
    use_playwright = force_playwright or _needs_playwright(url)

    if use_playwright:
        print(f"  [Playwright] {url[:60]}...")
        return _fetch_playwright_sync(url, max_chars)
    else:
        print(f"  [Static]     {url[:60]}...")
        return _fetch_static(url, max_chars)


# ──────────────────────────────────────────────
# 장소별 전체 텍스트 수집
# ──────────────────────────────────────────────

# ──────────────────────────────────────────────
# Step 1 전용: 검색결과 페이지 크롤링 → 장소명 후보 텍스트 수집
# ──────────────────────────────────────────────
def collect_candidate_texts(
    query: str,
    num_results: int = 5,
    gl: str = "kr",
    hl: str = "ko",
    max_chars: int = 3000,
    delay: float = 0.3,
) -> list[str]:
    """
    쿼리 1개 → 검색결과 페이지들을 실제 크롤링 → 본문 텍스트 리스트 반환.
    snippet(150자) 대신 실제 페이지 본문을 읽어서 장소명을 더 많이 추출 가능.

    Returns: 각 페이지의 본문 텍스트 리스트
    """
    if hl == "en":
        results = search_places(query, num=num_results, gl=gl, hl=hl)
    else:
        results = search_places(query, num=num_results, gl=gl, hl=hl)

    texts = []
    for r in results[:num_results]:
        # snippet이 충분히 길면 그냥 사용 (크롤링 시간 절약)
        if len(r.snippet) >= 200:
            texts.append(f"{r.title}\n{r.snippet}")
            continue

        # snippet 짧으면 실제 페이지 크롤링
        time.sleep(delay)
        fetched = fetch_text(r.url, max_chars=max_chars)
        if fetched.text and len(fetched.text) > 100:
            texts.append(f"{r.title}\n{fetched.text}")
        elif r.snippet:
            texts.append(f"{r.title}\n{r.snippet}")

    return texts

def collect_raw_text(
    destination: str,
    place_name: str,
    category: str,
    extra_queries: list[str] | None = None,
    max_sources: int = 5,
    delay: float = 0.5,
) -> tuple[str, list[str]]:
    """
    장소명으로 다중 소스 수집:
      1. 한국어 구글 검색 (네이버 블로그 포함)
      2. 네이버 블로그 직접 타겟
      3. 영어 구글 검색 (TripAdvisor, TimeOut 등)
    Returns (combined_text, source_urls)
    """
    # ── 1. 한국어 쿼리 ──
    ko_queries = [
        f"{destination} {place_name} 후기 리뷰",
        f"{destination} {place_name} 방문 블로그",
    ]
    if extra_queries:
        ko_queries += [q for q in extra_queries if not q[0].isascii()]

    # ── 2. 네이버 블로그 전용 ──
    naver_queries = [
        f"site:blog.naver.com {destination} {place_name} 후기",
    ]

    # ── 3. 영어 쿼리 ──
    en_queries = [
        f"{place_name} {destination} review guide",
        f"{place_name} {destination} tripadvisor",
    ]
    if extra_queries:
        en_queries += [q for q in extra_queries if q[0].isascii()]

    all_parts: list[str] = []
    source_urls: list[str] = []
    seen_urls: set[str] = set()
    MAX_TOTAL = 6  # 전체 소스 최대 6개 (한국어 3 + 영어 3 수준)

    def _process_results(results, lang_tag: str):
        for r in results[:2]:  # 쿼리당 최대 2개
            if len(all_parts) >= MAX_TOTAL:
                return
            if r.url in seen_urls:
                continue
            seen_urls.add(r.url)

            if len(r.snippet) >= 300:
                all_parts.append(f"[{lang_tag} | {r.title}]\n{r.snippet}")
                source_urls.append(r.url)
                continue

            time.sleep(delay)
            result = fetch_text(r.url)
            if result.text:
                all_parts.append(f"[{lang_tag} | {r.title} | {result.method}]\n{result.text}")
                source_urls.append(r.url)
            elif r.snippet:
                all_parts.append(f"[{lang_tag} | {r.title} | fallback]\n{r.snippet}")
                source_urls.append(r.url)

    # 한국어 검색
    for query in ko_queries[:2]:
        if len(all_parts) >= MAX_TOTAL: break
        results = search_places(query, num=3, gl="kr", hl="ko")
        _process_results(results, "KO")

    # 네이버 블로그
    for query in naver_queries:
        if len(all_parts) >= MAX_TOTAL: break
        results = search_places(query, num=3, gl="kr", hl="ko")
        _process_results(results, "NAVER")

    # 영어 검색
    for query in en_queries[:2]:
        if len(all_parts) >= MAX_TOTAL: break
        results = search_places_en(query, num=3)
        _process_results(results, "EN")

    combined = "\n\n---\n\n".join(all_parts)
    return combined, source_urls


# ──────────────────────────────────────────────
# Mock (API 키 없을 때)
# ──────────────────────────────────────────────
def _mock_results(query: str) -> list[SearchResult]:
    return [
        SearchResult(
            title=f"[Mock] {query} — 네이버 블로그",
            url="https://blog.naver.com/mock/12345",
            snippet=f"{query} 방문 후기. 교통 편리하고 청결. 한국인 많음. 평점 4.5/5. "
                    "문화적 깊이 있고 포토스팟 많음. 혼잡하지만 가볼 만함.",
        ),
        SearchResult(
            title=f"[Mock] {query} — 트립어드바이저",
            url="https://tripadvisor.com/mock",
            snippet=f"{query} 상세 리뷰. 분위기 좋고 사진 잘 나옴. 대기 없음. 강력 추천.",
        ),
    ]
