"""
인스타그램 취향 분석기

공개 계정 URL → 최근 피드 캡션 수집 → Claude 취향 분석
→ preference_text + preferences dict 반환

사용:
  from utils.instagram_analyzer import analyze_instagram
  result = analyze_instagram("https://www.instagram.com/username/")
  # result = {
  #   "preference_text": "카페 감성 브런치 홍대 ...",
  #   "preferences": {"food": 5, "culture": 3, ...},
  #   "summary": "...",
  #   "post_count": 20,
  # }
"""

import re
import json
import anthropic
from dotenv import load_dotenv
load_dotenv()

client = anthropic.Anthropic()


def _extract_username(url_or_username: str) -> str:
    """URL 또는 @username → 순수 username 추출."""
    s = url_or_username.strip().rstrip("/")
    # URL 형태
    m = re.search(r"instagram\.com/([^/?#]+)", s)
    if m:
        return m.group(1)
    # @username 형태
    return s.lstrip("@")


def _fetch_captions(username: str, max_posts: int = 30) -> list[str]:
    """Playwright로 인스타그램 공개 계정 피드 캡션 수집."""
    import os, asyncio, json as _json

    ig_id = os.getenv("INSTAGRAM_ID")
    ig_pw = os.getenv("INSTAGRAM_PW")

    async def _scrape():
        from playwright.async_api import async_playwright
        captions = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 900},
            )

            # 쿠키 파일이 있으면 로드 (재로그인 방지)
            cookie_file = os.path.join(os.path.dirname(__file__), ".instagram_cookies.json")
            if os.path.exists(cookie_file):
                with open(cookie_file, encoding="utf-8") as f:
                    await context.add_cookies(_json.load(f))
                print("[Instagram] 쿠키 로드 완료")
            elif ig_id and ig_pw:
                # 로그인
                page = await context.new_page()
                await page.goto("https://www.instagram.com/accounts/login/")
                await page.wait_for_timeout(4000)
                # 셀렉터가 바뀔 수 있으므로 여러 방법 시도
                await page.wait_for_selector("input[name='username'], input[aria-label='전화번호, 사용자 이름 또는 이메일'], input[aria-label='Phone number, username, or email']", timeout=20000)
                await page.wait_for_selector("input[name='email']", timeout=15000)
                await page.fill("input[name='email']", ig_id)
                await page.wait_for_timeout(500)
                await page.fill("input[name='pass']", ig_pw)
                await page.wait_for_timeout(500)
                await page.click("div[role='button']:has-text('Log in')")
                await page.wait_for_timeout(6000)
                # 쿠키 저장
                cookies = await context.cookies()
                with open(cookie_file, "w", encoding="utf-8") as f:
                    _json.dump(cookies, f)
                print(f"[Instagram] @{ig_id} 로그인 완료, 쿠키 저장")
                await page.close()

            # 프로필 페이지 접근
            page = await context.new_page()
            url = f"https://www.instagram.com/{username}/"
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)

            # 비공개 계정 체크
            body = await page.inner_text("body")
            if "This Account is Private" in body or "비공개 계정" in body:
                await browser.close()
                raise ValueError(f"비공개 계정: @{username}")

            # 게시물 링크 수집 (최대 max_posts개)
            post_links = await page.eval_on_selector_all(
                "a[href*='/p/']",
                "els => [...new Set(els.map(e => e.href))].filter(h => h.includes('/p/'))"
            )
            post_links = post_links[:max_posts]

            if not post_links:
                await browser.close()
                raise ValueError(f"게시물을 찾을 수 없음: @{username} (비공개이거나 게시물 없음)")

            print(f"[Instagram] 게시물 {len(post_links)}개 발견 → 캡션 수집 중...")

            # 각 게시물 캡션 수집
            for link in post_links:
                try:
                    await page.goto(link, wait_until="domcontentloaded", timeout=30000)
                    await page.wait_for_timeout(1500)

                    caption_text = ""

                    # 1) og:description 메타 태그 (서버사이드 렌더링 → 항상 존재)
                    try:
                        caption_text = await page.get_attribute(
                            "meta[property='og:description']", "content"
                        ) or ""
                        caption_text = caption_text.strip()
                    except Exception:
                        pass

                    # 2) og:description 없으면 description 메타 태그
                    if not caption_text:
                        try:
                            caption_text = await page.get_attribute(
                                "meta[name='description']", "content"
                            ) or ""
                            caption_text = caption_text.strip()
                        except Exception:
                            pass

                    # 3) 그래도 없으면 JSON-LD
                    if not caption_text:
                        try:
                            ld_texts = await page.eval_on_selector_all(
                                "script[type='application/ld+json']",
                                "els => els.map(e => e.textContent)"
                            )
                            import json as _j
                            for ld_raw in ld_texts:
                                try:
                                    ld = _j.loads(ld_raw)
                                    desc = ld.get("description") or ld.get("caption") or ""
                                    if len(desc) > 5:
                                        caption_text = desc
                                        break
                                except Exception:
                                    pass
                        except Exception:
                            pass

                    if caption_text and len(caption_text) > 5:
                        captions.append(caption_text[:500])
                except Exception:
                    continue

            await browser.close()
        return captions

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import nest_asyncio
            nest_asyncio.apply()
            return loop.run_until_complete(_scrape())
        else:
            return asyncio.run(_scrape())
    except RuntimeError:
        return asyncio.run(_scrape())


