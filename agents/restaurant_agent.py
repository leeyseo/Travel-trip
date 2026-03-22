"""
맛집 브라우징 에이전트 v3

- 서울 13개 지역별 한국어 쿼리
- 영어 쿼리 (TripAdvisor/Google Maps 결과 수집)
- 카페/브런치/간식/미슐랭 전용 쿼리
- 목표: 서울 전역 300개 맛집/카페 수집
"""
import json, time, hashlib
from typing import Iterator
import anthropic

from models.schemas import TripInput, PlaceNode, PlaceCategory, RestaurantFeatures
from utils.web_collector import search_places, search_places_en, collect_raw_text, collect_candidate_texts
from utils.feature_extractor import extract_features
from utils.scorer import score_restaurant

client = anthropic.Anthropic()

def _make_place_id(name: str, destination: str) -> str:
    return hashlib.md5(f"{destination}_{name}".encode()).hexdigest()[:12]


# ──────────────────────────────────────────────
# 쿼리 목록 (한국어 + 영어)
# ──────────────────────────────────────────────
def _build_queries(dest: str, age_str: str) -> list[dict]:
    ko = [
        {"q": f"{dest} 유명 맛집 대표 음식", "lang": "ko"},
        {"q": f"{dest} 꼭 먹어야 할 음식 맛집", "lang": "ko"},
        {"q": f"{dest} 현지인 맛집 로컬 추천", "lang": "ko"},
        {"q": f"{dest} 맛집 {age_str} 추천", "lang": "ko"},
        {"q": f"{dest} 카페 디저트 브런치 유명한 곳", "lang": "ko"},
        {"q": f"{dest} 미슐랭 맛집 가이드", "lang": "ko"},
    ]
    en = [
        {"q": f"best food {dest} must eat", "lang": "en"},
        {"q": f"top restaurants {dest} tripadvisor locals", "lang": "en"},
        {"q": f"michelin restaurants {dest}", "lang": "en"},
        {"q": f"{dest} food guide what to eat", "lang": "en"},
    ]
    return ko + en


# ──────────────────────────────────────────────
# 식당명 추출 LLM
# ──────────────────────────────────────────────
def extract_restaurant_names(raw_text: str, destination: str, max_places: int = 20) -> list[str]:
    prompt = f"""
다음은 {destination} 맛집/카페 관련 웹페이지 본문입니다. 한국어 또는 영어로 작성되어 있을 수 있습니다.

[본문]
{raw_text[:3000]}

실제로 방문할 수 있는 식당/카페 이름만 JSON 배열로 추출하세요.
- 포함: 한국어 식당명, 영어 식당명, 카페명, 술집명, 베이커리명, 포장마차명
- 제외: 음식 종류("라멘", "pasta"), 일반 표현("맛집 추천", "best food"), 관광지·숙소
- 영어 이름도 그대로 추출 (예: "Cafe Onion", "Noodle House Seoul")
- 한국어+영어 혼용 이름도 추출 (예: "카페 오니언", "Moments Coffee")

JSON 배열만 응답 (다른 텍스트 없이):
["이름1", "이름2", ...]

추출 불가면 []. 최대 {max_places}개.
"""
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        raw = raw.replace("```json","").replace("```","").strip()
        s, e = raw.find("["), raw.rfind("]")
        if s != -1 and e != -1:
            raw = raw[s:e+1]
        names = json.loads(raw)
        return [n for n in names if isinstance(n, str) and 1 < len(n) < 50]
    except Exception as ex:
        print(f"  [WARN] 이름 추출 실패: {ex}")
        return []


