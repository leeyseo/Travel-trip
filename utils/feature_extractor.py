"""
LLM 피처 추출기 — 수집된 텍스트 → 구조화된 피처 딕셔너리

수정사항:
- JSON 파싱 오류 방지 (응답에서 JSON 블록만 안전하게 추출)
- 좌표 유효성 검증 + Nominatim으로 실제 좌표 보정
"""

import json
import os
import re
import time
import anthropic
import requests

client = anthropic.Anthropic()

COORD_BOUNDS = {
    # 국내
    "서울":    {"lat": (37.4, 37.7),   "lng": (126.7, 127.2)},
    "부산":    {"lat": (35.0, 35.3),   "lng": (128.8, 129.4)},
    "제주":    {"lat": (33.1, 33.6),   "lng": (126.1, 126.95)},
    "대구":    {"lat": (35.7, 36.0),   "lng": (128.4, 128.8)},
    "인천":    {"lat": (37.3, 37.6),   "lng": (126.4, 126.8)},
    "광주":    {"lat": (35.0, 35.3),   "lng": (126.7, 127.0)},
    "대전":    {"lat": (36.2, 36.5),   "lng": (127.2, 127.6)},
    "울산":    {"lat": (35.4, 35.7),   "lng": (129.1, 129.5)},
    "경주":    {"lat": (35.7, 36.0),   "lng": (129.1, 129.4)},
    "전주":    {"lat": (35.7, 35.9),   "lng": (126.9, 127.2)},
    "강릉":    {"lat": (37.6, 37.9),   "lng": (128.8, 129.1)},
    "여수":    {"lat": (34.6, 34.9),   "lng": (127.5, 127.9)},
    "속초":    {"lat": (38.1, 38.3),   "lng": (128.5, 128.7)},
    "춘천":    {"lat": (37.8, 38.0),   "lng": (127.6, 127.9)},
    # 일본
    "도쿄":    {"lat": (35.5, 35.9),   "lng": (139.4, 139.9)},
    "오사카":  {"lat": (34.5, 34.8),   "lng": (135.3, 135.7)},
    "교토":    {"lat": (34.9, 35.1),   "lng": (135.6, 135.9)},
    "후쿠오카":{"lat": (33.5, 33.7),   "lng": (130.2, 130.6)},
    "삿포로":  {"lat": (43.0, 43.2),   "lng": (141.2, 141.5)},
    "나고야":  {"lat": (35.0, 35.3),   "lng": (136.8, 137.1)},
    # 동남아
    "방콕":    {"lat": (13.5, 14.0),   "lng": (100.3, 100.8)},
    "싱가포르":{"lat": (1.2, 1.5),     "lng": (103.6, 104.1)},
    "발리":    {"lat": (-8.9, -8.3),   "lng": (114.9, 115.7)},
    "다낭":    {"lat": (15.9, 16.2),   "lng": (108.1, 108.4)},
    "하노이":  {"lat": (20.9, 21.2),   "lng": (105.7, 106.0)},
    "호치민":  {"lat": (10.6, 11.0),   "lng": (106.5, 106.9)},
    # 유럽/기타
    "파리":    {"lat": (48.7, 49.0),   "lng": (2.2, 2.5)},
    "런던":    {"lat": (51.3, 51.7),   "lng": (-0.4, 0.2)},
    "바르셀로나":{"lat": (41.2, 41.6), "lng": (2.0, 2.3)},
    "로마":    {"lat": (41.7, 42.0),   "lng": (12.3, 12.7)},
    "뉴욕":    {"lat": (40.5, 40.9),   "lng": (-74.3, -73.7)},
}

DEFAULT_COORDS = {
    "제주":  (33.4890, 126.4983),
    "서울":  (37.5665, 126.9780),
    "부산":  (35.1796, 129.0756),
    "도쿄":  (35.6762, 139.6503),
    "오사카":(34.6937, 135.5023),
    "교토":  (35.0116, 135.7681),
}

