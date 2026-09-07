import os
import tempfile

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# 조각 개수를 물어볼 때 안내 문구에 표시할 권장 범위.
RECOMMENDED_TILE_COUNT_MIN = int(os.getenv("RECOMMENDED_TILE_COUNT_MIN", "6"))
RECOMMENDED_TILE_COUNT_MAX = int(os.getenv("RECOMMENDED_TILE_COUNT_MAX", "10"))

# 이모지 팩 하나에 들어갈 수 있는 최대 타일(이모지) 개수. 텔레그램 자체 한도(현재 200)에 맞춤.
MAX_TILES = int(os.getenv("MAX_TILES", "200"))

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

# 하루 무료 처리 횟수 (이모지 팩 제작 1건 = 1회)
FREE_USES_PER_DAY = int(os.getenv("FREE_USES_PER_DAY", "2"))

# 무료 횟수를 다 쓴 뒤 텔레그램 Stars로 결제할 때의 가격 (Stars 개수, 정수)
STAR_PRICE = int(os.getenv("STAR_PRICE", "10"))

# 무료 횟수 제한과 Stars 결제 없이 항상 무제한으로 처리할 관리자 텔레그램 유저 ID 목록
# (쉼표로 여러 명 구분 가능, 예: "111,222,333").
ADMIN_USER_IDS = {
    int(uid) for uid in os.getenv("ADMIN_USER_IDS", "7648288400").split(",") if uid.strip()
}

# 글자 이모지화 기능에서 쓸 폰트 파일이 들어있는 디렉터리.
FONT_DIR = os.getenv("FONT_DIR", os.path.join(os.path.dirname(__file__), "..", "assets", "fonts"))

# 영상 이모지(글자 이모지화의 움직이는 버전, 그리고 사용자가 직접 올린 GIF/영상) 타일
# 하나의 최대 길이(초)/용량(바이트). 텔레그램 영상 커스텀 이모지 규격상 3초, 256KB
# 이하여야 한다.
ANIM_TILE_MAX_DURATION = float(os.getenv("ANIM_TILE_MAX_DURATION", "3"))
ANIM_TILE_MAX_BYTES = int(os.getenv("ANIM_TILE_MAX_BYTES", str(256 * 1024)))
