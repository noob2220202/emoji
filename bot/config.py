import os
import tempfile

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# 타일 하나가 원본 이미지에서 차지했으면 하는 목표 픽셀 크기.
# 이 값을 기준으로 원본 이미지 비율에 맞춰 열/행 개수가 자동 계산된다.
# 값이 작을수록 타일(이모지)이 더 잘게, 더 많이 나뉜다.
TARGET_TILE_PX = int(os.getenv("TARGET_TILE_PX", "60"))

# 이모지 팩 하나에 들어갈 수 있는 최대 타일(이모지) 개수. 텔레그램 자체 한도(현재 200)에 맞춤.
MAX_TILES = int(os.getenv("MAX_TILES", "200"))

# 영상/GIF 이모지 최대 길이(초). 텔레그램 커스텀 이모지 영상 스티커 규격상 3초 이하.
MAX_VIDEO_DURATION = float(os.getenv("MAX_VIDEO_DURATION", "3"))

# 영상 이모지 하나의 최대 용량(바이트). 텔레그램 규격상 256KB 이하.
MAX_VIDEO_BYTES = int(os.getenv("MAX_VIDEO_BYTES", str(256 * 1024)))

# 생성되는 각 이모지에 기본으로 매핑할 연관 이모지(검색용, 화면에 보이는 그림과는 무관).
EMOJI_PLACEHOLDER = os.getenv("EMOJI_PLACEHOLDER", "\U0001F642")  # 🙂

TMP_DIR = os.getenv("TMP_DIR", tempfile.gettempdir())

# 배경 제거(누끼)에 사용할 rembg 모델. u2netp는 가볍고 빠른 대신 품질이 다소 낮고,
# u2net/isnet-general-use 등은 더 정확하지만 느리다.
REMBG_MODEL = os.getenv("REMBG_MODEL", "u2netp")

# 배경 제거를 요청할지 물어보는 선택지를 보여줄지 여부. false면 항상 원본 그대로 처리한다.
OFFER_BACKGROUND_REMOVAL = os.getenv("OFFER_BACKGROUND_REMOVAL", "true").lower() != "false"

# 무료 횟수/이모지팩 목록을 저장하는 SQLite 파일 경로.
DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "..", "data", "bot.db"))

# 하루 무료 횟수가 초기화되는 기준 시간대. 자정(00:00) 기준으로 리셋된다.
QUOTA_TIMEZONE = os.getenv("QUOTA_TIMEZONE", "Asia/Seoul")

# 하루 무료 처리 횟수 (이미지/GIF 별도 카운트)
FREE_IMAGE_PER_DAY = int(os.getenv("FREE_IMAGE_PER_DAY", "2"))
FREE_GIF_PER_DAY = int(os.getenv("FREE_GIF_PER_DAY", "1"))

# 무료 횟수를 다 쓴 뒤 텔레그램 Stars로 결제할 때의 가격 (Stars 개수, 정수)
STAR_PRICE_IMAGE = int(os.getenv("STAR_PRICE_IMAGE", "10"))
STAR_PRICE_GIF = int(os.getenv("STAR_PRICE_GIF", "20"))