ATTRACTION_EXTRACTION_PROMPT = """
당신은 여행지 데이터 추출 전문가입니다.
아래 장소에 대한 텍스트를 읽고 피처를 추출하세요.

[장소명]: {place_name}
[여행지]: {destination}
[수집된 텍스트]:
{raw_text}

다음 JSON 형식으로만 응답하세요. JSON 외 다른 텍스트는 절대 쓰지 마세요:
{{
  "overall_rating": <0-5>,
  "review_count": <정수>,
  "transit_access": <0-5, 차량필수=1, 도보5분=5>,
  "korean_popular": <0-5>,
  "price_level": <0-4>,
  "entry_fee_krw": <정수, 무료=0>,
  "avg_duration_hr": <소수>,
  "photo_worthiness": <0-5>,
  "activity_level": <0-5>,
  "culture_depth": <0-5>,
  "nature_score": <0-5>,
  "crowd_level": <0-5>,
  "nightlife_score": <0-5>,
  "indoor": <true 또는 false>,
  "korean_signage": <true 또는 false>,
  "age_suitability": <"all" 또는 "young" 또는 "family" 또는 "senior">,
  "category": <"문화유산" 또는 "자연" 또는 "테마파크" 또는 "박물관" 또는 "전망대" 또는 "쇼핑" 또는 "현대예술체험" 또는 "공원박물관">
}}
"""

HOTEL_EXTRACTION_PROMPT = """
당신은 숙소 데이터 추출 전문가입니다.

[숙소명]: {place_name}
[여행지]: {destination}
[수집된 텍스트]:
{raw_text}

다음 JSON 형식으로만 응답하세요. JSON 외 다른 텍스트는 절대 쓰지 마세요:
{{
  "overall_rating": <0-5>,
  "review_count": <정수>,
  "transit_access": <0-5>,
  "korean_popular": <0-5>,
  "price_level": <0-4>,
  "price_per_night": <정수, KRW>,
  "star_grade": <0-5>,
  "cleanliness_score": <0-5>,
  "service_score": <0-5>,
  "breakfast_quality": <0-5>,
  "wifi_quality": <0-5>,
  "center_distance_km": <소수>,
  "korean_friendly": <0-5>,
  "has_gym": <true 또는 false>,
  "has_pool": <true 또는 false>,
  "family_friendly": <true 또는 false>,
  "late_checkin": <true 또는 false>
}}
"""

RESTAURANT_EXTRACTION_PROMPT = """
당신은 맛집 데이터 추출 전문가입니다.

[식당명]: {place_name}
[여행지]: {destination}
[수집된 텍스트]:
{raw_text}

다음 JSON 형식으로만 응답하세요. JSON 외 다른 텍스트는 절대 쓰지 마세요:
{{
  "overall_rating": <0-5>,
  "review_count": <정수>,
  "transit_access": <0-5>,
  "korean_popular": <0-5>,
  "price_level": <0-4>,
  "cuisine_type": <문자열>,
  "meal_type": <"아침" 또는 "점심" 또는 "저녁" 또는 "카페" 또는 "전체">,
  "avg_price_per_person": <정수, KRW>,
  "taste_score": <0-5>,
  "food_diversity": <0-5>,
  "local_authenticity": <0-5>,
  "michelin_tier": <"없음" 또는 "빕구르망" 또는 "1스타" 또는 "2스타" 또는 "3스타">,
  "ambiance_score": <0-5>,
  "cleanliness_score": <0-5>,
  "wait_time_min": <정수>,
  "reservation_required": <true 또는 false>,
  "korean_menu": <true 또는 false>,
  "dietary_options": <문자열>
}}
"""

PROMPT_MAP = {
    "attraction": ATTRACTION_EXTRACTION_PROMPT,
    "hotel":      HOTEL_EXTRACTION_PROMPT,
    "restaurant": RESTAURANT_EXTRACTION_PROMPT,
}


def _extract_json_from_text(text: str) -> str:
    """LLM 응답에서 JSON 블록만 안전하게 추출"""
    text = re.sub(r"```(?:json)?", "", text).strip()
    start = text.find("{")
    if start == -1:
        return text
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i+1]
    return text[start:]


