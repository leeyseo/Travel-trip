"""
취향 → 피처 가중치 매핑 + 노드 점수 계산

ScoringStyle별 계산 방식:
  BALANCED    : 가중 평균 (기존)
  THRESHOLD   : min(핵심 요소들) 기반 — 최악 요소가 전체를 결정
  PEAK        : max(핵심 요소) 보너스 — 하나의 극강 경험 중시
  RISK_AVERSE : 평점/리뷰 낮으면 강한 페널티
  BUDGET_SAFE : 예산 초과 시 강한 페널티
"""

from dataclasses import dataclass
from models.schemas import (
    TravelerPreferences, AttractionFeatures,
    HotelFeatures, RestaurantFeatures, ScoringStyle
)


@dataclass
class ScoredFeature:
    name: str
    raw_value: float
    normalized: float
    weight: float
    contribution: float


def normalize(val: float, min_=0.0, max_=5.0) -> float:
    return max(0.0, min(1.0, (val - min_) / (max_ - min_)))


# ──────────────────────────────────────────────
# 성향별 최종 점수 계산 엔진
# ──────────────────────────────────────────────
def _apply_scoring_style(
    base_score: float,
    scored: list[ScoredFeature],
    style: str,
    critical_scores: list[float],   # 핵심 요소들의 normalized 점수
    overall_rating: float,          # 종합 평점 (0-5)
    review_count: int,              # 리뷰 수
    budget_fit: float = 1.0,        # 예산 적합도 (0-1)
    bonus: float = 0.0,
) -> float:
    """
    성향에 따라 최종 점수 계산.

    BALANCED    : 가중 평균 그대로
    THRESHOLD   : base * 0.4 + min(critical) * 0.6
                  → 핵심 요소 중 최솟값이 낮으면 전체 점수 끌어내림
    PEAK        : base * 0.4 + max(critical) * 0.6
                  → 핵심 요소 중 최댓값이 높으면 전체 점수 올라감
    RISK_AVERSE : 평점 < 3.5 or 리뷰 < 50이면 강한 페널티
    BUDGET_SAFE : budget_fit < 0.7이면 강한 페널티
    """
    style = style or ScoringStyle.BALANCED

    if style == ScoringStyle.THRESHOLD:
        # 최악의 핵심 요소가 전체를 결정
        min_critical = min(critical_scores) if critical_scores else base_score
        score = base_score * 0.4 + min_critical * 0.6

    elif style == ScoringStyle.PEAK:
        # 하나의 극강 경험이 있으면 나머지 감내
        max_critical = max(critical_scores) if critical_scores else base_score
        score = base_score * 0.4 + max_critical * 0.6

    elif style == ScoringStyle.RISK_AVERSE:
        score = base_score
        # 평점 낮으면 강한 페널티
        if overall_rating < 3.5:
            score *= 0.5
        elif overall_rating < 4.0:
            score *= 0.75
        # 리뷰 부족하면 신뢰도 페널티
        if review_count < 10:
            score *= 0.5
        elif review_count < 50:
            score *= 0.8

    elif style == ScoringStyle.BUDGET_SAFE:
        score = base_score
        # 예산 초과 시 강한 페널티
        if budget_fit < 0.5:
            score *= 0.3   # 예산 크게 초과 → 거의 선택 안 됨
        elif budget_fit < 0.7:
            score *= 0.6
        elif budget_fit < 0.9:
            score *= 0.85

    else:  # BALANCED (기본)
        score = base_score

    return round(min(1.0, score + bonus), 4)


# ──────────────────────────────────────────────
# 관광지 점수 계산
# ──────────────────────────────────────────────
def score_attraction(
    features: AttractionFeatures,
    prefs: TravelerPreferences,
) -> tuple[float, dict]:
    p = prefs
    style = getattr(p, "scoring_style", ScoringStyle.BALANCED)

    scored = [
        ScoredFeature("문화깊이",    features.culture_depth,          normalize(features.culture_depth),          p.culture/5,              0),
        ScoredFeature("포토·감성",   features.photo_worthiness,       normalize(features.photo_worthiness),       p.food/5*0.5,             0),
        ScoredFeature("교통편의",    features.transit_access,         normalize(features.transit_access),         p.walking_aversion/5,     0),
        ScoredFeature("쾌적도",      5-features.crowd_level,          normalize(5-features.crowd_level),          p.cleanliness/5*0.6,      0),
        ScoredFeature("액티비티",    features.activity_level,         normalize(features.activity_level),         p.activity/5,             0),
        ScoredFeature("자연환경",    features.nature_score,           normalize(features.nature_score),           p.nature/5,               0),
        ScoredFeature("야경·나이트", features.nightlife_score,        normalize(features.nightlife_score),        p.nightlife/5,            0),
        ScoredFeature("종합평점",    features.overall_rating,         normalize(features.overall_rating),         0.8,                      0),
    ]
    korean_bonus = 0.05 if features.korean_signage else 0.0
    base, breakdown = _compute(scored)

    # 성향별 핵심 요소: 관광지는 교통편의 + 쾌적도 + 종합평점
    critical = [
        normalize(features.transit_access),
        normalize(5-features.crowd_level),
        normalize(features.overall_rating),
    ]
    final = _apply_scoring_style(
        base, scored, style, critical,
        features.overall_rating, features.review_count,
        bonus=korean_bonus,
    )
    breakdown["scoring_style"] = style
    return final, breakdown


