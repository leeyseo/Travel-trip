"""
그래프 빌더
PlaceNode 리스트 → 노드 + 엣지 그래프 구조
엣지 가중치 = 이동편의성 (거리 + 교통편의 복합)
"""

import math
from dataclasses import dataclass, field
from models.schemas import PlaceNode


@dataclass
class Edge:
    source_id: str
    target_id: str
    distance_km: float      # 직선 거리
    transit_score: float    # 0–1 이동 편의성 (엣지 가중치)
    walk_minutes: float     # 예상 도보 시간


@dataclass
class PlaceGraph:
    nodes: list[PlaceNode] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    category: str = "attraction"  # "attraction" | "hotel" | "restaurant"

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.__dict__ for e in self.edges],
        }


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """위경도 직선거리 (km)"""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat/2)**2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlng/2)**2)
    return R * 2 * math.asin(math.sqrt(a))


def compute_transit_score(
    node_a: PlaceNode,
    node_b: PlaceNode,
    distance_km: float,
) -> float:
    """
    이동 편의성 점수 (0–1, 높을수록 이동하기 편함)
    = 거리 패널티 × 양쪽 교통 접근성 평균
    """
    # 거리 패널티: 2km 이하 = 거의 패널티 없음, 10km 이상 = 0.2
    dist_factor = max(0.2, 1.0 - (distance_km / 12))

    # 양쪽 transit_access 평균 (0–5 → 0–1)
    ta = (node_a.features.transit_access + node_b.features.transit_access) / 2
    transit_factor = ta / 5.0

    return round(dist_factor * transit_factor, 4)


def build_graph(
    nodes: list[PlaceNode],
    category: str = "attraction",
    max_edge_km: float = 15.0,   # 이 거리 이상은 엣지 생성 안 함
    top_k_edges: int = 3,        # 각 노드당 가장 가까운 k개만 연결
) -> PlaceGraph:
    """
    노드 리스트 → PlaceGraph
    모든 노드 쌍에 대해 거리 계산 후 가까운 것끼리만 엣지 생성
    """
    graph = PlaceGraph(nodes=nodes, category=category)

    for i, na in enumerate(nodes):
        # 이 노드와 다른 모든 노드의 거리 계산
        neighbors = []
        for j, nb in enumerate(nodes):
            if i >= j:
                continue
            dist = haversine_km(
                na.features.lat, na.features.lng,
                nb.features.lat, nb.features.lng,
            )
            if dist <= max_edge_km:
                neighbors.append((dist, nb))

        # 거리 가까운 순으로 top_k만 엣지 생성
        neighbors.sort(key=lambda x: x[0])
        for dist, nb in neighbors[:top_k_edges]:
            transit = compute_transit_score(na, nb, dist)
            walk_min = round(dist * 12, 1)  # 도보 4km/h 기준 분

            edge = Edge(
                source_id=na.place_id,
                target_id=nb.place_id,
                distance_km=round(dist, 3),
                transit_score=transit,
                walk_minutes=walk_min,
            )
            graph.edges.append(edge)

    return graph


_printed_summaries = set()

def print_graph_summary(graph: PlaceGraph):
    # 동일 그래프 중복 출력 방지
    key = f"{graph.category}_{len(graph.nodes)}_{len(graph.edges)}"
    if key in _printed_summaries:
        return
    _printed_summaries.add(key)
    print(f"\n{'='*50}", flush=True)
    print(f"그래프 요약 [{graph.category}]", flush=True)
    print(f"  노드: {len(graph.nodes)}개", flush=True)
    print(f"  엣지: {len(graph.edges)}개", flush=True)
    print(f"\n노드 목록 (점수 순):", flush=True)
    for n in sorted(graph.nodes, key=lambda x: x.node_score, reverse=True):
        print(f"  {n.name:20s}  score={n.node_score:.3f}  "
              f"({n.features.lat:.4f}, {n.features.lng:.4f})", flush=True)
    print(f"\n엣지 목록 (transit_score 순):", flush=True)
    id_to_name = {n.place_id: n.name for n in graph.nodes}
    for e in sorted(graph.edges, key=lambda x: x.transit_score, reverse=True):
        a = id_to_name.get(e.source_id, e.source_id)
        b = id_to_name.get(e.target_id, e.target_id)
        print(f"  {a:18s} ↔ {b:18s}  "
              f"dist={e.distance_km:.1f}km  transit={e.transit_score:.3f}  "
              f"walk={e.walk_minutes:.0f}min", flush=True)
