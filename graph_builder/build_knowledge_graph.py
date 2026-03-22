"""
지식 그래프 빌더 v4

나이대별로 관광지 + 맛집/카페 + 호텔을 한 번에 수집.
중복 장소는 age_scores에 나이대별 점수만 추가.
각 노드에 서울 지역 클러스터 자동 태깅.

실행:
  python graph_builder/build_knowledge_graph.py --age 20s
  python graph_builder/build_knowledge_graph.py --age 30s
  python graph_builder/build_knowledge_graph.py           # 전체 나이대
  python graph_builder/build_knowledge_graph.py --force   # 재빌드
"""
import sys, io
if hasattr(sys.stdout, 'buffer') and not isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
elif hasattr(sys.stdout, 'reconfigure'):
    try: sys.stdout.reconfigure(encoding='utf-8')
    except: pass

from dotenv import load_dotenv
load_dotenv()

import json, argparse
from datetime import datetime
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).parent.parent))
from models.schemas import TripInput, TravelerPreferences

# ──────────────────────────────────────────────
# 서울 지역 클러스터
# ──────────────────────────────────────────────
SEOUL_CLUSTERS = {
    "홍대/마포":     {"lat": (37.54, 37.57), "lng": (126.90, 126.94)},
    "신촌/연남":     {"lat": (37.55, 37.58), "lng": (126.92, 126.95)},
    "종로/광화문":   {"lat": (37.56, 37.59), "lng": (126.96, 126.99)},
    "강북/북촌":     {"lat": (37.57, 37.61), "lng": (126.97, 127.01)},
    "용산/서울역":   {"lat": (37.53, 37.56), "lng": (126.96, 126.98)},
    "명동/중구":     {"lat": (37.55, 37.57), "lng": (126.97, 126.99)},
    "이태원/한남":   {"lat": (37.53, 37.56), "lng": (126.98, 127.01)},
    "성수/왕십리":   {"lat": (37.54, 37.57), "lng": (127.03, 127.07)},
    "강남/서초":     {"lat": (37.47, 37.53), "lng": (127.01, 127.06)},
    "잠실/송파":     {"lat": (37.50, 37.53), "lng": (127.08, 127.12)},
    "여의도/영등포": {"lat": (37.51, 37.54), "lng": (126.90, 126.94)},
    "강서/마곡":     {"lat": (37.54, 37.57), "lng": (126.82, 126.87)},
    "강동/천호":     {"lat": (37.53, 37.56), "lng": (127.12, 127.17)},
}

def assign_cluster(lat: float, lng: float) -> str:
    for name, b in SEOUL_CLUSTERS.items():
        if b["lat"][0] <= lat <= b["lat"][1] and b["lng"][0] <= lng <= b["lng"][1]:
            return name
    return "기타"

# ──────────────────────────────────────────────
# 나이대 프로파일
# ──────────────────────────────────────────────
AGE_PROFILES = {
    "20s": {"label": "20대", "preferences": {
        "cleanliness": 3, "food": 5, "activity": 5, "nature": 3,
        "culture": 3, "nightlife": 5, "shopping": 5, "walking_aversion": 2,
        "scoring_style": "balanced"}},
    "30s": {"label": "30대", "preferences": {
        "cleanliness": 5, "food": 5, "activity": 3, "nature": 2,
        "culture": 4, "nightlife": 2, "shopping": 3, "walking_aversion": 4,
        "scoring_style": "balanced"}},
    "40s": {"label": "40대", "preferences": {
        "cleanliness": 5, "food": 4, "activity": 3, "nature": 4,
        "culture": 5, "nightlife": 1, "shopping": 3, "walking_aversion": 3,
        "scoring_style": "balanced"}},
    "50s": {"label": "50대", "preferences": {
        "cleanliness": 5, "food": 4, "activity": 2, "nature": 5,
        "culture": 5, "nightlife": 1, "shopping": 2, "walking_aversion": 4,
        "scoring_style": "balanced"}},
    "60s": {"label": "60대", "preferences": {
        "cleanliness": 5, "food": 3, "activity": 1, "nature": 5,
        "culture": 5, "nightlife": 1, "shopping": 2, "walking_aversion": 5,
        "scoring_style": "balanced"}},
}

