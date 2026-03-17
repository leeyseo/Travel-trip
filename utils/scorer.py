"""
취향 → 피처 가중치 매핑 + 노드 점수 계산
"""

from dataclasses import dataclass
from models.schemas import (
    TravelerPreferences, AttractionFeatures,
    HotelFeatures, RestaurantFeatures
)


@dataclass
class ScoredFeature:
    name: str
    raw_value: float    # 원본 피처값 (0–5)
    normalized: float   # 0–1 정규화
    weight: float       # 취향 가중치
    contribution: float # normalized × weight


def normalize(val: float, min_=0.0, max_=5.0) -> float:
    return max(0.0, min(1.0, (val - min_) / (max_ - min_)))


# ──────────────────────────────────────────────
# 관광지 점수 계산
# preference × feature 가중치 매핑 테이블
# ──────────────────────────────────────────────
def score_attraction(
    features: AttractionFeatures,
    prefs: TravelerPreferences,
) -> tuple[float, dict]:
    """
    Returns (node_score 0–1, breakdown dict)
    """
    p = prefs

    scored = [
        ScoredFeature(
            "문화깊이",
            features.culture_depth,
            normalize(features.culture_depth),
            weight=p.culture / 5,
            contribution=0,
        ),
        ScoredFeature(
            "포토·감성",
            features.photo_worthiness,
            normalize(features.photo_worthiness),
            weight=p.food / 5 * 0.5,   # 식도락 취향 = 감성 경험 선호와 상관
            contribution=0,
        ),
        ScoredFeature(
            "교통편의",
            features.transit_access,
            normalize(features.transit_access),
            weight=p.walking_aversion / 5,
            contribution=0,
        ),
        ScoredFeature(
            "쾌적도(혼잡역)",
            5 - features.crowd_level,   # 혼잡도 역방향
            normalize(5 - features.crowd_level),
            weight=p.cleanliness / 5 * 0.6,
            contribution=0,
        ),
        ScoredFeature(
            "액티비티",
            features.activity_level,
            normalize(features.activity_level),
            weight=p.activity / 5,
            contribution=0,
        ),
        ScoredFeature(
            "자연환경",
            features.nature_score,
            normalize(features.nature_score),
            weight=p.nature / 5,
            contribution=0,
        ),
        ScoredFeature(
            "야경·나이트",
            features.nightlife_score,
            normalize(features.nightlife_score),
            weight=p.nightlife / 5,
            contribution=0,
        ),
        ScoredFeature(
            "종합평점",
            features.overall_rating,
            normalize(features.overall_rating),
            weight=0.8,  # 기본 품질 앵커 (고정)
            contribution=0,
        ),
    ]

    # 한국어 안내 보너스
    korean_bonus = 0.05 if features.korean_signage else 0.0

    return _compute(scored, korean_bonus)


# ──────────────────────────────────────────────
# 숙소 점수 계산
# ──────────────────────────────────────────────
def score_hotel(
    features: HotelFeatures,
    prefs: TravelerPreferences,
    budget_per_night_krw: int,
) -> tuple[float, dict]:
    # 예산 적합도: 1박 가격이 예산 내면 1.0, 초과할수록 감소
    budget_fit = max(0.0, 1.0 - (features.price_per_night - budget_per_night_krw)
                     / max(budget_per_night_krw, 1))

    scored = [
        ScoredFeature(
            "청결도",
            features.cleanliness_score,
            normalize(features.cleanliness_score),
            weight=p.cleanliness / 5,
            contribution=0,
        )
        for p in [prefs]  # list comprehension 활용 (single item)
    ]

    # 더 명확한 방식으로 재정의
    scored = [
        ScoredFeature("청결도",      features.cleanliness_score, normalize(features.cleanliness_score), prefs.cleanliness/5, 0),
        ScoredFeature("서비스",      features.service_score,     normalize(features.service_score),     prefs.cleanliness/5*0.7, 0),
        ScoredFeature("교통편의",    features.transit_access,    normalize(features.transit_access),    prefs.walking_aversion/5, 0),
        ScoredFeature("한국인친화",  features.korean_friendly,   normalize(features.korean_friendly),   prefs.food/5*0.4, 0),
        ScoredFeature("조식품질",    features.breakfast_quality, normalize(features.breakfast_quality), prefs.food/5*0.6, 0),
        ScoredFeature("예산적합도",  budget_fit*5,               budget_fit,                            1.0, 0),
        ScoredFeature("성급",        features.star_grade,        normalize(features.star_grade),        0.6, 0),
        ScoredFeature("종합평점",    features.overall_rating,    normalize(features.overall_rating),    0.8, 0),
    ]
    return _compute(scored)


# ──────────────────────────────────────────────
# 맛집 점수 계산
# ──────────────────────────────────────────────
def score_restaurant(
    features: RestaurantFeatures,
    prefs: TravelerPreferences,
) -> tuple[float, dict]:
    # 미슐랭 티어 수치 변환
    michelin_map = {"없음": 0, "빕구르망": 2.5, "1스타": 3.5, "2스타": 4.5, "3스타": 5.0}
    michelin_val = michelin_map.get(features.michelin_tier, 0)

    # 대기시간 역방향 (30분이상 = 0, 0분 = 5)
    wait_score = max(0, 5 - features.wait_time_min / 6)

    scored = [
        ScoredFeature("맛",           features.taste_score,        normalize(features.taste_score),        prefs.food/5,       0),
        ScoredFeature("분위기",       features.ambiance_score,     normalize(features.ambiance_score),     prefs.food/5*0.5,   0),
        ScoredFeature("현지정통성",   features.local_authenticity, normalize(features.local_authenticity), prefs.culture/5*0.4, 0),
        ScoredFeature("청결·위생",    features.cleanliness_score,  normalize(features.cleanliness_score),  prefs.cleanliness/5, 0),
        ScoredFeature("교통편의",     features.transit_access,     normalize(features.transit_access),     prefs.walking_aversion/5, 0),
        ScoredFeature("미슐랭티어",   michelin_val,                normalize(michelin_val),                prefs.food/5*0.4,   0),
        ScoredFeature("대기시간(역)", wait_score,                  normalize(wait_score),                  prefs.walking_aversion/5*0.5, 0),
        ScoredFeature("종합평점",     features.overall_rating,     normalize(features.overall_rating),     0.8,                0),
    ]
    korean_bonus = 0.04 if features.korean_menu else 0.0
    return _compute(scored, korean_bonus)


# ──────────────────────────────────────────────
# 공통 계산 엔진
# ──────────────────────────────────────────────
def _compute(
    scored: list[ScoredFeature],
    bonus: float = 0.0,
) -> tuple[float, dict]:
    total_weight = sum(s.weight for s in scored)
    if total_weight == 0:
        return 0.0, {}

    weighted_sum = 0.0
    breakdown = {}
    for s in scored:
        s.contribution = s.normalized * s.weight
        weighted_sum += s.contribution
        breakdown[s.name] = {
            "raw": round(s.raw_value, 2),
            "normalized": round(s.normalized, 3),
            "weight": round(s.weight, 3),
            "contribution": round(s.contribution, 4),
        }

    score = min(1.0, weighted_sum / total_weight + bonus)
    return round(score, 4), breakdown
