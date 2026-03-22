"""
Booking.com 딥링크 테스트
python test_booking_link.py
"""

AFFILIATE_ID = "2815136"  # ← 여기에 Awin Publisher ID 입력

def make_booking_link(
    destination: str,
    checkin: str,      # "2026-04-01"
    checkout: str,     # "2026-04-03"
    adults: int = 2,
    hotel_name: str = None,
) -> str:
    """Booking.com 딥링크 생성"""
    base = "https://www.booking.com"

    if hotel_name:
        # 특정 호텔 검색
        url = (
            f"{base}/searchresults.html"
            f"?ss={hotel_name}+{destination}"
            f"&checkin={checkin}"
            f"&checkout={checkout}"
            f"&group_adults={adults}"
            f"&aid={AFFILIATE_ID}"
        )
    else:
        # 도시 전체 검색
        url = (
            f"{base}/searchresults.html"
            f"?ss={destination}"
            f"&checkin={checkin}"
            f"&checkout={checkout}"
            f"&group_adults={adults}"
            f"&aid={AFFILIATE_ID}"
        )
    return url


if __name__ == "__main__":
    print("=== Booking.com 딥링크 테스트 ===\n")

    # 테스트 1: 서울 호텔 검색
    link1 = make_booking_link("서울", "2026-04-01", "2026-04-05", adults=2)
    print(f"[서울 전체 검색]")
    print(f"  {link1}\n")

    # 테스트 2: 특정 호텔
    link2 = make_booking_link("서울", "2026-04-01", "2026-04-05", hotel_name="롯데호텔")
    print(f"[특정 호텔 검색]")
    print(f"  {link2}\n")

    # 테스트 3: 부산
    link3 = make_booking_link("부산", "2026-04-01", "2026-04-05", adults=2)
    print(f"[부산 전체 검색]")
    print(f"  {link3}\n")

    print("위 링크를 브라우저에 붙여넣어서 정상 작동하는지 확인하세요!")
    print(f"(aid={AFFILIATE_ID} 파라미터가 URL에 포함되어 있으면 수수료 추적 됩니다)")