# ──────────────────────────────────────────────
# 그래프 I/O
# ──────────────────────────────────────────────
def load_graph(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"destination": path.stem, "last_updated": "", "nodes": {}, "build_log": []}

def save_graph(graph: dict, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    graph["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    path.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")

def _haversine(lat1, lng1, lat2, lng2) -> float:
    import math
    R = 6371000  # 미터
    d1 = math.radians(lat2 - lat1)
    d2 = math.radians(lng2 - lng1)
    a = math.sin(d1/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d2/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def _find_nearby_node(graph: dict, lat: float, lng: float, category: str, threshold_m: float = 200) -> str | None:
    """같은 카테고리에서 threshold_m 이내 기존 노드 place_id 반환. 없으면 None."""
    if lat == 0 and lng == 0:
        return None
    for pid, node in graph["nodes"].items():
        if node.get("category") != category:
            continue
        n_lat, n_lng = node.get("lat", 0), node.get("lng", 0)
        if n_lat == 0 and n_lng == 0:
            continue
        if _haversine(lat, lng, n_lat, n_lng) <= threshold_m:
            return pid
    return None


def upsert_node(graph: dict, node: dict, age_group: str, score: float) -> str:
    lat = node.get("lat", 0)
    lng = node.get("lng", 0)
    category = node.get("category", "")

    # 클러스터 태깅
    node["cluster"] = assign_cluster(lat, lng)

    # 좌표 기반 중복 확인 (200m 이내 같은 카테고리 노드 = 같은 장소)
    pid = node["place_id"]
    nearby_pid = _find_nearby_node(graph, lat, lng, category, threshold_m=200)
    if nearby_pid and nearby_pid != pid:
        # 기존 노드에 병합 (이름이 다른 동일 장소 — 한국어/영어 표기 차이 등)
        pid = nearby_pid
        existing_name = graph["nodes"][pid]["name"]
        if node["name"] not in existing_name:
            # 별명 추가
            graph["nodes"][pid].setdefault("aliases", [])
            if node["name"] not in graph["nodes"][pid]["aliases"]:
                graph["nodes"][pid]["aliases"].append(node["name"])

    if pid not in graph["nodes"]:
        new_node = dict(node)
        new_node["meta"] = {
            "pull_count": 1,
            "first_seen": datetime.now().strftime("%Y-%m-%d"),
            "last_updated": datetime.now().strftime("%Y-%m-%d"),
            "age_scores": {age_group: score},
            "seen_in_age_groups": [age_group],
        }
        graph["nodes"][pid] = new_node
        return "new"
    else:
        existing = graph["nodes"][pid]
        existing["cluster"] = node["cluster"]
        meta = existing.setdefault("meta", {
            "pull_count": 0, "age_scores": {}, "seen_in_age_groups": []})
        meta["pull_count"] = meta.get("pull_count", 0) + 1
        meta["last_updated"] = datetime.now().strftime("%Y-%m-%d")
        meta["age_scores"][age_group] = score
        if age_group not in meta.get("seen_in_age_groups", []):
            meta["seen_in_age_groups"].append(age_group)
        existing["features"] = node.get("features", existing.get("features", {}))
        existing["node_score"] = score
        return "updated"

# ──────────────────────────────────────────────
# 단일 나이대 빌드
# ──────────────────────────────────────────────
def build_age(destination: str, age_group: str, graph_path: Path,
              force: bool = False, only: str = None):
    graph = load_graph(graph_path)

    already = any(
        log.get("age_group") == age_group and log.get("destination") == destination
        for log in graph.get("build_log", []))
    if already and not force and not only:
        print(f"  → {age_group} 이미 빌드됨. 스킵 (재빌드: --force)")
        return graph

    profile = AGE_PROFILES[age_group]
    prefs = TravelerPreferences(**profile["preferences"])
    trip = TripInput(
        destination=destination, duration_days=4, traveler_count=2,
        age_group=age_group, budget_krw=9_999_999, preferences=prefs)

    print(f"\n{'='*60}")
    print(f"  빌드: {destination} × {profile['label']}")
    print(f"  취향: {profile['preferences']}")
    print(f"{'='*60}\n")

    stats = {"new": 0, "updated": 0}
    counts = {}

    if not only or only == "attraction":
        from agents.attraction_agent import AttractionBrowsingAgent
        a_agent = AttractionBrowsingAgent(trip, verbose=True)
        attractions = a_agent.run()
        counts["attraction"] = len(attractions)
        print(f"  → 관광지 {len(attractions)}개 수집\n")
        for node in attractions:
            d = node.to_dict(); d["category"] = "attraction"
            stats[upsert_node(graph, d, age_group, node.node_score)] += 1

    if not only or only == "restaurant":
        from agents.restaurant_agent import RestaurantBrowsingAgent
        r_agent = RestaurantBrowsingAgent(trip, verbose=True)
        restaurants = r_agent.run()
        counts["restaurant"] = len(restaurants)
        print(f"  → 맛집/카페 {len(restaurants)}개 수집\n")
        for node in restaurants:
            d = node.to_dict(); d["category"] = "restaurant"
            stats[upsert_node(graph, d, age_group, node.node_score)] += 1

    if not only or only == "hotel":
        from agents.hotel_agent import HotelBrowsingAgent
        h_agent = HotelBrowsingAgent(trip, verbose=True)
        hotels = h_agent.run()
        counts["hotel"] = len(hotels)
        print(f"  → 호텔 {len(hotels)}개 수집\n")
        for node in hotels:
            d = node.to_dict(); d["category"] = "hotel"
            stats[upsert_node(graph, d, age_group, node.node_score)] += 1

    # 클러스터 분포 출력
    _print_cluster_stats(graph)

    graph["build_log"].append({
        "destination": destination,
        "age_group": age_group,
        "built_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        **counts,
        "new_nodes": stats["new"],
        "updated_nodes": stats["updated"],
    })
    save_graph(graph, graph_path)
    print(f"\n  → 완료: 신규 {stats['new']}개 / 업데이트 {stats['updated']}개")
    print(f"  → 전체 노드: {len(graph['nodes'])}개")
    return graph

# ──────────────────────────────────────────────
# 클러스터 분포 출력
# ──────────────────────────────────────────────
def _print_cluster_stats(graph: dict):
    nodes = list(graph["nodes"].values())
    print("\n  [클러스터 분포]")
    for cat in ["attraction", "restaurant", "hotel"]:
        cat_nodes = [n for n in nodes if n.get("category") == cat]
        if not cat_nodes:
            continue
        dist = Counter(n.get("cluster", "기타") for n in cat_nodes)
        print(f"    {cat} ({len(cat_nodes)}개):")
        for cluster, cnt in sorted(dist.items(), key=lambda x: -x[1])[:6]:
            print(f"      {cluster:15s} {cnt:3d}개  {'█'*min(cnt,20)}")

# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="지식 그래프 빌더 v4")
    parser.add_argument("--destination", default="서울")
    parser.add_argument("--age", default=None,
                        help="나이대 지정 (20s/30s/40s/50s/60s). 없으면 전체")
    parser.add_argument("--graph-dir", default="knowledge_graph")
    parser.add_argument("--force", action="store_true", help="재빌드")
    parser.add_argument("--only", default=None,
                        choices=["attraction", "restaurant", "hotel"],
                        help="특정 카테고리만 수집 (기존 빌드에 추가)")
    args = parser.parse_args()

    graph_path = Path(args.graph_dir) / f"{args.destination}.json"
    age_groups = [args.age] if args.age else list(AGE_PROFILES.keys())

    for age_group in age_groups:
        build_age(args.destination, age_group, graph_path,
                  force=args.force, only=args.only)

    # 최종 요약
    graph = load_graph(graph_path)
    cat = Counter(n.get("category") for n in graph["nodes"].values())
    print(f"\n{'='*60}")
    print(f"전체 빌드 완료! 총 노드: {len(graph['nodes'])}개")
    for c, cnt in cat.items():
        print(f"  {c}: {cnt}개")
    _print_cluster_stats(graph)
    print(f"\n저장: {graph_path}")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()
