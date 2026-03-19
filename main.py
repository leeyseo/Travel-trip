"""
브라우징 에이전트 메인 실행 파일

실행 예시:
  python main.py                          # 관광지 + 맛집 전체 실행
  python main.py --only attraction        # 관광지만
  python main.py --only restaurant        # 맛집만
  python main.py --destination 부산       # 목적지 변경
"""
from dotenv import load_dotenv
load_dotenv()

import sys
import io
import json
import argparse
from pathlib import Path

# Windows 인코딩 설정
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

sys.path.insert(0, str(Path(__file__).parent))

from models.schemas import TripInput
from utils.graph_builder import build_graph, print_graph_summary


def run_attraction(trip: TripInput, output_dir: str):
    from agents.attraction_agent import AttractionBrowsingAgent
    print("\n[ 관광지 브라우징 ]", flush=True)
    agent = AttractionBrowsingAgent(trip, max_places=20, verbose=True)
    nodes = agent.run()

    print("\n[ 관광지 그래프 빌딩 ]", flush=True)
    graph = build_graph(nodes, category="attraction")
    print_graph_summary(graph)

    import dataclasses
    def trip_to_dict(t):
        d = dataclasses.asdict(t)
        return d

    result = {
        "trip": trip_to_dict(trip),
        "attraction_nodes": [n.to_dict() for n in nodes],
        "attraction_graph": graph.to_dict(),
    }
    out_path = Path(output_dir) / f"{trip.destination}_attractions.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n결과 저장 → {out_path}", flush=True)
    return result


def run_restaurant(trip: TripInput, output_dir: str):
    from agents.restaurant_agent import RestaurantBrowsingAgent
    print("\n[ 맛집 브라우징 ]", flush=True)
    agent = RestaurantBrowsingAgent(trip, max_places=10, verbose=True)
    nodes = agent.run()

    print("\n[ 맛집 그래프 빌딩 ]", flush=True)
    graph = build_graph(nodes, category="restaurant")
    print_graph_summary(graph)

    import dataclasses
    def trip_to_dict(t):
        d = dataclasses.asdict(t)
        return d

    result = {
        "trip": trip_to_dict(trip),
        "restaurant_nodes": [n.to_dict() for n in nodes],
        "restaurant_graph": graph.to_dict(),
    }
    out_path = Path(output_dir) / f"{trip.destination}_restaurants.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n결과 저장 → {out_path}", flush=True)
    return result


def run_hotel(trip: TripInput, output_dir: str, checkin: str = "", checkout: str = ""):
    from agents.hotel_agent import HotelBrowsingAgent
    print("\n[ 숙소 브라우징 ]", flush=True)
    agent = HotelBrowsingAgent(trip, max_places=10, verbose=True, checkin=checkin, checkout=checkout)
    nodes = agent.run()

    print("\n[ 숙소 그래프 빌딩 ]", flush=True)
    graph = build_graph(nodes, category="hotel")
    print_graph_summary(graph)

    import dataclasses
    result = {
        "trip": dataclasses.asdict(trip),
        "hotel_nodes": [n.to_dict() for n in nodes],
        "hotel_graph": graph.to_dict(),
    }
    out_path = Path(output_dir) / f"{trip.destination}_hotels.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n결과 저장 → {out_path}", flush=True)
    return result


