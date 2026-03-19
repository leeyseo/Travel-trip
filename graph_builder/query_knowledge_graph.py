"""
지식 그래프 쿼리 엔진

knowledge_graph/서울.json에서 유저 조건에 맞는 노드 필터링 후 플래닝

실행:
  python graph_builder/query_knowledge_graph.py
  python graph_builder/query_knowledge_graph.py --age 30s --budget 1500000
"""
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from dotenv import load_dotenv
load_dotenv()

import json
import argparse
import math
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))


# ──────────────────────────────────────────────
# 유저 조건 → 최적 나이대 매핑
# ──────────────────────────────────────────────
def map_to_age_group(age_group: str, available_groups: list[str]) -> list[str]:
    """
    유저 나이대에서 그래프에 있는 나이대로 매핑.
    정확히 있으면 그것만, 없으면 인접 나이대로 fallback.
    """
    age_order = ["20s", "30s", "40s", "50s", "60s"]

    if age_group in available_groups:
        return [age_group]

    # 인접 나이대 fallback
    if age_group in age_order:
        idx = age_order.index(age_group)
        candidates = []
        if idx > 0 and age_order[idx-1] in available_groups:
            candidates.append(age_order[idx-1])
        if idx < len(age_order)-1 and age_order[idx+1] in available_groups:
            candidates.append(age_order[idx+1])
        if candidates:
            return candidates

    return available_groups[:1]


# ──────────────────────────────────────────────
# 그래프에서 노드 필터링 + 점수 재계산
# ──────────────────────────────────────────────
def filter_nodes(
    graph: dict,
    age_group: str,
    preferences: dict,
    budget_krw: int,
    category: str,
    duration_days: int = 4,
    top_n: int = 20,
) -> list[dict]:
    """
    그래프에서 조건에 맞는 노드 필터링.
    - age_group에서 뽑힌 노드 우선
    - 없으면 인접 나이대 노드
    - 점수 재계산 (유저 취향 가중치 적용)
    """
    all_nodes = list(graph["nodes"].values())
    available_ages = list(set(
        age for node in all_nodes
        for age in node.get("meta", {}).get("seen_in_age_groups", [])
    ))

    target_ages = map_to_age_group(age_group, available_ages)

    # 카테고리 필터
    candidates = [n for n in all_nodes if n.get("category") == category]

    # 나이대 필터 (해당 나이대에서 뽑힌 것만)
    age_filtered = [
        n for n in candidates
        if any(age in n.get("meta", {}).get("seen_in_age_groups", []) for age in target_ages)
    ]

    if not age_filtered:
        age_filtered = candidates  # fallback: 전체

    # 점수 재계산
    def calc_score(node: dict) -> float:
        meta = node.get("meta", {})
        age_scores = meta.get("age_scores", {})

        # 해당 나이대 점수
        base_score = max(
            (age_scores.get(age, 0) for age in target_ages),
            default=node.get("node_score", 0.5)
        )

        # pull_count 보너스 (많이 뽑힐수록 신뢰도 높음)
        pull_bonus = min(meta.get("pull_count", 1) * 0.01, 0.05)

        # 맛집: 1끼 예산 상한 필터링
        # 총예산의 30%를 식비(끼니당)로 배분
        # balanced/기본 → 상한 3배 / peak → 상한 6배 (극강 경험 허용)
        if category == "restaurant":
            price = node.get("features", {}).get("avg_price_per_person", 0)
            if price > 0:
                meal_budget = budget_krw * 0.30 / (duration_days * 2)
                scoring_style = preferences.get("scoring_style", "balanced")
                multiplier = 6 if scoring_style == "peak" else 3
                if price > meal_budget * multiplier:
                    return -1.0

        # 숙소: 예산 필터링
        if category == "hotel":
            price = node.get("features", {}).get("price_per_night", 100000)
            # 1박 예산 = 총예산 / 박수 * 40% (숙박 배분 비율)
            budget_per_night = budget_krw / duration_days * 0.4
            # 예산 1.5배 초과 시 제외 (기존 2배 → 1.5배로 강화)
            if price > budget_per_night * 1.5:
                return -1.0
            # 예산 범위 내에서 가까울수록 높은 점수
            budget_fit = max(0, 1 - abs(price - budget_per_night) / max(budget_per_night, 1))
            return base_score * 0.5 + budget_fit * 0.5 + pull_bonus

        return base_score + pull_bonus

    scored = [(n, calc_score(n)) for n in age_filtered]
    scored = [(n, s) for n, s in scored if s >= 0]  # 예산 초과 제외
    scored.sort(key=lambda x: x[1], reverse=True)

    result = []
    for node, score in scored[:top_n]:
        n = dict(node)
        n["node_score"] = round(score, 4)
        result.append(n)

    return result


