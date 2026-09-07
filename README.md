# 텔레그램 프리미엄(커스텀) 이모지 팩 봇

이미지나 GIF/영상을 보내면 자동으로 격자로 잘라 텔레그램 커스텀 이모지 팩(`t.me/addemoji/...`)을 만들어주는 봇입니다.

## 빠른 시작 (git clone → pm2 start)

```bash
git clone <이 저장소 URL>
cd emoji

pip install -r requirements.txt        # 가상환경 없이 시스템에 바로 설치
# (에러 나면: pip install --break-system-packages -r requirements.txt)

cp .env.example .env
# .env를 열어 BOT_TOKEN=... 에 BotFather에서 받은 토큰을 넣는다

npm install -g pm2                     # pm2가 없다면
pm2 start ecosystem.config.js
pm2 logs emoji-bot                     # 정상 기동됐는지 로그 확인
```

각 단계에 대한 자세한 설명은 아래 [설치](#설치), [설정](#설정), [실행](#실행) 항목을 참고하세요.

## 동작 방식

1. 사용자가 사진/파일(이미지) 또는 GIF/영상을 보냅니다.
2. 봇이 **"🖼 원본 그대로" / "✂️ 배경 제거(누끼) 후"**를 버튼으로 물어봅니다. 배경 제거를 고르면
   분할 전에 원본 전체에서 배경을 지웁니다.
   - 이미지: [rembg](https://github.com/danielgatis/rembg)로 배경 제거 → 투명 배경 PNG
   - GIF/영상: 프레임마다 rembg를 돌려 배경을 지운 뒤, 알파 채널을 가진 WEBM(VP9)으로 재조립
3. 원본 가로세로 비율을 보고 정사각형 타일 그리드(열x행)를 자동 계산해서 100x100 규격으로 자릅니다.
   - `TARGET_TILE_PX`(기본 60px)를 목표 타일 크기로 삼아 열 개수를 정하고,
     그 열 개수로 가로를 정확히 나눈 값을 타일 크기로 삼아 행 개수를 정합니다.
   - 이렇게 하면 타일이 항상 정사각형이 되어 100x100으로 리사이즈해도 비율이 왜곡되지 않습니다.
   - 계산된 타일 개수가 `MAX_TILES`(기본 200, 텔레그램 한도)를 넘으면 목표 타일 크기를 키워가며
     (=개수를 줄여가며) 재계산합니다.
   - 정지 이미지: Pillow로 크롭 후 리사이즈 → PNG
   - GIF/영상: ffmpeg로 crop+scale, 3초 이하로 자르고, 256KB 이하가 될 때까지 CRF를 조정하며 재인코딩 → WEBM(VP9)
4. **어느 이모지 팩에 넣을지** 물어봅니다: "🆕 새 팩 만들기" 또는 이미 이 봇으로 만든 팩 중 포맷(이미지/GIF)이
   같고 용량이 남은 팩 목록. 기존 팩을 고르면 텔레그램 서버에 실시간으로 남은 용량을 다시 확인한 뒤 이어 붙입니다.
5. **무료 횟수**가 남아있으면 바로 만들고, 없으면 **텔레그램 Stars**로 결제를 요청합니다(아래
   [요금제](#요금제-무료-횟수--stars-결제) 참고).
6. `createNewStickerSet`(새 팩) 또는 `addStickerToSet`(기존 팩, sticker_type=`custom_emoji`)로 이모지를
   만들고, 완성된 팩 링크를 답장으로 보냅니다.

타일은 왼쪽 위부터 순서대로 만들어지므로, 완성된 이모지들을 순서대로(행 우선) 이어 붙이면 원본 그림이 재구성됩니다.

## 요금제 (무료 횟수 + Stars 결제)

| 종류 | 하루 무료 횟수 | 무료 소진 후 |
|---|---|---|
| 🖼 이미지 | 2회 | 10 ⭐ (텔레그램 Stars) |
| 🎞 GIF/영상 | 1회 | 20 ⭐ (텔레그램 Stars) |

- 무료 횟수는 **매일 자정(기본 KST, `QUOTA_TIMEZONE`으로 변경 가능)**에 초기화됩니다.
- "1회"는 이미지/GIF 한 장을 처리(새 팩 생성이든 기존 팩에 추가든)하는 단위입니다.
- 무료 횟수가 없으면 봇이 텔레그램 결제 UI(Stars 인보이스)를 띄웁니다. 결제가 완료되면 자동으로 이어서
  처리하고, 만약 처리 중 오류가 나면 결제한 Stars를 자동으로 환불합니다(`refundStarPayment`).
- Stars 결제는 `provider_token` 없이 `currency="XTR"`로 보내는 텔레그램 기본 기능이라 별도 PG 연동이
  필요 없습니다.
- `/status` 명령으로 오늘 남은 무료 횟수와 내가 만든 이모지 팩 목록을 볼 수 있습니다.
- 가격/무료 횟수는 `.env`의 `STAR_PRICE_IMAGE`, `STAR_PRICE_GIF`, `FREE_IMAGE_PER_DAY`, `FREE_GIF_PER_DAY`로
  조정할 수 있습니다.

## 이모지 팩 선택 / 이어서 추가하기

텔레그램 Bot API에는 "이 봇이 만든 팩 목록"을 조회하는 방법이 없어서, 봇이 직접 `data/bot.db`
(SQLite, `.gitignore`에 포함되어 커밋되지 않음)에 사용자별로 만든 팩 이름/포맷/타일 수를 기억해둡니다.

- 새로 처리할 때마다 "새 팩 만들기" 또는 (포맷이 같고 용량이 남은) 기존 팩 중 하나를 선택할 수 있습니다.
- 이미지(정지)와 GIF/영상 팩은 텔레그램 규격상 섞을 수 없어서, 이미지는 이미지 팩끼리 GIF는 GIF 팩끼리만
  후보로 보여줍니다.
- 기존 팩을 고르면 로컬 DB 기록과 별개로 `getStickerSet`으로 실시간 상태(포맷/현재 개수)를 한 번 더
  확인해서, 사용자가 텔레그램 앱에서 직접 스티커를 지웠거나 팩이 삭제된 경우에도 안전하게 처리합니다.

## 배경 제거(누끼) 기능

이미지나 GIF/영상을 보내면 봇이 "원본 그대로" / "배경 제거(누끼) 후" 중 하나를 버튼으로 물어봅니다.
배경 제거는 [rembg](https://github.com/danielgatis/rembg)(U^2-Net 기반 오픈소스 배경 제거 라이브러리)를 사용합니다.

- 처음 실행 시 선택한 모델 파일을 자동으로 다운로드합니다(`~/.rembg`에 캐시됨, 인터넷 필요).
- 기본 모델은 빠른 `u2netp`입니다. 정확도가 더 필요하면 `.env`의 `REMBG_MODEL`을 `u2net`이나
  `isnet-general-use` 등으로 바꾸세요(대신 느려집니다).
- GIF/영상은 프레임마다 배경 제거를 돌리기 때문에 프레임 수가 많으면 시간이 꽤 걸릴 수 있습니다.
- 버튼을 누르지 않고 방치된 요청은 15분 후 자동으로 정리됩니다(임시 파일 포함).
- 버튼 자체가 필요 없다면 `.env`의 `OFFER_BACKGROUND_REMOVAL=false`로 끌 수 있습니다(항상 원본 그대로 처리).

## 설치

이 프로젝트는 가상환경 없이 시스템 파이썬에 바로 설치해서 씁니다.

```bash
pip install -r requirements.txt
```

배포판에 따라(Debian/Ubuntu 최신 버전 등) `error: externally-managed-environment` 에러가 날 수 있습니다.
그럴 경우:

```bash
pip install --break-system-packages -r requirements.txt
```

GIF/영상 처리를 위해 시스템에 `ffmpeg`이 설치되어 있으면 그것을 사용하고,
없으면 `imageio-ffmpeg`이 제공하는 내장 바이너리를 자동으로 사용합니다
(내장 바이너리도 동작은 하지만, 가능하면 시스템 ffmpeg 설치를 권장합니다: `apt-get install ffmpeg`).

## 설정

`.env.example`을 `.env`로 복사한 뒤 값을 채웁니다.

```bash
cp .env.example .env
```

- `BOT_TOKEN`: [@BotFather](https://t.me/BotFather)에서 `/newbot`으로 발급받은 토큰을 넣습니다.
  **토큰은 절대 커밋하거나 채팅/로그에 노출하지 마세요.**
- 나머지 값은 필요할 때만 조정하면 됩니다 (`.env.example` 주석 참고).

## 실행

### 그냥 실행 (포그라운드)

```bash
python -m bot.main
```

봇에게 `/start`를 보내거나 바로 이미지/GIF를 전송하면 됩니다. `/status`로 오늘 남은 무료 횟수와
내 이모지 팩 목록을 볼 수 있습니다. 첫 실행 시 `data/bot.db`(SQLite)가 자동으로 생성됩니다.

### pm2로 실행 (백그라운드 + 자동 재시작)

서버에 계속 띄워둘 거라면 [pm2](https://pm2.keymetrics.io/)로 관리하는 걸 추천합니다.

```bash
npm install -g pm2   # pm2가 없다면 먼저 설치 (Node.js/npm 필요)
pm2 start ecosystem.config.js
```

- `ecosystem.config.js`는 저장소에 이미 포함되어 있고, `python3 run.py`를 실행하도록 되어 있습니다
  (`bot/main.py`는 상대 임포트를 쓰기 때문에 `python -m bot.main`처럼 패키지로 실행해야 하는데,
  pm2는 스크립트 파일 경로만 받을 수 있어서 `run.py`라는 얇은 진입점을 따로 뒀습니다).
- `.env` 파일은 `bot/config.py`가 `python-dotenv`로 알아서 읽으므로 pm2 설정에 토큰을 직접 넣을 필요는 없습니다.
  단, `pm2 start`를 실행하는 디렉터리(`cwd`)가 저장소 루트여야 `.env`를 찾습니다.

자주 쓰는 pm2 명령어:

```bash
pm2 status              # 실행 상태 확인
pm2 logs emoji-bot       # 실시간 로그
pm2 restart emoji-bot    # 재시작 (코드나 .env 수정 후)
pm2 stop emoji-bot       # 중지
pm2 delete emoji-bot     # pm2 목록에서 제거
pm2 save                 # 현재 pm2 프로세스 목록 저장
pm2 startup              # OS 재부팅 시 pm2가 자동으로 살아나도록 등록 (출력되는 명령어를 그대로 실행)
```

### 문제 해결: pm2에 online으로 뜨는데 봇이 응답이 없음

`pm2 status`에는 `online`(재시작 횟수 0)으로 나오는데 텔레그램에서 `/start`를 보내도 아무 반응이 없고
로그도 텅 비어있다면, 배경 제거 기능(`rembg`)이 내부적으로 물고 오는 `pymatting`/`numba`가 원인일 수
있습니다. `numba`는 처음 임포트될 때 JIT 컴파일을 시도하는데, CPU 자원이 넉넉하지 않은(다른 프로세스와
공유하는) 서버에서는 이 컴파일이 수십 초~몇 분씩 걸리거나 사실상 멈춘 것처럼 보일 수 있습니다. 이 경우
프로세스는 죽지 않고 계속 "그 임포트 도중"에 멈춰 있는 상태라 pm2는 정상(`online`)이라고 판단하지만,
실제로는 `run_polling()`까지 코드가 도달하지 못해 텔레그램 요청을 전혀 처리하지 못합니다.

이 봇은 `bot/__init__.py`에서 `NUMBA_DISABLE_JIT=1`을 자동으로 설정해서 numba가 컴파일 없이 순수
파이썬으로 즉시 동작하도록 미리 막아뒀습니다(실행 속도 이득은 필요 없는 부분이라 안전합니다). 그래도
같은 증상이 재현된다면:

```bash
pm2 restart emoji-bot
pm2 logs emoji-bot --raw   # 재시작 직후 잠깐 걸린 뒤 정상적으로 응답하는지 확인
```

## 테스트

네트워크나 텔레그램 토큰 없이 실행 가능한 단위 테스트(그리드 계산, 이미지 분할 로직)가 있습니다.

```bash
pip install pytest
pytest
```

영상 분할(`bot/video_split.py`)과 배경 제거(`bot/background_removal.py`)는 각각 ffmpeg 바이너리와
rembg 모델 다운로드(인터넷)가 필요해 단위 테스트에는 포함하지 않았습니다. 실제로 이미지/GIF를
보내서 동작을 확인해보세요.

## 알아두면 좋은 제약사항

- 텔레그램 커스텀 이모지는 정지 이미지는 PNG/WEBP, 애니메이션은 WEBM(VP9)만 지원하며 항상 100x100이어야 합니다.
- 영상 이모지는 최대 3초, 최대 256KB여야 합니다. 이 봇은 자동으로 잘라내고 용량을 맞춥니다(품질 저하가 있을 수 있음).
- 이모지 팩 하나에 넣을 수 있는 이모지 개수는 텔레그램 자체 한도가 있습니다(`MAX_TILES`로 여유 있게 제한).
- 만들어진 이모지 팩(`custom_emoji`)은 텔레그램 프리미엄 사용자만 실제로 "전송"할 수 있고,
  프리미엄이 아닌 사용자도 보는 것은 가능합니다.
- 텔레그램 Stars는 테스트/샌드박스 모드가 따로 없어서, 유료 결제 흐름을 실제로 테스트하려면 계정에
  진짜 Stars가 있어야 합니다. 무료 횟수(`FREE_IMAGE_PER_DAY`/`FREE_GIF_PER_DAY`) 안에서는 결제 없이
  전체 흐름을 테스트할 수 있습니다.
- `data/bot.db`는 이 봇 프로세스가 만든 팩만 기억합니다. 서버를 옮기거나 DB 파일을 지우면 "기존 팩에
  추가" 후보 목록에서 사라지지만(텔레그램에는 팩 자체는 그대로 남아있음), 팩 이름을 알면 텔레그램 앱에서
  직접 편집할 수는 있습니다.

## 다음에 추가할 기능 (로드맵)

- [x] 배경 제거(누끼) 옵션
- [x] 이모지 팩 선택(신규/기존에 이어서 추가)
- [x] 텔레그램 Stars 결제 연동 + 하루 무료 횟수
- [ ] 등장/사라짐 등 이펙트 적용
- [ ] 그리드 크기 수동 지정 옵션 (`/split 5x5`)
- [ ] 생성 직후 완성된 그림을 커스텀 이모지 메시지로 미리보기 전송
