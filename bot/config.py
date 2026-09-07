import os
import tempfile

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# 타일 하나가 원본 이미지에서 차지했으면 하는 목표 픽셀 크기.
# 이 값을 기준으로 원본 이미지 비율에 맞춰 열/행 개수가 자동 계산된다.
TARGET_TILE_PX = int(os.getenv("TARGET_TILE_PX", "120"))

# 이모지 팩 하나에 들어갈 수 있는 최대 타일(이모지) 개수.
# 텔레그램 자체 한도(현재 최대 200)보다 낮게 잡아 여유를 둔다.
MAX_TILES = int(os.getenv("MAX_TILES", "100"))

# 영상/GIF 이모지 최대 길이(초). 텔레그램 커스텀 이모지 영상 스티커 규격상 3초 이하.
MAX_VIDEO_DURATION = float(os.getenv("MAX_VIDEO_DURATION", "3"))

# 영상 이모지 하나의 최대 용량(바이트). 텔레그램 규격상 256KB 이하.
MAX_VIDEO_BYTES = int(os.getenv("MAX_VIDEO_BYTES", str(256 * 1024)))

# 생성되는 각 이모지에 기본으로 매핑할 연관 이모지(검색용, 화면에 보이는 그림과는 무관).
EMOJI_PLACEHOLDER = os.getenv("EMOJI_PLACEHOLDER", "\U0001F642")  # 🙂

TMP_DIR = os.getenv("TMP_DIR", tempfile.gettempdir())
