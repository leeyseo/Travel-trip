# 🗺️ 여행 플래닝 에이전트

개인화 원스톱 여행 일정 생성 시스템. 여행지·나이대·취향·예산을 입력하면 관광지/맛집/숙소를 수집하고, 지식 그래프 기반으로 동선 최적화된 N박 일정을 자동 생성합니다.

![서울 50대 여행 일정 지도 예시](docs/map_preview.png)

---

## 주요 기능

- **나이대별 개인화** — 20대~60대 취향 프로파일로 장소 수집 및 점수 계산
- **지식 그래프** — 도시별 관광지/맛집/숙소 노드를 로컬에 캐싱, 쿼리 시 5초 이내 일정 생성
- **동선 최적화** — `walking_aversion` 기반 클러스터링 + Nearest Neighbor 라우팅
- **5가지 여행 성향** — balanced / threshold / peak / risk_averse / budget_safe
- **예산 자동 배분** — 숙소 40%, 식비 30% 기준 필터링
- **지도 시각화** — Leaflet 기반 HTML 지도, 날짜별 동선 토글

---

## 폴더 구조

```
files/
├── main.py                        # 브라우징 파이프라인 (관광지+맛집+숙소+플래닝)
├── requirements.txt
├── run_query_tests.py             # 시나리오 테스트 스크립트
├── itinerary_map.html             # 일정 지도 시각화
├── .env                           # API 키 (git 제외)
│
├── agents/
│   ├── attraction_agent.py        # 관광지 브라우징
│   ├── restaurant_agent.py        # 맛집 브라우징
│   ├── hotel_agent.py             # 숙소 브라우징 + 네이버 가격 크롤링
│   └── planning_agent.py          # 일정 생성 (동선 최적화)
│
├── models/
│   └── schemas.py                 # 데이터 모델 + ScoringStyle enum
│
├── utils/
│   ├── web_collector.py           # Serper 검색 + Playwright 크롤링
│   ├── feature_extractor.py       # LLM 피처 추출 + 카카오맵 좌표/교통
│   ├── scorer.py                  # 취향 × 피처 점수 계산
│   └── graph_builder.py           # 엣지/그래프 빌딩 유틸
│
├── graph_builder/
│   ├── build_knowledge_graph.py   # 오프라인 빌드 (나이대별 장소 수집)
│   └── query_knowledge_graph.py   # 실시간 쿼리 (5초 이내 일정 생성)
│
├── knowledge_graph/
│   └── 서울.json                  # 빌드 결과 (125개 노드, 20/30/40/50대)
│
└── output/                        # 생성된 일정 JSON
    ├── 서울_itinerary_20s.json
    ├── 서울_itinerary_30s.json
    ├── 서울_itinerary_40s.json
    └── 서울_itinerary_50s.json
```

---

## 시작하기

### 1. 환경 설정

```bash
git clone https://github.com/your-repo/travel-planning-agent
cd travel-planning-agent
pip install -r requirements.txt
```

`.env` 파일 생성:

```env
ANTHROPIC_API_KEY=...
SERPER_API_KEY=...
KAKAO_API_KEY=...          # 카카오 REST API (장소검색 + 지하철 접근성)
BOOKING_AFFILIATE_ID=...   # Awin Publisher ID (Booking.com 딥링크)
```

### 2. 지식 그래프 빌드 (최초 1회)

```bash
# 특정 나이대만
python graph_builder/build_knowledge_graph.py --age 30s

# 전체 나이대
python graph_builder/build_knowledge_graph.py
```

빌드 결과는 `knowledge_graph/서울.json`에 누적 저장됩니다. 이미 빌드된 나이대는 자동 스킵됩니다.

### 3. 일정 생성 (쿼리)

```bash
python graph_builder/query_knowledge_graph.py \
  --age 30s \
  --budget 1500000 \
  --days 4 \
  --travelers 2 \
  --checkin 2026-05-01 \
  --style balanced
```

결과는 `output/서울_itinerary_30s.json`에 저장됩니다.

### 4. 지도 시각화

브라우저에서 `itinerary_map.html`을 열고, 생성된 JSON 파일을 드래그 앤 드롭합니다.

또는 로컬 서버로 실행:

```bash
python -m http.server 8000
# http://localhost:8000/itinerary_map.html
```

---

## 아키텍처

### 파이프라인 흐름

