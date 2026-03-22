"""
클러스터 리매핑 스크립트
========================
서울.json의 모든 노드를 새로운 클러스터 바운드 + nearest-center fallback으로 재할당.

변경사항:
1. 바운드 확장 — 기존 사각지대 해소
2. "동대문/회기" 클러스터 신설 — 성수 위쪽 사각지대 전담
3. 겹침 영역 → nearest-center 타이브레이크 (기존: dict 순서 의존)
4. fallback: 바운드 밖 → nearest center (5km 이내만), 초과 → "기타" 유지
5. planning_agent.py용 업데이트된 상수도 함께 출력

사용법:
  python remap_clusters.py 서울.json                    # dry-run (변경 내역만 출력)
  python remap_clusters.py 서울.json --apply             # JSON 파일 덮어쓰기
  python remap_clusters.py 서울.json --apply -o out.json # 별도 파일로 저장
"""

import json
import math
import argparse
from collections import Counter, defaultdict


# ──────────────────────────────────────────────
# 새 클러스터 정의 (14개: 기존 13 + 동대문/회기)
# ──────────────────────────────────────────────

CLUSTER_BOUNDS_NEW = {
    "홍대/마포":     {"lat": (37.530, 37.580), "lng": (126.880, 126.950)},
    "신촌/연남":     {"lat": (37.540, 37.610), "lng": (126.920, 126.960)},
    "종로/광화문":   {"lat": (37.550, 37.600), "lng": (126.950, 127.000)},
    "강북/북촌":     {"lat": (37.570, 37.660), "lng": (126.970, 127.020)},
    "용산/서울역":   {"lat": (37.510, 37.570), "lng": (126.950, 126.990)},
    "명동/중구":     {"lat": (37.540, 37.580), "lng": (126.970, 127.010)},
    "이태원/한남":   {"lat": (37.520, 37.560), "lng": (126.970, 127.020)},
    "성수/왕십리":   {"lat": (37.530, 37.580), "lng": (127.020, 127.080)},
    "동대문/회기":   {"lat": (37.570, 37.630), "lng": (127.020, 127.080)},  # 신규
    "강남/서초":     {"lat": (37.440, 37.540), "lng": (126.990, 127.070)},
    "잠실/송파":     {"lat": (37.480, 37.530), "lng": (127.070, 127.130)},
    "여의도/영등포": {"lat": (37.470, 37.550), "lng": (126.880, 126.950)},
    "강서/마곡":     {"lat": (37.480, 37.600), "lng": (126.800, 126.880)},
    "강동/천호":     {"lat": (37.530, 37.570), "lng": (127.090, 127.180)},
}

CLUSTER_CENTERS_NEW = {
    "홍대/마포":     (37.555, 126.922),
    "신촌/연남":     (37.565, 126.935),
    "종로/광화문":   (37.575, 126.978),
    "강북/북촌":     (37.590, 126.985),
    "용산/서울역":   (37.540, 126.970),
    "명동/중구":     (37.560, 126.983),
    "이태원/한남":   (37.540, 126.993),
    "성수/왕십리":   (37.550, 127.050),
    "동대문/회기":   (37.595, 127.050),  # 신규
    "강남/서초":     (37.503, 127.030),
    "잠실/송파":     (37.511, 127.100),
    "여의도/영등포": (37.523, 126.918),
    "강서/마곡":     (37.554, 126.845),
    "강동/천호":     (37.545, 127.145),
}

CLUSTER_CONCEPTS_NEW = {
    "홍대/마포":     "힙한 거리·클럽·인디 문화·쇼핑",
    "신촌/연남":     "감성 카페·연트럴파크·브런치",
    "종로/광화문":   "역사·궁궐·전통시장·한옥",
    "강북/북촌":     "북촌한옥·삼청동·낙산공원",
    "용산/서울역":   "전쟁기념관·서울로7017·남대문",
    "명동/중구":     "쇼핑·화장품·길거리음식·남산",
    "이태원/한남":   "다국적 음식·바·이색 문화",
    "성수/왕십리":   "힙한 카페·공방·뚝섬한강",
    "동대문/회기":   "동대문시장·경희대·경춘선숲길",  # 신규
    "강남/서초":     "코엑스·한강공원·도시 쇼핑",
    "잠실/송파":     "롯데월드·석촌호수·올림픽공원",
    "여의도/영등포": "한강 뷰·IFC몰·벚꽃길",
    "강서/마곡":     "서울식물원·마곡나루",
    "강동/천호":     "암사동유적·고덕천",
}

# 서울 외곽 한계 (이 밖이면 "기타" 확정)
SEOUL_LAT = (37.42, 37.70)
SEOUL_LNG = (126.75, 127.20)
MAX_FALLBACK_KM = 5.0  # nearest center fallback 허용 최대 거리