# ──────────────────────────────────────────────
# 플래닝 에이전트 연결
# ──────────────────────────────────────────────
def query_and_plan(
    destination: str,
    age_group: str,
    preferences: dict,
    budget_krw: int,
    duration_days: int,
    traveler_count: int,
    checkin: str,
    scoring_style: str,
    graph_dir: str,
    output_dir: str,
) -> dict:
    graph_path = Path(graph_dir) / f"{destination}.json"
    if not graph_path.exists():
        raise FileNotFoundError(f"그래프 없음: {graph_path} — 먼저 build_knowledge_graph.py 실행")

    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    total_nodes = len(graph["nodes"])
    print(f"[Query] {destination} 그래프 로드 — 노드 {total_nodes}개")

    # 카테고리별 필터링
    attractions = filter_nodes(graph, age_group, preferences, budget_krw, "attraction", duration_days, top_n=20)
    restaurants = filter_nodes(graph, age_group, preferences, budget_krw, "restaurant", duration_days, top_n=15)
    hotels      = filter_nodes(graph, age_group, preferences, budget_krw, "hotel",      duration_days, top_n=10)

    print(f"  → 관광지 {len(attractions)}개 / 맛집 {len(restaurants)}개 / 숙소 {len(hotels)}개 필터링")

    # 임시 JSON으로 플래닝 에이전트에 전달
    import tempfile, os
    from models.schemas import TripInput, TravelerPreferences

    # scoring_style 주입
    prefs_with_style = dict(preferences)
    prefs_with_style["scoring_style"] = scoring_style
    prefs_obj = TravelerPreferences(**prefs_with_style)
    trip = TripInput(
        destination=destination,
        duration_days=duration_days,
        traveler_count=traveler_count,
        age_group=age_group,
        budget_krw=budget_krw,
        preferences=prefs_obj,
    )

    trip_dict = {
        "destination": destination,
        "duration_days": duration_days,
        "traveler_count": traveler_count,
        "age_group": age_group,
        "budget_krw": budget_krw,
        "checkin": checkin,
        "preferences": preferences,
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        a_path = Path(tmpdir) / "a.json"
        r_path = Path(tmpdir) / "r.json"
        h_path = Path(tmpdir) / "h.json"

        a_path.write_text(json.dumps({"trip": trip_dict, "attraction_nodes": attractions}, ensure_ascii=False), encoding="utf-8")
        r_path.write_text(json.dumps({"trip": trip_dict, "restaurant_nodes": restaurants}, ensure_ascii=False), encoding="utf-8")
        h_path.write_text(json.dumps({"trip": trip_dict, "hotel_nodes": hotels}, ensure_ascii=False), encoding="utf-8")

        from agents.planning_agent import PlanningAgent
        agent = PlanningAgent(str(a_path), str(r_path), str(h_path), verbose=True)
        result = agent.run()

    result["trip"] = trip_dict
    result["query_meta"] = {
        "source": "knowledge_graph",
        "graph_nodes_total": total_nodes,
        "filtered": {"attraction": len(attractions), "restaurant": len(restaurants), "hotel": len(hotels)},
        "queried_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    # 저장
    out_path = Path(output_dir) / f"{destination}_itinerary_{age_group}.json"
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n결과 저장 → {out_path}")

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="지식 그래프 쿼리")
    parser.add_argument("--destination", default="서울")
    parser.add_argument("--age",         default="30s",      help="나이대: 20s/30s/40s/50s/60s")
    parser.add_argument("--budget",      type=int, default=1500000, help="총 예산 (원)")
    parser.add_argument("--days",        type=int, default=4, help="여행 박수")
    parser.add_argument("--travelers",   type=int, default=2, help="인원 수")
    parser.add_argument("--checkin",     default="2026-04-01", help="체크인 날짜")
    parser.add_argument("--style",       default="balanced",
                        help="평가 성향: balanced/threshold/peak/risk_averse/budget_safe")
    parser.add_argument("--graph-dir",   default="knowledge_graph")
    parser.add_argument("--output",      default="output")
    args = parser.parse_args()

    # 나이대별 기본 취향
    from graph_builder.build_knowledge_graph import AGE_PROFILES
    prefs = AGE_PROFILES.get(args.age, AGE_PROFILES["30s"])["preferences"]

    query_and_plan(
        destination=args.destination,
        age_group=args.age,
        preferences=prefs,
        budget_krw=args.budget,
        duration_days=args.days,
        traveler_count=args.travelers,
        checkin=args.checkin,
        scoring_style=args.style,
        graph_dir=args.graph_dir,
        output_dir=args.output,
    )
