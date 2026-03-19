"""
장소 데이터 모델 — 브라우징 에이전트가 생성하는 Node 객체 정의
"""

from dataclasses import dataclass, field
from typing import Literal, Optional
from enum import Enum


class ScoringStyle(str, Enum):
    """
    여행자 평가 성향 유형
    
    BALANCED    : 가중 평균 (기존 방식) — 전반적으로 무난한 여행 선호
    THRESHOLD   : 하한 기준형 — 최악의 요소 하나가 전체를 결정
                  "숙박이 별로면 여행 전체가 망함"
    PEAK        : 하이라이트형 — 하나의 극강 경험이 있으면 나머지는 감내
                  "죽기 전에 꼭 먹어볼 레스토랑이 있으면 OK"
    RISK_AVERSE : 리스크 회피형 — 검증된 곳만, 평점 낮은 곳은 강하게 거부
                  "후기 없는 식당은 절대 안 감"
    BUDGET_SAFE : 예산 최우선형 — 예산 초과 항목이 하나라도 있으면 전체 거부
                  "계획한 예산 넘으면 스트레스"
    """
    BALANCED    = "balanced"
    THRESHOLD   = "threshold"
    PEAK        = "peak"
    RISK_AVERSE = "risk_averse"
    BUDGET_SAFE = "budget_safe"


class PlaceCategory(str, Enum):
    ATTRACTION = "attraction"
    RESTAURANT = "restaurant"
    HOTEL = "hotel"


# ──────────────────────────────────────────────
# 공통 피처 (모든 카테고리에 존재)
# ──────────────────────────────────────────────
@dataclass
class BaseFeatures:
    overall_rating: float       # 0–5  종합 리뷰 평점
    review_count: int           # 리뷰 수
    transit_access: float       # 0–5  대중교통 접근성
    korean_popular: float       # 0–5  한국인 언급 빈도
    price_level: int            # 0–4  가격대 (0=무료/매우저렴)
    lat: float
    lng: float


# ──────────────────────────────────────────────
# 관광지 피처
# ──────────────────────────────────────────────
@dataclass
class AttractionFeatures(BaseFeatures):
    category: Literal[
        "문화유산", "자연", "테마파크", "박물관",
        "전망대", "쇼핑", "현대예술·체험", "신사·자연", "공원·박물관"
    ] = "문화유산"
    entry_fee_krw: int = 0          # 입장료
    avg_duration_hr: float = 2.0    # 평균 체류 시간
    photo_worthiness: float = 3.0   # 0–5 포토스팟 가치
    activity_level: float = 2.0     # 0–5 액티비티 강도
    culture_depth: float = 3.0      # 0–5 역사·문화 깊이
    nature_score: float = 2.0       # 0–5 자연환경 비중
    crowd_level: float = 3.0        # 0–5 혼잡도 (높을수록 혼잡)
    nightlife_score: float = 1.0    # 0–5 야경·나이트라이프
    indoor: bool = False            # 실내 여부
    korean_signage: bool = False    # 한국어 안내 여부
    age_suitability: Literal["all", "young", "family", "senior"] = "all"


# ──────────────────────────────────────────────
# 숙소 피처
# ──────────────────────────────────────────────
@dataclass
class HotelFeatures(BaseFeatures):
    star_grade: float = 3.0         # 0–5 성급
    price_per_night: int = 100000   # 1박 가격 KRW
    room_type: Literal[
        "싱글", "더블", "트윈", "스위트", "패밀리"
    ] = "더블"
    cleanliness_score: float = 3.0  # 0–5 청결도
    service_score: float = 3.0      # 0–5 서비스
    breakfast_quality: float = 0.0  # 0–5 조식 품질 (0=미제공)
    wifi_quality: float = 3.0       # 0–5 와이파이 품질
    center_distance_km: float = 2.0 # 중심부까지 거리 km
    korean_friendly: float = 3.0    # 0–5 한국인 친화도
    has_gym: bool = False
    has_pool: bool = False
    family_friendly: bool = False
    late_checkin: bool = False
    booking_url: str = ""           # Booking.com 딥링크
    booking_url: str = ""


# ──────────────────────────────────────────────
# 맛집 피처
# ──────────────────────────────────────────────
@dataclass
class RestaurantFeatures(BaseFeatures):
    cuisine_type: str = "현지식"    # 현지식/한식/양식/퓨전/길거리
    meal_type: Literal[
        "아침", "점심", "저녁", "카페", "야식", "전체"
    ] = "저녁"
    avg_price_per_person: int = 15000  # 1인 평균 가격 KRW
    taste_score: float = 3.0           # 0–5 맛
    food_diversity: float = 3.0        # 0–5 메뉴 다양성
    local_authenticity: float = 3.0    # 0–5 현지 정통성
    michelin_tier: Literal[
        "없음", "빕구르망", "1스타", "2스타", "3스타"
    ] = "없음"
    ambiance_score: float = 3.0        # 0–5 분위기
    cleanliness_score: float = 3.0     # 0–5 위생
    wait_time_min: int = 15            # 평균 대기 시간 (분)
    reservation_required: bool = False
    korean_menu: bool = False          # 한국어/사진 메뉴 여부
    dietary_options: str = "없음"      # 채식/할랄 등


# ──────────────────────────────────────────────
# 노드 (그래프의 기본 단위)
# ──────────────────────────────────────────────
@dataclass
class PlaceNode:
    place_id: str
    name: str
    address: str
    category: PlaceCategory
    features: AttractionFeatures | HotelFeatures | RestaurantFeatures
    node_score: float = 0.0         # 취향 매칭 점수 (0–1), 그래프 노드 크기
    score_breakdown: dict = field(default_factory=dict)  # 피처별 기여도
    sources: list[str] = field(default_factory=list)    # 데이터 출처 URL

    def to_dict(self) -> dict:
        return {
            "place_id": self.place_id,
            "name": self.name,
            "address": self.address,
            "category": self.category.value,
            "lat": self.features.lat,
            "lng": self.features.lng,
            "node_score": round(self.node_score, 4),
            "score_breakdown": self.score_breakdown,
            "features": self.features.__dict__,
            "sources": self.sources,
        }


# ──────────────────────────────────────────────
# 입력 JSON 스키마
# ──────────────────────────────────────────────
@dataclass
class TravelerPreferences:
    cleanliness: int = 3    # 1–5
    food: int = 3
    activity: int = 3
    nature: int = 3
    culture: int = 3
    nightlife: int = 3
    shopping: int = 3
    walking_aversion: int = 3   # 높을수록 도보 이동 싫어함
    scoring_style: str = ScoringStyle.BALANCED  # 여행자 평가 성향


@dataclass
class TripInput:
    destination: str
    duration_days: int
    traveler_count: int
    age_group: str          # "20s" | "30s" | "40s" | "family" | "senior"
    budget_krw: int
    preferences: TravelerPreferences

    @classmethod
    def from_dict(cls, d: dict) -> "TripInput":
        prefs = TravelerPreferences(**d.get("preferences", {}))
        travelers = d.get("travelers", {})
        return cls(
            destination=d["destination"],
            duration_days=d["duration_days"],
            traveler_count=travelers.get("count", 2),
            age_group=travelers.get("age_group", "30s"),
            budget_krw=d.get("budget_krw", 1000000),
            preferences=prefs,
        )
