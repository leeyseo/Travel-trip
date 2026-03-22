"""
플래닝 에이전트 v6

v5 → v6 변경사항:
- LLM 호출 완전 제거 (순수 룰 기반)
- 클러스터별 랜드마크 필수 포함 (CLUSTER_LANDMARKS)
- 동선 기반 맛집 선택 (prev→식당→next detour 최소화)
- 시간 기반 타임라인 (관광지 소요시간 반영, 카페 14:30~17:30 자동 삽입)
"""

import json
import math
from dataclasses import dataclass, field
from typing import Optional
from collections import defaultdict


# ──────────────────────────────────────────────
# 클러스터 정의 (14개)
# ──────────────────────────────────────────────
CLUSTER_BOUNDS = {
    "홍대/마포":     {"lat": (37.53, 37.58), "lng": (126.88, 126.95)},
    "신촌/연남":     {"lat": (37.54, 37.61), "lng": (126.92, 126.96)},
    "종로/광화문":   {"lat": (37.55, 37.60), "lng": (126.95, 127.00)},
    "강북/북촌":     {"lat": (37.57, 37.66), "lng": (126.97, 127.02)},
    "용산/서울역":   {"lat": (37.51, 37.57), "lng": (126.95, 126.99)},
    "명동/중구":     {"lat": (37.54, 37.58), "lng": (126.97, 127.01)},
    "이태원/한남":   {"lat": (37.52, 37.56), "lng": (126.97, 127.02)},
    "성수/왕십리":   {"lat": (37.53, 37.58), "lng": (127.02, 127.08)},
    "동대문/회기":   {"lat": (37.57, 37.63), "lng": (127.02, 127.08)},
    "강남/서초":     {"lat": (37.44, 37.54), "lng": (126.99, 127.07)},
    "잠실/송파":     {"lat": (37.48, 37.53), "lng": (127.07, 127.13)},
    "여의도/영등포": {"lat": (37.47, 37.55), "lng": (126.88, 126.95)},
    "강서/마곡":     {"lat": (37.48, 37.60), "lng": (126.80, 126.88)},
    "강동/천호":     {"lat": (37.53, 37.57), "lng": (127.09, 127.18)},
}

CLUSTER_CENTERS = {
    "홍대/마포":     (37.555, 126.922),
    "신촌/연남":     (37.565, 126.935),
    "종로/광화문":   (37.575, 126.978),
    "강북/북촌":     (37.590, 126.985),
    "용산/서울역":   (37.540, 126.970),
    "명동/중구":     (37.560, 126.983),
    "이태원/한남":   (37.540, 126.993),
    "성수/왕십리":   (37.550, 127.050),
    "동대문/회기":   (37.595, 127.050),
    "강남/서초":     (37.503, 127.030),
    "잠실/송파":     (37.511, 127.100),
    "여의도/영등포": (37.523, 126.918),
    "강서/마곡":     (37.554, 126.845),
    "강동/천호":     (37.545, 127.145),
}

CLUSTER_CONCEPTS = {
    "홍대/마포":     "힙한 거리·클럽·인디 문화·쇼핑",
    "신촌/연남":     "감성 카페·연트럴파크·브런치",
    "종로/광화문":   "역사·궁궐·전통시장·한옥",
    "강북/북촌":     "북촌한옥·삼청동·낙산공원",
    "용산/서울역":   "전쟁기념관·서울로7017·남대문",
    "명동/중구":     "쇼핑·화장품·길거리음식·남산",
    "이태원/한남":   "다국적 음식·바·이색 문화",
    "성수/왕십리":   "힙한 카페·공방·뚝섬한강",
    "동대문/회기":   "동대문시장·경희대·경춘선숲길",
    "강남/서초":     "코엑스·한강공원·도시 쇼핑",
    "잠실/송파":     "롯데월드·석촌호수·올림픽공원",
    "여의도/영등포": "한강 뷰·IFC몰·벚꽃길",
    "강서/마곡":     "서울식물원·마곡나루",
    "강동/천호":     "암사동유적·고덕천",
}