def run_plan(trip: TripInput, output_dir: str):
    from agents.planning_agent import PlanningAgent
    import dataclasses

    dest = trip.destination
    a_path = Path(output_dir) / f"{dest}_attractions.json"
    r_path = Path(output_dir) / f"{dest}_restaurants.json"
    h_path = Path(output_dir) / f"{dest}_hotels.json"

    missing = [str(p) for p in [a_path, r_path, h_path] if not p.exists()]
    if missing:
        print(f"\n❌ 아래 파일이 없어요. 먼저 브라우징을 실행해주세요:")
        for m in missing:
            print(f"   {m}")
        return {}

    print("\n[ 일정 플래닝 ]", flush=True)
    agent = PlanningAgent(str(a_path), str(r_path), str(h_path), verbose=True)
    result = agent.run()

    # trip 딕셔너리 병합 (checkin/checkout 포함)
    result["trip"] = dataclasses.asdict(trip)

    out_path = Path(output_dir) / f"{dest}_itinerary.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n결과 저장 → {out_path}", flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description="여행 브라우징 에이전트")
    parser.add_argument("--input",       default=None,  help="입력 JSON 파일 경로")
    parser.add_argument("--output",      default="output", help="출력 디렉토리")
    parser.add_argument("--only",        default=None,  help="attraction / restaurant / hotel 중 하나만 실행")
    parser.add_argument("--destination", default=None,  help="여행지 (기본값 덮어쓰기)")
    parser.add_argument("--checkin",     default="",    help="체크인 날짜 (예: 2026-04-01)")
    parser.add_argument("--checkout",    default="",    help="체크아웃 날짜 (예: 2026-04-05)")
    args = parser.parse_args()

    # 입력 로드
    if args.input:
        with open(args.input, encoding="utf-8") as f:
            trip_dict = json.load(f)
    else:
        trip_dict = {
            "destination": "서울",
            "duration_days": 4,
            "travelers": {"count": 2, "age_group": "30s"},
            "budget_krw": 1500000,
            "checkin":  "2026-04-01",   # 체크인 날짜 (호텔 딥링크용)
            "checkout": "2026-04-05",   # 체크아웃 날짜 (호텔 딥링크용)
            "preferences": {
                "cleanliness": 5, "food": 5, "activity": 3,
                "nature": 2, "culture": 4, "nightlife": 2,
                "shopping": 3, "walking_aversion": 4,
            },
        }

    # --destination 으로 목적지 덮어쓰기
    if args.destination:
        trip_dict["destination"] = args.destination

    trip = TripInput.from_dict(trip_dict)
    Path(args.output).mkdir(exist_ok=True)

    print(f"\n{'='*60}", flush=True)
    print(f" 여행 브라우징 에이전트", flush=True)
    print(f" 여행지: {trip.destination} | {trip.duration_days}박 | {trip.traveler_count}인", flush=True)
    print(f" 예산: {trip.budget_krw:,}원 | 연령: {trip.age_group}", flush=True)
    print(f" 실행 모드: {args.only or '전체'}", flush=True)
    print(f"{'='*60}", flush=True)

    # 날짜: trip_dict에 있으면 우선 사용, 없으면 --checkin/--checkout 인자, 둘 다 없으면 자동
    checkin  = trip_dict.get("checkin", args.checkin or "")
    checkout = trip_dict.get("checkout", args.checkout or "")

    only = args.only

    dest = trip.destination
    a_path = Path(args.output) / f"{dest}_attractions.json"
    r_path = Path(args.output) / f"{dest}_restaurants.json"
    h_path = Path(args.output) / f"{dest}_hotels.json"

    if only == "attraction":
        run_attraction(trip, args.output)
    elif only == "restaurant":
        run_restaurant(trip, args.output)
    elif only == "hotel":
        run_hotel(trip, args.output, checkin, checkout)
    elif only == "plan":
        run_plan(trip, args.output)
    else:
        # 전체 실행 — 이미 있는 파일은 스킵
        if not a_path.exists():
            run_attraction(trip, args.output)
        else:
            print(f"\n[ 관광지 ] 이미 있음 → 스킵 ({a_path.name})", flush=True)

        if not r_path.exists():
            run_restaurant(trip, args.output)
        else:
            print(f"[ 맛집 ] 이미 있음 → 스킵 ({r_path.name})", flush=True)

        if not h_path.exists():
            run_hotel(trip, args.output, checkin, checkout)
        else:
            print(f"[ 숙소 ] 이미 있음 → 스킵 ({h_path.name})", flush=True)

        run_plan(trip, args.output)


if __name__ == "__main__":
    main()
