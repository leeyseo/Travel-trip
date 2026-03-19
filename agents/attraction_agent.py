"""
관광지 브라우징 에이전트 (2단계 장소 추출 버전)

흐름:
  1단계: 검색 → 블로그/페이지 본문 수집
  2단계: LLM으로 본문에서 실제 장소명만 추출
  3단계: 각 장소명으로 개별 검색 + 피처 추출 + 점수 계산
"""

import json
import time
import hashlib
from typing import Iterator

import anthropic

from models.schemas import (
    TripInput, PlaceNode, PlaceCategory,
    AttractionFeatures,
)
from utils.web_collector import build_queries, search_places, collect_raw_text
from utils.feature_extractor import extract_features
from utils.scorer import score_attraction


client = anthropic.Anthropic()


def _make_place_id(name: str, destination: str) -> str:
    raw = f"{destination}_{name}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


# ──────────────────────────────────────────────
# 핵심 추가: 본문 텍스트 → 실제 장소명 리스트 추출
# ──────────────────────────────────────────────
def extract_place_names(
    raw_text: str,
    destination: str,
    max_places: int = 15,
) -> list[str]:
    """
    블로그/여행 페이지 본문에서 실제 방문 가능한 장소명만 추출.
    블로그 제목, 작성자명, 광고 등은 제외.
    """
    prompt = f"""
다음은 {destination} 여행 관련 웹페이지 본문입니다.

[본문]
{raw_text[:3000]}

위 본문에서 실제로 방문할 수 있는 구체적인 장소명만 추출하세요.
- 포함: 관광지, 공원, 사원, 궁궐, 전망대, 박물관, 테마파크, 거리/골목 이름
- 제외: 블로그 제목, 작성자명, "서울 여행", "추천 코스" 같은 일반 표현, 음식점, 숙소

반드시 JSON 배열로만 응답하세요 (다른 텍스트 없이):
["장소명1", "장소명2", ...]

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
        names = json.loads(raw)
        # 너무 긴 문자열(블로그 제목 등) 필터링
        names = [n for n in names if isinstance(n, str) and 1 < len(n) < 20]
        return names
    except Exception as e:
        print(f"  [WARN] 장소명 추출 실패: {e}")
        return []


class AttractionBrowsingAgent:
    """
    관광지 브라우징 에이전트.

    사용법:
        agent = AttractionBrowsingAgent(trip_input)
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
            print(f"[AttractionAgent] {msg}")

    # ── Step 1: 검색 → 본문 수집 → 장소명 추출 ──
    def _collect_candidates(self) -> list[str]:
        self._log(f"Step 1: 장소명 수집 — {self.trip.destination}")
        dest = self.trip.destination

        queries = build_queries(
            dest, "attraction",
            self.trip.age_group,
            self.trip.preferences.__dict__,
        )

        all_place_names: list[str] = []

        for query in queries[:3]:
            self._log(f"  검색: {query}")
            results = search_places(query, num=5)

            for r in results[:4]:
                # snippet + title 합쳐서 장소명 추출
                combined_text = f"{r.title}\n{r.snippet}"
                names = extract_place_names(combined_text, dest)
                if names:
                    self._log(f"    → {names}")
                    all_place_names.extend(names)
                time.sleep(self.delay)

        # 중복 제거 + 최대 개수 제한
        seen = set()
        unique_names = []
        for name in all_place_names:
            if name not in seen:
                seen.add(name)
                unique_names.append(name)

        self._log(f"  → 총 {len(unique_names)}개 장소명 추출: {unique_names[:8]}...")
        return unique_names[:self.max_places]

    # ── Step 2–4: 단일 장소 처리 ──
    def _process_place(self, place_name: str) -> PlaceNode | None:
        dest = self.trip.destination
        self._log(f"  처리 중: {place_name}")

        # 장소명으로 개별 검색 + 텍스트 수집
        raw_text, sources = collect_raw_text(dest, place_name, "attraction")
        if not raw_text:
            self._log(f"  [SKIP] 텍스트 없음: {place_name}")
            return None

        # LLM 피처 추출
        feat_dict = extract_features(place_name, raw_text, "attraction", destination=dest)
        if not feat_dict:
            return None

        # 서울 기본 좌표 (피처 추출 실패 시 fallback)
        default_coords = {
            "서울": (37.5665, 126.9780),
            "도쿄": (35.6762, 139.6503),
            "오사카": (34.6937, 135.5023),
            "교토": (35.0116, 135.7681),
        }
        default_lat, default_lng = default_coords.get(dest, (37.5665, 126.9780))

        # 좌표 검증 — _validate_coords가 None 반환하면 범위 밖 장소
        from utils.feature_extractor import _validate_coords
        raw_lat = feat_dict.get("lat", 0.0)
        raw_lng = feat_dict.get("lng", 0.0)
        validated = _validate_coords(raw_lat, raw_lng, dest, place_name)
        if validated is None:
            return None
        final_lat, final_lng = validated

        try:
            features = AttractionFeatures(
                overall_rating=feat_dict.get("overall_rating", 3.5),
                review_count=feat_dict.get("review_count", 100),
                transit_access=feat_dict.get("transit_access", 3.0),
                korean_popular=feat_dict.get("korean_popular", 3.0),
                price_level=feat_dict.get("price_level", 1),
                lat=final_lat,
                lng=final_lng,
                entry_fee_krw=feat_dict.get("entry_fee_krw", 0),
                avg_duration_hr=feat_dict.get("avg_duration_hr", 2.0),
                photo_worthiness=feat_dict.get("photo_worthiness", 3.0),
                activity_level=feat_dict.get("activity_level", 2.0),
                culture_depth=feat_dict.get("culture_depth", 3.0),
                nature_score=feat_dict.get("nature_score", 2.0),
                crowd_level=feat_dict.get("crowd_level", 3.0),
                nightlife_score=feat_dict.get("nightlife_score", 1.0),
                indoor=feat_dict.get("indoor", False),
                korean_signage=feat_dict.get("korean_signage", False),
                age_suitability=feat_dict.get("age_suitability", "all"),
                category=feat_dict.get("category", "문화유산"),
            )
        except Exception as e:
            self._log(f"  [WARN] Feature build failed: {e}")
            return None

        # 좌표 기반 소속 여행지 검증
        from utils.feature_extractor import verify_place_belongs, _get_transit_score
        if not verify_place_belongs(place_name, features.lat, features.lng, dest):
            return None

        # 카카오로 교통 접근성 실측값으로 덮어씌우기
        transit_score, transit_desc = _get_transit_score(features.lat, features.lng)
        features.transit_access = transit_score
        self._log(f"  [교통] {transit_desc} → {transit_score}/5")

        score, breakdown = score_attraction(features, self.trip.preferences)

        node = PlaceNode(
            place_id=_make_place_id(place_name, dest),
            name=place_name,
            address=f"{dest}, {place_name}",
            category=PlaceCategory.ATTRACTION,
            features=features,
            node_score=score,
            score_breakdown=breakdown,
            sources=sources[:3],
        )
        self._log(f"  ✓ {place_name} → score={score:.3f}")
        return node

    # ── 전체 실행 ──
    def run(self) -> list[PlaceNode]:
        candidates = self._collect_candidates()

        if not candidates:
            self._log("장소명을 찾지 못했습니다.")
            return []

        self._log(f"\nStep 2: 장소별 피처 추출 시작 ({len(candidates)}개)")
        nodes = []
        for name in candidates:
            node = self._process_place(name)
            if node:
                nodes.append(node)
            time.sleep(self.delay)

        nodes.sort(key=lambda n: n.node_score, reverse=True)
        # 저품질 노드 제거 (score < 0.3 또는 피처가 전부 0인 경우)
        nodes = [n for n in nodes if n.node_score >= 0.3]
        self._log(f"\n완료: {len(nodes)}개 노드 생성")
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
