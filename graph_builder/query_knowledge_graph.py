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
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))


# ──────────────────────────────────────────────
# 유저 조건 → 최적 나이대 매핑
# ──────────────────────────────────────────────
# ──────────────────────────────────────────────
# 그래프에서 노드 필터링 + 점수 재계산
# ──────────────────────────────────────────────
def filter_nodes(
    graph: dict,
    preferences: dict,
    budget_krw: int,
    category: str,
    duration_days: int = 4,
    top_n: int = 20,
) -> list[dict]:
    """
    그래프에서 조건에 맞는 노드 필터링.
    - 카테고리 필터 + 예산 필터
    - 유저 preferences로 score_* 재호출 → 취향 반영 랭킹
    """
    from models.schemas import (
        TravelerPreferences, RestaurantFeatures, AttractionFeatures, HotelFeatures
    )
    from utils.scorer import score_restaurant, score_attraction, score_hotel

    prefs_obj = TravelerPreferences(**preferences)

    all_nodes = list(graph["nodes"].values())
    candidates = [n for n in all_nodes if n.get("category") == category]

    def calc_score(node: dict) -> float:
        pull_bonus = min(node.get("meta", {}).get("pull_count", 1) * 0.01, 0.05)
        feat = node.get("features", {})

        try:
            if category == "restaurant":
                price = feat.get("avg_price_per_person", 0)
                if price > 0:
                    meal_budget = budget_krw * 0.30 / (duration_days * 2)
                    scoring_style = preferences.get("scoring_style", "balanced")
                    multiplier = 6 if scoring_style == "peak" else 3
                    if price > meal_budget * multiplier:
                        return -1.0
                features = RestaurantFeatures(**feat)
                base_score, _ = score_restaurant(features, prefs_obj)

            elif category == "hotel":
                price = feat.get("price_per_night", 100000)
                budget_per_night = budget_krw / duration_days * 0.4
                if price > budget_per_night * 1.5:
                    return -1.0
                features = HotelFeatures(**feat)
                base_score, _ = score_hotel(features, prefs_obj, int(budget_per_night))

            elif category == "attraction":
                features = AttractionFeatures(**feat)
                base_score, _ = score_attraction(features, prefs_obj)

            else:
                base_score = node.get("node_score", 0.5)

        except Exception:
            base_score = node.get("node_score", 0.5)

        return base_score + pull_bonus

    scored = [(n, calc_score(n)) for n in candidates]
    scored = [(n, s) for n, s in scored if s >= 0]
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
    n_variants: int = 1,
    preference_text: str = "",
) -> dict:
    graph_path = Path(graph_dir) / f"{destination}.json"
    if not graph_path.exists():
        raise FileNotFoundError(f"그래프 없음: {graph_path} — 먼저 build_knowledge_graph.py 실행")

    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    total_nodes = len(graph["nodes"])
    print(f"[Query] {destination} 그래프 로드 — 노드 {total_nodes}개")

    # 카테고리별 필터링 — 전체 통과 (예산 필터만 적용)
    # 플래닝 에이전트가 클러스터별로 선택하므로 미리 잘라내지 않음
    attractions = filter_nodes(graph, preferences, budget_krw, "attraction", duration_days, top_n=9999)
    restaurants = filter_nodes(graph, preferences, budget_krw, "restaurant", duration_days, top_n=9999)
    hotels      = filter_nodes(graph, preferences, budget_krw, "hotel",      duration_days, top_n=9999)

    print(f"  → 관광지 {len(attractions)}개 / 맛집 {len(restaurants)}개 / 숙소 {len(hotels)}개 필터링")

    # 임시 JSON으로 플래닝 에이전트에 전달
    import tempfile

    trip_dict = {
        "destination": destination,
        "duration_days": duration_days,
        "traveler_count": traveler_count,
        "age_group": age_group,
        "budget_krw": budget_krw,
        "checkin": checkin,
        "preferences": {**preferences, "scoring_style": scoring_style},
        "preference_text": preference_text,
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        a_path = Path(tmpdir) / "a.json"
        r_path = Path(tmpdir) / "r.json"
        h_path = Path(tmpdir) / "h.json"

        a_path.write_text(json.dumps({"trip": trip_dict, "attraction_nodes": attractions}, ensure_ascii=False), encoding="utf-8")
        r_path.write_text(json.dumps({"trip": trip_dict, "restaurant_nodes": restaurants}, ensure_ascii=False), encoding="utf-8")
        h_path.write_text(json.dumps({"trip": trip_dict, "hotel_nodes": hotels}, ensure_ascii=False), encoding="utf-8")

        from agents.planning_agent import PlanningAgent
        agent = PlanningAgent(str(a_path), str(r_path), str(h_path), graph_json=str(graph_path), verbose=True)
        result = agent.run(n_variants=n_variants)

    meta = {
        "source": "knowledge_graph",
        "graph_nodes_total": total_nodes,
        "filtered": {"attraction": len(attractions), "restaurant": len(restaurants), "hotel": len(hotels)},
        "queried_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    if n_variants <= 1:
        # 단일 결과
        result["trip"] = trip_dict
        result["query_meta"] = meta
        out_path = Path(output_dir) / f"{destination}_itinerary_{age_group}.json"
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n결과 저장 → {out_path}")
        return result
    else:
        # 복수 결과를 하나의 JSON 구조로 통합
        combined_result = {
            "trip": trip_dict,
            "query_meta": meta,
            "variants": []
        }
        
        for i, r in enumerate(result):
            combined_result["variants"].append({
                "variant_id": i + 1,
                "summary": r.get("summary", {}),
                "itinerary": r.get("itinerary", [])
            })

        out_path = Path(output_dir) / f"{destination}_itinerary_{age_group}_combined.json"
        out_path.write_text(json.dumps(combined_result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n통합 결과 저장 → {out_path}")
        return combined_result


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
    parser.add_argument("--variants",    type=int, default=1, help="생성할 일정 수 (1~3)")
    parser.add_argument("--preference-text", default="", help="자연어 취향 설명 (인스타 결과에 추가 합산)")
    parser.add_argument("--instagram", default="", help="인스타그램 URL 또는 @username — 피드 분석으로 취향 자동 추출")
    # 개별 취향 수치 오버라이드 (1~5, 미지정 시 나이대/인스타 기본값 사용)
    parser.add_argument("--food",             type=int, default=None, help="음식/맛집 관심도 (1~5)")
    parser.add_argument("--culture",          type=int, default=None, help="역사/문화/박물관 관심도 (1~5)")
    parser.add_argument("--nature",           type=int, default=None, help="자연/공원/힐링 관심도 (1~5)")
    parser.add_argument("--activity",         type=int, default=None, help="액티비티/체험 관심도 (1~5)")
    parser.add_argument("--nightlife",        type=int, default=None, help="나이트라이프/술 관심도 (1~5)")
    parser.add_argument("--shopping",         type=int, default=None, help="쇼핑 관심도 (1~5)")
    parser.add_argument("--cleanliness",      type=int, default=None, help="위생/청결 민감도 (1~5)")
    parser.add_argument("--walking-aversion", type=int, default=None, help="도보 이동 기피도 (1~5, 5=이동 싫어함)")
    args = parser.parse_args()

    # 나이대별 기본 취향
    from graph_builder.build_knowledge_graph import AGE_PROFILES
    prefs = AGE_PROFILES.get(args.age, AGE_PROFILES["30s"])["preferences"]

    # --instagram: 피드 분석으로 취향 덮어쓰기
    preference_text = args.preference_text
    if args.instagram:
        from utils.instagram_analyzer import analyze_instagram
        try:
            ig = analyze_instagram(args.instagram)
            prefs = {**prefs, **ig["preferences"]}
            # preference_text: 인스타 결과 + 수동 입력 합산
            ig_text = ig["preference_text"]
            preference_text = f"{ig_text} {preference_text}".strip() if preference_text else ig_text
            args.style = ig["scoring_style"]
            print(f"[Instagram] @{ig['username']} 취향 적용: {ig['summary']}")
            print(f"  preference_text: {preference_text}")
            print(f"  scoring_style:   {args.style}")
        except Exception as e:
            print(f"[Instagram] 분석 실패: {e} → 기본 취향으로 진행")

    # 개별 취향 수치 오버라이드 (--food, --culture 등 명시된 항목만 덮어씀)
    PREF_OVERRIDES = {
        "food": args.food, "culture": args.culture, "nature": args.nature,
        "activity": args.activity, "nightlife": args.nightlife, "shopping": args.shopping,
        "cleanliness": args.cleanliness, "walking_aversion": args.walking_aversion,
    }
    overridden = []
    for key, val in PREF_OVERRIDES.items():
        if val is not None:
            prefs[key] = max(1, min(5, val))
            overridden.append(f"{key}={val}")
    if overridden:
        print(f"[취향 오버라이드] {', '.join(overridden)}")

    # --style 유효성 검사
    VALID_STYLES = {"balanced", "threshold", "peak", "risk_averse", "budget_safe"}
    if args.style not in VALID_STYLES:
        print(f"[경고] 알 수 없는 style '{args.style}' → 'balanced'로 대체")
        print(f"       유효한 옵션: {', '.join(sorted(VALID_STYLES))}")
        args.style = "balanced"

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
        n_variants=args.variants,
        preference_text=preference_text,
    )
