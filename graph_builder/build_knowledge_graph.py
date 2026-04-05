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

def assign_cluster(lat: float, lng: float, graph: dict = None) -> str:
    """그래프에 clusters가 있으면 nearest centroid, 없으면 '기타'."""
    if graph and graph.get("clusters"):
        clusters = graph["clusters"]
        import math
        def _hav(la1, ln1, la2, ln2):
            R = 6371
            a = math.sin(math.radians(la2-la1)/2)**2 + \
                math.cos(math.radians(la1))*math.cos(math.radians(la2))*math.sin(math.radians(ln2-ln1)/2)**2
            return R * 2 * math.asin(math.sqrt(a))
        return min(clusters.keys(), key=lambda n: _hav(lat, lng, *clusters[n]["center"]))
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


def upsert_node(graph: dict, node: dict, score: float) -> str:
    lat = node.get("lat", 0)
    lng = node.get("lng", 0)
    category = node.get("category", "")

    node["cluster"] = assign_cluster(lat, lng, graph)

    pid = node["place_id"]
    nearby_pid = _find_nearby_node(graph, lat, lng, category, threshold_m=200)
    if nearby_pid and nearby_pid != pid:
        pid = nearby_pid
        existing_name = graph["nodes"][pid]["name"]
        if node["name"] not in existing_name:
            graph["nodes"][pid].setdefault("aliases", [])
            if node["name"] not in graph["nodes"][pid]["aliases"]:
                graph["nodes"][pid]["aliases"].append(node["name"])

    if pid not in graph["nodes"]:
        new_node = dict(node)
        new_node["meta"] = {
            "pull_count": 1,
            "first_seen": datetime.now().strftime("%Y-%m-%d"),
            "last_updated": datetime.now().strftime("%Y-%m-%d"),
        }
        graph["nodes"][pid] = new_node
        return "new"
    else:
        existing = graph["nodes"][pid]
        existing["cluster"] = node["cluster"]
        meta = existing.setdefault("meta", {"pull_count": 0})
        meta["pull_count"] = meta.get("pull_count", 0) + 1
        meta["last_updated"] = datetime.now().strftime("%Y-%m-%d")
        existing["features"] = node.get("features", existing.get("features", {}))
        existing["node_score"] = score
        return "updated"

