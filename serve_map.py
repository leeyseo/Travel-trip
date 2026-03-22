"""
일정 지도 서버
.env에서 카카오 키를 읽어 HTML에 주입하고 로컬 서버를 띄움.

사용법:
  python serve_map.py
  python serve_map.py --port 8080

.env 필요 키:
  KAKAO_JS_KEY=your_javascript_key
  KAKAO_REST_KEY=your_rest_api_key
"""

import http.server
import socketserver
import os
import sys
from pathlib import Path

# .env 로드 (dotenv 없어도 동작)
def load_env(path=".env"):
    env = {}
    p = Path(path)
    if not p.exists():
        # 상위 폴더도 탐색
        for parent in [Path("."), Path(".."), Path("../..")]:
            candidate = parent / ".env"
            if candidate.exists():
                p = candidate
                break
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip("'\"")
    return env

env = load_env()
KAKAO_JS_KEY = env.get("KAKAO_JS_KEY", os.environ.get("KAKAO_JS_KEY", ""))
KAKAO_REST_KEY = env.get("KAKAO_REST_KEY", os.environ.get("KAKAO_REST_KEY", ""))

if not KAKAO_JS_KEY or not KAKAO_REST_KEY:
    print("⚠️  .env에 KAKAO_JS_KEY, KAKAO_REST_KEY를 설정하세요")
    print("   예시:")
    print("   KAKAO_JS_KEY=abc123...")
    print("   KAKAO_REST_KEY=def456...")
    print()

PORT = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8000
DIRECTORY = str(Path(__file__).parent / "output") if (Path(__file__).parent / "output").exists() else "."


class EnvInjectHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def do_GET(self):
        # itinerary_map.html 요청 시 키 주입
        if self.path == "/" or self.path.endswith("itinerary_map.html"):
            html_path = Path(DIRECTORY) / "itinerary_map.html"
            if html_path.exists():
                content = html_path.read_text(encoding="utf-8")
                content = content.replace("여기에_JAVASCRIPT_키", KAKAO_JS_KEY)
                content = content.replace("여기에_REST_API_키", KAKAO_REST_KEY)

                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content.encode("utf-8"))))
                self.end_headers()
                self.wfile.write(content.encode("utf-8"))
                return

        # 나머지는 일반 파일 서빙
        super().do_GET()


print(f"🗺️  일정 지도 서버")
print(f"   디렉토리: {DIRECTORY}")
print(f"   카카오 JS 키: {'✅ 로드됨' if KAKAO_JS_KEY else '❌ 없음'}")
print(f"   카카오 REST 키: {'✅ 로드됨' if KAKAO_REST_KEY else '❌ 없음'}")
print(f"   http://localhost:{PORT}/itinerary_map.html")
print()

with socketserver.TCPServer(("", PORT), EnvInjectHandler) as httpd:
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n서버 종료")