# 클러스터별 랜드마크 — 반드시 일정에 포함 (한글+영문 모두 포함)
CLUSTER_LANDMARKS = {
    "홍대/마포":     ["홍대 걷고 싶은 거리", "Hongdae", "홍대"],
    "신촌/연남":     ["연남동", "연트럴파크", "Yeonnam"],
    "종로/광화문":   ["경복궁", "Gyeongbokgung", "광화문광장", "Cheonggyecheon", "청계천"],
    "강북/북촌":     ["북촌 한옥마을", "Bukchon", "낙산공원", "Naksan"],
    "용산/서울역":   ["국립중앙박물관", "National Museum", "전쟁기념관", "서울로7017"],
    "명동/중구":     ["덕수궁", "Deoksugung", "남산타워", "N Seoul Tower", "Namsan", "명동"],
    "이태원/한남":   ["이태원", "Itaewon", "해방촌", "경리단길"],
    "성수/왕십리":   ["Seongsu", "성수", "카페 어니언"],
    "동대문/회기":   ["동대문", "Dongdaemun", "경춘선숲길"],
    "강남/서초":     ["코엑스", "COEX", "반포한강공원", "Banpo", "강남"],
    "잠실/송파":     ["롯데월드", "Lotte World", "석촌호수", "올림픽공원"],
    "여의도/영등포": ["여의도한강공원", "63빌딩", "63 SQUARE", "IFC"],
    "강서/마곡":     ["서울식물원", "Seoul Botanic"],
    "강동/천호":     ["암사동유적", "Amsa"],
}

MAX_FALLBACK_KM = 5.0


# ──────────────────────────────────────────────
# 유틸
# ──────────────────────────────────────────────
def _haversine(lat1, lng1, lat2, lng2):
    R = 6371
    a = math.sin(math.radians(lat2 - lat1) / 2) ** 2 + \
        math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * \
        math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _assign_cluster(lat, lng):
    matched = []
    for name, b in CLUSTER_BOUNDS.items():
        if b["lat"][0] <= lat <= b["lat"][1] and b["lng"][0] <= lng <= b["lng"][1]:
            matched.append((name, _haversine(lat, lng, *CLUSTER_CENTERS[name])))
    if matched:
        return min(matched, key=lambda x: x[1])[0]
    nn, nc = min(CLUSTER_CENTERS.items(), key=lambda x: _haversine(lat, lng, x[1][0], x[1][1]))
    return nn if _haversine(lat, lng, *nc) <= MAX_FALLBACK_KM else "기타"


def _get_cluster(node):
    stored = node.get("cluster", "")
    if stored and stored != "기타" and stored in CLUSTER_CENTERS:
        return stored
    return _assign_cluster(node["lat"], node["lng"])


# ──────────────────────────────────────────────
# 데이터 구조
# ──────────────────────────────────────────────
@dataclass
class ScheduleItem:
    time: str
    type: str
    meal_type: str
    node: dict
    duration_hr: float

@dataclass
class DayPlan:
    day: int
    date: str
    hotel: dict
    cluster_name: str = ""
    schedule: list = field(default_factory=list)
    notes: str = ""

    def to_dict(self):
        morning, afternoon, breakfast, lunch, dinner, cafes = [], [], None, None, None, []
        for item in self.schedule:
            if item.type == "meal":
                if item.meal_type == "breakfast": breakfast = item.node
                elif item.meal_type == "lunch": lunch = item.node
                elif item.meal_type == "dinner": dinner = item.node
            elif item.type == "cafe":
                cafes.append(item.node)
            elif item.type == "attraction":
                (morning if int(item.time.split(":")[0]) < 14 else afternoon).append(item.node)
        return {
            "day": self.day, "date": self.date, "cluster": self.cluster_name,
            "hotel": {"name": self.hotel["name"],
                      "price_per_night": self.hotel["features"]["price_per_night"],
                      "transit": self.hotel["features"]["transit_access"],
                      "booking_url": self.hotel["features"].get("booking_url", ""),
                      "lat": self.hotel["lat"], "lng": self.hotel["lng"]},
            "morning": [_slim(a) for a in morning],
            "afternoon": [_slim(a) for a in afternoon],
            "breakfast": _slim(breakfast) if breakfast else None,
            "lunch": _slim(lunch) if lunch else None,
            "dinner": _slim(dinner) if dinner else None,
            "cafes": [_slim(c) for c in cafes],
            "timeline": [{"time": s.time, "type": s.type, "meal_type": s.meal_type, **_slim(s.node)} for s in self.schedule],
            "notes": self.notes,
        }

