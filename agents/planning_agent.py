"""
플래닝 에이전트 v6

v5 → v6 변경사항:
- LLM 호출 완전 제거 (순수 룰 기반)
- 클러스터별 랜드마크 필수 포함 (CLUSTER_LANDMARKS)
- 동선 기반 맛집 선택 (prev→식당→next detour 최소화)
- 시간 기반 타임라인 (관광지 소요시간 반영, 카페 14:30~17:30 자동 삽입)
"""

import json
import math
from dataclasses import dataclass, field
from typing import Optional
from collections import defaultdict


# ──────────────────────────────────────────────
# 클러스터 정의 (14개)
# ──────────────────────────────────────────────
# ──────────────────────────────────────────────
# 런타임 클러스터 레지스트리 (그래프 로드 시 채워짐)
# ──────────────────────────────────────────────
_clusters: dict = {}  # name → {center, concept, landmarks, keywords}

_bbox_cache: dict | None = None  # {min_lat, max_lat, min_lng, max_lng}

def _load_clusters(clusters_dict: dict):
    """PlanningAgent.__init__에서 그래프의 clusters를 등록."""
    global _bbox_cache
    _clusters.clear()
    _clusters.update(clusters_dict)
    _bbox_cache = None  # 클러스터 변경 시 캐시 무효화

def _cluster_center(name: str) -> tuple | None:
    c = _clusters.get(name, {}).get("center")
    return tuple(c) if c else None

def _cluster_concept(name: str) -> str:
    return _clusters.get(name, {}).get("concept", "")

def _cluster_landmarks(name: str) -> list:
    return _clusters.get(name, {}).get("landmarks", [])

def _cluster_keywords(name: str) -> list:
    return _clusters.get(name, {}).get("keywords", [])

def _assign_cluster(lat: float, lng: float) -> str:
    """nearest centroid 매핑. 클러스터 미로드 시 '기타'."""
    if not _clusters:
        return "기타"
    return min(_clusters.keys(),
               key=lambda n: _haversine(lat, lng, *_clusters[n]["center"]))


# ──────────────────────────────────────────────
# 유틸
# ──────────────────────────────────────────────
def _haversine(lat1, lng1, lat2, lng2):
    R = 6371
    a = math.sin(math.radians(lat2 - lat1) / 2) ** 2 + \
        math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * \
        math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _get_cluster(node):
    stored = node.get("cluster", "")
    # _clusters 로드된 경우: centroid 기반 재배정 (가장 정확)
    if _clusters:
        if stored and stored in _clusters:
            return stored
        return _assign_cluster(node["lat"], node["lng"])
    # _clusters 미로드 (--cluster 미실행): 노드에 저장된 값 그대로 사용
    return stored if stored and stored != "기타" else "기타"


# ──────────────────────────────────────────────
# 데이터 구조
# ──────────────────────────────────────────────
@dataclass
class ScheduleItem:
    time: str
    type: str
    meal_type: str
    node: dict
    duration_hr: float

@dataclass
class DayPlan:
    day: int
    date: str
    hotel: dict
    cluster_name: str = ""
    schedule: list = field(default_factory=list)
    notes: str = ""

    def to_dict(self):
        morning, afternoon, breakfast, lunch, dinner, cafes, nightlife = [], [], None, None, None, [], None
        for item in self.schedule:
            if item.type == "meal":
                if item.meal_type == "breakfast": breakfast = item.node
                elif item.meal_type == "lunch": lunch = item.node
                elif item.meal_type == "dinner": dinner = item.node
            elif item.type == "cafe":
                cafes.append(item.node)
            elif item.type == "attraction":
                (morning if int(item.time.split(":")[0]) < 14 else afternoon).append(item.node)
            elif item.type == "nightlife":
                nightlife = item.node
        h = self.hotel or {}
        hf = h.get("features", {}) or {}
        return {
            "day": self.day, "date": self.date, "cluster": self.cluster_name,
            "hotel": {"name": h.get("name", ""),
                      "price_per_night": hf.get("price_per_night") or 0,
                      "transit": hf.get("transit_access", ""),
                      "booking_url": hf.get("booking_url", ""),
                      "lat": h.get("lat", 0), "lng": h.get("lng", 0)},
            "morning": [_slim(a) for a in morning],
            "afternoon": [_slim(a) for a in afternoon],
            "breakfast": _slim(breakfast) if breakfast else None,
            "lunch": _slim(lunch) if lunch else None,
            "dinner": _slim(dinner) if dinner else None,
            "cafes": [_slim(c) for c in cafes],
            "nightlife": _slim(nightlife) if nightlife else None,
            "timeline": [{"time": s.time, "type": s.type, "meal_type": s.meal_type, **_slim(s.node)} for s in self.schedule],
            "notes": self.notes,
        }

def _slim(node):
    if not node: return {}
    f = node.get("features", {})
    return {"name": node["name"], "score": node["node_score"],
            "lat": node["lat"], "lng": node["lng"],
            "category": f.get("category") or f.get("cuisine_type", ""),
            "duration_hr": f.get("avg_duration_hr") or 1.5,
            "price": f.get("entry_fee_krw") or f.get("avg_price_per_person") or 0}


# ──────────────────────────────────────────────
# 관광지 필터 + 랜드마크 필수 포함
# ──────────────────────────────────────────────
BAD_NAMES = {"Sokcho", "Gangneung", "속초", "강릉"}

def _is_valid(n):
    global _bbox_cache
    lat, lng = n.get("lat", 0), n.get("lng", 0)
    dur = n.get("features", {}).get("avg_duration_hr", 0) or 0
    if dur > 8 or n.get("name") in BAD_NAMES:
        return False
    # 클러스터 로드된 경우 centroid 기반 bbox, 없으면 통과
    if _clusters:
        if _bbox_cache is None:
            lats = [c["center"][0] for c in _clusters.values()]
            lngs = [c["center"][1] for c in _clusters.values()]
            margin = 0.15
            _bbox_cache = {
                "min_lat": min(lats) - margin,
                "max_lat": max(lats) + margin,
                "min_lng": min(lngs) - margin,
                "max_lng": max(lngs) + margin,
            }
        if not (_bbox_cache["min_lat"] <= lat <= _bbox_cache["max_lat"]
                and _bbox_cache["min_lng"] <= lng <= _bbox_cache["max_lng"]):
            return False
    return True

_ATTRACTION_BLOCK_CATS = [
    "주차장", "빌딩", "부동산", "공항철도", "지하철,전철", "입출구",
    "고가차도", "도로시설 > 육교", "주류도매", "제조업", "탁구장",
    # 음식점 계열이 attraction으로 잘못 분류된 경우 차단
    "음식점", "술집", "포장마차", "카페", "베이커리",
    # 도로/길 이름
    "도로명주소", "명예도로",
]

def _select_attractions(cluster_name, candidates, n=4, min_score=0.0, prefer_verified=False,
                        user_embedding=None):
    """랜드마크 우선 포함 → score 순 채움. 성향에 따라 필터링."""
    landmarks = _cluster_landmarks(cluster_name)

    # kakao_category 기반 부적합 관광지 제거
    def _is_bad_attraction(node):
        kc = node.get("kakao_category", "")
        name = node.get("name", "")
        if any(blk in kc for blk in _ATTRACTION_BLOCK_CATS):
            return True
        if "명예도로" in name:
            return True
        if any(kw in name for kw in ["휴업", "공사중", "임시휴관", "휴관"]):
            return True
        return False

    # min_score 컷
    filtered = [a for a in candidates
                if a.get("node_score", 0) >= min_score and not _is_bad_attraction(a)]
    if not filtered:
        filtered = candidates  # fallback

    # prefer_verified: 리뷰 100개 이상 우선
    if prefer_verified:
        verified = [a for a in filtered if a.get("features", {}).get("review_count", 0) >= 100]
        if len(verified) >= n:
            filtered = verified

    # 노드 임베딩 × 유저 취향 유사도 반영 (weight 0.3: node_score 주도, 취향은 보조)
    def _node_rank_score(node):
        base = node.get("node_score", 0)
        if user_embedding and "embedding" in node:
            from utils.embedder import cosine_sim
            sim = cosine_sim(user_embedding, node["embedding"])
            return base * 0.7 + sim * 0.3
        return base

    must, rest = [], []
    for att in filtered:
        is_lm = any(lm.lower() in att["name"].lower() or att["name"].lower() in lm.lower()
                     for lm in landmarks)
        (must if is_lm and len(must) < n else rest).append(att)
    rest.sort(key=_node_rank_score, reverse=True)
    return (must + rest)[:n]