# ──────────────────────────────────────────────
# 단일 나이대 빌드
# ──────────────────────────────────────────────
def build_age(destination: str, age_group: str, graph_path: Path,
              force: bool = False, only: str = None, target_area: str = ""):
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

    print(f"\n[빌드] {destination} 수집 시작")

    stats = {"new": 0, "updated": 0}
    counts = {}

    # 카테고리별 기존 노드명 셋 — 에이전트가 이미 있는 장소 스킵용
    def _existing_names(cat: str) -> set[str]:
        return {n["name"] for n in graph["nodes"].values() if n.get("category") == cat}

    if not only or only == "attraction":
        from agents.attraction_agent import AttractionBrowsingAgent
        a_agent = AttractionBrowsingAgent(trip, verbose=True, target_area=target_area)
        attractions = a_agent.run()
        counts["attraction"] = len(attractions)
        print(f"  → 관광지 {len(attractions)}개 수집\n")
        for node in attractions:
            _save_node("attraction", node)

    def _save_node(category: str, node):
        d = node.to_dict(); d["category"] = category
        result = upsert_node(graph, d, node.node_score)
        stats[result] += 1
        save_graph(graph, graph_path)

    if not only or only == "restaurant":
        from agents.restaurant_agent import RestaurantBrowsingAgent
        r_agent = RestaurantBrowsingAgent(
            trip, verbose=True,
            existing_names=_existing_names("restaurant"),
            on_node_found=lambda n: _save_node("restaurant", n),
            target_area=target_area,
        )
        restaurants = r_agent.run()
        counts["restaurant"] = len(restaurants)
        print(f"  → 맛집/카페 {len(restaurants)}개 수집\n")

    if not only or only == "hotel":
        from agents.hotel_agent import HotelBrowsingAgent
        h_agent = HotelBrowsingAgent(
            trip, verbose=True,
            existing_names=_existing_names("hotel"),
            on_node_found=lambda n: _save_node("hotel", n),
            target_area=target_area,
        )
        hotels = h_agent.run()
        counts["hotel"] = len(hotels)
        print(f"  → 호텔 {len(hotels)}개 수집\n")

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
# 카카오 API로 그래프 주소 보강
# ──────────────────────────────────────────────
def enrich_with_kakao(graph_path: Path):
    """
    기존 그래프의 모든 노드에 대해 카카오맵 API를 호출하여
    정확한 이름(name), 도로명 주소(address), 좌표, 카테고리를 보강.
    검색 시 name_ko(이전 보강값) → name 순서로 시도해 재실행 안전성 보장.
    카카오에서 조회 실패한 노드는 그래프에서 제거.

    실행:
      python graph_builder/build_knowledge_graph.py --enrich
      python graph_builder/build_knowledge_graph.py --enrich --destination 서울
    """
    import time
    from utils.feature_extractor import get_precise_geo_info

    graph = load_graph(graph_path)
    nodes = graph["nodes"]
    total = len(nodes)

    if total == 0:
        print("  → 노드가 없습니다.")
        return

    print(f"\n{'='*60}")
    print(f"  카카오 주소 보강 시작: {total}개 노드")
    print(f"{'='*60}")

    destination = graph.get("destination", "서울")
    updated = 0
    to_remove = []

    for i, (pid, node) in enumerate(nodes.items(), 1):
        # name_ko가 있으면 그걸로 검색 (재실행 시 영어 name으로 검색하는 문제 방지)
        search_name = node.get("name_ko") or node.get("name", "")
        print(f"  [{i:3d}/{total}] {search_name}", end="  ", flush=True)

        geo_info = get_precise_geo_info(search_name, destination)
        if geo_info:
            node["name"]           = geo_info["official_name"]
            node["address"]        = geo_info["address"]
            node["lat"]            = geo_info["lat"]
            node["lng"]            = geo_info["lng"]
            node["cluster"]        = assign_cluster(geo_info["lat"], geo_info["lng"], graph)
            node["kakao_category"] = geo_info.get("kakao_category", "")
            if "features" in node:
                node["features"]["lat"] = geo_info["lat"]
                node["features"]["lng"] = geo_info["lng"]
            print(f"→ {geo_info['official_name']}  {geo_info['address']}  ({geo_info.get('kakao_category', '')})")
            updated += 1
        else:
            print("→ [조회 실패, 제거]")
            to_remove.append(pid)

        time.sleep(0.1)

    for pid in to_remove:
        del graph["nodes"][pid]

    save_graph(graph, graph_path)

    print(f"\n{'='*60}")
    print(f"  보강 완료: 성공 {updated}개 / 제거 {len(to_remove)}개")
    print(f"  남은 노드: {len(graph['nodes'])}개")
    print(f"  저장: {graph_path}")
    print(f"{'='*60}")