def _get_transit_score(lat: float, lng: float) -> tuple[float, str]:
    """
    카카오 카테고리 검색으로 지하철 접근성 점수 계산.
    반경 1km 내 가장 가까운 지하철역 거리 기반.
    Returns: (score 1~5, 설명 문자열)
    """
    kakao_key = os.environ.get("KAKAO_API_KEY", "")
    if not kakao_key or lat == 0.0 or lng == 0.0:
        return 3.0, "측정 불가"
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
        headers = {"Authorization": f"KakaoAK {kakao_key}"}
        resp = requests.get(url, params=params, headers=headers, timeout=5)
        resp.raise_for_status()
        stations = resp.json().get("documents", [])

        if not stations:
            return 1.0, "반경 1km 내 지하철 없음"

        nearest = int(stations[0].get("distance", 9999))
        nearest_name = stations[0].get("place_name", "")
        count = len(stations)

        if nearest <= 200:
            score = 5.0
        elif nearest <= 400:
            score = 4.0
        elif nearest <= 700:
            score = 3.0
        elif nearest <= 1000:
            score = 2.0
        else:
            score = 1.0

        desc = f"{nearest_name} {nearest}m"
        return score, desc
    except Exception as e:
        return 3.0, f"조회 실패: {e}"


def _geocode_kakao(place_name: str, destination: str) -> tuple | None:
    """카카오맵 로컬 검색 API — 한국 장소/식당에 최적화"""
    kakao_key = os.environ.get("KAKAO_API_KEY", "")
    if not kakao_key:
        return None
    try:
        # "본점" 등 suffix 제거한 단순화 이름
        simplified = place_name
        for suffix in ["본점", "1호점", "2호점", "직영점", "명동점", "강남점", "홍대점", "대학로본점", "신촌점"]:
            simplified = simplified.replace(suffix, "").strip()

        # 여행지 중심 좌표 (카카오 반경 검색용)
        center = DEFAULT_COORDS.get(destination, (37.5665, 126.9780))

        url = "https://dapi.kakao.com/v2/local/search/keyword.json"
        headers = {"Authorization": f"KakaoAK {kakao_key}"}

        for query in [f"{destination} {place_name}", f"{destination} {simplified}", place_name, simplified]:
            if not query.strip():
                continue
            params = {"query": query, "size": 1}
            resp = requests.get(url, params=params, headers=headers, timeout=5)
            resp.raise_for_status()
            docs = resp.json().get("documents", [])
            if docs:
                lat = float(docs[0]["y"])
                lng = float(docs[0]["x"])
                name = docs[0].get("place_name", "")
                print(f"  [Kakao OK] '{name}' → ({lat:.4f}, {lng:.4f})")
                return (lat, lng)
    except Exception as e:
        print(f"  [Kakao WARN] {place_name}: {e}")
    return None


def _geocode_nominatim(place_name: str, destination: str) -> tuple | None:
    """Nominatim(OpenStreetMap) fallback geocoding"""
    try:
        from geopy.geocoders import Nominatim
        geolocator = Nominatim(user_agent="travel_agent_v2")

        simplified = place_name
        for suffix in ["본점", "1호점", "2호점", "직영점", "명동점", "강남점", "홍대점", "대학로본점"]:
            simplified = simplified.replace(suffix, "").strip()

        queries = [
            f"{place_name}, {destination}, 대한민국",
            f"{simplified}, {destination}, 대한민국",
            f"{place_name} {destination}",
            place_name,
        ]
        for query in queries:
            time.sleep(1.1)
            location = geolocator.geocode(query, timeout=10)
            if location:
                print(f"  [Nominatim OK] ({location.latitude:.4f}, {location.longitude:.4f})")
                return (location.latitude, location.longitude)
    except Exception as e:
        print(f"  [Nominatim WARN] {place_name}: {e}")
    return None


def _geocode(place_name: str, destination: str) -> tuple | None:
    """좌표 검색: 카카오맵 우선 → Nominatim 폴백"""
    # 1순위: 카카오맵 (한국 장소에 훨씬 정확)
    result = _geocode_kakao(place_name, destination)
    if result:
        return result
    # 2순위: Nominatim
    print(f"  [Kakao 실패] Nominatim 폴백 시도...")
    return _geocode_nominatim(place_name, destination)