# ──────────────────────────────────────────────
# 클러스터 컨셉 텍스트 생성 (임베딩용)
# ──────────────────────────────────────────────
def _cluster_concept_text(cluster_name: str, ca: dict, cr: dict) -> str:
    """
    클러스터 내 노드 데이터에서 임베딩 매칭용 영어 텍스트 생성.
    노드 concept_en 우선 사용, 없으면 피처 기반 영어 태그 생성.
    """
    atts  = ca.get(cluster_name, [])
    rests = cr.get(cluster_name, [])

    # 상위 노드의 concept_en 수집
    top_nodes = sorted(atts + rests, key=lambda n: n.get("node_score", 0), reverse=True)[:8]
    concept_parts = [n["concept_en"] for n in top_nodes if n.get("concept_en")]

    # name_en 수집 (랜드마크 이름으로 매칭 보조)
    top_atts = sorted(atts, key=lambda n: n.get("node_score", 0), reverse=True)[:4]
    name_parts = [n.get("name_en") or n.get("name", "") for n in top_atts]

    # features 평균 기반 영어 태그 (concept_en 없는 노드 보완)
    def _avg(nodes, key):
        vals = [n.get("features", {}).get(key, 0) or 0 for n in nodes]
        return sum(vals) / len(vals) if vals else 0.0

    tags = []
    if _avg(atts, "culture_depth")    >= 3.5: tags.append("historic culture heritage palace temple")
    if _avg(atts, "nature_score")     >= 3.5: tags.append("nature park forest hiking outdoor")
    if _avg(atts, "nightlife_score")  >= 3.0: tags.append("nightlife bar club night view rooftop")
    if _avg(atts, "activity_level")   >= 3.5: tags.append("activity experience adventure leisure")
    if _avg(atts, "photo_worthiness") >= 4.0: tags.append("instagrammable photo spot scenic view")

    # 나이트라이프 레스토랑 비중
    nl_rests = [r for r in rests if r.get("category_en") in ("nightclub", "bar", "pub", "lounge")
                or any(kw in r.get("concept_en", "") for kw in ["nightclub", "bar", "cocktail", "late night"])]
    if len(nl_rests) >= 2:
        tags.append("nightlife bars clubs entertainment")

    parts = []
    if name_parts:
        parts.append(" ".join(name_parts))
    if concept_parts:
        parts.append(" ".join(concept_parts[:4]))
    if tags:
        parts.append(" ".join(tags))

    return " ".join(parts) if parts else cluster_name