def _slim(node):
    if not node: return {}
    f = node.get("features", {})
    return {"name": node["name"], "score": node["node_score"],
            "lat": node["lat"], "lng": node["lng"],
            "category": f.get("category") or f.get("cuisine_type", ""),
            "duration_hr": f.get("avg_duration_hr") or 1.5,
            "price": f.get("entry_fee_krw") or f.get("avg_price_per_person") or 0}


# ──────────────────────────────────────────────
# 관광지 필터 + 랜드마크 필수 포함
# ──────────────────────────────────────────────
SEOUL_LAT, SEOUL_LNG = (37.4, 37.7), (126.7, 127.2)
BAD_NAMES = {"Sokcho", "Gangneung", "속초", "강릉"}

def _is_valid(n):
    lat, lng = n.get("lat", 0), n.get("lng", 0)
    dur = n.get("features", {}).get("avg_duration_hr", 0) or 0
    return (dur <= 8 and n.get("name") not in BAD_NAMES
            and SEOUL_LAT[0] <= lat <= SEOUL_LAT[1]
            and SEOUL_LNG[0] <= lng <= SEOUL_LNG[1])

def _select_attractions(cluster_name, candidates, n=4):
    """랜드마크 우선 포함 → score 순 채움"""
    landmarks = CLUSTER_LANDMARKS.get(cluster_name, [])
    must, rest = [], []
    for att in candidates:
        is_lm = any(lm.lower() in att["name"].lower() or att["name"].lower() in lm.lower()
                     for lm in landmarks)
        (must if is_lm and len(must) < n else rest).append(att)
    rest.sort(key=lambda x: x["node_score"], reverse=True)
    return (must + rest)[:n]


# ──────────────────────────────────────────────
# 클러스터 계획
# ──────────────────────────────────────────────
def _plan_clusters(attractions, restaurants, hotel, n_days, preferences, variant_idx=0):
    wa = preferences.get("walking_aversion", 3)
    h_lat = hotel.get("lat", 37.5665) if hotel else 37.5665
    h_lng = hotel.get("lng", 126.978) if hotel else 126.978
    ca, cr = defaultdict(list), defaultdict(list)
    for n in attractions: ca[_get_cluster(n)].append(n)
    for r in restaurants: cr[_get_cluster(r)].append(r)
    ca.pop("기타", None); cr.pop("기타", None)
    valid = [c for c in ca if len(ca[c]) >= 2]

    def _score(name):
        att, rest, cen = ca.get(name, []), cr.get(name, []), CLUSTER_CENTERS.get(name)
        if not cen: return 0.0
        return ((sum(n["node_score"] for n in att) / len(att) if att else 0) * 0.35
                + (sum(r["node_score"] for r in rest) / len(rest) if rest else 0) * 0.15
                + min(len(att) / 3.0, 1.0) * 0.25 + min(len(rest) / 3.0, 1.0) * 0.15
                - _haversine(h_lat, h_lng, cen[0], cen[1]) / max(20 - wa * 1.5, 5) * 0.1)

    scored = sorted(valid, key=_score, reverse=True)
    if len(scored) < n_days:
        scored += sorted([c for c in ca if c not in scored], key=_score, reverse=True)

    if variant_idx == 0: selected = scored[:n_days]
    elif variant_idx == 1:
        selected = scored[::2][:n_days]
        if len(selected) < n_days: selected += [c for c in scored if c not in selected][:n_days - len(selected)]
    else:
        selected = scored[1::2][:n_days]
        if len(selected) < n_days: selected += [c for c in scored if c not in selected][:n_days - len(selected)]

    ordered, rem, cur = [], list(selected), (h_lat, h_lng)
    while rem:
        nn = min(rem, key=lambda c: _haversine(cur[0], cur[1], *CLUSTER_CENTERS.get(c, cur)))
        ordered.append(nn); rem.remove(nn); cur = CLUSTER_CENTERS.get(nn, cur)
    return ordered, dict(ca), dict(cr)