# ──────────────────────────────────────────────
# RestaurantBrowsingAgent
# ──────────────────────────────────────────────
class RestaurantBrowsingAgent:
    def __init__(self, trip: TripInput, max_places: int = 300,
                 delay: float = 0.3, verbose: bool = True):
        self.trip = trip
        self.max_places = max_places
        self.delay = delay
        self.verbose = verbose

    def _log(self, msg: str):
        if self.verbose:
            print(f"[RestaurantAgent] {msg}", flush=True)

    def _collect_candidates(self) -> list[str]:
        dest = self.trip.destination
        age = self.trip.age_group
        age_str = {"20s":"20대","30s":"30대","40s":"40대",
                   "50s":"50대","60s":"60대","common":""}.get(age,"")

        queries = _build_queries(dest, age_str)
        self._log(f"쿼리 {len(queries)}개 (한국어 {sum(1 for q in queries if q['lang']=='ko')}개 + 영어 {sum(1 for q in queries if q['lang']=='en')}개)")

        seen: set[str] = set()
        all_names: list[str] = []
        exclude = {"추천","베스트","top","best","대표","맛집","리스트",
                   "코스","거리","골목","음식","식당","레스토랑","restaurant"}

        for item in queries:
            self._log(f"  [{item['lang'].upper()}] {item['q']}")

            # 실제 페이지 크롤링으로 본문 수집 (snippet 대신)
            gl = "us" if item["lang"] == "en" else "kr"
            hl = "en" if item["lang"] == "en" else "ko"
            texts = collect_candidate_texts(
                item["q"], num_results=5, gl=gl, hl=hl, delay=self.delay)

            for text in texts:
                names = extract_restaurant_names(text, dest)
                for name in names:
                    if name not in seen and not any(p in name for p in exclude):
                        seen.add(name)
                        all_names.append(name)
                        self._log(f"    + {name}")

            if len(all_names) >= self.max_places * 2:
                self._log(f"  후보 {len(all_names)}개 확보, 수집 중단")
                break

        self._log(f"  → 총 {len(all_names)}개 후보")
        return all_names

    def _process_place(self, place_name: str) -> PlaceNode | None:
        dest = self.trip.destination

        raw_text, sources = collect_raw_text(
            dest, place_name, "restaurant",
            extra_queries=[
                f"{dest} {place_name} 메뉴 가격 후기",
                f"{place_name} {dest} review menu price",
            ]
        )
        if not raw_text:
            return None

        feat_dict = extract_features(place_name, raw_text, "restaurant", destination=dest)
        if not feat_dict:
            return None

        from utils.feature_extractor import _geocode, DEFAULT_COORDS, COORD_BOUNDS, verify_place_belongs

        simplified = place_name
        for suffix in ["본점","1호점","2호점","직영점","명동점","강남점",
                       "홍대점","이태원점","성수점","신촌점"]:
            simplified = simplified.replace(suffix, "").strip()

        geo = _geocode(place_name, dest)
        if not geo and simplified != place_name:
            geo = _geocode(simplified, dest)

        bounds = COORD_BOUNDS.get(dest)
        if geo:
            geo_lat, geo_lng = geo
            if verify_place_belongs(place_name, geo_lat, geo_lng, dest):
                lat, lng = geo_lat, geo_lng
            else:
                return None
        else:
            lat, lng = DEFAULT_COORDS.get(dest, (37.5665, 126.9780))

        valid_meal_types = {"아침","점심","저녁","카페","간식","야식","전체"}
        meal_type = feat_dict.get("meal_type", "전체")
        if meal_type not in valid_meal_types:
            meal_type = "전체"

        try:
            features = RestaurantFeatures(
                overall_rating=feat_dict.get("overall_rating", 3.5),
                review_count=feat_dict.get("review_count", 50),
                transit_access=feat_dict.get("transit_access", 3.0),
                korean_popular=feat_dict.get("korean_popular", 3.0),
                price_level=feat_dict.get("price_level", 2),
                lat=lat, lng=lng,
                cuisine_type=feat_dict.get("cuisine_type", "현지식"),
                meal_type=meal_type,
                avg_price_per_person=feat_dict.get("avg_price_per_person", 15000),
                taste_score=feat_dict.get("taste_score", 3.5),
                food_diversity=feat_dict.get("food_diversity", 3.0),
                local_authenticity=feat_dict.get("local_authenticity", 3.0),
                michelin_tier=feat_dict.get("michelin_tier", "없음"),
                ambiance_score=feat_dict.get("ambiance_score", 3.0),
                cleanliness_score=feat_dict.get("cleanliness_score", 3.0),
                wait_time_min=feat_dict.get("wait_time_min", 15),
                reservation_required=feat_dict.get("reservation_required", False),
                korean_menu=feat_dict.get("korean_menu", False),
                dietary_options=feat_dict.get("dietary_options", "없음"),
            )
        except Exception as e:
            self._log(f"  [WARN] {place_name}: {e}")
            return None

        from utils.feature_extractor import _get_transit_score
        features.transit_access = _get_transit_score(lat, lng)[0]

        score, breakdown = score_restaurant(features, self.trip.preferences)
        self._log(f"  ✓ {place_name} → {score:.3f} ({features.cuisine_type}/{meal_type})")

        return PlaceNode(
            place_id=_make_place_id(place_name, dest),
            name=place_name,
            address=f"{dest}, {place_name}",
            category=PlaceCategory.RESTAURANT,
            features=features,
            node_score=score,
            score_breakdown=breakdown,
            sources=sources[:3],
        )

    def run(self) -> list[PlaceNode]:
        candidates = self._collect_candidates()
        if not candidates:
            return []

        self._log(f"\nStep 2: 피처 추출 (후보 {len(candidates)}개 → 목표 {self.max_places}개)")
        nodes: list[PlaceNode] = []
        seen_ids: set[str] = set()

        for name in candidates:
            if len(nodes) >= self.max_places:
                self._log(f"  목표 {self.max_places}개 달성, 중단")
                break
            node = self._process_place(name)
            if node and node.place_id not in seen_ids:
                seen_ids.add(node.place_id)
                nodes.append(node)
            time.sleep(self.delay)

        nodes.sort(key=lambda n: n.node_score, reverse=True)
        nodes = [n for n in nodes if n.node_score >= 0.3]

        from collections import Counter
        meal_dist = Counter(n.features.meal_type for n in nodes)
        self._log(f"\n완료: {len(nodes)}개 | meal_type: {dict(meal_dist)}")
        return nodes
