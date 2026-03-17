"""
카카오맵 API 테스트
python test_kakao.py
"""
from dotenv import load_dotenv
load_dotenv()

import os
import requests

KAKAO_KEY = os.environ.get("KAKAO_API_KEY", "")

def test_kakao(query: str, destination: str = "서울"):
    if not KAKAO_KEY:
        print("❌ KAKAO_API_KEY가 .env에 없습니다!")
        return

    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    params = {
        "query": f"{destination} {query}",
        "size": 3,
    }
    headers = {"Authorization": f"KakaoAK {KAKAO_KEY}"}

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=5)
        print(f"상태코드: {resp.status_code}")
        resp.raise_for_status()

        docs = resp.json().get("documents", [])
        if not docs:
            print(f"❌ '{query}' 검색 결과 없음")
            return

        print(f"✅ '{query}' 검색 결과 {len(docs)}개:")
        for d in docs:
            print(f"   - {d['place_name']}  ({d['y']}, {d['x']})  {d.get('address_name','')}")

    except requests.HTTPError as e:
        print(f"❌ HTTP 에러: {e}")
        print(f"   응답: {resp.text[:200]}")
    except Exception as e:
        print(f"❌ 에러: {e}")

if __name__ == "__main__":
    print("=== 카카오맵 API 테스트 ===\n")
    test_kakao("우래옥", "서울")
    print()
    test_kakao("경복궁", "서울")
    print()
    test_kakao("범어사", "부산")