# ──────────────────────────────────────────────
# 숙소 / 동선 / 맛집
# ──────────────────────────────────────────────
def _pick_hotel(cur_cluster, next_cluster, hotels, used_names):
    if not hotels: return {}
    cur_c = CLUSTER_CENTERS.get(cur_cluster, (37.5665, 126.978))
    next_c = CLUSTER_CENTERS.get(next_cluster, cur_c) if next_cluster else cur_c
    t_lat, t_lng = cur_c[0] * 0.7 + next_c[0] * 0.3, cur_c[1] * 0.7 + next_c[1] * 0.3
    for name in reversed(used_names):
        h = next((x for x in hotels if x["name"] == name), None)
        if h and _haversine(cur_c[0], cur_c[1], h["lat"], h["lng"]) <= 6.0: return h
    return max(hotels, key=lambda h: h["node_score"] - _haversine(t_lat, t_lng, h["lat"], h["lng"]) / 30)

def _optimize_route(nodes, hotel=None):
    """Nearest Neighbor TSP — 숙소에서 출발, 모든 노드를 최단 경로로 순회."""
    if len(nodes) <= 1: return nodes
    start_lat = hotel["lat"] if hotel and hotel.get("lat") else nodes[0]["lat"]
    start_lng = hotel["lng"] if hotel and hotel.get("lng") else nodes[0]["lng"]
    # 숙소에서 가장 가까운 노드부터 시작
    start = min(nodes, key=lambda n: _haversine(start_lat, start_lng, n["lat"], n["lng"]))
    rem, route = [n for n in nodes if n is not start], [start]
    while rem:
        last = route[-1]
        nn = min(rem, key=lambda n: _haversine(last["lat"], last["lng"], n["lat"], n["lng"]))
        route.append(nn); rem.remove(nn)
    return route


def _optimize_route_full(all_stops, hotel):
    """
    TSP 최적화 — 숙소 출발 → 모든 정류장(관광지+식사) 순회 → 숙소 복귀.
    2-opt 개선으로 교차 경로 제거.
    """
    if len(all_stops) <= 2:
        return _optimize_route(all_stops, hotel)

    h_lat = hotel.get("lat", 37.5665)
    h_lng = hotel.get("lng", 126.978)

    # 1단계: Nearest Neighbor로 초기 경로
    route = _optimize_route(all_stops, hotel)

    # 2단계: 2-opt 개선 (교차 경로 풀기)
    def total_dist(r):
        d = _haversine(h_lat, h_lng, r[0]["lat"], r[0]["lng"])
        for i in range(len(r) - 1):
            d += _haversine(r[i]["lat"], r[i]["lng"], r[i+1]["lat"], r[i+1]["lng"])
        d += _haversine(r[-1]["lat"], r[-1]["lng"], h_lat, h_lng)
        return d

    improved = True
    while improved:
        improved = False
        best_dist = total_dist(route)
        for i in range(len(route) - 1):
            for j in range(i + 1, len(route)):
                new_route = route[:i] + route[i:j+1][::-1] + route[j+1:]
                new_dist = total_dist(new_route)
                if new_dist < best_dist - 0.01:  # 10m 이상 개선 시
                    route = new_route
                    best_dist = new_dist
                    improved = True
                    break
            if improved:
                break

    return route

# 끼니별 cuisine 적합도
MEAL_FITNESS = {
    "breakfast": {
        "good": ["브런치", "카페", "베이커리", "팬케이크", "수제비", "칼국수", "국수", "분식", "해장국", "삼계탕", "죽", "토스트", "샌드위치"],
        "bad":  ["구이", "고기", "곱창", "막창", "양대창", "클럽", "펍", "칵테일", "와인바", "코스", "샤브", "족발", "보쌈"],
    },
    "lunch": {
        "good": ["한식", "냉면", "칼국수", "국수", "비빔", "쌀국수", "라멘", "파스타", "딤섬", "중식", "이탈리안", "베트남", "멕시칸", "분식", "만두", "삼계탕", "감자탕"],
        "bad":  ["클럽", "펍", "칵테일"],
    },
    "cafe": {
        "good": ["카페", "디저트", "커피", "베이커리", "차", "티", "팬케이크", "브런치", "아이스크림"],
        "bad":  ["구이", "고기", "곱창", "냉면", "해장국", "감자탕", "족발", "보쌈", "삼겹살"],
    },
    "dinner": {
        "good": ["구이", "고기", "곱창", "양대창", "한우", "삼겹살", "소금구이", "코스", "와인", "이탈리안", "프렌치", "한정식", "해산물", "복어", "샤브", "족발", "보쌈", "통닭", "퓨전"],
        "bad":  ["분식", "브런치", "팬케이크", "토스트"],
    },
}

