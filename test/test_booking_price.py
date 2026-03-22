"""
Booking.com 가격 크롤링 테스트
python test_booking_price.py
"""
from dotenv import load_dotenv
load_dotenv()

import asyncio
import urllib.parse
from playwright.async_api import async_playwright


async def fetch_booking_price(hotel_name: str, destination: str, checkin: str, checkout: str) -> dict:
    """Booking.com에서 호텔 가격 크롤링"""
    query = urllib.parse.quote(f"{hotel_name} {destination}")
    url = (
        f"https://www.booking.com/searchresults.html"
        f"?ss={query}"
        f"&checkin={checkin}"
        f"&checkout={checkout}"
        f"&group_adults=2"
        f"&lang=ko"
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_extra_http_headers({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })

        try:
            await page.goto(url, timeout=15000)
            await page.wait_for_timeout(3000)

            # 첫 번째 검색 결과 가격 파싱
            # Booking.com 가격 셀렉터 시도
            selectors = [
                "[data-testid='price-and-discounted-price']",
                ".prco-valign-middle-helper",
                "[data-testid='price']",
                ".bui-price-display__value",
                "span[class*='price']",
            ]

            price_text = None
            for sel in selectors:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        price_text = await el.inner_text()
                        if price_text and ("원" in price_text or "₩" in price_text or "KRW" in price_text):
                            break
                except:
                    continue

            # 호텔명 첫 번째 결과
            hotel_el = await page.query_selector("[data-testid='title']")
            found_name = await hotel_el.inner_text() if hotel_el else "?"

            await browser.close()
            return {
                "hotel_name": found_name,
                "price_text": price_text,
                "url": url,
            }

        except Exception as e:
            await browser.close()
            return {"error": str(e), "url": url}


def parse_price_krw(price_text: str) -> int | None:
    """가격 텍스트 → KRW 정수"""
    if not price_text:
        return None
    import re
    # "₩120,000" 또는 "120,000원" 형태
    numbers = re.findall(r'[\d,]+', price_text.replace(" ", ""))
    for n in numbers:
        val = int(n.replace(",", ""))
        if val >= 30000:  # 최소 3만원 이상만 유효
            return val
    return None


async def main():
    print("=== Booking.com 가격 크롤링 테스트 ===\n")

    hotels = [
        ("파크 하얏트 서울", "서울"),
        ("신라스테이", "서울"),
        ("L7 홍대 바이 롯데", "서울"),
    ]
    checkin = "2026-04-01"
    checkout = "2026-04-05"

    for hotel_name, dest in hotels:
        print(f"[{hotel_name}]")
        result = await fetch_booking_price(hotel_name, dest, checkin, checkout)

        if "error" in result:
            print(f"  ❌ 에러: {result['error']}")
        else:
            price = parse_price_krw(result.get("price_text", ""))
            print(f"  검색결과 호텔명: {result['hotel_name']}")
            print(f"  가격 텍스트: {result.get('price_text', '없음')}")
            print(f"  파싱된 가격: {price:,}원" if price else "  가격 파싱 실패")
        print()


if __name__ == "__main__":
    asyncio.run(main())
