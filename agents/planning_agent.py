"""
플래닝 에이전트 — 관광지/맛집/숙소 그래프 → 최적 여행 일정 생성

핵심 로직:
  1. 관광지 노드를 지리적 클러스터로 묶기
  2. 클러스터 순서로 날짜 배정
  3. 각 날짜 클러스터 중심에서 가장 가까운 숙소 배정
  4. 클러스터별 맛집 배정 (점심/저녁)
  5. Claude로 일정 설명 생성
"""

import json
import math
import os
from dataclasses import dataclass, field
from typing import Optional
import anthropic

client = anthropic.Anthropic()


# ──────────────────────────────────────────────
# 데이터 구조
# ──────────────────────────────────────────────
@dataclass
class DayPlan:
    day: int
    date: str
    hotel: dict
    morning: list[dict] = field(default_factory=list)    # 관광지
    lunch: Optional[dict] = None                          # 맛집
    afternoon: list[dict] = field(default_factory=list)  # 관광지
    dinner: Optional[dict] = None                         # 맛집
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "day": self.day,
            "date": self.date,
            "hotel": {
                "name": self.hotel["name"],
                "price_per_night": self.hotel["features"]["price_per_night"],
                "transit": self.hotel["features"]["transit_access"],
                "booking_url": self.hotel["features"].get("booking_url", ""),
                "lat": self.hotel["lat"],
                "lng": self.hotel["lng"],
            },
            "morning": [_slim(a) for a in self.morning],
            "lunch": _slim(self.lunch) if self.lunch else None,
            "afternoon": [_slim(a) for a in self.afternoon],
            "dinner": _slim(self.dinner) if self.dinner else None,
            "notes": self.notes,
        }


def _slim(node: dict) -> dict:
    """노드에서 필요한 정보만 추출"""
    if not node:
        return {}
    f = node.get("features", {})
    return {
        "name": node["name"],
        "score": node["node_score"],
        "lat": node["lat"],
        "lng": node["lng"],
        "category": f.get("category") or f.get("cuisine_type", ""),
        "duration_hr": f.get("avg_duration_hr") or 1.5,
        "price": f.get("entry_fee_krw") or f.get("avg_price_per_person") or 0,
    }