# ──────────────────────────────────────────────
# 클러스터 계획
# ──────────────────────────────────────────────
def _plan_clusters(attractions, restaurants, hotel, n_days, preferences, variant_idx=0,
                   user_embedding: list[float] | None = None,
                   cluster_embeddings: dict | None = None,
                   cluster_keywords: dict | None = None,
                   att_count: int = 3):
    wa = preferences.get("walking_aversion", 3)
    h_lat = hotel.get("lat", 37.5665) if hotel else 37.5665
    h_lng = hotel.get("lng", 126.978) if hotel else 126.978
    ca, cr = defaultdict(list), defaultdict(list)
    for n in attractions: ca[_get_cluster(n)].append(n)
    for r in restaurants: cr[_get_cluster(r)].append(r)
    ca.pop("기타", None); cr.pop("기타", None)
    valid = [c for c in ca if len(ca[c]) >= 4 and len(cr.get(c, [])) >= 4]

    # 저장된 클러스터 임베딩 로드 → 없으면 즉석 계산 (fallback)
    embed_sim_map: dict[str, float] = {}
    pref_text_lower = preferences.get("_pref_text_lower", "")
    if user_embedding:
        from utils.embedder import cosine_sim
        # 노드 임베딩 직접 비교
        # 관광지(ca) 노드 우선: 어디서 뭘 할지 쿼리 → 맛집 노드가 신호를 희석
        # - 관광지 2개 이상이면 관광지 TOP1(강한 신호) + 관광지 TOP3 평균(안정성) 혼합
        # - 관광지 부족 시 전체 노드 TOP3으로 fallback
        for name in valid:
            att_vecs  = [n["embedding"] for n in ca.get(name, []) if "embedding" in n]
            all_vecs  = att_vecs + [n["embedding"] for n in cr.get(name, []) if "embedding" in n]
            if att_vecs:
                att_sims = sorted([cosine_sim(user_embedding, v) for v in att_vecs], reverse=True)
                top1  = att_sims[0]
                top3_att = sum(att_sims[:3]) / min(3, len(att_sims))
                # 관광지 TOP1 70% + 관광지 TOP3 평균 30%
                # → 단 하나의 강한 관련 관광지(롯데월드)가 있으면 클러스터 점수에 강하게 반영
                embed_sim_map[name] = top1 * 0.7 + top3_att * 0.3
            elif all_vecs:
                all_sims = sorted([cosine_sim(user_embedding, v) for v in all_vecs], reverse=True)
                embed_sim_map[name] = sum(all_sims[:3]) / min(3, len(all_sims))
            elif cluster_embeddings and name in cluster_embeddings:
                embed_sim_map[name] = cosine_sim(user_embedding, cluster_embeddings[name])

        # 키워드 boost: 사용자 텍스트에 클러스터 키워드가 있으면 sim에 직접 가산
        # 자동 추출(장소명·카테고리) + 하드코딩(사용자 의도어) 합산
        # 매칭 키워드 1개당 0.05, 최대 0.15 (dense 한계 보완)
        if pref_text_lower:
            # 확장 키워드 포함 (keyword boost 범위 확대)
            # 부정 판단은 원문(pref_text_lower)만 사용 — 확장 텍스트에는 부정 표현 없음
            pref_expanded_lower = preferences.get("_pref_expanded_lower", "")
            pref_boost_text = f"{pref_text_lower} {pref_expanded_lower}".strip()
            # 공백 제거 버전도 준비 ("테마 파크" → "테마파크" 매칭)
            pref_nospace = pref_boost_text.replace(" ", "")

            # 부정 키워드 앞뒤로 나타나는 단어는 패널티 처리
            # "테마파크는 절대 넣지마", "등산 빼고", "X 싫어" 등
            _NEG_MARKERS = ["절대", "넣지마", "빼줘", "빼고", "싫어", "싫음", "제외", "하지마",
                            "빼", "안 넣", "없이", "노", "no", "말고"]
            def _is_negated(kw: str) -> bool:
                """키워드 주변 20자 이내에 부정 표현이 있으면 True."""
                kw_ns = kw.replace(" ", "")
                # 공백 제거 텍스트에서 위치 찾기
                for text in (pref_text_lower, pref_nospace):
                    kw_in = kw if text == pref_text_lower else kw_ns
                    idx = text.find(kw_in)
                    if idx == -1:
                        continue
                    window = text[max(0, idx - 20): idx + len(kw_in) + 20]
                    if any(neg in window for neg in _NEG_MARKERS):
                        return True
                return False

            def _in_pref(kw: str) -> bool:
                kw_ns = kw.replace(" ", "")
                return kw_ns in pref_nospace or kw in pref_boost_text

            # 클러스터 이름 + 소속 관광지 이름 직접 언급 보너스
            # "홍대" → "홍대/마포", "이태원" → "마포/용산" (이태원거리 노드 이름에서 추출) 등
            # 하드코딩 없이 클러스터/노드 이름 토큰으로 동작 → 도시 무관하게 적용
            import re as _re2
            _tok_pat = _re2.compile(r"[/,\s\(\)\[\]·>]+")
            for name in embed_sim_map:
                # 클러스터 이름 토큰
                name_parts = [p for p in _tok_pat.split(name) if len(p) >= 2]
                # 소속 관광지 이름 토큰 (이태원거리 → ["이태원", "거리"] 등)
                for node in ca.get(name, []):
                    for part in _tok_pat.split(node.get("name", "")):
                        if len(part) >= 2:
                            name_parts.append(part)
                for part in name_parts:
                    if _in_pref(part) and not _is_negated(part):
                        embed_sim_map[name] += 0.40
                        break  # 클러스터당 1회만

            # 먼저 모든 클러스터의 관광지 카테고리 토큰 수집 (배타적 매칭 계산용)
            all_att_cat_map: dict[str, set] = {}
            for _n2 in embed_sim_map:
                _toks: set[str] = set()
                for _node2 in ca.get(_n2, []):
                    for _part in _node2.get("kakao_category", "").split(">"):
                        _t = _part.strip()
                        if len(_t) >= 2:
                            _toks.add(_t)
                all_att_cat_map[_n2] = _toks

            for name in list(embed_sim_map.keys()):
                auto_kws = set(cluster_keywords.get(name, []) if cluster_keywords else [])
                hard_kws = set(_cluster_keywords(name))
                kws = auto_kws | hard_kws

                pos_hits = sum(1 for kw in kws if _in_pref(kw) and not _is_negated(kw))
                neg_hits = sum(1 for kw in kws if _in_pref(kw) and _is_negated(kw))

                # kakao_category 직접 매칭
                # 관광지(ca) 매칭은 강하게 (테마파크·고궁 등 활동 의도와 직결)
                # 맛집(cr) 매칭은 약하게 (cuisine은 임베딩으로도 충분)
                att_cat_tokens = all_att_cat_map.get(name, set())
                rest_cat_tokens: set[str] = set()
                for node in cr.get(name, []):
                    for part in node.get("kakao_category", "").split(">"):
                        t = part.strip()
                        if len(t) >= 2:
                            rest_cat_tokens.add(t)

                att_pos = sum(1 for t in att_cat_tokens if _in_pref(t) and not _is_negated(t))
                att_neg = sum(1 for t in att_cat_tokens if _in_pref(t) and _is_negated(t))
                rest_pos = sum(1 for t in rest_cat_tokens if _in_pref(t) and not _is_negated(t))
                rest_neg = sum(1 for t in rest_cat_tokens if _in_pref(t) and _is_negated(t))

                # 배타적 카테고리 매칭 보너스
                # 사용자가 명시한 카테고리(테마파크, 스키, 야시장 등)가 오직 이 클러스터에만 있을 때
                # → 강한 의도 신호이므로 임베딩 한계를 보완해 강하게 부스팅
                exclusive_bonus = 0.0
                for tok in att_cat_tokens:
                    if not _in_pref(tok) or _is_negated(tok):
                        continue
                    # 이 토큰을 가진 다른 클러스터 수
                    others = sum(1 for n2 in embed_sim_map if n2 != name and tok in all_att_cat_map.get(n2, set()))
                    if others == 0:
                        exclusive_bonus += 0.30  # 단 하나의 클러스터에만 있음 → 강한 보너스

                boost = (min(pos_hits * 0.05, 0.15)
                         + min(att_pos * 0.12, 0.25)   # 관광지 카테고리: 강하게
                         + min(rest_pos * 0.05, 0.10)  # 맛집 카테고리: 약하게
                         + min(exclusive_bonus, 0.50))  # 배타적 카테고리: 최대 0.50
                penalty = (min(neg_hits * 0.10, 0.25)
                           + min(att_neg * 0.15, 0.30)
                           + min(rest_neg * 0.05, 0.15))
                raw = embed_sim_map[name]
                embed_sim_map[name] = raw + boost - penalty

    # 임베딩 가중치를 sim 분포의 spread에 따라 동적 조정
    # spread가 클수록 임베딩이 클러스터를 잘 구분 → sim 가중치 상향
    # spread 0.05 → sim_w 0.40 (거의 수치 기반)
    # spread 0.20 → sim_w 0.70 (임베딩 주도)
    if embed_sim_map:
        sims = list(embed_sim_map.values())
        spread = max(sims) - min(sims)
        sim_w = min(0.40 + spread * 1.5, 0.75)
        base_w = 1.0 - sim_w - 0.10  # dist_pen 10% 고정
    else:
        sim_w = base_w = None

    def _score(name):
        att, rest, cen = ca.get(name, []), cr.get(name, []), _cluster_center(name)
        if not cen: return 0.0

        base = ((sum(n["node_score"] for n in att) / len(att) if att else 0) * 0.45
                + (sum(r["node_score"] for r in rest) / len(rest) if rest else 0) * 0.20
                + min(len(att) / 3.0, 1.0) * 0.35)
        dist_pen = _haversine(h_lat, h_lng, cen[0], cen[1]) / max(20 - wa * 1.5, 5) * 0.1

        if embed_sim_map:
            sim = embed_sim_map.get(name, 0.0)
            return base * base_w + sim * sim_w - dist_pen
        else:
            return base - dist_pen

    scored = sorted(valid, key=_score, reverse=True)
    if len(scored) < n_days:
        scored += sorted([c for c in ca if c not in scored], key=_score, reverse=True)

    # ── 클러스터 다일 배정 (Multi-day allocation) ──
    # 취향 유사도 높고 관광지/맛집이 충분한 클러스터는 여러 날 머물 수 있음.
    # 다양한 곳을 원하면 여러 클러스터, 좋아하는 곳이 명확하면 같은 클러스터에 집중.
    def _compute_allocation(scored_list: list, n: int) -> list:
        """scored_list 순서대로 클러스터에 날수 배정. 총합 = n."""
        # 클러스터별 최대 배정 가능 일수 결정
        # 기준: (1) 취향 유사도, (2) 관광지·맛집 충분 여부
        att_per_day = max(att_count, 1)
        alloc: dict[str, int] = {}
        for cn in scored_list:
            sim = embed_sim_map.get(cn, 0.0) if embed_sim_map else 0.0
            att_cap = len(ca.get(cn, []))
            rest_cap = len(cr.get(cn, []))
            # 실제 하루 소비량 기준으로 가능 일수 계산
            # 관광지: att_count개/일, 맛집: 3곳/일 (아침+점심+저녁)
            day_cap_att  = att_cap  // att_per_day  # 실제 하루 관광지 수 기준
            day_cap_rest = rest_cap // 3
            content_days = max(1, min(day_cap_att, day_cap_rest))

            if sim >= 0.70 and content_days >= 2:
                max_days = min(content_days, 3)   # 강한 취향 + 컨텐츠 충분 → 최대 3일
            elif sim >= 0.55 and content_days >= 2:
                max_days = 2                       # 중간 취향 + 컨텐츠 충분 → 최대 2일
            else:
                max_days = 1                       # 기본: 1일
            alloc[cn] = max_days

        # 높은 점수 클러스터부터 날수 배정
        result: list[str] = []
        remaining = n
        for cn in scored_list:
            if remaining <= 0:
                break
            days = min(alloc.get(cn, 1), remaining)
            # 다양성 보정: 한 클러스터가 전체 일정의 절반 이상 독점 방지
            # (단, 1~2일 여행이거나 n_days <= 2면 제한 없음)
            if n >= 3:
                days = min(days, max(1, n // 2))
            result.extend([cn] * days)
            remaining -= days
        return result

    if variant_idx == 0:
        base_pool = scored
    elif variant_idx == 1:
        # 짝수 인덱스 클러스터 우선 (2등, 4등, ... → 1등, 3등, ...)
        evens = scored[::2]
        base_pool = evens + [c for c in scored if c not in evens]
    elif variant_idx == 2:
        # 홀수 인덱스 클러스터 우선 (2등, 3등 우선 → 1등 뒤로)
        odds = scored[1::2]
        base_pool = odds + [c for c in scored if c not in odds]
    else:
        # variant 3+: 역순 정렬 (최하위 클러스터부터)
        base_pool = list(reversed(scored))

    selected = _compute_allocation(base_pool, n_days)

    # 동선 최적화: 같은 클러스터 연속 배치 보장 후 nearest-neighbor 정렬
    # 1) 중복 제거된 unique 클러스터를 거리 기준 정렬
    seen_order: list[str] = []
    for cn in selected:
        if cn not in seen_order:
            seen_order.append(cn)
    ordered_unique, rem_u, cur = [], list(seen_order), (h_lat, h_lng)
    while rem_u:
        nn = min(rem_u, key=lambda c: _haversine(cur[0], cur[1], *(_cluster_center(c) or cur)))
        ordered_unique.append(nn); rem_u.remove(nn); cur = _cluster_center(nn) or cur

    # 2) 같은 클러스터 연속 배치 (multi-day 클러스터 연일 방문)
    from collections import Counter as _Counter
    day_counts = _Counter(selected)
    ordered = []
    for cn in ordered_unique:
        ordered.extend([cn] * day_counts[cn])

    _embed_meta = {"sim_w": round(sim_w, 2), "spread": round(spread, 3)} if embed_sim_map else {}

    # multi-day 클러스터 로그
    multi = {cn: d for cn, d in day_counts.items() if d > 1}
    if multi:
        _embed_meta["multi_day"] = multi

    return ordered, dict(ca), dict(cr), _embed_meta


# ──────────────────────────────────────────────
# 숙소 / 동선 / 맛집
# ──────────────────────────────────────────────
def _pick_hotel(cur_cluster, next_cluster, hotels, used_names, user_embedding=None):
    if not hotels: return {}
    cur_c = _cluster_center(cur_cluster) or (37.5665, 126.978)
    next_c = _cluster_center(next_cluster) or cur_c if next_cluster else cur_c
    t_lat, t_lng = cur_c[0] * 0.7 + next_c[0] * 0.3, cur_c[1] * 0.7 + next_c[1] * 0.3
    for name in reversed(used_names):
        h = next((x for x in hotels if x["name"] == name), None)
        if h and _haversine(cur_c[0], cur_c[1], h["lat"], h["lng"]) <= 6.0: return h
    def hotel_score(h):
        base = h["node_score"] - _haversine(t_lat, t_lng, h["lat"], h["lng"]) / 30
        if user_embedding and "embedding" in h:
            from utils.embedder import cosine_sim
            base += cosine_sim(user_embedding, h["embedding"]) * 0.25
        return base
    return max(hotels, key=hotel_score)

def _optimize_route(nodes, hotel=None):
    """Nearest Neighbor TSP — 숙소에서 출발, 모든 노드를 최단 경로로 순회."""
    if len(nodes) <= 1: return nodes
    start_lat = hotel["lat"] if hotel and hotel.get("lat") else nodes[0]["lat"]
    start_lng = hotel["lng"] if hotel and hotel.get("lng") else nodes[0]["lng"]
    # 숙소에서 가장 가까운 노드부터 시작
    start = min(nodes, key=lambda n: _haversine(start_lat, start_lng, n["lat"], n["lng"]))
    rem, route = [n for n in nodes if n is not start], [start]
    while rem:
        last = route[-1]
        nn = min(rem, key=lambda n: _haversine(last["lat"], last["lng"], n["lat"], n["lng"]))
        route.append(nn); rem.remove(nn)
    return route


def _optimize_route_full(all_stops, hotel):
    """
    TSP 최적화 — 숙소 출발 → 모든 정류장(관광지+식사) 순회 → 숙소 복귀.
    2-opt 개선으로 교차 경로 제거.
    """
    if len(all_stops) <= 2:
        return _optimize_route(all_stops, hotel)

    h_lat = hotel.get("lat", 37.5665)
    h_lng = hotel.get("lng", 126.978)

    # 1단계: Nearest Neighbor로 초기 경로
    route = _optimize_route(all_stops, hotel)

    # 2단계: 2-opt 개선 (교차 경로 풀기)
    def total_dist(r):
        d = _haversine(h_lat, h_lng, r[0]["lat"], r[0]["lng"])
        for i in range(len(r) - 1):
            d += _haversine(r[i]["lat"], r[i]["lng"], r[i+1]["lat"], r[i+1]["lng"])
        d += _haversine(r[-1]["lat"], r[-1]["lng"], h_lat, h_lng)
        return d

    improved = True
    while improved:
        improved = False
        best_dist = total_dist(route)
        for i in range(len(route) - 1):
            for j in range(i + 1, len(route)):
                new_route = route[:i] + route[i:j+1][::-1] + route[j+1:]
                new_dist = total_dist(new_route)
                if new_dist < best_dist - 0.01:  # 10m 이상 개선 시
                    route = new_route
                    best_dist = new_dist
                    improved = True
                    break
            if improved:
                break

    return route

# 끼니별 cuisine 적합도
MEAL_FITNESS = {
    "breakfast": {
        "good": ["브런치", "카페", "베이커리", "팬케이크", "수제비", "칼국수", "국수", "분식", "해장국", "삼계탕", "죽", "토스트", "샌드위치"],
        "bad":  ["구이", "고기", "곱창", "막창", "양대창", "클럽", "펍", "칵테일", "와인바", "코스", "샤브", "족발", "보쌈", "통닭", "치킨", "삼겹살", "갈비", "바베큐"],
    },
    "lunch": {
        "good": ["한식", "냉면", "칼국수", "국수", "비빔", "쌀국수", "라멘", "파스타", "딤섬", "중식", "이탈리안", "베트남", "멕시칸", "분식", "만두", "삼계탕", "감자탕"],
        "bad":  ["클럽", "펍", "칵테일"],
    },
    "cafe": {
        "good": ["카페", "디저트", "커피", "베이커리", "차", "티", "팬케이크", "브런치", "아이스크림"],
        "bad":  ["구이", "고기", "곱창", "냉면", "해장국", "감자탕", "족발", "보쌈", "삼겹살"],
    },
    "dinner": {
        "good": ["구이", "고기", "곱창", "양대창", "한우", "삼겹살", "소금구이", "코스", "와인", "이탈리안", "프렌치", "한정식", "해산물", "복어", "샤브", "족발", "보쌈", "통닭", "퓨전"],
        "bad":  ["분식", "브런치", "팬케이크", "토스트"],
    },
}

def _meal_fitness(cuisine: str, meal_type: str, kakao_category: str = "") -> float:
    """끼니 타입에 맞는 cuisine이면 보너스, 안 맞으면 패널티."""
    # kakao_category 기반 하드 룰 (cuisine_type보다 신뢰도 높음)
    if kakao_category:
        if "술집" in kakao_category or "클럽" in kakao_category:
            return -0.8
        if "카페" in kakao_category:
            if meal_type in ("breakfast", "cafe"):
                return 0.2
            if meal_type in ("dinner", "lunch"):
                return -0.3
        # 구이/고기/치킨 계열 → 아침 하드 차단
        if meal_type == "breakfast":
            _HEAVY_FOR_MORNING = ["육류", "고기", "구이", "통닭", "치킨", "갈비", "삼겹살",
                                   "곱창", "족발", "보쌈", "바베큐", "샤브샤브"]
            if any(kw in kakao_category for kw in _HEAVY_FOR_MORNING):
                return -1.0  # 완전 차단

    rules = MEAL_FITNESS.get(meal_type)
    if not rules or not cuisine:
        return 0.0
    cl = cuisine.lower()
    if any(g in cl for g in rules["good"]):
        return 0.15
    if any(b in cl for b in rules["bad"]):
        return -0.25
    return 0.0


def _pick_meal(prev_stop, next_stop, pool, fallback, used, used_cuisines,
               max_dist, price_range=(0, 999999), prefer_types=None, meal_type="",
               min_score=0.0, prefer_verified=False, user_embedding=None,
               budget_target=0):
    """
    budget_target: 이 끼니에 쓸 수 있는 1인 예산 상한 (dn_max 등).
                   0이면 미사용. 설정 시 예산 대비 가격 적합도 보너스 부여.
                   고예산 → 비싼 식당 선호 / 저예산 → 저렴한 식당 선호.
    """
    p_lat, p_lng = prev_stop["lat"], prev_stop["lng"]
    n_lat, n_lng = next_stop.get("lat", p_lat), next_stop.get("lng", p_lng)
    direct = _haversine(p_lat, p_lng, n_lat, n_lng)
    def score(r):
        cuisine = r.get("features", {}).get("cuisine_type", "")
        kakao_cat = r.get("kakao_category", "")
        fit = _meal_fitness(cuisine, meal_type, kakao_cat)
        if fit <= -1.0:
            return -9999.0  # 하드 차단된 업종은 점수 산정 없이 최하위
        detour = (_haversine(p_lat, p_lng, r["lat"], r["lng"]) + _haversine(r["lat"], r["lng"], n_lat, n_lng)) - direct
        c_pen = 0.15 if cuisine and cuisine in (used_cuisines[-2:] if used_cuisines else []) else 0
        p_bon = 0.1 if prefer_types and any(p in cuisine for p in prefer_types) else 0
        v_bon = 0.1 if prefer_verified and r.get("features", {}).get("review_count", 0) >= 100 else 0
        # 예산 적합도: 예산 상한 대비 식당 가격 비율로 보너스/패널티
        # 고예산(target이 크면): 가격이 target의 50~100% 범위 식당 선호
        # 저예산(target이 작으면): 가격이 낮을수록 유리 (기본 동작)
        price_bon = 0.0
        if budget_target > 0:
            price = r.get("features", {}).get("avg_price_per_person", 0) or 0
            if price > 0:
                ratio = price / budget_target  # 예산 대비 가격 비율
                # 0.5~1.0 구간: 최대 +0.15 보너스 (예산을 잘 활용)
                # 0~0.3 구간: 최대 -0.10 패널티 (예산이 있는데 너무 저렴)
                if 0.5 <= ratio <= 1.0:
                    price_bon = 0.15 * (ratio - 0.5) / 0.5
                elif ratio < 0.3 and budget_target > 30000:
                    price_bon = -0.10 * (0.3 - ratio) / 0.3
        # 유저 취향 임베딩 유사도 보너스
        emb_bon = 0.0
        if user_embedding and "embedding" in r:
            from utils.embedder import cosine_sim
            emb_bon = cosine_sim(user_embedding, r["embedding"]) * 0.35
        return r["node_score"] - detour / 3.0 - c_pen + p_bon + fit + v_bon + price_bon + emb_bon
    # 식당이 아닌 업종은 모든 식사 슬롯에서 하드 제외 (탁구장, 스포츠시설 등)
    _NON_FOOD_CATS = ["스포츠", "레저", "탁구", "볼링", "수영", "헬스", "골프",
                      "부동산", "빌딩", "주차장", "병원", "약국"]
    def _is_non_food(r):
        kc = r.get("kakao_category", "")
        return any(kw in kc for kw in _NON_FOOD_CATS)

    # 나이트라이프(술집/클럽)는 breakfast/lunch/cafe 슬롯에서 하드 제외
    _NL_BLOCK = ["술집", "클럽", "나이트", "이자카야", "포차", "와인바", "라운지바"]
    def _is_nightlife_venue(r):
        kc = r.get("kakao_category", "")
        return any(kw in kc for kw in _NL_BLOCK)
    # dinner 포함 모든 식사 슬롯에서 술집/클럽 카테고리 하드 제외
    # 나이트라이프는 별도 _pick_nightlife 슬롯(20:30)에서만 배정
    block_nightlife = meal_type in ("breakfast", "lunch", "cafe", "dinner")

    # 거리 단계별 탐색:
    # 1) 현재 클러스터(pool) max_dist 내
    # 2) 현재 클러스터(pool) max_dist*2 내
    # 3) 인근 클러스터 포함(fallback) 2km 내  ← 클러스터 경계 완화
    # 4) 인근 클러스터 포함(fallback) 5km 내  ← 최대 허용 거리
    # (무제한 탐색 제거 → 뜬금없는 먼 장소 방지)
    _NEARBY_CAP  = 2.0  # 인근 클러스터 1차 (km)
    _FAR_CAP     = 5.0  # 인근 클러스터 2차 (km)

    for pool_, cap in [(pool, max_dist), (pool, max_dist*2),
                       (fallback, _NEARBY_CAP), (fallback, _FAR_CAP)]:
        avail = [r for r in pool_ if r["name"] not in used
                 and not _is_non_food(r)
                 and r.get("node_score", 0) >= min_score
                 and price_range[0] <= (r.get("features",{}).get("avg_price_per_person",0) or 0) <= price_range[1]
                 and not (block_nightlife and _is_nightlife_venue(r))]
        nearby = [(r, _haversine(p_lat, p_lng, r["lat"], r["lng"])) for r in avail]
        nearby = [x for x in nearby if x[1] <= cap]
        if nearby: return max(nearby, key=lambda x: score(x[0]))[0]

    # min_score 완화 fallback (5km 내)
    for pool_, cap in [(pool, max_dist*2), (fallback, _FAR_CAP)]:
        avail = [r for r in pool_ if r["name"] not in used
                 and not _is_non_food(r)
                 and price_range[0] <= (r.get("features",{}).get("avg_price_per_person",0) or 0) <= price_range[1]
                 and not (block_nightlife and _is_nightlife_venue(r))]
        nearby = [(r, _haversine(p_lat, p_lng, r["lat"], r["lng"])) for r in avail]
        nearby = [x for x in nearby if x[1] <= cap]
        if nearby: return max(nearby, key=lambda x: score(x[0]))[0]

    # 최후 fallback: 예산/거리 무시하고 5km 내 가장 저렴한 곳
    for pool_ in [pool, fallback]:
        avail = [r for r in pool_ if r["name"] not in used
                 and not _is_non_food(r)
                 and not (block_nightlife and _is_nightlife_venue(r))
                 and _haversine(p_lat, p_lng, r["lat"], r["lng"]) <= _FAR_CAP]
        if avail:
            return min(avail, key=lambda r: r.get("features", {}).get("avg_price_per_person", 999999) or 999999)
    return None


_NIGHTLIFE_KAKAO_CATS = ["술집", "클럽", "나이트", "라운지바", "칵테일", "와인바", "이자카야", "포차"]
_NIGHTLIFE_CONCEPT_KWS = ["nightclub", "bar", "cocktail", "late night", "club", "pub", "lounge", "wine bar"]
_NIGHTLIFE_MAX_DIST_KM = 5.0

# romantic 모드: 클럽/나이트클럽 제외, wine bar·lounge·cocktail 선호
_ROMANTIC_NL_BLOCK_CATS  = ["클럽", "나이트"]
_ROMANTIC_NL_BLOCK_WORDS = ["nightclub", "club", "dancing", "rave", "edm"]
_ROMANTIC_NL_PREFER_WORDS = ["wine bar", "cocktail", "lounge", "rooftop", "bar", "jazz", "jazz bar"]

_NIGHTLIFE_MIN_SIM = 0.20  # user_embedding이 있을 때 나이트라이프 후보 최소 유사도

def _pick_nightlife(prev_stop, pool, all_restaurants, used, max_dist,
                    nightlife_mode="any", user_embedding=None):
    """
    저녁 식사 이후 나이트라이프 슬롯 선택.
    nightlife_mode:
      "any"      — 제한 없음 (기본)
      "romantic" — 클럽/나이트 제외, wine bar·cocktail·lounge 우선
    user_embedding이 있으면:
      - 나이트라이프 후보 각각의 sim 계산 → 점수에 반영
      - 모든 후보의 max_sim < _NIGHTLIFE_MIN_SIM → 슬롯 자체 스킵
        (취향과 너무 거리가 먼 나이트라이프는 자연스럽게 배제)
    1) 현재 클러스터(pool) 내 탐색
    2) 전체(all_restaurants)에서 5km 이내 탐색
    3) 없으면 None → 슬롯 생략
    """
    p_lat, p_lng = prev_stop["lat"], prev_stop["lng"]

    def _is_nightlife(r):
        kc = r.get("kakao_category", "")
        cuisine = r.get("features", {}).get("cuisine_type", "") or ""
        concept = r.get("concept_en", "").lower()

        # kakao_category 기반 (가장 신뢰도 높음)
        if any(kw in kc for kw in _NIGHTLIFE_KAKAO_CATS):
            # romantic 모드: 클럽/나이트 계열 차단 (kakao + concept_en 모두 확인)
            if nightlife_mode == "romantic":
                if any(kw in kc for kw in _ROMANTIC_NL_BLOCK_CATS):
                    return False
                concept_words = set(concept.split())
                if any(kw in concept_words for kw in _ROMANTIC_NL_BLOCK_WORDS):
                    return False
            return True

        # kakao_category가 명확히 음식점 계열이면 → cuisine/concept 오탐 방지
        _FOOD_ONLY_CATS = ["양식", "한식", "일식", "중식", "분식", "패스트푸드",
                           "카페", "베이커리", "디저트", "제과"]
        if kc and any(c in kc for c in _FOOD_ONLY_CATS):
            return False

        # concept_en 영어 키워드 (단어 경계 매칭)
        concept_words = set(concept.split())
        # romantic 모드: concept_en에 클럽/댄스/나이트클럽 단어 있으면 차단
        if nightlife_mode == "romantic":
            if any(kw in concept_words for kw in _ROMANTIC_NL_BLOCK_WORDS):
                return False

        for kw in _NIGHTLIFE_CONCEPT_KWS:
            kw_parts = kw.split()
            if len(kw_parts) == 1:
                if kw in concept_words:
                    return True
            else:
                if kw in concept:
                    return True

        # cuisine_type 한국어
        _CUISINE_NL_KWS = ["술집", "클럽", "이자카야", "포차", "와인바", "칵테일바", "펍", "바&그릴"]
        if any(kw in cuisine for kw in _CUISINE_NL_KWS):
            if nightlife_mode == "romantic" and any(kw in cuisine for kw in ["클럽"]):
                return False
            return True
        return False

    def _sim(r):
        """유저 임베딩과의 코사인 유사도. 임베딩 없으면 0."""
        if user_embedding and "embedding" in r:
            from utils.embedder import cosine_sim
            return cosine_sim(user_embedding, r["embedding"])
        return 0.0

    def score(r):
        dist = _haversine(p_lat, p_lng, r["lat"], r["lng"])
        base = r["node_score"] - dist / 3.0
        # 유저 임베딩 유사도 반영 (맛집 선택과 동일한 방식)
        if user_embedding:
            base += _sim(r) * 0.35
        # romantic 모드: wine bar·cocktail·lounge 가산점
        if nightlife_mode == "romantic":
            concept = r.get("concept_en", "").lower()
            if any(kw in concept for kw in _ROMANTIC_NL_PREFER_WORDS):
                base += 0.20
        return base

    def _check_sim_threshold(candidates):
        """user_embedding이 있을 때: 후보군 max_sim < 임계값이면 슬롯 스킵."""
        if not user_embedding:
            return True  # 임베딩 없으면 항상 진행
        sims = [_sim(r) for r in candidates if "embedding" in r]
        if not sims:
            return True  # 임베딩 데이터 없으면 진행
        return max(sims) >= _NIGHTLIFE_MIN_SIM

    # 거리 필터링된 후보 기준으로 sim 임계값 확인
    # (전역 후보가 아닌, 실제 선택 가능한 5km 이내 후보만 체크)
    reachable_nl = [r for r in all_restaurants
                    if _is_nightlife(r)
                    and _haversine(p_lat, p_lng, r["lat"], r["lng"]) <= _NIGHTLIFE_MAX_DIST_KM]
    if not _check_sim_threshold(reachable_nl):
        return None  # 취향과 너무 먼 나이트라이프 → 슬롯 자체 제거

    # 1) 현재 클러스터 내 탐색 (max_dist, max_dist*2)
    for cap in [max_dist, max_dist * 2]:
        avail = [r for r in pool if r["name"] not in used and _is_nightlife(r)]
        nearby = [(r, _haversine(p_lat, p_lng, r["lat"], r["lng"])) for r in avail
                  if _haversine(p_lat, p_lng, r["lat"], r["lng"]) <= cap]
        if nearby:
            return max(nearby, key=lambda x: score(x[0]))[0]

    # 2) 인접 클러스터 포함 전체에서 5km 이내 탐색
    avail_all = [r for r in all_restaurants if r["name"] not in used and _is_nightlife(r)]
    nearby_all = [(r, _haversine(p_lat, p_lng, r["lat"], r["lng"])) for r in avail_all
                  if _haversine(p_lat, p_lng, r["lat"], r["lng"]) <= _NIGHTLIFE_MAX_DIST_KM]
    if nearby_all:
        return max(nearby_all, key=lambda x: score(x[0]))[0]

    # 3) 없으면 None → 슬롯 생략
    return None


# 나이트라이프 클러스터 키워드 (해당 클러스터 당일은 우선 배정)
_NIGHTLIFE_CLUSTER_KEYWORDS = ["클럽", "바", "나이트", "이태원", "홍대"]

def _should_add_nightlife(day_idx: int, ordered: list, nightlife_pref: int, duration: int) -> bool:
    """
    취향 점수(1~5)로 나이트라이프를 넣을 날 수를 결정.
    - 나이트라이프 친화 클러스터(홍대·이태원 등)는 우선 배정
    - 나머지 날은 점수에 따라 추가
    """
    # 취향 점수 → 총 허용 일수
    # 1→0, 2→1, 3→절반(올림), 4→전체-1, 5→전체
    max_nights = {1: 0, 2: 1, 3: (duration + 1) // 2, 4: max(duration - 1, 1), 5: duration}.get(
        max(1, min(5, nightlife_pref)), 0
    )
    if max_nights == 0:
        return False

    # 친화 클러스터인 날을 우선으로 max_nights 개 날을 선택
    nightlife_days = sorted(
        range(len(ordered)),
        key=lambda i: (
            not any(kw in _cluster_concept(ordered[i]) or kw in ordered[i]
                    for kw in _NIGHTLIFE_CLUSTER_KEYWORDS),
            i  # 같은 우선순위면 앞 날 우선
        )
    )[:max_nights]

    return day_idx in nightlife_days


# ──────────────────────────────────────────────
# 플래닝 에이전트
# ──────────────────────────────────────────────
class PlanningAgent:
    def __init__(self, attractions_json, restaurants_json, hotels_json,
                 graph_json=None, verbose=True):
        """
        graph_json: knowledge_graph/서울.json 경로 (선택).
                    --embed 로 임베딩이 저장된 경우 클러스터·노드 임베딩을 로드해
                    preference_text 기반 시맨틱 매칭에 활용.
        """
        self.verbose = verbose
        with open(attractions_json, encoding="utf-8") as f: a_data = json.load(f)
        with open(restaurants_json, encoding="utf-8") as f: r_data = json.load(f)
        with open(hotels_json, encoding="utf-8") as f: h_data = json.load(f)
        self.trip = a_data.get("trip", {})
        self.attractions = a_data.get("attraction_nodes", [])
        self.restaurants = r_data.get("restaurant_nodes", [])
        self.hotels = h_data.get("hotel_nodes", [])
        self.duration = self.trip.get("duration_days", 4)
        self.checkin = self.trip.get("checkin", "")

        # 클러스터 임베딩 + 키워드 로드 (--embed 실행 후 저장된 값)
        self.cluster_embeddings: dict = {}
        self.cluster_keywords: dict = {}
        if graph_json:
            try:
                with open(graph_json, encoding="utf-8") as f:
                    g = json.load(f)
                self.cluster_embeddings = g.get("cluster_embeddings", {})
                self.cluster_keywords = g.get("cluster_keywords", {})
                # 클러스터 메타(center/concept/landmarks/keywords) 런타임 레지스트리에 등록
                if g.get("clusters"):
                    _load_clusters(g["clusters"])
                    self._log(f"클러스터 로드 완료: {len(g['clusters'])}개")
                if self.cluster_embeddings:
                    self._log(f"클러스터 임베딩 로드 완료: {len(self.cluster_embeddings)}개")
            except Exception as e:
                self._log(f"[WARN] 그래프 로드 실패 → 임베딩 미사용: {e}")

    def _log(self, msg):
        if self.verbose: print(f"[PlanningAgent] {msg}", flush=True)

    def run(self, n_variants=1):
        if n_variants <= 1: return self._build_one(variant_idx=0)
        results = []
        for vi in range(n_variants):
            self._log(f"\n{'='*50}\n  일정 {vi+1}/{n_variants} (variant {vi})\n{'='*50}")
            r = self._build_one(variant_idx=vi); r["variant"] = vi + 1; results.append(r)
        return results

    @staticmethod
    def _expand_preference(text: str) -> tuple[str, str]:
        """
        Claude Haiku로 취향 텍스트를 영어 키워드로 확장.
        노드 임베딩이 영어(concept_en) 기준이므로 유저 텍스트도 영어로 변환.
        반환: (임베딩용 영어 텍스트, 확장된 영어 키워드만)
        실패 시 (원문, "") 반환.
        """
        try:
            import anthropic, os
            client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=200,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Travel preference (Korean): \"{text}\"\n\n"
                        "Translate and expand into ~25 English keywords describing the desired travel vibe, place types, and atmosphere.\n"
                        "- Include: place types, activities, ambiance, mood, attraction names\n"
                        "- Exclude: accommodation types, transport, budget words\n"
                        "- Exclude keywords with negation ('no', 'avoid', 'hate')\n"
                        "Output English keywords only, space-separated, no explanation."
                    )
                }]
            )
            expanded = resp.content[0].text.strip()
            return expanded, expanded
        except Exception:
            return text, ""

    def _build_one(self, variant_idx=0):
        self._log(f"일정 생성 — {self.trip.get('destination')} {self.duration}박")
        prefs = self.trip.get("preferences", {})

        # ── 자연어 취향 텍스트 → 쿼리 확장 → 임베딩 ──
        pref_text = self.trip.get("preference_text", "").strip()
        user_embedding = None
        if pref_text:
            try:
                from utils.embedder import embed
                embed_text, expansion_only = self._expand_preference(pref_text)
                if expansion_only:
                    self._log(f"  취향 확장: '{expansion_only[:70]}...'")
                user_embedding = embed(embed_text)
                self._log(f"  취향 임베딩 완료: '{pref_text[:50]}'")
                prefs = dict(prefs)
                # keyword boost: 원문 + 확장 키워드 모두 사용 (부정은 원문 기준으로만 판단)
                prefs["_pref_text_lower"] = pref_text.lower()
                prefs["_pref_expanded_lower"] = expansion_only.lower()
            except Exception as e:
                self._log(f"  [WARN] 임베딩 실패 → 수치 기반 fallback: {e}")

        food, budget = prefs.get("food", 3), self.trip.get("budget_krw", 1500000)
        days_cnt, travelers = self.duration, self.trip.get("traveler_count", 2)
        style = prefs.get("scoring_style", "balanced")

        # ── 성향별 예산 배분 + 선택 전략 ──
        STYLE_CONFIG = {
            "balanced": {
                "food_ratio": 0.30,       # 총 예산 중 식비 비율
                "bk_ratio": 0.25,         # 아침 예산 비율 (식비 내)
                "dn_ratio": 1.00,         # 저녁 예산 비율
                "att_count": 4,           # 하루 관광지 수
                "prefer_verified": False, # 검증된 곳 선호 여부
                "allow_expensive": 1.0,   # 고가 허용 배수 (1.0 = 기본)
                "min_score": 0.0,         # 최소 스코어 컷
                "nightlife_enabled": True,
            },
            "threshold": {
                "food_ratio": 0.30,
                "bk_ratio": 0.30,         # 아침도 어느정도 투자
                "dn_ratio": 0.80,         # 저녁 예산 약간 절제
                "att_count": 3,           # 관광지 줄여서 여유롭게
                "prefer_verified": True,  # 리뷰 많은 검증된 곳
                "allow_expensive": 0.8,   # 고가 비허용 (안전하게)
                "min_score": 0.4,         # 최소 스코어 높게 (하한 보장)
                "nightlife_enabled": False,  # 검증 어려운 술집 제외
            },
            "peak": {
                "food_ratio": 0.5,        # 식비 비율 높게
                "bk_ratio": 0.15,         # 아침 절약
                "dn_ratio": 1.50,         # 저녁에 몰빵
                "att_count": 2,           # 관광지 줄이고 퀄리티 집중
                "prefer_verified": False,
                "allow_expensive": 3.0,   # 고가 경험 허용
                "min_score": 0.0,
                "nightlife_enabled": True,  # 하이라이트 나이트라이프 포함
            },
            "risk_averse": {
                "food_ratio": 0.28,
                "bk_ratio": 0.25,
                "dn_ratio": 0.90,
                "att_count": 3,           # 여유로운 일정
                "prefer_verified": True,  # 검증된 곳만
                "allow_expensive": 0.7,
                "min_score": 0.5,         # 높은 최소 스코어
                "nightlife_enabled": False,  # 리스크 회피 → 나이트라이프 스킵
            },
            "budget_safe": {
                "food_ratio": 0.15,       # 식비 최소화
                "bk_ratio": 0.20,
                "dn_ratio": 0.70,         # 저녁도 절약
                "att_count": 5,           # 무료/저가 관광지 많이
                "prefer_verified": False,
                "allow_expensive": 0.5,   # 고가 거의 차단
                "min_score": 0.0,
                "nightlife_enabled": False,  # 예산 절약 → 나이트라이프 스킵
            },
        }
        cfg = dict(STYLE_CONFIG.get(style, STYLE_CONFIG["balanced"]))  # mutable copy

        # ── preference_text 기반 나이트라이프 자동 조정 ──
        # 영어 확장 텍스트 우선 사용 — 한국어 substring 오탐 방지
        # ("아이돌"→"아이" 같은 오탐은 영어 확장에서 "idol"로 변환되므로 해소)
        expanded_text = prefs.get("_pref_expanded_lower", "")
        det_text = expanded_text if expanded_text else pref_text.lower()

        # 1) 가족/아이 → 완전 비활성 (영어 기준)
        _FAMILY_KW_EN = ["children", "kids", "family", "baby", "toddler", "infant",
                         "child", "kindergarten", "elementary"]
        if det_text and any(kw in det_text for kw in _FAMILY_KW_EN):
            if cfg.get("nightlife_enabled"):
                self._log("  [취향] 가족/아이 동반 감지 → 나이트라이프 비활성")
                cfg["nightlife_enabled"] = False

        # 취향 기반 나이트라이프 모드 결정 (영어 키워드 기준)
        _QUIET_KW_EN   = ["quiet", "peaceful", "serene", "solo", "alone", "meditation",
                          "temple", "gallery", "museum", "exhibition", "contemplat",
                          "tranquil", "calm", "relax", "stroll", "walk"]
        _ROMANTIC_KW_EN = ["romantic", "night view", "rooftop", "date night",
                            "han river view", "nightscape", "scenic night", "night scenery"]
        _CLUB_KW_EN    = ["club", "party", "rave", "edm"]
        _ACTIVE_NL_KW_EN = ["club", "nightlife", "bar", "pub", "izakaya", "nightclub",
                             "cocktail lounge", "party", "drinking", "alcohol", "brewery"]

        def _has_nl_kw_en(text: str) -> bool:
            return any(kw in text for kw in _ACTIVE_NL_KW_EN)

        cfg["nightlife_mode"] = "any"  # 기본: 제한 없음

        if det_text and cfg.get("nightlife_enabled"):
            has_quiet    = any(kw in det_text for kw in _QUIET_KW_EN)
            has_active_nl = _has_nl_kw_en(det_text)
            has_romantic  = any(kw in det_text for kw in _ROMANTIC_KW_EN)
            has_club      = any(kw in det_text for kw in _CLUB_KW_EN)

            if has_quiet and not has_active_nl and not has_romantic:
                # 2) 조용한/사색/예술 취향 → 완전 비활성
                self._log("  [취향] 조용한/사색 취향 감지 → 나이트라이프 비활성")
                cfg["nightlife_enabled"] = False
            elif has_romantic and not has_club:
                # 3) 야경/로맨틱 → 클럽 제외하고 wine bar·루프탑·라운지 선호 모드
                self._log("  [취향] 야경/로맨틱 감지 → 나이트라이프 wine bar/루프탑 선호 모드")
                cfg["nightlife_mode"] = "romantic"
            elif not has_active_nl and not has_romantic:
                # 3-a) 나이트라이프 관련 키워드 없음 → 슬롯 억제
                cur_nl = prefs.get("nightlife", 3)
                if cur_nl >= 2:
                    prefs = dict(prefs)
                    prefs["nightlife"] = 1
                    self._log("  [취향] 나이트라이프 키워드 없음 → nightlife 선호도 1로 하향 (배정 없음)")

        self._log(f"  성향: {style} → 식비 {cfg['food_ratio']*100:.0f}% / 관광지 {cfg['att_count']}개 / 고가배수 {cfg['allow_expensive']}x")

        dfb = budget * cfg["food_ratio"] / days_cnt / travelers
        bk_max = int(dfb * cfg["bk_ratio"])
        ln_max = int(dfb * 0.60)
        dn_max = int(dfb * cfg["dn_ratio"] * cfg["allow_expensive"])
        max_dist = 1.5 + food * 0.7

        best_hotel = max(self.hotels, key=lambda h: h["node_score"]) if self.hotels else None
        valid_att = [n for n in self.attractions if _is_valid(n)]
        seen, deduped = set(), []
        for n in sorted(valid_att, key=lambda x: x["node_score"], reverse=True):
            k = n.get("place_id") or n["name"].replace(" ", "").lower()
            if k not in seen: seen.add(k); seen.add(n["name"].replace(" ", "").lower()); deduped.append(n)

        ordered, ca, cr, embed_meta = _plan_clusters(deduped, self.restaurants, best_hotel, self.duration, prefs, variant_idx,
                                                      user_embedding=user_embedding,
                                                      cluster_embeddings=self.cluster_embeddings,
                                                      cluster_keywords=self.cluster_keywords,
                                                      att_count=cfg["att_count"])
        if embed_meta:
            self._log(f"  임베딩 가중치: sim={embed_meta['sim_w']} base={round(1-embed_meta['sim_w']-0.1,2)} (spread={embed_meta['spread']})")

        # 클러스터 center 좌표 맵 (인근 클러스터 관광지 탐색용)
        # _clusters 전역 레지스트리에서 로드 (self.graph_json 불필요)
        _cluster_centers: dict[str, tuple] = {}
        for _cn, _cmeta in _clusters.items():
            _ctr = _cmeta.get("center")
            if _ctr and len(_ctr) == 2:
                _cluster_centers[_cn] = tuple(_ctr)

        # 취향 유사도 → 인근 클러스터 허용 반경 매핑
        # 유사도가 높을수록 더 멀리 있는 장소도 가져올 수 있음
        _SIM_TO_RADIUS = [
            (0.70, 5.0),   # 강한 매칭: 5km까지 (반드시 가야 할 장소)
            (0.50, 4.0),   # 중간 매칭: 4km까지
            (0.30, 3.0),   # 약한 매칭: 3km (기본값)
            # 0.30 미만: 제외 (뜬금없는 장소 방지)
        ]
        _ABS_MAX_RADIUS = 5.0  # 절대 최대 탐색 반경

        def _nearby_attraction_pool(cn: str, base_radius_km: float = 3.0) -> list:
            """현재 클러스터 관광지 + 취향 유사도 기반 동적 반경 내 인근 클러스터 관광지 합산.

            user_embedding 있을 때:
              - 유사도 ≥ 0.70 → 최대 5km (취향 완벽 매칭 장소는 멀어도 포함)
              - 유사도 ≥ 0.50 → 최대 4km
              - 유사도 ≥ 0.30 → base_radius_km (기본 3km)
              - 유사도 < 0.30 → 제외
            user_embedding 없을 때: base_radius_km 고정
            """
            base = list(ca.get(cn, []))
            used_names = {a["name"] for a in base}
            c_lat, c_lng = _cluster_centers.get(cn, (None, None))
            if c_lat is None:
                return base

            pulled_log = []  # 디버그용

            for other_cn in _cluster_centers:
                if other_cn == cn:
                    continue
                for att in ca.get(other_cn, []):
                    if att["name"] in used_names:
                        continue
                    # 실제 관광지 거리로 필터 (클러스터 중심 거리가 아님)
                    # 클러스터 중심이 멀어도 관광지 자체가 가까우면 포함해야 함
                    att_dist = _haversine(c_lat, c_lng, att["lat"], att["lng"])
                    if att_dist > _ABS_MAX_RADIUS:
                        continue  # 관광지 자체가 ABS_MAX(5km) 초과 → 스킵

                    if user_embedding and "embedding" in att:
                        from utils.embedder import cosine_sim
                        sim = cosine_sim(user_embedding, att["embedding"])
                        # 유사도 → 허용 반경 결정
                        allowed_radius = 0.0
                        for threshold, radius in _SIM_TO_RADIUS:
                            if sim >= threshold:
                                allowed_radius = max(radius, base_radius_km)
                                break
                        if allowed_radius == 0.0 or att_dist > allowed_radius:
                            continue
                        if sim >= 0.60:  # 높은 매칭 장소 로그
                            pulled_log.append(f"{att['name']}({other_cn}, sim={sim:.2f}, {att_dist:.1f}km)")
                    else:
                        if att_dist > base_radius_km:
                            continue

                    base.append(att)
                    used_names.add(att["name"])

            if pulled_log:
                self._log(f"    [인근클러스터 고취향 장소] {', '.join(pulled_log)}")
            return base

        # 관광지 중복 방지: 일자 인덱스 기준 캐시 (같은 클러스터 multi-day 지원)
        # 같은 클러스터 2일차엔 1일차에 배정된 관광지 제외 → 다른 관광지 배정
        _global_used_att: set[str] = set()
        att_sel_cache: dict[int, list] = {}  # {day_idx: [attractions]}
        for idx, cn in enumerate(ordered):
            pool = _nearby_attraction_pool(cn, base_radius_km=3.0)
            pool_dedup = [a for a in pool if a["name"] not in _global_used_att]
            selected = _select_attractions(cn, pool_dedup,
                                           n=cfg["att_count"],
                                           min_score=cfg["min_score"],
                                           prefer_verified=cfg["prefer_verified"],
                                           user_embedding=user_embedding)
            for a in selected:
                _global_used_att.add(a["name"])
            att_sel_cache[idx] = selected

        # multi-day 클러스터 로그
        if embed_meta.get("multi_day"):
            for cn, d in embed_meta["multi_day"].items():
                self._log(f"  [multi-day] {cn} → {d}일 배정")
        for idx, cn in enumerate(ordered):
            self._log(f"  Day{idx+1} [{cn}] 관광지후보={[a['name'] for a in att_sel_cache[idx]]} 맛집={len(cr.get(cn,[]))}개")

        from datetime import datetime, timedelta
        base_date = datetime.strptime(self.checkin, "%Y-%m-%d") if self.checkin else datetime.now()
        days, used_rest, used_hotels, used_cuisines = [], set(), [], []

        def fmt(h):
            return f"{int(h):02d}:{int((h-int(h))*60):02d}"
        def get_dur(n):
            return n.get("features", {}).get("avg_duration_hr", 0) or 1.5

        for i, cname in enumerate(ordered):
            day_num = i + 1
            date_str = (base_date + timedelta(days=i)).strftime("%Y-%m-%d")
            next_cl = ordered[i+1] if i+1 < len(ordered) else None

            att_sel = att_sel_cache[i]
            rest_pool = cr.get(cname, [])
            hotel = _pick_hotel(cname, next_cl, self.hotels, used_hotels, user_embedding=user_embedding)
            used_hotels.append(hotel.get("name", ""))
            hp = {"lat": hotel.get("lat", 37.5665), "lng": hotel.get("lng", 126.978)}

            # ── 1단계: 관광지만 TSP 최적화 (2-opt) ──
            optimized = _optimize_route_full(att_sel, hotel)

            # ── 2단계: 소요시간 cap ──
            MAX_DAY_ATT_HR = 7.0
            total_att_hr = sum(get_dur(a) for a in optimized)
            scale = min(1.0, MAX_DAY_ATT_HR / total_att_hr) if total_att_hr > 0 else 1.0

            def pick(prev, nxt, pm, pt=None, mt=""):
                return _pick_meal(prev, nxt, rest_pool, self.restaurants, used_rest, used_cuisines, max_dist, (0, pm), pt, meal_type=mt,
                                  min_score=cfg["min_score"], prefer_verified=cfg["prefer_verified"],
                                  user_embedding=user_embedding, budget_target=pm)
            def reg(r):
                if r:
                    used_rest.add(r["name"])
                    c = r.get("features", {}).get("cuisine_type", "")
                    if c: used_cuisines.append(c)

            # ── 3단계: 시간 흐름에 따라 관광지 배치 + 식사 삽입 ──
            schedule = []
            clock = 9.0

            # 아침 (09:00, 숙소 → 첫 관광지 사이)
            first = optimized[0] if optimized else hp
            b = pick(hp, first, bk_max, mt="breakfast"); reg(b)
            if b:
                schedule.append(ScheduleItem(fmt(clock), "meal", "breakfast", b, 1.0))
                clock += 1.0

            # 관광지 총 소요시간이 짧을 때 clock을 점심 타이밍까지 앞당김 방지
            # 예: 관광지 2개 × 1.5hr = 3hr → 9+1(아침)+3 = 13시. 정상
            # 예: 관광지 1개 × 1.0hr = 1hr → 9+1+1 = 11시. 점심 못 먹음 → 12로 보정 예정

            # 관광지 순회하면서 시간대에 맞춰 식사 삽입
            had_lunch = had_cafe = False
            for ai, att in enumerate(optimized):
                dur = round(get_dur(att) * scale, 1)
                nxt = optimized[ai + 1] if ai + 1 < len(optimized) else hp

                # 점심: 관광지가 적으면 11:30부터도 허용 (관광지 소요시간이 짧아 12시 못 넘는 경우)
                lunch_threshold = 11.5 if len(optimized) <= 2 else 12.0
                if not had_lunch and clock >= lunch_threshold:
                    prev = schedule[-1].node if schedule else hp
                    l = pick(prev, att, ln_max, mt="lunch"); reg(l)
                    if l:
                        schedule.append(ScheduleItem(fmt(clock), "meal", "lunch", l, 1.0))
                        clock += 1.0
                    had_lunch = True

                # 카페 (14:30~17:30 사이) — 실제 카페/디저트 업종만 배정
                if not had_cafe and 14.5 <= clock <= 17.5:
                    prev = schedule[-1].node if schedule else hp
                    c = pick(prev, att, ln_max, pt=["카페","디저트","커피","베이커리"], mt="cafe"); reg(c)
                    # 카페/디저트 키워드가 없는 업소는 스킵 (식당이 배정되는 것 방지)
                    if c:
                        kc = c.get("kakao_category", "")
                        cuisine = c.get("features", {}).get("cuisine_type", "")
                        is_real_cafe = any(kw in kc or kw in cuisine for kw in ["카페", "디저트", "커피", "베이커리", "차", "아이스크림"])
                        if is_real_cafe:
                            schedule.append(ScheduleItem(fmt(clock), "cafe", "cafe", c, 0.5))
                            clock += 0.5
                            had_cafe = True  # 실제 카페 배정 성공 시에만 True
                        else:
                            used_rest.discard(c["name"])  # 등록 취소, 재시도 허용
                    # c가 None이거나 비카페면 had_cafe = False 유지 → backup에서 재시도

                # 관광지
                schedule.append(ScheduleItem(fmt(clock), "attraction", "", att, dur))
                clock += dur

            # 관광지가 적어 clock이 12시 전에 끝난 경우: 점심 타이밍 보정
            # (관광지 1~2개짜리 클러스터에서 식사 슬롯이 누락되는 것 방지)
            if not had_lunch and clock < 12.0:
                clock = 12.0

            # 보충: 아직 점심 못 먹었으면
            if not had_lunch:
                prev = schedule[-1].node if schedule else hp
                l = pick(prev, hp, ln_max, mt="lunch"); reg(l)
                if l:
                    schedule.append(ScheduleItem(fmt(clock), "meal", "lunch", l, 1.0))
                    clock += 1.0

            # 보충: 아직 카페 못 갔으면
            if not had_cafe and clock <= 18.0:
                prev = schedule[-1].node if schedule else hp
                c = pick(prev, hp, ln_max, pt=["카페","디저트","커피","베이커리"], mt="cafe"); reg(c)
                if c:
                    kc = c.get("kakao_category", "")
                    cuisine = c.get("features", {}).get("cuisine_type", "")
                    is_real_cafe = any(kw in kc or kw in cuisine for kw in ["카페", "디저트", "커피", "베이커리", "차", "아이스크림"])
                    if is_real_cafe:
                        schedule.append(ScheduleItem(fmt(clock), "cafe", "cafe", c, 0.5))
                        clock += 0.5
                    else:
                        used_rest.discard(c["name"])

            # 저녁 (18:30~20:00, 마지막 관광지 → 숙소 방향)
            last = schedule[-1].node if schedule else hp
            d = pick(last, hp, dn_max, mt="dinner"); reg(d)
            if d:
                dinner_time = max(min(clock, 20.0), 18.5)
                schedule.append(ScheduleItem(fmt(dinner_time), "meal", "dinner", d, 1.5))

            # 나이트라이프 (20:30, 저녁 식사 이후 — 취향 점수 + 클러스터 성격 기반)
            if cfg["nightlife_enabled"] and _should_add_nightlife(
                    i, ordered, prefs.get("nightlife", 3), self.duration):
                dinner_node = d if d else last
                nl = _pick_nightlife(dinner_node, rest_pool, self.restaurants, used_rest, max_dist,
                                    nightlife_mode=cfg.get("nightlife_mode", "any"),
                                    user_embedding=user_embedding)
                if nl:
                    reg(nl)
                    schedule.append(ScheduleItem("20:30", "nightlife", "nightlife", nl, 2.0))

            concept = _cluster_concept(cname)
            atts = [s.node["name"] for s in schedule if s.type == "attraction"]
            meals = [s.node["name"] for s in schedule if s.type in ("meal", "cafe")]
            lms = [a for a in atts if any(lm.lower() in a.lower() or a.lower() in lm.lower() for lm in _cluster_landmarks(cname))]
            style_desc = {"balanced":"균형형","threshold":"하한보장형","peak":"피크경험형","risk_averse":"위험회피형","budget_safe":"예산안전형"}.get(style, style)
            notes = f"[{style_desc}] {cname}({concept}) 탐방. {'필수 코스 ' + ', '.join(lms) + ' 포함. ' if lms else ''}{len(atts)}곳 관광, {len(meals)}곳 맛집."

            dp = DayPlan(day=day_num, date=date_str, hotel=hotel, cluster_name=cname, schedule=schedule, notes=notes)
            self._log(f"  Day{day_num} [{cname}] 관광={atts} 식사={meals}")
            tl = " → ".join(f"{s.time} {s.node['name']}" for s in schedule)
            self._log(f"  타임라인: {tl}")
            days.append(dp)

        unique_hotels = {}
        for d in days:
            h_name = d.hotel.get("name", "") if d.hotel else ""
            unique_hotels[h_name] = unique_hotels.get(h_name, 0) + 1
        unique_hotels.pop("", None)  # 호텔 없는 날(빈 이름) 제외
        hotel_cost = sum((h["features"].get("price_per_night") or 0) * nights
                         for h in self.hotels
                         for name, nights in unique_hotels.items()
                         if h["name"] == name)

        return {
            "trip": self.trip,
            "summary": {
                "total_days": self.duration, "destination": self.trip.get("destination"),
                "scoring_style": style,
                "cluster_plan": ordered, "hotels_used": list(unique_hotels.keys()),
                "estimated_hotel_cost": hotel_cost,
                "top_attractions": [a["name"] for a in sorted(self.attractions, key=lambda x: x["node_score"], reverse=True)[:5]],
                "top_restaurants": [r["name"] for r in sorted(self.restaurants, key=lambda x: x["node_score"], reverse=True)[:5]],
            },
            "itinerary": [d.to_dict() for d in days],
        }
