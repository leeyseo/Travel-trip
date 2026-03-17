# 여행 브라우징 에이전트

개인화 여행 플래닝 에이전트의 **브라우징 레이어** — 관광지 탐색 모듈.

## 구조

```
travel_agent/
├── main.py                        # 엔트리포인트
├── requirements.txt
├── models/
│   └── schemas.py                 # 데이터 모델 (TripInput, PlaceNode, Features)
├── agents/
│   └── attraction_agent.py        # 관광지 브라우징 에이전트
└── utils/
    ├── web_collector.py           # Serper 검색 + URL 텍스트 수집
    ├── feature_extractor.py       # Claude API로 피처 추출
    ├── scorer.py                  # 취향 가중치 × 피처 → node_score
    └── graph_builder.py           # 노드 + 이동편의성 엣지 → PlaceGraph
```

## 파이프라인

```
JSON 입력
  → [1] 후보 장소명 수집  (시드 목록 + Serper 검색)
  → [2] 장소별 텍스트 수집  (Serper snippet + URL fetch)
  → [3] LLM 피처 추출  (Claude API → AttractionFeatures)
  → [4] 취향 점수 계산  (dot product → node_score 0–1)
  → [5] 그래프 생성  (haversine 거리 × transit_access → 엣지)
  → JSON 출력
```

## 설치 & 실행

```bash
pip install -r requirements.txt

# 환경변수 설정
export ANTHROPIC_API_KEY=sk-ant-...
export SERPER_API_KEY=...          # 없어도 mock 데이터로 동작

# 기본 실행 (도쿄 예시)
python main.py

# 커스텀 입력
python main.py --input my_trip.json --output results/
```

## 입력 JSON 형식

```json
{
  "destination": "도쿄",
  "duration_days": 4,
  "travelers": {
    "count": 2,
    "age_group": "30s"
  },
  "budget_krw": 1500000,
  "preferences": {
    "cleanliness": 5,
    "food": 5,
    "activity": 3,
    "nature": 2,
    "culture": 4,
    "nightlife": 2,
    "shopping": 3,
    "walking_aversion": 4
  }
}
```

## 출력 형식

```json
{
  "attraction_nodes": [
    {
      "place_id": "abc123",
      "name": "센소지",
      "node_score": 0.782,
      "score_breakdown": {
        "문화깊이": {"raw": 4.8, "normalized": 0.96, "weight": 0.8, "contribution": 0.768},
        ...
      },
      "features": { ... }
    }
  ],
  "attraction_graph": {
    "nodes": [...],
    "edges": [
      {
        "source_id": "abc123",
        "target_id": "def456",
        "distance_km": 1.2,
        "transit_score": 0.88,
        "walk_minutes": 14
      }
    ]
  }
}
```

## 다음 단계

- `agents/hotel_agent.py` — 숙소 브라우징 (동일 구조)
- `agents/restaurant_agent.py` — 맛집 브라우징
- `utils/random_walk.py` — 그래프 위 랜덤워크 시뮬레이션
- `agents/planning_agent.py` — 3개 그래프 임베딩 → 일정 최적화