```
[브라우징 파이프라인]                    [쿼리 파이프라인]
Serper 검색                              knowledge_graph/*.json
    ↓                                         ↓
Playwright 크롤링               filter_nodes() — 나이대·예산 필터
    ↓                                         ↓
LLM 피처 추출 (Haiku)           _cluster_by_day() — 지역 클러스터링
    ↓                                         ↓
카카오맵 좌표·교통 보정          _optimize_route() — Nearest Neighbor
    ↓                                         ↓
scorer.py — 취향 점수 계산       _best_restaurant_near() — 동선 맛집 배정
    ↓                                         ↓
knowledge_graph/*.json upsert    planning_agent.py — 일정 생성 + Claude 설명
```

### 지식 그래프 노드 구조

```json
{
  "place_id": "abc123",
  "name": "경복궁",
  "category": "attraction",
  "lat": 37.5776, "lng": 126.9769,
  "features": {
    "culture_depth": 5,
    "transit_access": 4,
    "entry_fee_krw": 3000
  },
  "meta": {
    "pull_count": 3,
    "seen_in_age_groups": ["20s", "30s", "40s", "50s"],
    "age_scores": {
      "20s": 0.612, "30s": 0.756, "40s": 0.790, "50s": 0.765
    }
  }
}
```

### 계층적 개인화

| 단계 | 내용 |
|------|------|
| **1단계 (빌드)** | 나이대별 취향으로 장소 수집. 20대는 nightlife·activity·shopping 중심, 50대는 culture·nature 중심 |
| **2단계 (쿼리)** | 세부 선호도로 점수 재계산. 예산 필터링 (숙소: 총예산×40%/박수, 맛집: 총예산×30%/끼니×3배 상한) |
| **3단계 (쿼리)** | ScoringStyle로 최종 점수 조정 |

### ScoringStyle

| 스타일 | 설명 | 맛집 예산 상한 |
|--------|------|---------------|
| `balanced` | 가중 평균 (기본) | 끼당 기준×3배 |
| `threshold` | 최악 요소가 전체 결정 | 끼당 기준×3배 |
| `peak` | 극강 경험 하나가 있으면 OK | 끼당 기준×6배 |
| `risk_averse` | 평점·리뷰 낮으면 강한 페널티 | 끼당 기준×3배 |
| `budget_safe` | 예산 초과 시 강한 페널티 | 끼당 기준×3배 |

### 동선 최적화 로직

```
1. 클러스터링: walking_aversion 기반 반경 제한
   - walking_aversion=1 → 최대 18km (이동 OK)
   - walking_aversion=3 → 최대 12km (기본)
   - walking_aversion=5 → 최대  6km (이동 최소화)

2. Seed 선택: 클러스터 반경×1.5 간격 이상 떨어진 장소를 기준점으로

3. 순서 최적화: Nearest Neighbor (숙소 출발 기준)

4. 맛집 선택: 연쇄 동선
   - 점심: 오전 마지막 장소 ↔ 오후 첫 장소 중간 지점
   - 저녁: 오후 마지막 장소 ↔ 숙소 중간 지점

5. 숙소: 클러스터 중심 기반 + walking_aversion 반영 거리 페널티
```

---

## 테스트 시나리오 실행

```bash
# 나이대별 비교 (20s/30s/40s/50s)
python run_query_tests.py --scenario age

# scoring_style별 비교
python run_query_tests.py --scenario style

# 예산 구간별 비교
python run_query_tests.py --scenario budget

# 인원/박수 조건 변화
python run_query_tests.py --scenario travelers

# 전체 16개 시나리오
python run_query_tests.py
```

결과는 `output/test_summary.txt`와 각 시나리오 폴더에 저장됩니다.

---

## 현재 지식 그래프 상태 (서울)

| 항목 | 수치 |
|------|------|
| 총 노드 | 125개 |
| 관광지 | 59개 |
| 맛집 | 37개 |
| 숙소 | 29개 |
| 빌드 완료 나이대 | 20s / 30s / 40s / 50s |
| 미완료 | 60s |

---

## PENDING

- [ ] 60대 knowledge_graph 빌드
- [ ] 카페/간식 카테고리 별도 수집
- [ ] scoring_style 쿼리 시 실제 반영 검증
- [ ] 일정 출력 시간 측정 (목표: 5초 이내)
- [ ] 다른 도시 확장 (부산, 제주 등)
- [ ] `walking_aversion`에 따른 하루 관광지 수 성향 반영

---

## 사용 API·라이브러리

| 항목 | 용도 |
|------|------|
| Anthropic Claude API | 피처 추출 (Haiku), 일정 설명 생성 (Haiku), 플래닝 |
| Serper API | 웹 검색 |
| Playwright | 블로그/리뷰 페이지 크롤링 |
| 카카오 REST API | 장소 좌표 검색, 지하철 접근성 측정 |
| Booking.com Awin | 숙소 딥링크 (수수료 구조) |
| Leaflet.js | 일정 지도 시각화 |

---

## 라이선스

MIT
