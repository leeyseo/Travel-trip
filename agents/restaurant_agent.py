"""
맛집 브라우징 에이전트 (2단계 장소 추출)

흐름:
  1단계: 검색 → 블로그/페이지 본문 수집 → LLM으로 실제 식당명 추출
  2단계: 각 식당명으로 개별 검색 + 피처 추출 + 점수 계산
"""

import json
import time
import hashlib
from typing import Iterator

import anthropic

from models.schemas import (
    TripInput, PlaceNode, PlaceCategory,
    RestaurantFeatures,
)
from utils.web_collector import build_queries, search_places, collect_raw_text
from utils.feature_extractor import extract_features, _validate_coords
from utils.scorer import score_restaurant


client = anthropic.Anthropic()


def _make_place_id(name: str, destination: str) -> str:
    return hashlib.md5(f"{destination}_{name}".encode()).hexdigest()[:12]


# ──────────────────────────────────────────────
# 맛집명 추출 (블로그 본문 → 실제 식당 이름)
# ──────────────────────────────────────────────
def extract_restaurant_names(
    raw_text: str,
    destination: str,
    max_places: int = 15,
) -> list[str]:
    """
    블로그/리뷰 본문에서 실제 식당명만 추출.
    메뉴명, 음식 종류, 블로그 제목 등은 제외.
    """
    prompt = f"""
다음은 {destination} 맛집 관련 웹페이지 본문입니다.

[본문]
{raw_text[:3000]}

위 본문에서 실제로 방문할 수 있는 구체적인 식당/카페 이름만 추출하세요.
- 포함: 식당명, 카페명, 술집명, 포장마차명
- 제외: 음식 종류("라멘", "초밥"), 블로그 제목, "맛집 추천" 같은 일반 표현, 관광지·숙소 이름

반드시 JSON 배열로만 응답하세요 (다른 텍스트 없이):
["식당명1", "식당명2", ...]

추출할 수 없으면 빈 배열 []로 응답.
최대 {max_places}개까지만.
"""
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()

        # JSON 배열만 안전하게 추출
        start = raw.find("[")
        end = raw.rfind("]")
        if start != -1 and end != -1:
            raw = raw[start:end+1]

        names = json.loads(raw)
        # 너무 길거나 짧은 건 필터링
        names = [n for n in names if isinstance(n, str) and 1 < len(n) < 25]
        return names
    except Exception as e:
        print(f"  [WARN] 맛집명 추출 실패: {e}")
        return []


