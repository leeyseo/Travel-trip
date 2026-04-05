"""
취향 텍스트 임베딩 유틸리티

유저의 자연어 취향 텍스트와 클러스터 컨셉 텍스트를 임베딩하여
코사인 유사도로 매칭합니다.

사용 모델: paraphrase-multilingual-MiniLM-L12-v2
  - 한국어·영어 지원
  - 384차원 임베딩
  - 첫 실행 시 자동 다운로드 (~420MB)

설치:
  pip install sentence-transformers
"""

from __future__ import annotations

_model = None


def _get_model():
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "sentence-transformers 미설치.\n"
                "pip install sentence-transformers 실행 후 재시도하세요."
            )
        print("[Embedder] 모델 로딩 중... (paraphrase-multilingual-MiniLM-L12-v2)")
        _model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
        print("[Embedder] 모델 로딩 완료")
    return _model


def embed(text: str) -> list[float]:
    """텍스트 한 건 → 정규화된 임베딩 벡터 (list[float], 384차원)"""
    model = _get_model()
    return model.encode(text, normalize_embeddings=True).tolist()


def embed_batch(texts: list[str]) -> list[list[float]]:
    """텍스트 여러 건 배치 처리 → 임베딩 벡터 리스트 (단건 반복보다 빠름)"""
    if not texts:
        return []
    model = _get_model()
    return model.encode(texts, normalize_embeddings=True).tolist()


def cosine_sim(a: list[float], b: list[float]) -> float:
    """
    코사인 유사도 계산.
    normalize_embeddings=True 로 인코딩된 벡터는 내적과 동일.
    반환값: -1 ~ 1 (높을수록 유사)
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))