def _analyze_with_claude(username: str, captions: list[str]) -> dict:
    """캡션 목록 → Claude로 취향 분석."""
    if not captions:
        raise ValueError("분석할 게시물 캡션이 없습니다 (피드가 비어있거나 캡션 없는 이미지만 있음)")

    captions_text = "\n---\n".join(captions[:25])

    prompt = f"""
다음은 인스타그램 @{username} 계정의 최근 게시물 캡션들입니다.

[캡션 목록]
{captions_text}

이 사람의 여행/라이프스타일 취향을 분석해서 JSON으로 응답하세요.

분석 항목:
1. preference_text: 이 사람의 취향을 잘 나타내는 한국어 키워드 나열 (10~20개, 공백 구분)
   예: "카페 감성 브런치 홍대 힙한 인테리어 사진 맛집 파스타"
2. preferences: 취향 수치 (각 1~5, 5=매우 선호)
   - food: 음식/맛집 관심도
   - culture: 역사/문화/박물관 관심도
   - nature: 자연/공원/힐링 관심도
   - activity: 액티비티/체험 관심도
   - nightlife: 나이트라이프/술/클럽 관심도
   - shopping: 쇼핑 관심도
   - cleanliness: 위생/청결 민감도
   - walking_aversion: 도보 이동 기피도 (5=이동 싫어함)
3. scoring_style: "balanced"/"peak"/"threshold"/"risk_averse"/"budget_safe" 중 하나
   - peak: 하이라이트 경험 추구형
   - risk_averse: 검증된 유명 맛집만
   - budget_safe: 가성비 중시
   - threshold: 전반적 품질 중시
   - balanced: 무난한 균형
4. summary: 이 사람의 여행 취향 한 줄 요약 (한국어, 30자 이내)

캡션이 여행과 무관한 일상 내용이면 추정 가능한 범위에서 분석하세요.

JSON만 응답 (다른 텍스트 없이):
{{
  "preference_text": "...",
  "preferences": {{"food": 4, "culture": 3, "nature": 2, "activity": 3, "nightlife": 2, "shopping": 3, "cleanliness": 4, "walking_aversion": 3}},
  "scoring_style": "balanced",
  "summary": "..."
}}
"""

    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    s, e = raw.find("{"), raw.rfind("}")
    if s == -1 or e == -1:
        raise ValueError(f"Claude 응답 파싱 실패: {raw[:200]}")
    return json.loads(raw[s:e+1])


def analyze_instagram(url_or_username: str, max_posts: int = 30) -> dict:
    """
    메인 함수. Instagram URL 또는 username을 받아 취향 분석 결과 반환.

    Returns:
        {
            "username": str,
            "post_count": int,
            "preference_text": str,
            "preferences": dict,
            "scoring_style": str,
            "summary": str,
        }
    """
    username = _extract_username(url_or_username)
    print(f"[Instagram] @{username} 피드 수집 중...")

    captions = _fetch_captions(username, max_posts)
    print(f"[Instagram] 캡션 {len(captions)}개 수집 완료 → Claude 취향 분석 중...")

    result = _analyze_with_claude(username, captions)

    # scoring_style을 preferences에 병합 (query_and_plan 호환)
    prefs = result.get("preferences", {})
    prefs["scoring_style"] = result.get("scoring_style", "balanced")

    print(f"[Instagram] 분석 완료: {result.get('summary', '')}")

    return {
        "username": username,
        "post_count": len(captions),
        "preference_text": result.get("preference_text", ""),
        "preferences": prefs,
        "scoring_style": result.get("scoring_style", "balanced"),
        "summary": result.get("summary", ""),
    }