def _haversine(lat1, lng1, lat2, lng2):
    R = 6371
    a = (math.sin(math.radians(lat2 - lat1) / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(math.radians(lng2 - lng1) / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def assign_cluster_new(lat: float, lng: float) -> str:
    """
    1단계: bounds 매칭 (복수 매칭 시 nearest center로 타이브레이크)
    2단계: bounds 밖 → nearest center (MAX_FALLBACK_KM 이내)
    3단계: 초과 → "기타"
    """
    # 서울 범위 밖이면 즉시 기타
    if not (SEOUL_LAT[0] <= lat <= SEOUL_LAT[1] and SEOUL_LNG[0] <= lng <= SEOUL_LNG[1]):
        return "기타"

    # 1단계: bounds에 매칭되는 클러스터 모두 수집
    matched = []
    for name, b in CLUSTER_BOUNDS_NEW.items():
        if b["lat"][0] <= lat <= b["lat"][1] and b["lng"][0] <= lng <= b["lng"][1]:
            dist = _haversine(lat, lng, *CLUSTER_CENTERS_NEW[name])
            matched.append((name, dist))

    if matched:
        # 가장 가까운 center를 가진 클러스터 선택 (겹침 해소)
        return min(matched, key=lambda x: x[1])[0]

    # 2단계: fallback — nearest center (거리 제한)
    nearest_name, nearest_dist = min(
        CLUSTER_CENTERS_NEW.items(),
        key=lambda x: _haversine(lat, lng, x[1][0], x[1][1])
    )
    nearest_dist_km = _haversine(lat, lng, *CLUSTER_CENTERS_NEW[nearest_name])

    if nearest_dist_km <= MAX_FALLBACK_KM:
        return nearest_name

    return "기타"


def remap(graph_path, output_path=None, apply=False):
    with open(graph_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    nodes = data["nodes"]
    changes = []
    old_dist = Counter()
    new_dist = Counter()

    for pid, node in nodes.items():
        lat, lng = node["lat"], node["lng"]
        old_cl = node.get("cluster", "기타")
        new_cl = assign_cluster_new(lat, lng)

        old_dist[old_cl] += 1
        new_dist[new_cl] += 1

        if old_cl != new_cl:
            changes.append({
                "name": node["name"],
                "category": node["category"],
                "old": old_cl,
                "new": new_cl,
                "lat": lat,
                "lng": lng,
            })
            if apply:
                node["cluster"] = new_cl

    # ── 리포트 출력 ──
    print("=" * 60)
    print("  클러스터 리매핑 리포트")
    print("=" * 60)

    print(f"\n총 노드: {len(nodes)}개")
    print(f"변경된 노드: {len(changes)}개\n")

    # 분포 비교
    all_clusters = sorted(set(list(old_dist.keys()) + list(new_dist.keys())),
                          key=lambda x: new_dist.get(x, 0), reverse=True)
    print(f"{'클러스터':16s} {'기존':>5s} {'변경후':>5s} {'차이':>5s}")
    print("-" * 40)
    for cl in all_clusters:
        o, n = old_dist.get(cl, 0), new_dist.get(cl, 0)
        diff = n - o
        arrow = f"+{diff}" if diff > 0 else str(diff) if diff < 0 else "  0"
        print(f"  {cl:14s} {o:5d} {n:5d} {arrow:>5s}")

    # 변경 상세
    if changes:
        print(f"\n{'변경 상세':=^50}")
        by_new = defaultdict(list)
        for c in changes:
            by_new[c["new"]].append(c)
        for cl in sorted(by_new.keys()):
            items = by_new[cl]
            print(f"\n→ {cl} ({len(items)}개 유입)")
            for c in items:
                print(f"    {c['name']:25s} [{c['category']:10s}] {c['old']:14s} → {cl}")

    # 저장
    if apply:
        out = output_path or graph_path
        with open(out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"\n✅ 저장 완료: {out}")
    else:
        print(f"\n⚠️  dry-run 모드입니다. 실제 적용하려면 --apply 플래그를 추가하세요.")

    # planning_agent.py에 붙여넣을 상수 출력
    print("\n" + "=" * 60)
    print("  planning_agent.py에 교체할 상수 (복사용)")
    print("=" * 60)
    print("\nCLUSTER_BOUNDS = {")
    for name, b in CLUSTER_BOUNDS_NEW.items():
        print(f'    "{name}": {{"lat": {b["lat"]}, "lng": {b["lng"]}}},')
    print("}\n")
    print("CLUSTER_CENTERS = {")
    for name, c in CLUSTER_CENTERS_NEW.items():
        print(f'    "{name}": {c},')
    print("}\n")
    print("CLUSTER_CONCEPTS = {")
    for name, c in CLUSTER_CONCEPTS_NEW.items():
        print(f'    "{name}": "{c}",')
    print("}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="서울.json 클러스터 리매핑")
    parser.add_argument("graph", help="서울.json 경로")
    parser.add_argument("--apply", action="store_true", help="실제 JSON 파일 수정")
    parser.add_argument("-o", "--output", help="출력 파일 경로 (미지정시 원본 덮어쓰기)")
    args = parser.parse_args()
    remap(args.graph, args.output, args.apply)
