"""
쿼리 테스트 스크립트 — subprocess로 query_knowledge_graph.py 직접 호출

실행:
  python run_query_tests.py                       # 전체 16개 시나리오
  python run_query_tests.py --scenario age        # 나이대별 4개
  python run_query_tests.py --scenario style      # scoring_style별 5개
  python run_query_tests.py --scenario budget     # 예산별 4개
  python run_query_tests.py --scenario travelers  # 인원/박수별 4개

결과:
  output/test_summary.txt          — 전체 실행 요약
  output/<id>/서울_itinerary_*.json — 각 시나리오 일정 JSON
"""
import subprocess
import sys
import time
import argparse
from pathlib import Path
from datetime import datetime

OUTPUT_DIR = Path("output")

# ──────────────────────────────────────────────
# 시나리오 정의
# ──────────────────────────────────────────────
SCENARIO_AGE = [
    {"id": "age_20s", "label": "20대 (액티브·야간)",  "age": "20s", "budget": 1_500_000, "days": 4, "travelers": 2, "style": "balanced"},
    {"id": "age_30s", "label": "30대 (균형형)",        "age": "30s", "budget": 1_500_000, "days": 4, "travelers": 2, "style": "balanced"},
    {"id": "age_40s", "label": "40대 (문화·역사)",     "age": "40s", "budget": 1_500_000, "days": 4, "travelers": 2, "style": "balanced"},
    {"id": "age_50s", "label": "50대 (여유·자연)",     "age": "50s", "budget": 1_500_000, "days": 4, "travelers": 2, "style": "balanced"},
]

SCENARIO_STYLE = [
    {"id": "style_balanced",    "label": "30대 balanced    (균형형)",     "age": "30s", "budget": 1_500_000, "days": 4, "travelers": 2, "style": "balanced"},
    {"id": "style_threshold",   "label": "30대 threshold   (최악회피)",   "age": "30s", "budget": 1_500_000, "days": 4, "travelers": 2, "style": "threshold"},
    {"id": "style_peak",        "label": "30대 peak        (극강경험)",   "age": "30s", "budget": 1_500_000, "days": 4, "travelers": 2, "style": "peak"},
    {"id": "style_risk_averse", "label": "30대 risk_averse (검증선호)",   "age": "30s", "budget": 1_500_000, "days": 4, "travelers": 2, "style": "risk_averse"},
    {"id": "style_budget_safe", "label": "30대 budget_safe (예산최우선)", "age": "30s", "budget": 1_500_000, "days": 4, "travelers": 2, "style": "budget_safe"},
]

SCENARIO_BUDGET = [
    {"id": "budget_low",     "label": "저예산  80만원 4박  (1박 ~80k)",   "age": "30s", "budget":   800_000, "days": 4, "travelers": 2, "style": "budget_safe"},
    {"id": "budget_mid",     "label": "중예산 150만원 4박  (1박 ~150k)",  "age": "30s", "budget": 1_500_000, "days": 4, "travelers": 2, "style": "balanced"},
    {"id": "budget_high",    "label": "고예산 300만원 4박  (1박 ~300k)",  "age": "30s", "budget": 3_000_000, "days": 4, "travelers": 2, "style": "balanced"},
    {"id": "budget_premium", "label": "프리미엄 600만원 4박 (1박 ~600k)", "age": "40s", "budget": 6_000_000, "days": 4, "travelers": 2, "style": "peak"},
]

SCENARIO_TRAVELERS = [
    {"id": "trv_solo",   "label": "1인 3박 혼자여행", "age": "30s", "budget": 1_000_000, "days": 3, "travelers": 1, "style": "balanced"},
    {"id": "trv_couple", "label": "2인 4박 커플",      "age": "30s", "budget": 1_500_000, "days": 4, "travelers": 2, "style": "balanced"},
    {"id": "trv_family", "label": "4인 5박 가족여행",  "age": "40s", "budget": 4_000_000, "days": 5, "travelers": 4, "style": "risk_averse"},
    {"id": "trv_group",  "label": "6인 3박 친구단체",  "age": "20s", "budget": 3_000_000, "days": 3, "travelers": 6, "style": "peak"},
]

ALL_SCENARIOS = {
    "age":       SCENARIO_AGE,
    "style":     SCENARIO_STYLE,
    "budget":    SCENARIO_BUDGET,
    "travelers": SCENARIO_TRAVELERS,
}


# ──────────────────────────────────────────────
# 단일 시나리오 실행
# ──────────────────────────────────────────────
def run_one(s: dict) -> dict:
    out_dir = str(OUTPUT_DIR / s["id"])

    cmd = [
        sys.executable,
        "graph_builder/query_knowledge_graph.py",
        "--age",       s["age"],
        "--budget",    str(s["budget"]),
        "--days",      str(s["days"]),
        "--travelers", str(s["travelers"]),
        "--style",     s["style"],
        "--output",    out_dir,
    ]

    print(f"  > python graph_builder/query_knowledge_graph.py"
          f" --age {s['age']} --budget {s['budget']}"
          f" --days {s['days']} --travelers {s['travelers']}"
          f" --style {s['style']} --output {out_dir}")

    t0 = time.time()
    proc = subprocess.run(cmd, text=True, encoding="utf-8")
    elapsed = round(time.time() - t0, 1)

    return {
        "id":         s["id"],
        "label":      s["label"],
        "elapsed":    elapsed,
        "ok":         proc.returncode == 0,
    }


# ──────────────────────────────────────────────
# 요약 출력 + 저장
# ──────────────────────────────────────────────
def print_and_save_summary(all_results: dict):
    lines = [
        "",
        "=" * 65,
        f"  테스트 요약  [{datetime.now().strftime('%Y-%m-%d %H:%M')}]",
        "=" * 65,
    ]
    total_ok = total_fail = 0
    for group, results in all_results.items():
        lines.append(f"\n[{group.upper()}]")
        for r in results:
            mark = "✓" if r["ok"] else "✗ FAIL"
            lines.append(f"  {mark}  {r['label']:42s}  {r['elapsed']}s")
            if r["ok"]: total_ok += 1
            else:        total_fail += 1

    lines += ["", f"  결과: {total_ok}개 성공 / {total_fail}개 실패", "=" * 65]
    summary = "\n".join(lines)
    print(summary)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUT_DIR / "test_summary.txt"
    out.write_text(summary, encoding="utf-8")
    print(f"\n  요약 저장 → {out}")


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default="all",
                        choices=["all", "age", "style", "budget", "travelers"])
    args = parser.parse_args()

    target = ALL_SCENARIOS if args.scenario == "all" else {args.scenario: ALL_SCENARIOS[args.scenario]}

    all_results = {}
    for group_name, scenarios in target.items():
        print(f"\n{'#'*65}")
        print(f"  시나리오 그룹: {group_name.upper()}  ({len(scenarios)}개)")
        print(f"{'#'*65}")

        results = []
        for s in scenarios:
            print(f"\n[{s['label']}]")
            results.append(run_one(s))

        all_results[group_name] = results

    print_and_save_summary(all_results)
