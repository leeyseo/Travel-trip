"""
지식 그래프 빌더

서울 × [20s, 30s, 40s, 50s, 60s] 5가지 조건으로 브라우징 후
knowledge_graph/서울.json에 노드 upsert

실행:
  python graph_builder/build_knowledge_graph.py
  python graph_builder/build_knowledge_graph.py --age 30s  # 특정 나이대만
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)

from dotenv import load_dotenv
load_dotenv()

import json
import argparse
import hashlib
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from models.schemas import TripInput, TravelerPreferences

# ──────────────────────────────────────────────
# 나이대별 기본 취향 프로파일
# ──────────────────────────────────────────────
AGE_PROFILES = {
    "20s": {
        "label": "20대",
        "preferences": {
            "cleanliness": 3, "food": 5, "activity": 5,
            "nature": 3, "culture": 3, "nightlife": 5,
            "shopping": 5, "walking_aversion": 2,
            "scoring_style": "balanced",
        },
    },
    "30s": {
        "label": "30대",
        "preferences": {
            "cleanliness": 5, "food": 5, "activity": 3,
            "nature": 2, "culture": 4, "nightlife": 2,
            "shopping": 3, "walking_aversion": 4,
            "scoring_style": "balanced",
        },
    },
    "40s": {
        "label": "40대",
        "preferences": {
            "cleanliness": 5, "food": 4, "activity": 3,
            "nature": 4, "culture": 5, "nightlife": 1,
            "shopping": 3, "walking_aversion": 3,
            "scoring_style": "balanced",
        },
    },
    "50s": {
        "label": "50대",
        "preferences": {
            "cleanliness": 5, "food": 4, "activity": 2,
            "nature": 5, "culture": 5, "nightlife": 1,
            "shopping": 2, "walking_aversion": 4,
            "scoring_style": "balanced",
        },
    },
    "60s": {
        "label": "60대",
        "preferences": {
            "cleanliness": 5, "food": 3, "activity": 1,
            "nature": 5, "culture": 5, "nightlife": 1,
            "shopping": 2, "walking_aversion": 5,
            "scoring_style": "balanced",
        },
    },
}


# ──────────────────────────────────────────────
# 노드 upsert 로직
# ──────────────────────────────────────────────
def load_graph(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {
        "destination": path.stem,
        "last_updated": "",
        "nodes": {},   # place_id → node
        "build_log": [],
    }


def save_graph(graph: dict, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    graph["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    path.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")


def upsert_node(graph: dict, node: dict, age_group: str, score: float):
    """
    노드를 그래프에 upsert.
    - 새 노드면 추가
    - 기존 노드면 meta.age_scores에 점수 추가, pull_count 증가
    """
    pid = node["place_id"]

    if pid not in graph["nodes"]:
        # 새 노드 생성
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
        # 기존 노드 업데이트
        existing = graph["nodes"][pid]
        meta = existing.setdefault("meta", {
            "pull_count": 0,
            "age_scores": {},
            "seen_in_age_groups": [],
        })
        meta["pull_count"] = meta.get("pull_count", 0) + 1
        meta["age_scores"][age_group] = score
        meta["last_updated"] = datetime.now().strftime("%Y-%m-%d")
        if age_group not in meta.get("seen_in_age_groups", []):
            meta["seen_in_age_groups"].append(age_group)

        # 피처 평균 업데이트 (선택적 — 최신 값으로 덮어쓰기)
        existing["features"] = node.get("features", existing.get("features", {}))
        existing["node_score"] = score  # 현재 프로파일 score 유지
        return "updated"


# ──────────────────────────────────────────────
# 단일 조건 브라우징
# ──────────────────────────────────────────────
def run_single_build(destination: str, age_group: str, output_dir: str) -> dict:
    """한 가지 나이대 조건으로 브라우징 실행"""
    profile = AGE_PROFILES[age_group]
    prefs = TravelerPreferences(**profile["preferences"])
    trip = TripInput(
        destination=destination,
        duration_days=4,
        traveler_count=2,
        age_group=age_group,
        budget_krw=9999999,  # 빌드 시 예산 무관 — 모든 장소 수집
        preferences=prefs,
    )

    print(f"\n{'='*60}")
    print(f"  빌드: {destination} × {profile['label']}")
    print(f"  취향: {profile['preferences']}")
    print(f"  (예산은 쿼리 시점에 적용)")
    print(f"{'='*60}\n")

    nodes = {"attraction": [], "restaurant": [], "hotel": []}

    # 관광지
    from agents.attraction_agent import AttractionBrowsingAgent
    a_agent = AttractionBrowsingAgent(trip, max_places=20, verbose=True)
    nodes["attraction"] = a_agent.run()
    print(f"  → 관광지 {len(nodes['attraction'])}개 수집\n")

    # 맛집
    from agents.restaurant_agent import RestaurantBrowsingAgent
    r_agent = RestaurantBrowsingAgent(trip, max_places=15, verbose=True)
    nodes["restaurant"] = r_agent.run()
    print(f"  → 맛집 {len(nodes['restaurant'])}개 수집\n")

    # 숙소
    from agents.hotel_agent import HotelBrowsingAgent
    h_agent = HotelBrowsingAgent(trip, max_places=10, verbose=True)
    nodes["hotel"] = h_agent.run()
    print(f"  → 숙소 {len(nodes['hotel'])}개 수집\n")

    return nodes


# ──────────────────────────────────────────────
# 메인 빌드
# ──────────────────────────────────────────────
def build(destination: str, age_groups: list[str], graph_dir: str):
    graph_path = Path(graph_dir) / f"{destination}.json"
    graph = load_graph(graph_path)

    for age_group in age_groups:
        print(f"\n[Build] {destination} × {age_group} 시작...")

        # 이미 빌드된 경우 스킵
        already_built = any(
            log["age_group"] == age_group and log["destination"] == destination
            for log in graph.get("build_log", [])
        )
        if already_built:
            print(f"  → 이미 빌드됨, 스킵 (재빌드하려면 --force 옵션)")
            continue

        nodes = run_single_build(destination, age_group, graph_dir)

        # upsert
        stats = {"new": 0, "updated": 0}
        for category, node_list in nodes.items():
            for node in node_list:
                node_dict = node.to_dict()
                node_dict["category"] = category
                result = upsert_node(graph, node_dict, age_group, node.node_score)
                stats[result] += 1

        graph["build_log"].append({
            "destination": destination,
            "age_group": age_group,
            "built_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "new_nodes": stats["new"],
            "updated_nodes": stats["updated"],
        })

        save_graph(graph, graph_path)
        print(f"\n  → upsert 완료: 신규 {stats['new']}개 / 업데이트 {stats['updated']}개")
        print(f"  → 전체 노드: {len(graph['nodes'])}개")
        print(f"  → 저장: {graph_path}")

    print(f"\n{'='*60}")
    print(f"빌드 완료!")
    print(f"총 노드: {len(graph['nodes'])}개")
    print(f"저장 위치: {graph_path}")
    print(f"{'='*60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="지식 그래프 빌더")
    parser.add_argument("--destination", default="서울")
    parser.add_argument("--age", default=None, help="특정 나이대만 빌드 (예: 30s)")
    parser.add_argument("--graph-dir", default="knowledge_graph")
    parser.add_argument("--force", action="store_true", help="이미 빌드된 것도 재빌드")
    args = parser.parse_args()

    age_groups = [args.age] if args.age else list(AGE_PROFILES.keys())
    build(args.destination, age_groups, args.graph_dir)