def _meal_fitness(cuisine: str, meal_type: str) -> float:
    """끼니 타입에 맞는 cuisine이면 보너스, 안 맞으면 패널티."""
    rules = MEAL_FITNESS.get(meal_type)
    if not rules or not cuisine:
        return 0.0
    cl = cuisine.lower()
    if any(g in cl for g in rules["good"]):
        return 0.15
    if any(b in cl for b in rules["bad"]):
        return -0.25
    return 0.0


def _pick_meal(prev_stop, next_stop, pool, fallback, used, used_cuisines,
               max_dist, price_range=(0, 999999), prefer_types=None, meal_type=""):
    p_lat, p_lng = prev_stop["lat"], prev_stop["lng"]
    n_lat, n_lng = next_stop.get("lat", p_lat), next_stop.get("lng", p_lng)
    direct = _haversine(p_lat, p_lng, n_lat, n_lng)
    def score(r):
        detour = (_haversine(p_lat, p_lng, r["lat"], r["lng"]) + _haversine(r["lat"], r["lng"], n_lat, n_lng)) - direct
        cuisine = r.get("features", {}).get("cuisine_type", "")
        c_pen = 0.15 if cuisine and cuisine in (used_cuisines[-2:] if used_cuisines else []) else 0
        p_bon = 0.1 if prefer_types and any(p in cuisine for p in prefer_types) else 0
        fit = _meal_fitness(cuisine, meal_type)
        return r["node_score"] - detour / 3.0 - c_pen + p_bon + fit
    for pool_, cap in [(pool, max_dist), (pool, max_dist*2), (fallback, max_dist*2), (fallback, float("inf"))]:
        avail = [r for r in pool_ if r["name"] not in used
                 and price_range[0] <= (r.get("features",{}).get("avg_price_per_person",0) or 0) <= price_range[1]]
        nearby = [(r, _haversine(p_lat, p_lng, r["lat"], r["lng"])) for r in avail]
        nearby = [x for x in nearby if x[1] <= cap]
        if nearby: return max(nearby, key=lambda x: score(x[0]))[0]
    for pool_, cap in [(pool, max_dist*2), (fallback, float("inf"))]:
        avail = [r for r in pool_ if r["name"] not in used]
        nearby = [(r, _haversine(p_lat, p_lng, r["lat"], r["lng"])) for r in avail]
        nearby = [x for x in nearby if x[1] <= cap]
        if nearby: return max(nearby, key=lambda x: score(x[0]))[0]
    return None