# ──────────────────────────────────────────────
# 임베딩 생성 및 저장
# ──────────────────────────────────────────────
def _node_concept_text(node: dict) -> str:
    """노드 하나의 특성을 임베딩용 텍스트로 변환. concept_en이 있으면 영어 우선."""
    if node.get("concept_en"):
        return node["concept_en"]

    import re as _re
    f   = node.get("features", {})
    cat = node.get("kakao_category", "")
    parts = [node.get("name", "")]

    if node.get("address"):
        parts.append(node["address"])

    # kakao_category 원문 포함 + 토큰 분해하여 단어별 추가
    # "여행 > 관광,명소 > 고궁" → ["여행", "관광", "명소", "고궁"] 개별 단어도 포함
    if cat:
        parts.append(cat)
        tokens = [t.strip() for t in _re.split(r"[>,\s·]+", cat) if len(t.strip()) >= 2]
        if tokens:
            parts.append(" ".join(tokens))

    # 수치 피처 → 서술형 키워드 (임베딩 모델이 연결하기 쉬운 자연어)
    tags = []
    category_type = node.get("category", "")

    if category_type == "attraction":
        if f.get("culture_depth", 0)    >= 3.5: tags.append("역사 문화 유적 고궁 궁궐 사찰")
        if f.get("nature_score", 0)     >= 3.5: tags.append("자연 공원 녹지 산 등산 트래킹 숲")
        if f.get("nightlife_score", 0)  >= 3.0: tags.append("야경 나이트라이프 바 클럽")
        if f.get("activity_level", 0)   >= 3.5: tags.append("체험 액티비티 스포츠 레저")
        if f.get("photo_worthiness", 0) >= 4.0: tags.append("감성 포토스팟 인스타 뷰맛집")

    elif category_type == "restaurant":
        cuisine = f.get("cuisine_type", "")
        meal_type = f.get("meal_type", "")
        ambiance = f.get("ambiance_score", 0)
        taste = f.get("taste_score", 0)
        price = f.get("avg_price_per_person", 0)
        michelin = f.get("michelin_tier", "없음")
        name_lower = node.get("name", "").lower()

        if cuisine:
            tags.append(cuisine)

        # 이름 기반 직접 태그 (LLM 평가보다 이름이 더 신뢰할 수 있음)
        CASUAL_KEYWORDS = ["포장마차", "국수", "분식", "국밥", "순대", "떡볶이",
                           "김밥", "라면", "순댓국", "해장", "24시", "야식"]
        NIGHTLIFE_KEYWORDS = ["클럽", "바", "펍", "pub", "bar", "club", "와인",
                              "칵테일", "위스키", "루프탑", "테라스", "라운지"]
        CAFE_KEYWORDS = ["카페", "cafe", "커피", "coffee", "브런치", "디저트", "베이커리"]

        is_casual = any(kw in name_lower for kw in CASUAL_KEYWORDS)
        is_nightlife = any(kw in name_lower for kw in NIGHTLIFE_KEYWORDS)
        is_cafe = any(kw in name_lower for kw in CAFE_KEYWORDS)

        if is_casual:
            tags.append("로컬 서민 대중적 포장마차 국수 분식 저렴 가성비")
        if is_nightlife:
            tags.append("바 펍 와인바 클럽 나이트라이프 야간 야경 칵테일")
        if is_cafe:
            tags.append("카페 커피 디저트 브런치 감성")

        # 식사 시간대 태그
        meal_tags = {
            "아침": "아침식사 브런치",
            "점심": "점심 런치",
            "저녁": "저녁 디너",
            "카페": "카페 디저트 커피 브런치",
            "야식": "야식 야간 늦은밤",
            "전체": "식사",
        }
        if meal_type in meal_tags:
            tags.append(meal_tags[meal_type])

        # 분위기 태그 (이름 기반 태그가 없을 때만 적용)
        if not is_casual and not is_nightlife:
            if ambiance >= 4.0:
                tags.append("감성 분위기 좋은 인테리어 데이트")
            if ambiance <= 2.0:
                tags.append("로컬 허름한 대중적 서민")

        # 가격대 태그
        if price >= 80000:
            tags.append("파인다이닝 고급 레스토랑 오마카세")
        elif price >= 30000:
            tags.append("분위기 있는 레스토랑 중고급")
        elif price <= 10000:
            tags.append("가성비 저렴 서민 대중식")

        if michelin != "없음":
            tags.append(f"미슐랭 {michelin}")
        if taste >= 4.0:
            tags.append("맛집 맛있는 맛")

        # 나이트라이프 성격 식당 (피처 기반)
        if f.get("nightlife_score", 0) and f.get("nightlife_score", 0) >= 3.0:
            tags.append("바 펍 와인바 칵테일 나이트라이프 야간")

    elif category_type == "hotel":
        star = f.get("star_grade", 0)
        if star >= 4.0:
            tags.append("고급 럭셔리 호텔 특급")
        elif star <= 2.0:
            tags.append("게스트하우스 저렴 가성비 숙박")

    if tags:
        parts.append(" ".join(tags))
    if node.get("cluster"):
        parts.append(node["cluster"])

    return " | ".join(p for p in parts if p)