class RestaurantBrowsingAgent:
    """
    맛집 브라우징 에이전트.

    사용법:
        agent = RestaurantBrowsingAgent(trip_input)
        nodes = agent.run()
    """

    def __init__(
        self,
        trip: TripInput,
        max_places: int = 10,
        delay: float = 0.3,
        verbose: bool = True,
    ):
        self.trip = trip
        self.max_places = max_places
        self.delay = delay
        self.verbose = verbose

    def _log(self, msg: str):
        if self.verbose:
            print(f"[RestaurantAgent] {msg}", flush=True)

    # ── Step 1: 검색 → 식당명 추출 ──
    def _collect_candidates(self) -> list[str]:
        self._log(f"Step 1: 맛집 수집 — {self.trip.destination}")
        dest = self.trip.destination

        # 맛집 특화 쿼리 직접 구성
        prefs = self.trip.preferences.__dict__
        age = self.trip.age_group
        age_str = {"20s":"20대","30s":"30대","40s":"40대",
                   "family":"가족","senior":"중장년"}.get(age, "")

        queries = [
            f"{dest} 맛집 현지 로컬 {age_str} 추천 2024",
            f"{dest} 유명 맛집 한국인 후기 블로그",
            f"{dest} 꼭 먹어야 할 음식 맛집 베스트",
        ]

        all_names: list[str] = []

        for query in queries:
            self._log(f"  검색: {query}")
            results = search_places(query, num=5)

            for r in results[:4]:
                combined = f"{r.title}\n{r.snippet}"
                names = extract_restaurant_names(combined, dest)
                if names:
                    self._log(f"    → {names}")
                    all_names.extend(names)
                time.sleep(self.delay)

        # 중복 제거 + 품질 필터
        exclude_patterns = ["추천", "베스트", "top", "best", "대표", "맛집", "리스트", "코스", "거리", "골목"]
        seen = set()
        unique = []
        for name in all_names:
            if name in seen:
                continue
            if len(name) < 2 or len(name) > 20:
                continue
            if any(p in name.lower() for p in exclude_patterns):
                continue
            seen.add(name)
            unique.append(name)

        self._log(f"  → 총 {len(unique)}개 식당명: {unique[:6]}...")
        return unique[:self.max_places]

    # ── Step 2-4: 단일 식당 처리 ──
    def _process_place(self, place_name: str) -> PlaceNode | None:
        dest = self.trip.destination
        self._log(f"  처리 중: {place_name}")

        # 맛집 특화 검색 쿼리
        raw_text, sources = collect_raw_text(
            dest, place_name, "restaurant",
            extra_queries=[f"{dest} {place_name} 메뉴 가격 후기"]
        )
        if not raw_text:
            self._log(f"  [SKIP] 텍스트 없음: {place_name}")
            return None

        # 피처 추출
        feat_dict = extract_features(
            place_name, raw_text, "restaurant", destination=dest
        )
        if not feat_dict:
            return None

        # 좌표 처리 + 소속 여행지 검증
        from utils.feature_extractor import _geocode, DEFAULT_COORDS, COORD_BOUNDS, verify_place_belongs

        # "본점" 등 suffix 제거한 단순화 이름으로도 geocoding 시도
        simplified = place_name
        for suffix in ["본점", "1호점", "2호점", "직영점", "명동점", "강남점", "홍대점", "대학로본점", "신촌점"]:
            simplified = simplified.replace(suffix, "").strip()

        geo = _geocode(place_name, dest)
        if not geo and simplified != place_name:
            self._log(f"  [Geocode 재시도] '{simplified}'")
            geo = _geocode(simplified, dest)

        bounds = COORD_BOUNDS.get(dest)

        if geo:
            geo_lat, geo_lng = geo
            if verify_place_belongs(place_name, geo_lat, geo_lng, dest):
                lat, lng = geo_lat, geo_lng
            else:
                # geocoding 결과가 범위 밖 → 엉뚱한 장소
                return None
        elif bounds:
            # geocoding 실패 + bounds 있는 여행지 → 좌표 불명확, 제외하지 않고 기본 좌표 사용
            # (실제로 서울에 있는 유명 맛집일 가능성이 높음)
            lat, lng = DEFAULT_COORDS.get(dest, (37.5665, 126.9780))
            self._log(f"  [좌표 fallback] {place_name} → geocoding 실패, 기본 좌표 사용")
        else:
            lat, lng = DEFAULT_COORDS.get(dest, (37.5665, 126.9780))

        try:
            features = RestaurantFeatures(
                overall_rating=feat_dict.get("overall_rating", 3.5),
                review_count=feat_dict.get("review_count", 50),
                transit_access=feat_dict.get("transit_access", 3.0),
                korean_popular=feat_dict.get("korean_popular", 3.0),
                price_level=feat_dict.get("price_level", 2),
                lat=lat,
                lng=lng,
                cuisine_type=feat_dict.get("cuisine_type", "현지식"),
                meal_type=feat_dict.get("meal_type", "전체"),
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
            self._log(f"  [WARN] Feature build failed: {e}")
            return None

        # 카카오로 교통 접근성 실측값으로 덮어씌우기
        from utils.feature_extractor import _get_transit_score
        transit_score, transit_desc = _get_transit_score(lat, lng)
        features.transit_access = transit_score
        self._log(f"  [교통] {transit_desc} → {transit_score}/5")

        score, breakdown = score_restaurant(features, self.trip.preferences)

        node = PlaceNode(
            place_id=_make_place_id(place_name, dest),
            name=place_name,
            address=f"{dest}, {place_name}",
            category=PlaceCategory.RESTAURANT,
            features=features,
            node_score=score,
            score_breakdown=breakdown,
            sources=sources[:3],
        )
        self._log(f"  ✓ {place_name} → score={score:.3f}  ({features.cuisine_type})")
        return node

    # ── 전체 실행 ──
    def run(self) -> list[PlaceNode]:
        candidates = self._collect_candidates()

        if not candidates:
            self._log("식당명을 찾지 못했습니다.")
            return []

        self._log(f"\nStep 2: 식당별 피처 추출 ({len(candidates)}개)")
        nodes = []
        for name in candidates:
            node = self._process_place(name)
            if node:
                nodes.append(node)
            time.sleep(self.delay)

        nodes.sort(key=lambda n: n.node_score, reverse=True)
        # 저품질 노드 제거 (score < 0.3 또는 피처가 전부 0인 경우)
        nodes = [n for n in nodes if n.node_score >= 0.3]
        self._log(f"\n완료: {len(nodes)}개 맛집 노드 생성")
        return nodes

    # ── 스트리밍 실행 ──
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
        yield "done", {
            "total_nodes": len(nodes),
            "top3": [n.to_dict() for n in nodes[:3]],
        }
