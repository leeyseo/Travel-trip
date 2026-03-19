"""
숙소 브라우징 에이전트

흐름:
  1단계: 검색 → 블로그/리뷰 수집 → LLM으로 호텔명 추출
  2단계: 카카오맵으로 좌표 + 지하철 접근성 계산
  3단계: 웹 크롤링 + LLM으로 피처 추출
  4단계: 점수 계산 + Booking.com 딥링크 생성
"""

import json
import os
import time
import hashlib
import requests
from typing import Iterator

import anthropic

from models.schemas import (
    TripInput, PlaceNode, PlaceCategory,
    HotelFeatures,
)
from utils.web_collector import search_places, collect_raw_text
from utils.feature_extractor import extract_features, DEFAULT_COORDS, COORD_BOUNDS
from utils.scorer import score_hotel

client = anthropic.Anthropic()
KAKAO_KEY = os.environ.get("KAKAO_API_KEY", "")
BOOKING_AFFILIATE_ID = os.environ.get("BOOKING_AFFILIATE_ID", "")


def _make_place_id(name: str, destination: str) -> str:
    return hashlib.md5(f"{destination}_{name}".encode()).hexdigest()[:12]


# ──────────────────────────────────────────────
# 카카오맵으로 호텔 좌표 + 지하철 접근성
# ──────────────────────────────────────────────
async def _fetch_naver_price(hotel_name: str) -> int | None:
    """네이버 검색으로 호텔 평균 가격 크롤링"""
    try:
        import urllib.parse
        from playwright.async_api import async_playwright
        import statistics

        query = urllib.parse.quote(hotel_name)
        url = f"https://search.naver.com/search.naver?query={query}&where=nexearch"

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_extra_http_headers({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            await page.goto(url, timeout=15000)
            await page.wait_for_timeout(2000)
            text = await page.inner_text("body")
            await browser.close()

        import re
        prices = re.findall(r'(\d{2,3},\d{3})\s*원|₩\s*([\d,]+)', text)
        values = []
        for p1, p2 in prices:
            raw = p1 or p2
            val = int(raw.replace(",", ""))
            if 50000 <= val <= 3000000:
                values.append(val)

        if not values:
            return None

        # 중간값 사용 (최저가/최고가 이상치 제거)
        median = int(statistics.median(values))
        return median

    except Exception as e:
        print(f"  [Naver Price WARN] {e}")
        return None


def _get_naver_price(hotel_name: str) -> int | None:
    """동기 래퍼"""
    import asyncio
    try:
        return asyncio.run(_fetch_naver_price(hotel_name))
    except Exception:
        return None


def _geocode_hotel(hotel_name: str, destination: str) -> tuple | None:
    """카카오맵으로 호텔 좌표 검색"""
    if not KAKAO_KEY:
        return None
    try:
        url = "https://dapi.kakao.com/v2/local/search/keyword.json"
        headers = {"Authorization": f"KakaoAK {KAKAO_KEY}"}
        for query in [f"{destination} {hotel_name}", hotel_name]:
            params = {"query": query, "size": 1}
            resp = requests.get(url, params=params, headers=headers, timeout=5)
            resp.raise_for_status()
            docs = resp.json().get("documents", [])
            if docs:
                lat = float(docs[0]["y"])
                lng = float(docs[0]["x"])
                print(f"  [Kakao OK] '{docs[0]['place_name']}' → ({lat:.4f}, {lng:.4f})")
                return (lat, lng)
    except Exception as e:
        print(f"  [Kakao WARN] {hotel_name}: {e}")
    return None


def _get_transit_score(lat: float, lng: float) -> tuple[int, str]:
    """카카오 카테고리 검색으로 지하철 접근성 점수 계산"""
    if not KAKAO_KEY:
        return 3, "API 없음"
    try:
        url = "https://dapi.kakao.com/v2/local/search/category.json"
        params = {
            "category_group_code": "SW8",  # 지하철역
            "x": lng,
            "y": lat,
            "radius": 1000,
            "sort": "distance",
            "size": 5,
        }
        headers = {"Authorization": f"KakaoAK {KAKAO_KEY}"}
        resp = requests.get(url, params=params, headers=headers, timeout=5)
        resp.raise_for_status()
        stations = resp.json().get("documents", [])

        if not stations:
            return 1, "반경 1km 내 지하철 없음"

        nearest = int(stations[0].get("distance", 9999))
        count = len(stations)
        nearest_name = stations[0].get("place_name", "")

        if nearest <= 200:
            score = 5
        elif nearest <= 400:
            score = 4
        elif nearest <= 700:
            score = 3
        elif nearest <= 1000:
            score = 2
        else:
            score = 1

        return score, f"{nearest_name} {nearest}m"
    except Exception as e:
        print(f"  [Transit WARN] {e}")
        return 3, "조회 실패"


# ──────────────────────────────────────────────
# Booking.com 딥링크 생성
# ──────────────────────────────────────────────
def make_booking_url(
    destination: str,
    hotel_name: str,
    duration_days: int,
    traveler_count: int,
    checkin: str = None,
) -> str:
    """Booking.com 딥링크 생성"""
    import urllib.parse
    from datetime import datetime, timedelta

    if not checkin:
        checkin = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    checkout_dt = datetime.strptime(checkin, "%Y-%m-%d") + timedelta(days=duration_days)
    checkout = checkout_dt.strftime("%Y-%m-%d")

    query = urllib.parse.quote(f"{hotel_name} {destination}")
    aid = BOOKING_AFFILIATE_ID or "default"

    url = (
        f"https://www.booking.com/searchresults.html"
        f"?ss={query}"
        f"&checkin={checkin}"
        f"&checkout={checkout}"
        f"&group_adults={traveler_count}"
        f"&aid={aid}"
    )
    return url


# ──────────────────────────────────────────────
# 호텔명 추출
# ──────────────────────────────────────────────
def extract_hotel_names(raw_text: str, destination: str, max_hotels: int = 15) -> list[str]:
    prompt = f"""
다음은 {destination} 숙소 관련 웹페이지 본문입니다.

[본문]
{raw_text[:3000]}

위 본문에서 실제 호텔/게스트하우스/펜션 이름만 추출하세요.
- 포함: 호텔명, 게스트하우스명, 펜션명, 리조트명
- 제외: 지역명, 블로그 제목, "추천", "베스트" 같은 일반 표현

반드시 JSON 배열로만 응답 (다른 텍스트 없이):
["호텔명1", "호텔명2", ...]

추출 불가시 빈 배열 [].
최대 {max_hotels}개.
"""
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        start, end = raw.find("["), raw.rfind("]")
        if start != -1 and end != -1:
            names = json.loads(raw[start:end+1])
            return [n for n in names if isinstance(n, str) and 1 < len(n) < 30]
    except Exception as e:
        print(f"  [WARN] 호텔명 추출 실패: {e}")
    return []


# ──────────────────────────────────────────────
# 숙소 브라우징 에이전트
# ──────────────────────────────────────────────
class HotelBrowsingAgent:
    def __init__(self, trip: TripInput, max_places: int = 10, delay: float = 0.3, verbose: bool = True, checkin: str = "", checkout: str = ""):
        self.trip = trip
        self.max_places = max_places
        self.delay = delay
        self.verbose = verbose
        self.checkin = checkin
        self.checkout = checkout

    def _log(self, msg: str):
        if self.verbose:
            print(f"[HotelAgent] {msg}", flush=True)

    def _collect_candidates(self) -> list[str]:
        self._log(f"Step 1: 숙소 수집 — {self.trip.destination}")
        dest = self.trip.destination
        age = {"20s":"20대","30s":"30대","40s":"40대","family":"가족"}.get(self.trip.age_group, "")
        budget = self.trip.budget_krw // self.trip.duration_days // self.trip.traveler_count

        queries = [
            f"{dest} 호텔 추천 {age} 2024",
            f"{dest} 숙소 한국인 후기 가성비",
            f"{dest} 호텔 베스트 위치 좋은",
        ]

        exclude_patterns = ["추천", "베스트", "top", "best", "리스트", "숙소", "호텔가"]
        all_names: list[str] = []

        for query in queries:
            self._log(f"  검색: {query}")
            results = search_places(query, num=5)
            for r in results[:4]:
                combined = f"{r.title}\n{r.snippet}"
                names = extract_hotel_names(combined, dest)
                if names:
                    self._log(f"    → {names}")
                    all_names.extend(names)
                time.sleep(self.delay)

        seen, unique = set(), []
        for name in all_names:
            if name in seen:
                continue
            if len(name) < 2 or len(name) > 25:
                continue
            if any(p in name.lower() for p in exclude_patterns):
                continue
            seen.add(name)
            unique.append(name)

        self._log(f"  → 총 {len(unique)}개 숙소명: {unique[:5]}...")
        return unique[:self.max_places]

    def _process_place(self, hotel_name: str) -> PlaceNode | None:
        dest = self.trip.destination
        self._log(f"  처리 중: {hotel_name}")

        # 웹 크롤링
        raw_text, sources = collect_raw_text(
            dest, hotel_name, "hotel",
            extra_queries=[f"{dest} {hotel_name} 후기 가격 위치"]
        )
        if not raw_text:
            self._log(f"  [SKIP] 텍스트 없음: {hotel_name}")
            return None

        # LLM 피처 추출
        feat_dict = extract_features(hotel_name, raw_text, "hotel", destination=dest)
        if not feat_dict:
            return None

        # 카카오맵으로 좌표 확정
        geo = _geocode_hotel(hotel_name, dest)
        if geo:
            lat, lng = geo
            # 좌표 범위 검증
            bounds = COORD_BOUNDS.get(dest)
            if bounds:
                if not (bounds["lat"][0] <= lat <= bounds["lat"][1] and
                        bounds["lng"][0] <= lng <= bounds["lng"][1]):
                    self._log(f"  [제외] {hotel_name} 좌표 범위 밖")
                    return None
        else:
            lat, lng = DEFAULT_COORDS.get(dest, (37.5665, 126.9780))
            self._log(f"  [좌표 fallback] {hotel_name}")

        # 카카오로 지하철 접근성 계산
        transit_score, transit_desc = _get_transit_score(lat, lng)
        self._log(f"  [교통] {transit_desc} → {transit_score}/5")

        # Booking.com 딥링크
        booking_url = make_booking_url(
            dest, hotel_name,
            self.trip.duration_days,
            self.trip.traveler_count,
            checkin=self.checkin or None,
        )

        try:
            features = HotelFeatures(
                overall_rating=feat_dict.get("overall_rating", 3.5),
                review_count=feat_dict.get("review_count", 100),
                transit_access=transit_score,  # 카카오로 계산한 값 사용
                korean_popular=feat_dict.get("korean_popular", 3.0),
                price_level=feat_dict.get("price_level", 2),
                lat=lat,
                lng=lng,
                star_grade=feat_dict.get("star_grade", 3),
                price_per_night=feat_dict.get("price_per_night") or feat_dict.get("avg_price_per_night") or {
                    5: 350000, 4: 180000, 3: 90000, 2: 55000
                }.get(int(feat_dict.get("star_grade", 3)), 100000),
                cleanliness_score=feat_dict.get("cleanliness_score", 3.5),
                service_score=feat_dict.get("service_score", 3.5),
                breakfast_quality=feat_dict.get("breakfast_quality", 0.0),
                wifi_quality=feat_dict.get("wifi_quality", 3.0),
                center_distance_km=feat_dict.get("center_distance_km", 2.0),
                korean_friendly=feat_dict.get("korean_friendly", 3.0),
                has_gym=feat_dict.get("has_gym", False),
                has_pool=feat_dict.get("has_pool", False),
                family_friendly=feat_dict.get("family_friendly", False),
                late_checkin=feat_dict.get("late_checkin", False),
                booking_url=booking_url,
            )
        except Exception as e:
            self._log(f"  [WARN] Feature build failed: {e}")
            return None

        # 네이버에서 실제 가격 크롤링 후 덮어쓰기
        naver_price = _get_naver_price(hotel_name)
        if naver_price:
            features.price_per_night = naver_price
            self._log(f"  [가격] 네이버 실측 {naver_price:,}원/박")
        else:
            self._log(f"  [가격] 네이버 실패 → LLM 추정 {features.price_per_night:,}원/박")

        # 1박 예산 = 총 예산 / 박수 / 인원 * 0.4 (숙소에 40% 배분)
        budget_per_night = int(
            self.trip.budget_krw / self.trip.duration_days / self.trip.traveler_count * 0.4
        )
        score, breakdown = score_hotel(features, self.trip.preferences, budget_per_night)
        if score < 0.3:
            self._log(f"  [SKIP] score 낮음: {score:.3f}")
            return None

        node = PlaceNode(
            place_id=_make_place_id(hotel_name, dest),
            name=hotel_name,
            address=f"{dest}, {hotel_name}",
            category=PlaceCategory.HOTEL,
            features=features,
            node_score=score,
            score_breakdown=breakdown,
            sources=sources[:3],
        )
        self._log(f"  ✓ {hotel_name} → score={score:.3f}  ★{features.star_grade}  {transit_desc}")
        return node

    def run(self) -> list[PlaceNode]:
        candidates = self._collect_candidates()
        if not candidates:
            self._log("숙소명을 찾지 못했습니다.")
            return []

        self._log(f"\nStep 2: 숙소별 피처 추출 ({len(candidates)}개)")
        nodes = []
        for name in candidates:
            node = self._process_place(name)
            if node:
                nodes.append(node)
            time.sleep(self.delay)

        nodes.sort(key=lambda n: n.node_score, reverse=True)
        self._log(f"\n완료: {len(nodes)}개 숙소 노드 생성")
        return nodes

    def run_stream(self) -> Iterator[tuple[str, dict]]:
        candidates = self._collect_candidates()
        yield "candidates", {"count": len(candidates), "names": candidates}

        nodes = []
        for i, name in enumerate(candidates):
            yield "processing", {"index": i+1, "total": len(candidates), "name": name}
            node = self._process_place(name)
            if node:
                nodes.append(node)
                yield "node_ready", node.to_dict()
            time.sleep(self.delay)

        nodes.sort(key=lambda n: n.node_score, reverse=True)
        yield "done", {"total_nodes": len(nodes)}