# ──────────────────────────────────────────────
# 유틸 함수
# ──────────────────────────────────────────────
def _haversine(lat1, lng1, lat2, lng2) -> float:
    R = 6371
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = math.sin(d_lat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lng/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def _centroid(nodes: list[dict]) -> tuple[float, float]:
    lats = [n["lat"] for n in nodes]
    lngs = [n["lng"] for n in nodes]
    return sum(lats)/len(lats), sum(lngs)/len(lngs)


def _cluster_by_day(
    attractions: list[dict],
    n_days: int,
    hotel_loc: dict = None,
    walking_aversion: int = 3,
) -> list[list[dict]]:
    """
    관광지를 n_days개의 클러스터로 나누기.
    - 숙소 위치가 있으면 숙소에서 가까운 노드 우선 배정
    - 클러스터 내 최대 거리 8km 제한
    - 산/자연은 하루 1개 제한
    """
    if not attractions:
        return [[] for _ in range(n_days)]

    sorted_nodes = sorted(attractions, key=lambda x: x["node_score"], reverse=True)

    max_cluster_km = 6 + (5 - walking_aversion) * 3  # wa=4→9km, wa=3→12km, wa=1→18km

    # seed 선택: 서로 max_cluster_km*1.5 이상 떨어진 노드를 n_days개 선택
    # (max_cluster_km 간격은 너무 좁아서 같은 생활권이 여러 seed로 잡힘)
    seed_min_gap = max_cluster_km * 1.5
    seeds = [sorted_nodes[0]]
    for node in sorted_nodes[1:]:
        if len(seeds) >= n_days:
            break
        if all(_haversine(node["lat"], node["lng"], s["lat"], s["lng"]) >= seed_min_gap for s in seeds):
            seeds.append(node)

    # seed 부족 시 간격 완화하며 재시도
    if len(seeds) < n_days:
        for gap in [max_cluster_km, max_cluster_km * 0.5]:
            for node in sorted_nodes:
                if node in seeds:
                    continue
                if all(_haversine(node["lat"], node["lng"], s["lat"], s["lng"]) >= gap for s in seeds):
                    seeds.append(node)
                if len(seeds) >= n_days:
                    break
            if len(seeds) >= n_days:
                break

    clusters = [[s] for s in seeds[:n_days]]
    remaining = [n for n in sorted_nodes if n not in seeds[:n_days]]

    HEAVY_CATEGORIES = {"자연", "등산"}

    def _is_heavy(node: dict) -> bool:
        cat = node.get("features", {}).get("category", "")
        name = node.get("name", "")
        return cat in HEAVY_CATEGORIES or any(k in name for k in ["산", "등산", "트레킹"])

    for node in remaining:
        best_cluster = -1
        best_score = float("-inf")
        node_is_heavy = _is_heavy(node)

        for i, cluster in enumerate(clusters):
            if len(cluster) >= 4:
                continue
            c_lat, c_lng = _centroid(cluster)
            dist_to_center = _haversine(node["lat"], node["lng"], c_lat, c_lng)

            # centroid 기준 거리 체크 — 새 노드가 클러스터 중심에서 max_cluster_km 초과 시 제외
            # (기존 max_inner_dist 방식은 클러스터가 한쪽으로 쏠릴 경우 원거리 노드를 걸러내지 못함)
            if dist_to_center > max_cluster_km:
                continue

            heavy_in_cluster = sum(1 for m in cluster if _is_heavy(m))
            if node_is_heavy and heavy_in_cluster >= 1:
                continue

            # 거리 페널티: walking_aversion 높을수록 거리에 더 민감
            dist_sensitivity = 10 + walking_aversion * 2  # 3→16, 5→20, 1→12
            penalty = dist_to_center / dist_sensitivity

            # 숙소에서 클러스터까지 거리 페널티 (walking_aversion 높을수록 강화)
            if hotel_loc:
                hotel_to_cluster = _haversine(
                    hotel_loc["lat"], hotel_loc["lng"], c_lat, c_lng
                )
                hotel_sensitivity = 20 + walking_aversion * 5  # 3→35, 5→45, 1→25
                penalty += hotel_to_cluster / hotel_sensitivity

            score = node["node_score"] - penalty
            if score > best_score:
                best_score = score
                best_cluster = i

        if best_cluster == -1:
            # 모든 클러스터가 거리 초과 → centroid 기준 가장 가까운 클러스터에 배정
            best_cluster = min(
                range(len(clusters)),
                key=lambda i: _haversine(
                    node["lat"], node["lng"],
                    *_centroid(clusters[i])
                )
            )
        clusters[best_cluster].append(node)

    # ── 클러스터 최소 관광지 보장 ──────────────────────────────
    # 1개짜리 클러스터가 있으면 다른 클러스터(4개 초과분)에서 가장 가까운 노드를 이동
    # walking_aversion >= 4면 최소 2개, 그 이상은 최소 2개로 통일
    for _ in range(3):  # 최대 3회 반복 (다중 thin 클러스터 대응)
        thin_clusters = [i for i, c in enumerate(clusters) if len(c) < 2]
        if not thin_clusters:
            break
        for ci in thin_clusters:
            c_lat, c_lng = _centroid(clusters[ci])
            best_node = None
            best_score = float("-inf")
            best_src = -1

            # 다른 클러스터에서 2개 이상인 경우에만 노드를 빌려옴
            for src_i, src_cluster in enumerate(clusters):
                if src_i == ci or len(src_cluster) < 2:
                    continue
                for node in src_cluster:
                    dist = _haversine(node["lat"], node["lng"], c_lat, c_lng)
                    # 이동 후 src 클러스터 centroid 변화 최소화를 위해 score에서 dist 페널티
                    score = node["node_score"] - dist / (max_cluster_km * 3)
                    if score > best_score:
                        best_score = score
                        best_node = node
                        best_src = src_i

            if best_node and best_src >= 0:
                clusters[best_src].remove(best_node)
                clusters[ci].append(best_node)

    return [c[:4] for c in clusters]


def _optimize_route(nodes: list[dict], hotel: dict = None) -> list[dict]:
    """
    관광지 순서 최적화 (Nearest Neighbor)
    숙소 위치가 있으면 숙소에서 가장 가까운 노드부터 시작
    """
    if len(nodes) <= 1:
        return nodes

    # 시작점: 숙소에서 가장 가까운 관광지
    if hotel and hotel.get("lat") and hotel.get("lng"):
        start = min(
            nodes,
            key=lambda n: _haversine(hotel["lat"], hotel["lng"], n["lat"], n["lng"])
        )
    else:
        start = nodes[0]  # 점수 최고 노드

    remaining = [n for n in nodes if n is not start]
    route = [start]

    while remaining:
        last = route[-1]
        nearest = min(
            remaining,
            key=lambda n: _haversine(last["lat"], last["lng"], n["lat"], n["lng"])
        )
        route.append(nearest)
        remaining.remove(nearest)

    return route


def _best_restaurant_near(
    prev_place: dict,
    next_place: dict,
    restaurants: list[dict],
    used: set,
    used_cuisines: list[str],
) -> dict | None:
    """
    이전 장소 → 다음 장소 사이 동선에서 가장 자연스러운 맛집 선택.
    이전 장소와 다음 장소의 중간 지점 기준으로 선택.
    """
    if not restaurants:
        return None

    # 중간 지점 계산
    if prev_place and next_place:
        mid_lat = (prev_place["lat"] + next_place["lat"]) / 2
        mid_lng = (prev_place["lng"] + next_place["lng"]) / 2
    elif prev_place:
        mid_lat, mid_lng = prev_place["lat"], prev_place["lng"]
    elif next_place:
        mid_lat, mid_lng = next_place["lat"], next_place["lng"]
    else:
        mid_lat, mid_lng = 37.5665, 126.978

    candidates = [r for r in restaurants if r["name"] not in used]
    if not candidates:
        candidates = list(restaurants)

    def score(r):
        dist = _haversine(mid_lat, mid_lng, r["lat"], r["lng"])
        dist_penalty = dist / 50
        cuisine = r.get("features", {}).get("cuisine_type", "")
        cuisine_penalty = 0.15 if cuisine and cuisine in (used_cuisines[-2:] if used_cuisines else []) else 0
        return r["node_score"] - dist_penalty - cuisine_penalty

    return max(candidates, key=score)


def _best_hotel_for_cluster(
    cluster: list[dict],
    hotels: list[dict],
    used_hotels: list[str],
    walking_aversion: int = 3,
) -> dict:
    """클러스터 중심에서 가장 가까운 호텔 선택"""
    if not cluster or not hotels:
        return hotels[0] if hotels else {}

    c_lat, c_lng = _centroid(cluster)  # 한 번만 계산, 재할당 없음

    # 거리 페널티: walking_aversion 높을수록 거리에 민감
    # wa=1→200, wa=3→100, wa=5→40 (낮을수록 거리 둔감)
    dist_sensitivity = 200 - walking_aversion * 30

    def hotel_score(h):
        dist = _haversine(c_lat, c_lng, h["lat"], h["lng"])
        dist_penalty = dist / dist_sensitivity
        return h["node_score"] - dist_penalty

    # 연박 유지: 이전에 쓴 호텔이 클러스터와 충분히 가까우면 재사용
    # 연박 허용 거리도 walking_aversion에 따라 조정 (wa=5→6km, wa=1→15km)
    max_reuse_km = 6 + (5 - walking_aversion) * 2.25
    for h in sorted(hotels, key=hotel_score, reverse=True):
        if h["name"] in used_hotels:
            dist_to_cluster = _haversine(c_lat, c_lng, h["lat"], h["lng"])
            if dist_to_cluster <= max_reuse_km:
                return h

    # 새 호텔 선택
    return max(hotels, key=hotel_score)


def _best_restaurant(
    cluster: list[dict],
    restaurants: list[dict],
    used: set,
    meal_type: str,
    used_cuisines: list[str] = None,
) -> Optional[dict]:
    """
    클러스터 중심에서 가까운 고점수 맛집 선택.
    - 이미 쓴 맛집 제외
    - 연속 같은 cuisine_type 피하기
    """
    if not restaurants:
        return None

    c_lat, c_lng = _centroid(cluster) if cluster else (37.5665, 126.978)
    used_cuisines = used_cuisines or []

    candidates = [r for r in restaurants if r["name"] not in used]
    if not candidates:
        candidates = list(restaurants)

    def resto_score(r):
        dist = _haversine(c_lat, c_lng, r["lat"], r["lng"])
        dist_penalty = dist / 50
        cuisine = r.get("features", {}).get("cuisine_type", "")
        # 최근 2개 식사와 같은 cuisine이면 페널티
        cuisine_penalty = 0.15 if cuisine and cuisine in used_cuisines[-2:] else 0
        return r["node_score"] - dist_penalty - cuisine_penalty

    return max(candidates, key=resto_score)


# ──────────────────────────────────────────────
# 일정 설명 생성 (Claude)
# ──────────────────────────────────────────────
def _generate_day_notes(day_plan: DayPlan, trip_info: dict) -> str:
    """Claude로 하루 일정 설명 생성"""
    morning_names = [a["name"] for a in day_plan.morning]
    afternoon_names = [a["name"] for a in day_plan.afternoon]
    lunch_name = day_plan.lunch["name"] if day_plan.lunch else "없음"
    dinner_name = day_plan.dinner["name"] if day_plan.dinner else "없음"

    prompt = f"""
여행 일정 설명을 2~3문장으로 간결하게 써주세요.

여행지: {trip_info['destination']}
여행자: {trip_info['age_group']} {trip_info['traveler_count']}명

Day {day_plan.day} 일정:
- 숙소: {day_plan.hotel['name']}
- 오전: {', '.join(morning_names) or '자유시간'}
- 점심: {lunch_name}
- 오후: {', '.join(afternoon_names) or '자유시간'}
- 저녁: {dinner_name}

동선과 분위기를 자연스럽게 설명해주세요. 이모지 없이 한국어로.
"""
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception:
        return ""


# ──────────────────────────────────────────────
# 플래닝 에이전트 메인
# ──────────────────────────────────────────────
class PlanningAgent:
    def __init__(
        self,
        attractions_json: str,
        restaurants_json: str,
        hotels_json: str,
        verbose: bool = True,
    ):
        self.verbose = verbose

        with open(attractions_json, encoding="utf-8") as f:
            a_data = json.load(f)
        with open(restaurants_json, encoding="utf-8") as f:
            r_data = json.load(f)
        with open(hotels_json, encoding="utf-8") as f:
            h_data = json.load(f)

        self.trip = a_data.get("trip", {})
        self.attractions = a_data.get("attraction_nodes", [])
        self.restaurants = r_data.get("restaurant_nodes", [])
        self.hotels = h_data.get("hotel_nodes", [])

        self.duration = self.trip.get("duration_days", 4)
        self.checkin = self.trip.get("checkin", "")

    def _log(self, msg: str):
        if self.verbose:
            print(f"[PlanningAgent] {msg}", flush=True)

    def run(self) -> dict:
        self._log(f"일정 생성 시작 — {self.trip.get('destination')} {self.duration}박")

        # 1. 관광지 전처리 — 산/자연 최대 2개로 제한 (과부하 방지)
        def _is_heavy(node: dict) -> bool:
            cat = node.get("features", {}).get("category", "")
            name = node.get("name", "")
            return cat in {"자연", "등산"} or any(k in name for k in ["산", "등산", "트레킹"])

        heavy_nodes = [n for n in self.attractions if _is_heavy(n)]
        light_nodes = [n for n in self.attractions if not _is_heavy(n)]

        # 산/자연은 score 상위 1개만 유지 (4박 기준)
        max_heavy = max(1, self.duration // 4)
        heavy_nodes_sorted = sorted(heavy_nodes, key=lambda x: x["node_score"], reverse=True)
        filtered_attractions = light_nodes + heavy_nodes_sorted[:max_heavy]

        if len(heavy_nodes) > max_heavy:
            removed = [n["name"] for n in heavy_nodes_sorted[max_heavy:]]
            self._log(f"  산/자연 노드 {len(removed)}개 제외: {removed}")

        # 점수 순 정렬
        filtered_attractions = sorted(filtered_attractions, key=lambda x: x["node_score"], reverse=True)

        # 0. 대표 숙소 위치 파악 (클러스터링 시 참고용)
        best_hotel = max(self.hotels, key=lambda h: h["node_score"]) if self.hotels else None
        hotel_loc = {"lat": best_hotel["lat"], "lng": best_hotel["lng"]} if best_hotel else None

        # 1. 관광지 중복 제거 (place_id 기준)
        def _norm(s: str) -> str:
            return s.replace(" ", "").lower()

        seen_ids = set()
        deduped = []
        for n in filtered_attractions:
            # place_id 우선, 없으면 정규화된 이름으로 중복 체크
            pid = n.get("place_id") or _norm(n.get("name", ""))
            if pid not in seen_ids:
                seen_ids.add(pid)
                # 이름도 추가로 체크 (표기 다른 동일 장소)
                seen_ids.add(_norm(n.get("name", "")))
                deduped.append(n)
        filtered_attractions = deduped

        # walking_aversion 추출 (없으면 기본값 3)
        walking_aversion = self.trip.get("preferences", {}).get("walking_aversion", 3)
        if isinstance(walking_aversion, str):
            walking_aversion = 3

        # 관광지 클러스터링 — 숙소 위치 + walking_aversion 반영
        clusters = _cluster_by_day(
            filtered_attractions, self.duration, hotel_loc, walking_aversion
        )
        self._log(f"  클러스터: {[len(c) for c in clusters]}개씩 배분")

        # 2. 날짜별 일정 생성
        days = []
        used_restaurants: set = set()
        used_hotel_names: list = []
        used_cuisines: list = []   # 최근 식사 cuisine 추적 (다양성용)

        from datetime import datetime, timedelta
        base_date = datetime.strptime(self.checkin, "%Y-%m-%d") if self.checkin else datetime.now()

        for i, cluster in enumerate(clusters):
            day_num = i + 1
            date_str = (base_date + timedelta(days=i)).strftime("%Y-%m-%d")

            # 숙소 선택 — walking_aversion 반영
            hotel = _best_hotel_for_cluster(cluster, self.hotels, used_hotel_names, walking_aversion)
            used_hotel_names.append(hotel.get("name", ""))

            # 관광지 중복 제거
            def _normalize_name(name: str) -> str:
                return name.replace(" ", "").replace("　", "").lower()

            seen_names = set()
            deduped_cluster = []
            for n in cluster:
                norm = _normalize_name(n.get("name", ""))
                if norm not in seen_names:
                    seen_names.add(norm)
                    deduped_cluster.append(n)

            # 동선 최적화: 숙소 위치 기반 nearest neighbor 재정렬
            optimized = _optimize_route(deduped_cluster, hotel)

            morning = optimized[:2]
            afternoon = optimized[2:4] if len(optimized) > 2 else []

            if len(optimized) == 1:
                morning = optimized
                afternoon = []

            # 연쇄 동선 기반 맛집 선택
            # 점심: 오전 마지막 장소 → 오후 첫 장소 사이
            morning_last = morning[-1] if morning else None
            afternoon_first = afternoon[0] if afternoon else None
            lunch = _best_restaurant_near(
                morning_last, afternoon_first,
                self.restaurants, used_restaurants, used_cuisines
            )
            if lunch:
                used_restaurants.add(lunch["name"])
                cuisine = lunch.get("features", {}).get("cuisine_type", "")
                if cuisine:
                    used_cuisines.append(cuisine)

            # 저녁: 오후 마지막 장소 → 숙소 사이
            afternoon_last = afternoon[-1] if afternoon else morning_last
            hotel_place = {"lat": hotel.get("lat", 37.5665), "lng": hotel.get("lng", 126.978)}
            dinner = _best_restaurant_near(
                afternoon_last, hotel_place,
                self.restaurants, used_restaurants, used_cuisines
            )
            if dinner:
                used_restaurants.add(dinner["name"])
                cuisine = dinner.get("features", {}).get("cuisine_type", "")
                if cuisine:
                    used_cuisines.append(cuisine)

            # 오후 비어있으면 근처 카페/디저트 맛집으로 채우기
            if not afternoon:
                cafe_candidates = [
                    r for r in self.restaurants
                    if r["name"] not in used_restaurants
                    and any(k in r.get("features", {}).get("cuisine_type", "")
                            for k in ["카페", "디저트", "커피", "베이커리", "케이크"])
                ]
                if cafe_candidates and cluster:
                    c_lat, c_lng = _centroid(cluster)
                    best_cafe = min(cafe_candidates, key=lambda r: _haversine(c_lat, c_lng, r["lat"], r["lng"]))
                    # 카페를 오후 플레이스홀더로 사용
                    cafe_slim = _slim(best_cafe)
                    cafe_slim["type"] = "cafe"
                    afternoon = [cafe_slim]
                    used_restaurants.add(best_cafe["name"])

            day_plan = DayPlan(
                day=day_num,
                date=date_str,
                hotel=hotel,
                morning=morning,
                lunch=lunch,
                afternoon=afternoon,
                dinner=dinner,
            )

            # Claude로 일정 설명 생성
            self._log(f"  Day {day_num} 설명 생성 중...")
            day_plan.notes = _generate_day_notes(day_plan, self.trip)

            days.append(day_plan)
            self._log(f"  Day {day_num}: 오전={[a['name'] for a in morning]} 오후={[a['name'] for a in afternoon]} | 숙소: {hotel.get('name')}")

        # 3. 예산 요약
        total_hotel = sum(
            d.hotel["features"]["price_per_night"] * 1
            for d in days
        )
        # 연박 중복 제거
        unique_hotels = {}
        for d in days:
            name = d.hotel["name"]
            unique_hotels[name] = unique_hotels.get(name, 0) + 1
        hotel_cost = sum(
            h["features"]["price_per_night"] * nights
            for h in self.hotels
            for name, nights in unique_hotels.items()
            if h["name"] == name
        )

        result = {
            "trip": self.trip,
            "summary": {
                "total_days": self.duration,
                "destination": self.trip.get("destination"),
                "hotels_used": list(unique_hotels.keys()),
                "estimated_hotel_cost": hotel_cost,
                "top_attractions": [a["name"] for a in sorted(self.attractions, key=lambda x: x["node_score"], reverse=True)[:5]],
                "top_restaurants": [r["name"] for r in sorted(self.restaurants, key=lambda x: x["node_score"], reverse=True)[:5]],
            },
            "itinerary": [d.to_dict() for d in days],
        }

        self._log(f"\n완료: {self.duration}일 일정 생성")
        return result
