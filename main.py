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

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

sys.path.insert(0, str(Path(__file__).parent))

from models.schemas import TripInput
from utils.graph_builder import build_graph, print_graph_summary


def run_attraction(trip: TripInput, output_dir: str):
    from agents.attraction_agent import AttractionBrowsingAgent
    print("\n[ 관광지 브라우징 ]", flush=True)
    agent = AttractionBrowsingAgent(trip, max_places=10, verbose=True)
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


def run_hotel(trip: TripInput, output_dir: str):
    # 숙소 에이전트는 추후 구현
    print("\n[ 숙소 브라우징 ] — 준비 중", flush=True)
    return {}


def main():
    parser = argparse.ArgumentParser(description="여행 브라우징 에이전트")
    parser.add_argument("--input",       default=None,  help="입력 JSON 파일 경로")
    parser.add_argument("--output",      default="output", help="출력 디렉토리")
    parser.add_argument("--only",        default=None,  help="attraction / restaurant / hotel 중 하나만 실행")
    parser.add_argument("--destination", default=None,  help="여행지 (기본값 덮어쓰기)")
    args = parser.parse_args()

    # 입력 로드
    if args.input:
        with open(args.input, encoding="utf-8") as f:
            trip_dict = json.load(f)
    else:
        trip_dict = {
            "destination": "서울",
            "duration_days": 4,
            "travelers": {"count": 2, "age_group": "40s"},
            "budget_krw": 1500000,
            "preferences": {
                "cleanliness": 5, "food": 5, "activity": 3,
                "nature": 4, "culture": 4, "nightlife": 2,
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

    only = args.only

    if only == "attraction":
        run_attraction(trip, args.output)
    elif only == "restaurant":
        run_restaurant(trip, args.output)
    elif only == "hotel":
        run_hotel(trip, args.output)
    else:
        # 전체 실행
        run_attraction(trip, args.output)
        run_restaurant(trip, args.output)


if __name__ == "__main__":
    main()