# ──────────────────────────────────────────────
# 숙소 점수 계산
# ──────────────────────────────────────────────
def score_hotel(
    features: HotelFeatures,
    prefs: TravelerPreferences,
    budget_per_night_krw: int,
) -> tuple[float, dict]:
    style = getattr(prefs, "scoring_style", ScoringStyle.BALANCED)
    budget_fit = max(0.0, 1.0 - (features.price_per_night - budget_per_night_krw)
                     / max(budget_per_night_krw, 1))

    scored = [
        ScoredFeature("청결도",     features.cleanliness_score,  normalize(features.cleanliness_score),  prefs.cleanliness/5,       0),
        ScoredFeature("서비스",     features.service_score,      normalize(features.service_score),      prefs.cleanliness/5*0.7,   0),
        ScoredFeature("교통편의",   features.transit_access,     normalize(features.transit_access),     prefs.walking_aversion/5,  0),
        ScoredFeature("한국인친화", features.korean_friendly,    normalize(features.korean_friendly),    prefs.food/5*0.4,          0),
        ScoredFeature("조식품질",   features.breakfast_quality,  normalize(features.breakfast_quality),  prefs.food/5*0.6,          0),
        ScoredFeature("예산적합도", budget_fit*5,                budget_fit,                             1.0,                       0),
        ScoredFeature("성급",       features.star_grade,         normalize(features.star_grade),         0.6,                       0),
        ScoredFeature("종합평점",   features.overall_rating,     normalize(features.overall_rating),     0.8,                       0),
    ]
    base, breakdown = _compute(scored)

    # 핵심 요소: 청결도 + 교통 + 예산적합도
    critical = [
        normalize(features.cleanliness_score),
        normalize(features.transit_access),
        budget_fit,
    ]
    final = _apply_scoring_style(
        base, scored, style, critical,
        features.overall_rating, features.review_count,
        budget_fit=budget_fit,
    )
    breakdown["scoring_style"] = style
    return final, breakdown


# ──────────────────────────────────────────────
# 맛집 점수 계산
# ──────────────────────────────────────────────
def score_restaurant(
    features: RestaurantFeatures,
    prefs: TravelerPreferences,
) -> tuple[float, dict]:
    style = getattr(prefs, "scoring_style", ScoringStyle.BALANCED)
    michelin_map = {"없음": 0, "빕구르망": 2.5, "1스타": 3.5, "2스타": 4.5, "3스타": 5.0}
    michelin_val = michelin_map.get(features.michelin_tier, 0)
    wait_score = max(0, 5 - features.wait_time_min / 6)

    scored = [
        ScoredFeature("맛",         features.taste_score,        normalize(features.taste_score),        prefs.food/5,              0),
        ScoredFeature("분위기",     features.ambiance_score,     normalize(features.ambiance_score),     prefs.food/5*0.5,          0),
        ScoredFeature("현지정통성", features.local_authenticity, normalize(features.local_authenticity), prefs.culture/5*0.4,       0),
        ScoredFeature("청결·위생", features.cleanliness_score,  normalize(features.cleanliness_score),  prefs.cleanliness/5,       0),
        ScoredFeature("교통편의",   features.transit_access,     normalize(features.transit_access),     prefs.walking_aversion/5,  0),
        ScoredFeature("미슐랭티어", michelin_val,                normalize(michelin_val),                prefs.food/5*0.4,          0),
        ScoredFeature("대기(역)",   wait_score,                  normalize(wait_score),                  prefs.walking_aversion/5*0.5, 0),
        ScoredFeature("종합평점",   features.overall_rating,     normalize(features.overall_rating),     0.8,                       0),
    ]
    korean_bonus = 0.04 if features.korean_menu else 0.0
    base, breakdown = _compute(scored)

    # 핵심 요소: 맛 + 청결도 + 종합평점
    critical = [
        normalize(features.taste_score),
        normalize(features.cleanliness_score),
        normalize(features.overall_rating),
    ]
    final = _apply_scoring_style(
        base, scored, style, critical,
        features.overall_rating, features.review_count,
        bonus=korean_bonus,
    )
    breakdown["scoring_style"] = style
    return final, breakdown


# ──────────────────────────────────────────────
# 공통 가중 평균 계산
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