def cluster_graph(graph_path: Path, n_clusters: int = None):
    """
    K-Means로 노드 좌표 클러스터링 → Claude API로 이름/컨셉/랜드마크/키워드 자동 생성.
    빌드 → enrich → cluster → embed 순서로 1회 실행.

    실행:
      python graph_builder/build_knowledge_graph.py --cluster
      python graph_builder/build_knowledge_graph.py --cluster --n-clusters 10
    """
    import numpy as np
    from sklearn.cluster import KMeans
    from collections import defaultdict
    import anthropic, os

    graph = load_graph(graph_path)
    nodes = graph["nodes"]
    destination = graph.get("destination", "")

    # 좌표가 있는 노드만
    valid = [(pid, node) for pid, node in nodes.items()
             if node.get("lat") and node.get("lng")]
    if not valid:
        print("  → 좌표 있는 노드가 없습니다.")
        return

    k = n_clusters or max(5, min(15, len(valid) // 10))
    print(f"\n{'='*60}")
    print(f"  클러스터링 시작: {len(valid)}개 노드 → {k}개 클러스터")
    print(f"{'='*60}")

    X = np.array([[n["lat"], n["lng"]] for _, n in valid])
    km = KMeans(n_clusters=k, random_state=42, n_init=10)
    labels = km.fit_predict(X)

    # 클러스터별 노드 수집
    cluster_nodes: dict[int, list] = defaultdict(list)
    for (pid, node), label in zip(valid, labels):
        cluster_nodes[label].append(node)

    # Claude API로 이름/컨셉/랜드마크/키워드 생성
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("  [WARN] ANTHROPIC_API_KEY 없음 → 클러스터 번호로 대체")

    client = anthropic.Anthropic(api_key=api_key) if api_key else None

    clusters: dict[str, dict] = {}
    label_to_name: dict[int, str] = {}

    for label in range(k):
        center = km.cluster_centers_[label].tolist()
        members = cluster_nodes[label]
        top_nodes = sorted(members, key=lambda x: x.get("node_score", 0), reverse=True)[:12]
        node_info = "\n".join(
            f"- {n['name']} ({n.get('kakao_category', '')}, score={n.get('node_score', 0):.2f})"
            for n in top_nodes
        )

        if client:
            prompt = (
                f"다음은 {destination}의 한 지역 클러스터에 속한 장소들입니다:\n{node_info}\n\n"
                f"이 장소들을 바탕으로 아래 JSON만 출력하세요 (설명 없이):\n"
                f'{{"name":"지역명 (예: 홍대/마포)","concept":"한 줄 컨셉 (예: 힙한 카페·클럽·인디문화)",'
                f'"landmarks":["대표장소1","대표장소2","대표장소3"],'
                f'"keywords":["여행자가 이 지역을 찾을 때 실제로 입력할 법한 검색 키워드 20개 이상.'
                f' 장소명, 활동(등산/트래킹/쇼핑/카페투어), 분위기(힙한/조용한/로맨틱), '
                f'동반자(아이/가족/커플/혼자), 테마(역사/자연/맛집/야경/테마파크) 등 다양하게 포함"]}}'
            )
            try:
                resp = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=600,
                    messages=[{"role": "user", "content": prompt}]
                )
                import re as _re
                raw = resp.content[0].text.strip()
                # JSON 블록만 추출
                m = _re.search(r'\{.*\}', raw, _re.DOTALL)
                meta = json.loads(m.group()) if m else {}
            except Exception as e:
                print(f"  [WARN] Claude API 오류 ({e}) → fallback")
                meta = {}
        else:
            meta = {}

        name = meta.get("name") or f"클러스터{label+1}"
        # 이름 중복 방지
        base = name
        suffix = 1
        while name in clusters:
            name = f"{base}_{suffix}"; suffix += 1

        clusters[name] = {
            "center": [round(center[0], 6), round(center[1], 6)],
            "concept": meta.get("concept", ""),
            "landmarks": meta.get("landmarks", [n["name"] for n in top_nodes[:3]]),
            "keywords": meta.get("keywords", []),
        }
        label_to_name[label] = name
        print(f"  [{name}] {meta.get('concept','')} ({len(members)}개 노드)")

    # 모든 노드에 클러스터 이름 재태깅
    for (pid, node), label in zip(valid, labels):
        node["cluster"] = label_to_name[label]

    graph["clusters"] = clusters
    save_graph(graph, graph_path)

    print(f"\n  → clusters 저장 완료: {k}개 / {graph_path}")
    print(f"{'='*60}")


def embed_graph(graph_path: Path):
    """
    그래프의 모든 노드 + 클러스터에 대해 임베딩을 계산하고 JSON에 저장.
    빌드 → enrich → cluster → embed 순서로 1회 실행.

    실행:
      python graph_builder/build_knowledge_graph.py --embed
      python graph_builder/build_knowledge_graph.py --embed --destination 서울
    """
    from collections import defaultdict
    from utils.embedder import embed_batch

    graph = load_graph(graph_path)
    nodes = graph["nodes"]
    total = len(nodes)

    if total == 0:
        print("  → 노드가 없습니다.")
        return

    print(f"\n{'='*60}")
    print(f"  임베딩 생성 시작: {total}개 노드")
    print(f"  (첫 실행 시 모델 다운로드 ~420MB)")
    print(f"{'='*60}")

    # ── 1. 노드 임베딩 (배치) ──
    pids  = list(nodes.keys())
    texts = [_node_concept_text(nodes[pid]) for pid in pids]

    print(f"  노드 텍스트 생성 완료. 임베딩 중...")
    vecs = embed_batch(texts)

    for pid, vec in zip(pids, vecs):
        nodes[pid]["embedding"] = vec

    print(f"  → 노드 임베딩 {len(pids)}개 완료")

    # ── 2. 클러스터 임베딩 (클러스터별 노드 집계 텍스트) ──
    # planning_agent의 _cluster_concept_text 로직을 여기서도 사용
    # (실제 쿼리 타임 매칭은 노드 임베딩 직접 비교로 처리됨 — 이건 fallback용)
    import re
    ca: dict = defaultdict(list)
    cr: dict = defaultdict(list)
    for node in nodes.values():
        cluster = node.get("cluster", "기타")
        if cluster == "기타":
            continue
        if node.get("category") == "attraction":
            ca[cluster].append(node)
        elif node.get("category") == "restaurant":
            cr[cluster].append(node)

    from agents.planning_agent import _cluster_concept_text
    cluster_names = list(set(ca.keys()) | set(cr.keys()))
    cluster_texts_map = {name: _cluster_concept_text(name, dict(ca), dict(cr)) for name in cluster_names}

    print(f"  클러스터 임베딩 중... ({len(cluster_names)}개 클러스터)")
    cluster_vecs = embed_batch(list(cluster_texts_map.values()))

    graph["cluster_embeddings"] = {
        name: vec for name, vec in zip(cluster_texts_map.keys(), cluster_vecs)
    }

    # ── 3. 클러스터 키워드 추출 (keyword boost용) ──
    # Claude 생성 키워드 + 노드 이름 토큰화 → 장소명 직접 언급 시 부스트
    ca: dict = defaultdict(list)
    cr: dict = defaultdict(list)
    for node in nodes.values():
        cluster = node.get("cluster", "기타")
        if cluster == "기타":
            continue
        if node.get("category") == "attraction":
            ca[cluster].append(node)
        elif node.get("category") == "restaurant":
            cr[cluster].append(node)

    _STOP_KO = {"지역", "여행지", "주요", "관광지", "카테고리", "맛집", "다양한", "등", "및",
                "음식점", "여행", "관광", "명소", "서비스", "산업", "부동산", "가정", "생활"}
    _STOP_EN = {"the", "a", "an", "and", "or", "in", "at", "of", "for", "with",
                "is", "are", "to", "from", "by", "on", "as", "it", "its"}

    def _extract_keywords(cluster_name: str) -> list[str]:
        # Claude 생성 키워드 (한국어)
        claude_kws = graph.get("clusters", {}).get(cluster_name, {}).get("keywords", [])
        all_nodes = ca.get(cluster_name, []) + cr.get(cluster_name, [])
        tokens = []
        for n in all_nodes:
            # 한국어: 장소명 토큰
            for part in re.split(r"[\s\(\)\[\]·]+", n["name"]):
                if len(part.strip()) >= 2:
                    tokens.append(part.strip())
            # 한국어: kakao_category 토큰
            for part in re.split(r"[>,\s·,]+", n.get("kakao_category", "")):
                if len(part.strip()) >= 2:
                    tokens.append(part.strip())
            # 영어: name_en 토큰 (keyword boost 영어 매칭용)
            for part in re.split(r"[\s\-,]+", n.get("name_en", "")):
                if len(part.strip()) >= 3 and part.strip().lower() not in _STOP_EN:
                    tokens.append(part.strip().lower())
            # 영어: concept_en 토큰
            for part in re.split(r"[\s\-,]+", n.get("concept_en", "")):
                if len(part.strip()) >= 4 and part.strip().lower() not in _STOP_EN:
                    tokens.append(part.strip().lower())
            # 영어: category_en
            cat_en = n.get("category_en", "")
            if cat_en:
                tokens.append(cat_en.lower())

        combined = list(dict.fromkeys(claude_kws + tokens))
        return [kw for kw in combined if kw not in _STOP_KO]

    graph["cluster_keywords"] = {
        name: _extract_keywords(name) for name in cluster_names
    }
    print(f"  → 클러스터 임베딩 {len(cluster_names)}개 + 키워드 추출 완료")

    save_graph(graph, graph_path)

    print(f"\n{'='*60}")
    print(f"  임베딩 저장 완료")
    print(f"  노드: {len(pids)}개 / 클러스터: {len(cluster_names)}개")
    print(f"  저장: {graph_path}")
    print(f"{'='*60}")


# ──────────────────────────────────────────────
# 영어 컨셉 텍스트 생성 (임베딩 품질 향상용)
# ──────────────────────────────────────────────
def translate_graph(graph_path: Path):
    """
    각 노드에 영어 필드 추가:
      - name_en: 영어 장소명
      - address_en: 영어 주소 (구/동 수준)
      - category_en: 영어 카테고리
      - concept_en: 임베딩용 영어 컨셉 텍스트 (vibe + type + atmosphere)

    --embed 전에 1회 실행. 이후 임베딩은 concept_en 기준으로 계산됨.

    실행:
      python graph_builder/build_knowledge_graph.py --translate
    """
    import anthropic, time

    client = anthropic.Anthropic()
    graph = load_graph(graph_path)
    nodes = graph["nodes"]

    # concept_en 없는 노드만 처리 (재실행 안전)
    targets = [pid for pid, n in nodes.items() if not n.get("concept_en")]
    total = len(targets)

    if total == 0:
        print("  → 모든 노드에 영어 필드가 이미 있습니다.")
        return

    print(f"\n{'='*60}")
    print(f"  영어 필드 생성 시작: {total}개 노드 (배치 10개씩)")
    print(f"  생성 항목: name_en / address_en / category_en / concept_en")
    print(f"{'='*60}")

    BATCH = 10
    done = 0

    for i in range(0, total, BATCH):
        batch_pids = targets[i:i+BATCH]
        batch_nodes = [nodes[pid] for pid in batch_pids]

        items = []
        for idx, n in enumerate(batch_nodes):
            f = n.get("features", {})
            items.append(f"""[{idx}]
name: {n.get("name", "")}
address: {n.get("address", "")}
category: {n.get("category", "")}
kakao_category: {n.get("kakao_category", "")}
cuisine_type: {f.get("cuisine_type", "")}
meal_type: {f.get("meal_type", "")}
ambiance_score: {f.get("ambiance_score", "")}
avg_price_per_person: {f.get("avg_price_per_person", "")}
culture_depth: {f.get("culture_depth", "")}
nature_score: {f.get("nature_score", "")}
nightlife_score: {f.get("nightlife_score", "")}
activity_level: {f.get("activity_level", "")}
photo_worthiness: {f.get("photo_worthiness", "")}
michelin_tier: {f.get("michelin_tier", "")}
star_grade: {f.get("star_grade", "")}
price_per_night: {f.get("price_per_night", "")}""")

        prompt = f"""For each Korean place, generate 4 English fields.

Places:
{"".join(items)}

For each place output a JSON object with:
- "name_en": English name or romanized name (e.g. "Gyeongbokgung Palace", "Hongdae Street", "Club NB2")
- "address_en": English address at district level (e.g. "Jongno-gu, Seoul", "Mapo-gu, Seoul")
- "category_en": English category (e.g. "historic palace", "street food", "nightclub", "luxury hotel", "cafe", "nature park")
- "concept_en": 10-15 word English phrase capturing vibe + type + atmosphere for semantic search
  - attraction: "historic royal palace traditional Korean culture photography"
  - restaurant (casual/cheap): "street food stall cheap noodles local casual budget"
  - restaurant (nightlife): "nightclub bar cocktails late night dancing young crowd"
  - restaurant (fine dining): "fine dining French cuisine elegant romantic upscale"
  - cafe: "trendy cafe specialty coffee dessert instagrammable"
  - hotel (luxury): "luxury five-star hotel business amenities rooftop"
  - hotel (budget): "budget guesthouse affordable clean backpacker"

Output JSON array of objects, same order as input. JSON only:
[{{"name_en":"...","address_en":"...","category_en":"...","concept_en":"..."}}, ...]"""

        try:
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1200,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text.strip().replace("```json", "").replace("```", "").strip()
            s, e = raw.find("["), raw.rfind("]")
            results = json.loads(raw[s:e+1])

            for pid, eng in zip(batch_pids, results):
                nodes[pid]["name_en"]     = eng.get("name_en", "").strip()
                nodes[pid]["address_en"]  = eng.get("address_en", "").strip()
                nodes[pid]["category_en"] = eng.get("category_en", "").strip()
                nodes[pid]["concept_en"]  = eng.get("concept_en", "").strip()
                done += 1

            print(f"  [{done}/{total}] 완료")
            save_graph(graph, graph_path)
            time.sleep(0.3)

        except Exception as ex:
            print(f"  [WARN] 배치 {i//BATCH+1} 실패: {ex}")
            continue

    print(f"\n  → 영어 필드 생성 완료: {done}/{total}개")
    print(f"  저장: {graph_path}")


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
    parser.add_argument("--enrich", action="store_true",
                        help="기존 그래프 노드에 카카오 주소 보강 (빌드 없이 단독 실행 가능)")
    parser.add_argument("--cluster", action="store_true",
                        help="K-Means 클러스터링 + Claude로 이름/컨셉/키워드 자동 생성 (enrich 완료 후 실행)")
    parser.add_argument("--n-clusters", type=int, default=None,
                        help="클러스터 수 (기본: 노드수//10, 최소5 최대15)")
    parser.add_argument("--embed", action="store_true",
                        help="노드·클러스터 임베딩 계산 후 그래프에 저장 (cluster 완료 후 1회 실행)")
    parser.add_argument("--translate", action="store_true",
                        help="Claude로 각 노드의 영어 컨셉 텍스트(concept_en) 생성 (embed 전에 1회 실행)")
    parser.add_argument("--target-area", default="",
                        help="특정 지역 타겟 수집 (예: '강남 가로수길 청담', '잠실 송파 나이트라이프')")
    args = parser.parse_args()

    graph_path = Path(args.graph_dir) / f"{args.destination}.json"

    if args.enrich:
        enrich_with_kakao(graph_path)
        return

    if args.cluster:
        cluster_graph(graph_path, n_clusters=args.n_clusters)
        return

    if args.translate:
        translate_graph(graph_path)
        return

    if args.embed:
        embed_graph(graph_path)
        return

    # --only 모드(restaurant/hotel 보강)는 나이대 무관 → "30s" 한 번만 실행
    if args.only in ("restaurant", "hotel"):
        build_age(args.destination, args.age or "30s", graph_path,
                  force=args.force, only=args.only, target_area=args.target_area)
    else:
        age_groups = [args.age] if args.age else list(AGE_PROFILES.keys())
        for age_group in age_groups:
            build_age(args.destination, age_group, graph_path,
                      force=args.force, only=args.only, target_area=args.target_area)

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