# ──────────────────────────────────────────────
# 플래닝 에이전트
# ──────────────────────────────────────────────
class PlanningAgent:
    def __init__(self, attractions_json, restaurants_json, hotels_json, verbose=True):
        self.verbose = verbose
        with open(attractions_json, encoding="utf-8") as f: a_data = json.load(f)
        with open(restaurants_json, encoding="utf-8") as f: r_data = json.load(f)
        with open(hotels_json, encoding="utf-8") as f: h_data = json.load(f)
        self.trip = a_data.get("trip", {})
        self.attractions = a_data.get("attraction_nodes", [])
        self.restaurants = r_data.get("restaurant_nodes", [])
        self.hotels = h_data.get("hotel_nodes", [])
        self.duration = self.trip.get("duration_days", 4)
        self.checkin = self.trip.get("checkin", "")

    def _log(self, msg):
        if self.verbose: print(f"[PlanningAgent] {msg}", flush=True)

    def run(self, n_variants=1):
        if n_variants <= 1: return self._build_one(variant_idx=0)
        results = []
        for vi in range(n_variants):
            self._log(f"\n{'='*50}\n  일정 {vi+1}/{n_variants} (variant {vi})\n{'='*50}")
            r = self._build_one(variant_idx=vi); r["variant"] = vi + 1; results.append(r)
        return results

    def _build_one(self, variant_idx=0):
        self._log(f"일정 생성 — {self.trip.get('destination')} {self.duration}박")
        prefs = self.trip.get("preferences", {})
        food, budget = prefs.get("food", 3), self.trip.get("budget_krw", 1500000)
        days_cnt, travelers = self.duration, self.trip.get("traveler_count", 2)

        dfb = budget * 0.30 / days_cnt / travelers
        bk_max, ln_max, dn_max = int(dfb * 0.25), int(dfb * 0.60), int(dfb * 1.00)
        max_dist = 1.5 + food * 0.7

        best_hotel = max(self.hotels, key=lambda h: h["node_score"]) if self.hotels else None
        valid_att = [n for n in self.attractions if _is_valid(n)]
        seen, deduped = set(), []
        for n in sorted(valid_att, key=lambda x: x["node_score"], reverse=True):
            k = n.get("place_id") or n["name"].replace(" ", "").lower()
            if k not in seen: seen.add(k); seen.add(n["name"].replace(" ", "").lower()); deduped.append(n)

        ordered, ca, cr = _plan_clusters(deduped, self.restaurants, best_hotel, self.duration, prefs, variant_idx)

        for idx, cn in enumerate(ordered):
            sel = _select_attractions(cn, ca.get(cn, []))
            self._log(f"  Day{idx+1} [{cn}] 관광지후보={[a['name'] for a in sel]} 맛집={len(cr.get(cn,[]))}개")

        from datetime import datetime, timedelta
        base_date = datetime.strptime(self.checkin, "%Y-%m-%d") if self.checkin else datetime.now()
        days, used_rest, used_hotels, used_cuisines = [], set(), [], []

        def fmt(h):
            return f"{int(h):02d}:{int((h-int(h))*60):02d}"
        def get_dur(n):
            return n.get("features", {}).get("avg_duration_hr", 0) or 1.5

        for i, cname in enumerate(ordered):
            day_num = i + 1
            date_str = (base_date + timedelta(days=i)).strftime("%Y-%m-%d")
            next_cl = ordered[i+1] if i+1 < len(ordered) else None

            att_sel = _select_attractions(cname, ca.get(cname, []), n=4)
            rest_pool = cr.get(cname, [])
            hotel = _pick_hotel(cname, next_cl, self.hotels, used_hotels)
            used_hotels.append(hotel.get("name", ""))
            hp = {"lat": hotel.get("lat", 37.5665), "lng": hotel.get("lng", 126.978)}

            # ── 1단계: 관광지만 TSP 최적화 (2-opt) ──
            optimized = _optimize_route_full(att_sel, hotel)

            # ── 2단계: 소요시간 cap ──
            MAX_DAY_ATT_HR = 7.0
            total_att_hr = sum(get_dur(a) for a in optimized)
            scale = min(1.0, MAX_DAY_ATT_HR / total_att_hr) if total_att_hr > 0 else 1.0

            def pick(prev, nxt, pm, pt=None, mt=""):
                return _pick_meal(prev, nxt, rest_pool, self.restaurants, used_rest, used_cuisines, max_dist, (0, pm), pt, meal_type=mt)
            def reg(r):
                if r:
                    used_rest.add(r["name"])
                    c = r.get("features", {}).get("cuisine_type", "")
                    if c: used_cuisines.append(c)

            # ── 3단계: 시간 흐름에 따라 관광지 배치 + 식사 삽입 ──
            schedule = []
            clock = 9.0

            # 아침 (09:00, 숙소 → 첫 관광지 사이)
            first = optimized[0] if optimized else hp
            b = pick(hp, first, bk_max, mt="breakfast"); reg(b)
            if b:
                schedule.append(ScheduleItem(fmt(clock), "meal", "breakfast", b, 1.0))
                clock += 1.0

            # 관광지 순회하면서 시간대에 맞춰 식사 삽입
            had_lunch = had_cafe = False
            for ai, att in enumerate(optimized):
                dur = round(get_dur(att) * scale, 1)
                nxt = optimized[ai + 1] if ai + 1 < len(optimized) else hp

                # 점심 (12:00~13:30 사이)
                if not had_lunch and clock >= 12.0:
                    prev = schedule[-1].node if schedule else hp
                    l = pick(prev, att, ln_max, mt="lunch"); reg(l)
                    if l:
                        schedule.append(ScheduleItem(fmt(clock), "meal", "lunch", l, 1.0))
                        clock += 1.0
                    had_lunch = True

                # 카페 (14:30~17:30 사이)
                if not had_cafe and 14.5 <= clock <= 17.5:
                    prev = schedule[-1].node if schedule else hp
                    c = pick(prev, att, ln_max, pt=["카페","디저트","커피","베이커리"], mt="cafe"); reg(c)
                    if c:
                        schedule.append(ScheduleItem(fmt(clock), "cafe", "cafe", c, 0.5))
                        clock += 0.5
                    had_cafe = True

                # 관광지
                schedule.append(ScheduleItem(fmt(clock), "attraction", "", att, dur))
                clock += dur

            # 보충: 아직 점심 못 먹었으면
            if not had_lunch:
                prev = schedule[-1].node if schedule else hp
                l = pick(prev, hp, ln_max, mt="lunch"); reg(l)
                if l:
                    schedule.append(ScheduleItem(fmt(clock), "meal", "lunch", l, 1.0))
                    clock += 1.0

            # 보충: 아직 카페 못 갔으면
            if not had_cafe and clock <= 18.0:
                prev = schedule[-1].node if schedule else hp
                c = pick(prev, hp, ln_max, pt=["카페","디저트","커피","베이커리"], mt="cafe"); reg(c)
                if c:
                    schedule.append(ScheduleItem(fmt(clock), "cafe", "cafe", c, 0.5))
                    clock += 0.5

            # 저녁 (18:30~20:00, 마지막 관광지 → 숙소 방향)
            last = schedule[-1].node if schedule else hp
            d = pick(last, hp, dn_max, mt="dinner"); reg(d)
            if d:
                dinner_time = max(min(clock, 20.0), 18.5)
                schedule.append(ScheduleItem(fmt(dinner_time), "meal", "dinner", d, 1.5))

            concept = CLUSTER_CONCEPTS.get(cname, "")
            atts = [s.node["name"] for s in schedule if s.type == "attraction"]
            meals = [s.node["name"] for s in schedule if s.type in ("meal", "cafe")]
            lms = [a for a in atts if any(lm.lower() in a.lower() or a.lower() in lm.lower() for lm in CLUSTER_LANDMARKS.get(cname, []))]
            notes = f"{cname}({concept}) 탐방. {'필수 코스 ' + ', '.join(lms) + ' 포함. ' if lms else ''}{len(atts)}곳 관광, {len(meals)}곳 맛집."

            dp = DayPlan(day=day_num, date=date_str, hotel=hotel, cluster_name=cname, schedule=schedule, notes=notes)
            self._log(f"  Day{day_num} [{cname}] 관광={atts} 식사={meals}")
            tl = " → ".join(f"{s.time} {s.node['name']}" for s in schedule)
            self._log(f"  타임라인: {tl}")
            days.append(dp)

        unique_hotels = {}
        for d in days: unique_hotels[d.hotel["name"]] = unique_hotels.get(d.hotel["name"], 0) + 1
        hotel_cost = sum(h["features"]["price_per_night"] * nights for h in self.hotels for name, nights in unique_hotels.items() if h["name"] == name)

        return {
            "trip": self.trip,
            "summary": {
                "total_days": self.duration, "destination": self.trip.get("destination"),
                "cluster_plan": ordered, "hotels_used": list(unique_hotels.keys()),
                "estimated_hotel_cost": hotel_cost,
                "top_attractions": [a["name"] for a in sorted(self.attractions, key=lambda x: x["node_score"], reverse=True)[:5]],
                "top_restaurants": [r["name"] for r in sorted(self.restaurants, key=lambda x: x["node_score"], reverse=True)[:5]],
            },
            "itinerary": [d.to_dict() for d in days],
        }
