from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from typing import Literal
import uvicorn
import os

# 작성해두신 지식 그래프 쿼리 함수 임포트
# 경로(graph_builder)는 실제 프로젝트 구조에 맞게 수정하세요.
from graph_builder.query_knowledge_graph import query_and_plan 

app = FastAPI(
    title="AI Travel Planner API",
    description="지식 그래프 기반 맞춤형 여행 일정 생성 API",
    version="1.0.0"
)

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="AI Travel Planner API")

# --------- 추가할 부분 ---------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 개발 중에는 모두 허용 (실서비스 땐 프론트 도메인만)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/", include_in_schema=False)
def read_root():
    """기본 경로 접속 시 자동으로 Swagger UI(/docs)로 리다이렉트합니다."""
    return RedirectResponse(url="/docs")

# ──────────────────────────────────────────────
# 1. Pydantic Request Models
# ──────────────────────────────────────────────
class TravelerPreferences(BaseModel):
    cleanliness: int = Field(default=3, ge=1, le=5, description="청결도 중시 (1-5)")
    food: int = Field(default=3, ge=1, le=5, description="미식 중시 (1-5)")
    activity: int = Field(default=3, ge=1, le=5, description="액티비티 강도 (1-5)")
    nature: int = Field(default=3, ge=1, le=5, description="자연경관 선호 (1-5)")
    culture: int = Field(default=3, ge=1, le=5, description="역사/문화 선호 (1-5)")
    nightlife: int = Field(default=3, ge=1, le=5, description="야경/밤문화 선호 (1-5)")
    shopping: int = Field(default=3, ge=1, le=5, description="쇼핑 선호 (1-5)")
    walking_aversion: int = Field(default=3, ge=1, le=5, description="도보 기피도 (1-5)")
    scoring_style: Literal["balanced", "threshold", "peak", "risk_averse", "budget_safe"] = Field(
        default="balanced", description="여행자 평가 성향"
    )

class TripGenerateRequest(BaseModel):
    destination: str = Field(default="서울", description="목적지")
    duration_days: int = Field(default=4, ge=1, le=14, description="여행 박수")
    traveler_count: int = Field(default=2, ge=1, description="인원 수")
    age_group: Literal["20s", "30s", "40s", "50s", "60s"] = Field(default="20s", description="연령대")
    budget_krw: int = Field(default=1500000, ge=0, description="총 예산 (원)")
    checkin: str = Field(default="2026-04-01", description="체크인 날짜 (YYYY-MM-DD)")
    variants: int = Field(default=3, ge=1, le=3, description="생성할 일정 개수 (1~3안)")
    preferences: TravelerPreferences


# ──────────────────────────────────────────────
# 2. API Endpoints
# ──────────────────────────────────────────────
@app.post("/api/v1/plans/generate", summary="여행 일정 3안 생성")
async def generate_travel_plan(req: TripGenerateRequest):
    """
    유저의 취향과 예산을 바탕으로 최적화된 N개의 여행 일정을 생성하여 반환합니다.
    """
    try:
        # Pydantic 모델을 딕셔너리로 변환
        prefs_dict = req.preferences.model_dump()
        
        # 이전 단계에서 수정한 query_and_plan 호출 (통합된 JSON 반환)
        result = query_and_plan(
            destination=req.destination,
            age_group=req.age_group,
            preferences=prefs_dict,
            budget_krw=req.budget_krw,
            duration_days=req.duration_days,
            traveler_count=req.traveler_count,
            checkin=req.checkin,
            scoring_style=req.preferences.scoring_style,
            graph_dir="knowledge_graph",  # 실제 그래프 JSON이 있는 폴더
            output_dir="output",          # 결과물을 저장할 임시 폴더
            n_variants=req.variants
        )
        
        # 통합된 JSON 결과를 그대로 프론트엔드에 응답
        return result

    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=f"Knowledge graph data not found: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Plan generation failed: {str(e)}")

# ──────────────────────────────────────────────
# 3. 서버 실행 (테스트용)
# ──────────────────────────────────────────────
if __name__ == "__main__":
    # uvicorn main:app --reload
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)