def _validate_coords(lat: float, lng: float, destination: str, place_name: str) -> tuple:
    """좌표 범위 검증 → 벗어나면 Nominatim 보정 → 최종 fallback"""
    # (0,0)은 LLM이 좌표를 못 찾은 경우 — 바로 geocoding으로
    if lat == 0.0 and lng == 0.0:
        print(f"  [좌표 보정] {place_name} → (0,0) 감지, Nominatim 조회 중...")
        result = _geocode(place_name, destination)
        if result:
            return result
        return DEFAULT_COORDS.get(destination, (37.5665, 126.9780))

    bounds = COORD_BOUNDS.get(destination)
    if bounds:
        lat_ok = bounds["lat"][0] <= lat <= bounds["lat"][1]
        lng_ok = bounds["lng"][0] <= lng <= bounds["lng"][1]
        if lat_ok and lng_ok:
            return lat, lng

    print(f"  [좌표 보정] {place_name} ({lat:.4f}, {lng:.4f}) → Nominatim 조회 중...")
    result = _geocode(place_name, destination)
    if result:
        new_lat, new_lng = result
        if bounds:
            if bounds["lat"][0] <= new_lat <= bounds["lat"][1] and \
               bounds["lng"][0] <= new_lng <= bounds["lng"][1]:
                print(f"  [좌표 보정 성공] → ({new_lat:.4f}, {new_lng:.4f})")
                return new_lat, new_lng
            else:
                # Geocoding 성공했지만 범위 밖 → 엉뚱한 장소
                print(f"  [제외] {place_name} geocoding 결과 {destination} 범위 밖 → None")
                return None
        else:
            # bounds 없는 여행지면 geocoding 결과 그대로 사용
            return new_lat, new_lng

    # Geocoding 실패 + bounds 있는 경우 → fallback 대신 None (범위 검증 불가)
    if bounds:
        print(f"  [경고] {place_name} geocoding 실패 → fallback 좌표 사용")
    default = DEFAULT_COORDS.get(destination, (37.5665, 126.9780))
    return default


def extract_features(
    place_name: str,
    raw_text: str,
    category: str,
    destination: str = "",
    model: str = "claude-haiku-4-5-20251001",
) -> dict:
    prompt_template = PROMPT_MAP.get(category)
    if not prompt_template:
        raise ValueError(f"Unknown category: {category}")

    prompt = prompt_template.format(
        place_name=place_name,
        destination=destination or "알 수 없음",
        raw_text=raw_text[:4000],
    )

    try:
        response = client.messages.create(
            model=model,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        json_str = _extract_json_from_text(raw)
        result = json.loads(json_str)

        # 관광지만 좌표 검증
        if category == "attraction" and destination:
            lat = float(result.get("lat", 0))
            lng = float(result.get("lng", 0))
            result["lat"], result["lng"] = _validate_coords(lat, lng, destination, place_name)

        return result

    except (json.JSONDecodeError, anthropic.APIError) as e:
        print(f"[WARN] Feature extraction failed for '{place_name}': {e}")
        return {}


# ──────────────────────────────────────────────
# 장소 소속 여행지 검증 (좌표 범위 기반, API 호출 없음)
# ──────────────────────────────────────────────

def verify_place_belongs(
    place_name: str,
    lat: float,
    lng: float,
    destination: str,
) -> bool:
    """
    좌표가 여행지의 COORD_BOUNDS 범위 안에 있는지 확인.
    - API 호출 없이 즉시 판별 (빠름)
    - COORD_BOUNDS에 없는 여행지면 True 반환 (관대하게 허용)
    - 좌표가 (0,0)이면 아직 못 찾은 것이므로 True 반환
    """
    # 좌표 미확정이면 통과 (이후 geocoding에서 처리됨)
    if lat == 0.0 and lng == 0.0:
        return True

    bounds = COORD_BOUNDS.get(destination)
    if not bounds:
        return True  # 등록 안 된 여행지면 통과

    lat_ok = bounds["lat"][0] <= lat <= bounds["lat"][1]
    lng_ok = bounds["lng"][0] <= lng <= bounds["lng"][1]

    if lat_ok and lng_ok:
        return True

    print(f"  [제외] {place_name} ({lat:.4f}, {lng:.4f}) → {destination} 범위 밖")
    return False
