import html
import logging
import os
import shutil
import tempfile
import time
import uuid
from typing import Dict, List

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Message, Update
from telegram.constants import StickerFormat
from telegram.ext import ContextTypes

from . import config, db
from .background_removal import remove_background_image, remove_background_video
from .grid import Grid
from .image_split import split_static_image, split_static_image_single_row
from .stickers import MAX_PACK_CAPACITY, add_tiles_to_pack, create_new_pack, get_live_pack_state
from .text_emoji import FONTS, STYLES, render_text_emoji, render_text_emoji_animated
from .video_split import split_animated, split_animated_single_row

logger = logging.getLogger(__name__)

# 문서로 보낸 파일의 mime 타입 -> 임시 저장 확장자 (영상/GIF만 대상, 정지 이미지는 별도 처리).
_VIDEO_MIME_EXT = {"image/gif": ".gif", "video/mp4": ".mp4", "video/webm": ".webm"}

# 진행 중인 요청(배경 제거 -> 팩 선택 -> 조각 수 -> 결제)을 잠깐 들고 있는 저장소.
_jobs: Dict[str, dict] = {}
# 다음에 오는 일반 텍스트 메시지를 "이 job의 입력"으로 받아야 하는 유저 목록.
_awaiting: Dict[int, str] = {}
_JOB_TTL_SECONDS = 15 * 60


def _new_job(user_id: int, first_name: str) -> str:
    job_id = uuid.uuid4().hex[:10]
    _jobs[job_id] = {
        "user_id": user_id,
        "first_name": first_name,
        "kind": None,  # "photo" | "video" | "text" - 어느 입력으로 시작됐는지
        "chat_id": None,
        "status_message_id": None,
        "stage": None,
        "sticker_format": None,  # StickerFormat.STATIC | StickerFormat.VIDEO
        "image_bytes": None,  # 정지: PNG 바이트
        "source_video_path": None,  # 영상 계열: 분할 전 원본(업로드 영상 또는 글자 배너) webm/mp4/gif 경로
        "tile_dir": None,  # 영상 계열: 타일 webm들이 들어있는 임시 디렉터리
        "grid": None,
        "tiles": None,
        "pack_candidates": [],
        "target_pack": None,
        "tile_count": None,
        "phrase": None,
        "font_key": None,
        "style_key": None,
        "ts": time.time(),
    }
    return job_id


def _cleanup_job_files(job: dict) -> None:
    path = job.get("source_video_path")
    if path and os.path.exists(path):
        os.remove(path)
    tile_dir = job.get("tile_dir")
    if tile_dir and os.path.exists(tile_dir):
        shutil.rmtree(tile_dir, ignore_errors=True)


def _discard_job(job_id: str) -> None:
    """job을 완전히 정리한다(대기 텍스트 등록, 임시 파일 포함)."""
    job = _jobs.pop(job_id, None)
    if job is None:
        return
    if _awaiting.get(job["user_id"]) == job_id:
        _awaiting.pop(job["user_id"], None)
    _cleanup_job_files(job)


async def cleanup_pending(context: ContextTypes.DEFAULT_TYPE) -> None:
    """방치된 요청을 주기적으로 정리한다(임시 영상 파일 포함)."""
    now = time.time()
    stale_ids = [jid for jid, job in _jobs.items() if now - job["ts"] > _JOB_TTL_SECONDS]
    for jid in stale_ids:
        _discard_job(jid)


def _esc(text: str) -> str:
    return html.escape(text or "")


def _is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_USER_IDS


def _cancel_row(job_id: str) -> List[InlineKeyboardButton]:
    return [InlineKeyboardButton("❌ 취소", callback_data=f"cancel:{job_id}")]


def _with_cancel(rows: List[List[InlineKeyboardButton]], job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(rows + [_cancel_row(job_id)])


async def _send_status(message: Message, job: dict, text: str, reply_markup=None) -> None:
    """새 상태 메시지를 보내고 이후 계속 수정할 수 있도록 위치를 job에 기록한다."""
    msg = await message.reply_text(text, reply_markup=reply_markup)
    job["chat_id"] = msg.chat_id
    job["status_message_id"] = msg.message_id


async def _update_status(context: ContextTypes.DEFAULT_TYPE, job: dict, text: str, reply_markup=None) -> None:
    """job에 기록된 상태 메시지를 수정한다."""
    await context.bot.edit_message_text(
        chat_id=job["chat_id"],
        message_id=job["status_message_id"],
        text=text,
        reply_markup=reply_markup,
    )


async def on_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        _, job_id = query.data.split(":", 1)
    except ValueError:
        return

    job = _jobs.get(job_id)
    if job is None:
        await query.edit_message_text("⌛ 이미 종료된 요청이에요.")
        return

    _discard_job(job_id)
    await _update_status(context, job, "❌ 취소했어요. 사진을 보내거나 문구를 입력하면 다시 시작할 수 있어요.")


# ---------------------------------------------------------------------------
# 기본 명령어
# ---------------------------------------------------------------------------


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "✨ <b>이모지 팩 제작소</b>에 오신 걸 환영해요!\n\n"
        "📸 사진이나 🎞 GIF/영상을 보내거나, ✍️ 만들고 싶은 <b>문구를 그냥 채팅으로 입력</b>하면\n"
        "텔레그램 <b>커스텀(프리미엄) 이모지 팩</b>으로 만들어 드려요.\n"
        "(예: <code>펭구 화이팅</code>)\n\n"
        "<blockquote>💡 화질을 살리려면 사진이 아니라 <b>파일(문서)</b>로 압축 없이 보내주세요.</blockquote>\n\n"
        f"💰 이모지 팩 1회 제작에 <b>{config.STAR_PRICE} ⭐</b>이 들지만, 매일 무료로도 이용하실 수 있어요.\n"
        "📦 기존에 만든 팩이 있으면 이어서 추가할 수도 있어요!\n\n"
        "지금 바로 사진/GIF를 보내거나 원하는 문구를 입력해보세요 🚀"
    )


# ---------------------------------------------------------------------------
# 사진 수신 -> 배경 제거 여부 질문
# ---------------------------------------------------------------------------


async def _ask_bg_choice(message: Message, job_id: str) -> None:
    if config.OFFER_BACKGROUND_REMOVAL:
        rows = [
            [
                InlineKeyboardButton("🖼 원본 그대로", callback_data=f"bg:plain:{job_id}"),
                InlineKeyboardButton("✂️ 배경 제거(누끼) 후", callback_data=f"bg:nukki:{job_id}"),
            ]
        ]
        text = "🖌 <b>배경을 제거(누끼)</b>할까요?"
    else:
        rows = [[InlineKeyboardButton("▶️ 다음", callback_data=f"bg:plain:{job_id}")]]
        text = "이모지 팩을 만들까요?"
    await _send_status(message, _jobs[job_id], text, _with_cancel(rows, job_id))


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    photo = update.message.photo[-1]
    file = await photo.get_file()
    data = await file.download_as_bytearray()
    job_id = _new_job(user.id, user.first_name or "사용자")
    _jobs[job_id]["kind"] = "photo"
    _jobs[job_id]["raw_bytes"] = bytes(data)
    await _ask_bg_choice(update.message, job_id)


async def _start_video_job(update: Update, path: str) -> None:
    user = update.effective_user
    job_id = _new_job(user.id, user.first_name or "사용자")
    job = _jobs[job_id]
    job["kind"] = "video"
    job["source_video_path"] = path
    await _ask_bg_choice(update.message, job_id)


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    document = update.message.document
    mime = document.mime_type or ""
    file = await document.get_file()

    if mime.startswith("image/") and mime != "image/gif":
        user = update.effective_user
        data = await file.download_as_bytearray()
        job_id = _new_job(user.id, user.first_name or "사용자")
        _jobs[job_id]["kind"] = "photo"
        _jobs[job_id]["raw_bytes"] = bytes(data)
        await _ask_bg_choice(update.message, job_id)
        return

    if mime in _VIDEO_MIME_EXT:
        with tempfile.NamedTemporaryFile(suffix=_VIDEO_MIME_EXT[mime], delete=False) as tmp:
            path = tmp.name
        await file.download_to_drive(path)
        await _start_video_job(update, path)
        return

    await update.message.reply_text("🤔 지원하지 않는 파일 형식이에요. 이미지 또는 GIF/영상을 보내주세요.")


async def on_animation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    animation = update.message.animation
    file = await animation.get_file()
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        path = tmp.name
    await file.download_to_drive(path)
    await _start_video_job(update, path)


async def on_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    video = update.message.video
    file = await video.get_file()
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        path = tmp.name
    await file.download_to_drive(path)
    await _start_video_job(update, path)


async def on_bg_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    try:
        _, mode, job_id = query.data.split(":", 2)
    except ValueError:
        return

    job = _jobs.get(job_id)
    if job is None:
        await query.edit_message_text("⌛ 요청이 만료됐어요. 사진을 다시 보내주세요.")
        return

    remove_bg = mode == "nukki"

    if job["kind"] == "video":
        if remove_bg:
            await _update_status(context, job, "✂️ 배경을 제거하는 중... (프레임이 많으면 시간이 걸려요)")
            raw_path = job["source_video_path"]
            nukki_path = raw_path + ".nukki.webm"
            try:
                remove_background_video(raw_path, nukki_path, config.ANIM_TILE_MAX_DURATION)
            except Exception as exc:  # noqa: BLE001
                logger.exception("영상 배경 제거 실패")
                _discard_job(job_id)
                await _update_status(context, job, f"❌ 배경 제거에 실패했습니다: {_esc(str(exc))}")
                return
            if os.path.exists(raw_path):
                os.remove(raw_path)
            job["source_video_path"] = nukki_path
        job["sticker_format"] = StickerFormat.VIDEO
        await _ask_pack_selection(context, job_id)
        return

    image_bytes = job.pop("raw_bytes", None)
    if remove_bg:
        await _update_status(context, job, "✂️ 배경을 제거하는 중...")
        try:
            image_bytes = remove_background_image(image_bytes)
        except Exception as exc:  # noqa: BLE001
            logger.exception("배경 제거 실패")
            _discard_job(job_id)
            await _update_status(context, job, f"❌ 배경 제거에 실패했습니다: {_esc(str(exc))}")
            return

    job["image_bytes"] = image_bytes
    job["sticker_format"] = StickerFormat.STATIC
    await _ask_pack_selection(context, job_id)


# ---------------------------------------------------------------------------
# 글자 이모지화 (문구를 그냥 채팅으로 보내면 시작됨)
# ---------------------------------------------------------------------------


def _font_keyboard(job_id: str) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for key, label in FONTS.items():
        row.append(InlineKeyboardButton(label, callback_data=f"txtfont:{job_id}:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return _with_cancel(rows, job_id)


def _style_keyboard(job_id: str) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for key, style in STYLES.items():
        row.append(InlineKeyboardButton(style.label, callback_data=f"txtstyle:{job_id}:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return _with_cancel(rows, job_id)


def _anim_keyboard(job_id: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("🖼 정지 이미지", callback_data=f"txtanim:{job_id}:static"),
            InlineKeyboardButton("🎬 움직이는 GIF", callback_data=f"txtanim:{job_id}:gif"),
        ]
    ]
    return _with_cancel(rows, job_id)


async def _ask_font_choice(message: Message, job_id: str) -> None:
    await _send_status(message, _jobs[job_id], "🎨 폰트를 선택해주세요.", _font_keyboard(job_id))


async def on_font_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        _, job_id, font_key = query.data.split(":", 2)
    except ValueError:
        return

    job = _jobs.get(job_id)
    if job is None:
        await query.edit_message_text("⌛ 요청이 만료됐어요. 문구를 다시 입력해주세요.")
        return

    job["font_key"] = font_key
    await _update_status(context, job, "🎨 색상 스타일을 선택해주세요.", _style_keyboard(job_id))


async def on_style_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        _, job_id, style_key = query.data.split(":", 2)
    except ValueError:
        return

    job = _jobs.get(job_id)
    if job is None:
        await query.edit_message_text("⌛ 요청이 만료됐어요. 문구를 다시 입력해주세요.")
        return

    job["style_key"] = style_key
    await _update_status(context, job, "🖼 정지 이미지로 만들까요, 🎬 움직이는 GIF로 만들까요?", _anim_keyboard(job_id))


async def on_anim_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        _, job_id, mode = query.data.split(":", 2)
    except ValueError:
        return

    job = _jobs.get(job_id)
    if job is None:
        await query.edit_message_text("⌛ 요청이 만료됐어요. 문구를 다시 입력해주세요.")
        return

    phrase, font_key, style_key = job["phrase"], job["font_key"], job["style_key"]

    # 글자 이모지화는 세로를 항상 이모지 한 칸(1행)으로 고정한다 - 가로는 문구 길이에
    # 맞춰 자동으로 늘어나므로, 사진과 달리 "조각 개수"를 따로 물어볼 필요가 없다.
    if mode == "static":
        await _update_status(context, job, "🖌 이미지를 만드는 중...")
        try:
            job["image_bytes"] = render_text_emoji(phrase, font_key, style_key)
            grid, tiles_matrix = split_static_image_single_row(job["image_bytes"])
        except Exception as exc:  # noqa: BLE001
            logger.exception("글자 이모지 생성 실패")
            _discard_job(job_id)
            await _update_status(context, job, f"❌ 이미지 생성에 실패했습니다: {_esc(str(exc))}")
            return
        job["sticker_format"] = StickerFormat.STATIC
        tiles = [t for row in tiles_matrix for t in row]
    else:
        await _update_status(context, job, "🎬 움직이는 이미지를 만드는 중... (시간이 좀 걸려요)")
        video_path = os.path.join(tempfile.gettempdir(), f"text_emoji_{job_id}.webm")
        job["source_video_path"] = video_path
        try:
            render_text_emoji_animated(phrase, font_key, style_key, video_path)
            out_dir = tempfile.mkdtemp(prefix="emoji_tiles_")
            job["tile_dir"] = out_dir
            grid, tile_paths = split_animated_single_row(
                video_path, out_dir, config.ANIM_TILE_MAX_DURATION, config.ANIM_TILE_MAX_BYTES
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("글자 이모지 애니메이션 생성 실패")
            _discard_job(job_id)
            await _update_status(context, job, f"❌ 이미지 생성에 실패했습니다: {_esc(str(exc))}")
            return
        job["sticker_format"] = StickerFormat.VIDEO
        tiles = [p for row in tile_paths for p in row]

    if grid.total > config.MAX_TILES:
        _discard_job(job_id)
        await _update_status(
            context,
            job,
            f"❌ 문구가 너무 길어서 이모지 팩 최대 개수({config.MAX_TILES}개)를 초과해요. "
            "문구를 줄여서 다시 시도해주세요.",
        )
        return

    job["grid"] = grid
    job["tiles"] = tiles
    await _update_status(context, job, f"✅ <b>{grid.cols}x1</b> ({grid.total}개)로 만들어졌어요!")
    await _ask_pack_selection(context, job_id)


# ---------------------------------------------------------------------------
# 팩 선택(신규/기존) -> 조각 수 입력
# ---------------------------------------------------------------------------


async def _ask_pack_selection(context: ContextTypes.DEFAULT_TYPE, job_id: str) -> None:
    job = _jobs[job_id]
    candidates = db.list_user_packs(job["user_id"], job["sticker_format"].value)
    job["pack_candidates"] = candidates

    rows: List[List[InlineKeyboardButton]] = [
        [InlineKeyboardButton("🆕 새 팩 만들기", callback_data=f"packsel:new:{job_id}")]
    ]
    for idx, p in enumerate(candidates):
        label = f"📦 {p.title} ({p.tile_count}/{MAX_PACK_CAPACITY})"[:64]
        rows.append([InlineKeyboardButton(label, callback_data=f"packsel:use:{job_id}:{idx}")])

    await _update_status(context, job, "📦 어느 이모지 팩에 넣을까요?", _with_cancel(rows, job_id))


async def on_pack_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    if len(parts) < 3:
        return
    mode, job_id = parts[1], parts[2]

    job = _jobs.get(job_id)
    if job is None:
        await query.edit_message_text("⌛ 요청이 만료됐어요. 다시 시도해주세요.")
        return

    if mode == "new":
        job["stage"] = "await_pack_name"
        _awaiting[job["user_id"]] = job_id
        await _update_status(
            context, job, "📝 새 팩 이름을 입력해주세요. (예: 펭구팩)", _with_cancel([], job_id)
        )
        return

    idx = int(parts[3])
    candidates = job["pack_candidates"]
    if idx >= len(candidates):
        await _update_status(context, job, "⌛ 선택이 만료됐어요. 다시 시도해주세요.")
        return
    candidate = candidates[idx]
    job["target_pack"] = {"mode": "existing", "pack_name": candidate.pack_name, "title": candidate.title}
    await _after_target_chosen(context, job_id)


async def _after_target_chosen(context: ContextTypes.DEFAULT_TYPE, job_id: str) -> None:
    """팩 대상(새 팩/기존 팩)이 정해진 뒤 다음 단계로 이어간다.

    사진은 이 시점에 아직 분할 전이라 조각 수를 물어봐야 하고, 글자 이모지화는 팩
    선택 전에 이미 분할이 끝나있으므로(세로 1칸 고정이라 물어볼 필요가 없다) 곧바로
    기존 팩 용량 확인/결제 단계로 넘어간다.
    """
    job = _jobs[job_id]
    if job["grid"] is not None:
        await _finish_target_check_and_proceed(context, job_id)
    else:
        await _ask_tile_count(context, job_id)


async def _ask_tile_count(context: ContextTypes.DEFAULT_TYPE, job_id: str) -> None:
    job = _jobs[job_id]
    job["stage"] = "await_tile_count"
    _awaiting[job["user_id"]] = job_id
    lo, hi = config.RECOMMENDED_TILE_COUNT_MIN, config.RECOMMENDED_TILE_COUNT_MAX
    await _update_status(
        context,
        job,
        f"🔢 원하는 조각(이모지) 개수를 숫자로 입력해주세요.\n<blockquote>권장: {lo}~{hi}개</blockquote>",
        _with_cancel([], job_id),
    )


# ---------------------------------------------------------------------------
# 일반 텍스트 메시지 라우팅
# 대기 중인 job이 없으면 새로 보낸 문구를 글자 이모지화 시작으로 취급하고,
# 있으면 팩 이름/조각 수 입력으로 처리한다.
# ---------------------------------------------------------------------------


async def on_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    text = (update.message.text or "").strip()
    if not text:
        return

    job_id = _awaiting.get(user.id)
    if job_id is None:
        job_id = _new_job(user.id, user.first_name or "사용자")
        _jobs[job_id]["kind"] = "text"
        _jobs[job_id]["phrase"] = text
        await _ask_font_choice(update.message, job_id)
        return

    job = _jobs.get(job_id)
    if job is None:
        _awaiting.pop(user.id, None)
        return
    stage = job.get("stage")

    if stage == "await_pack_name":
        _awaiting.pop(user.id, None)
        job["target_pack"] = {"mode": "new", "title": text[:64]}
        await _after_target_chosen(context, job_id)
        return

    if stage == "await_tile_count":
        try:
            count = int(text)
        except ValueError:
            await update.message.reply_text("숫자로 입력해주세요. (예: 8)")
            return
        if count < 1 or count > config.MAX_TILES:
            await update.message.reply_text(f"1~{config.MAX_TILES} 사이의 숫자를 입력해주세요.")
            return
        _awaiting.pop(user.id, None)
        job["tile_count"] = count
        await _do_split_and_continue(context, job_id)
        return


# ---------------------------------------------------------------------------
# 분할 -> 무료 횟수/결제 확인
# ---------------------------------------------------------------------------


async def _do_split_and_continue(context: ContextTypes.DEFAULT_TYPE, job_id: str) -> None:
    """사진/업로드 영상 job 전용: 사용자가 입력한 조각 수로 실제 분할을 수행한다(2D 그리드
    근사 방식 - 글자 이모지화는 세로 1칸 고정이라 on_anim_choice에서 이미 분할까지 끝내고
    이 함수를 거치지 않는다)."""
    job = _jobs[job_id]

    if job["kind"] == "video":
        await _update_status(context, job, "🔍 영상을 분석하고 이모지로 쪼개는 중... (시간이 좀 걸릴 수 있어요)")
        out_dir = tempfile.mkdtemp(prefix="emoji_tiles_")
        job["tile_dir"] = out_dir
        try:
            grid, tile_paths = split_animated(
                job["source_video_path"],
                out_dir,
                job["tile_count"],
                config.MAX_TILES,
                config.ANIM_TILE_MAX_DURATION,
                config.ANIM_TILE_MAX_BYTES,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("영상 분할 실패")
            _discard_job(job_id)
            await _update_status(context, job, f"❌ 영상 처리 중 오류가 발생했습니다: {_esc(str(exc))}")
            return
        if os.path.exists(job["source_video_path"]):
            os.remove(job["source_video_path"])
            job["source_video_path"] = None
        job["grid"] = grid
        job["tiles"] = [p for row in tile_paths for p in row]
    else:
        await _update_status(context, job, "🔍 분석하고 이모지로 쪼개는 중...")
        try:
            grid, tiles_matrix = split_static_image(job["image_bytes"], job["tile_count"], config.MAX_TILES)
        except Exception as exc:  # noqa: BLE001
            logger.exception("분할 실패")
            _discard_job(job_id)
            await _update_status(context, job, f"❌ 처리 중 오류가 발생했습니다: {_esc(str(exc))}")
            return
        job["grid"] = grid
        job["tiles"] = [t for row in tiles_matrix for t in row]

    # 요청한 조각 수는 정사각형 타일 제약 때문에 정확히 맞지 않고 종횡비에 맞는 가장
    # 가까운 값으로 근사될 수 있으므로, 결제/무료 처리 전에 실제 결과를 보여준다.
    if grid.total != job["tile_count"]:
        await _update_status(
            context,
            job,
            f"✅ 요청하신 {job['tile_count']}개와 가장 비슷한 "
            f"<b>{grid.cols}x{grid.rows}</b> ({grid.total}개)로 분할했어요!",
        )

    await _finish_target_check_and_proceed(context, job_id)


async def _finish_target_check_and_proceed(context: ContextTypes.DEFAULT_TYPE, job_id: str) -> None:
    """분할이 끝난 뒤 공통으로 거치는 단계: 기존 팩을 골랐다면 텔레그램 서버 기준
    실시간 용량/포맷을 다시 확인하고, 문제가 없으면 무료/결제 처리로 넘어간다."""
    job = _jobs[job_id]
    target = job["target_pack"]
    if target["mode"] == "existing":
        live = await get_live_pack_state(context.bot, target["pack_name"])
        if live is None:
            _discard_job(job_id)
            await _update_status(
                context, job, f"❌ '{_esc(target['title'])}' 팩을 찾을 수 없어요. 다시 시도해주세요."
            )
            return
        live_fmt, live_count = live
        if live_fmt != job["sticker_format"].value or live_count + job["grid"].total > MAX_PACK_CAPACITY:
            _discard_job(job_id)
            await _update_status(
                context,
                job,
                f"❌ '{_esc(target['title'])}' 팩에는 더 넣을 수 없어요(용량 초과). 다른 팩으로 다시 시도해주세요.",
            )
            return

    await _check_quota_and_proceed(context, job_id)


async def _check_quota_and_proceed(context: ContextTypes.DEFAULT_TYPE, job_id: str) -> None:
    job = _jobs[job_id]
    user_id = job["user_id"]

    if _is_admin(user_id):
        await _update_status(context, job, "👑 관리자 계정이라 무제한으로 처리할게요!\n\n⏳ 이모지 팩 만드는 중...")
        await _finalize_job(context, job_id)
        return

    if db.try_consume_free_quota(user_id):
        await _update_status(context, job, "🎁 무료로 처리할게요!\n\n⏳ 이모지 팩 만드는 중...")
        await _finalize_job(context, job_id)
        return

    await _update_status(
        context,
        job,
        "🚫 오늘 무료 플랜을 모두 사용하셨어요. <u>내일 다시 무료로</u> 이용하실 수 있어요!\n\n"
        f"지금 바로 만들고 싶다면 <b>{config.STAR_PRICE} Stars</b>로 결제해주세요 ⭐",
    )
    await context.bot.send_invoice(
        chat_id=job["chat_id"],
        title="이모지 팩 제작",
        description=f"{job['grid'].cols}x{job['grid'].rows} ({job['grid'].total}개) 이모지 팩을 만들어요.",
        payload=f"emojijob:{job_id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice("이모지 팩 제작", config.STAR_PRICE)],
    )


async def on_pre_checkout_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.pre_checkout_query
    payload = query.invoice_payload or ""
    job_id = payload.split(":", 1)[1] if payload.startswith("emojijob:") else None
    if job_id is None or job_id not in _jobs:
        await query.answer(ok=False, error_message="요청이 만료됐어요. 이모지 팩을 처음부터 다시 만들어주세요.")
        return
    await query.answer(ok=True)


async def on_successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    payment = update.message.successful_payment
    payload = payment.invoice_payload or ""
    job_id = payload.split(":", 1)[1] if payload.startswith("emojijob:") else None

    if job_id is None or job_id not in _jobs:
        try:
            await context.bot.refund_star_payment(
                user_id=update.effective_user.id,
                telegram_payment_charge_id=payment.telegram_payment_charge_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception("환불 실패")
        await update.message.reply_text(
            "⚠️ 결제는 완료됐지만 요청이 만료돼서 처리할 수 없었어요. 결제한 Stars는 환불해드렸어요.\n"
            "다시 시도해주세요."
        )
        return

    job = _jobs[job_id]
    job["payment_charge_id"] = payment.telegram_payment_charge_id
    await _update_status(context, job, "✅ 결제 완료!\n\n⏳ 이모지 팩 만드는 중...")
    await _finalize_job(context, job_id)


# ---------------------------------------------------------------------------
# 실제 스티커 생성/추가
# ---------------------------------------------------------------------------


async def _finalize_job(context: ContextTypes.DEFAULT_TYPE, job_id: str) -> None:
    job = _jobs.pop(job_id, None)
    if job is None:
        return

    bot = context.bot
    tiles = job["tiles"]
    grid: Grid = job["grid"]
    target = job["target_pack"]
    user_id = job["user_id"]
    charge_id = job.get("payment_charge_id")
    sticker_format = job["sticker_format"].value

    try:
        if target["mode"] == "new":
            me = await bot.get_me()
            title = target["title"][:64]
            pack_name = await create_new_pack(
                bot, user_id, me.username, title, tiles, sticker_format, config.EMOJI_PLACEHOLDER
            )
            db.record_new_pack(pack_name, user_id, title, sticker_format, grid.total)
        else:
            pack_name = target["pack_name"]
            title = target["title"]
            await add_tiles_to_pack(bot, user_id, pack_name, tiles, sticker_format, config.EMOJI_PLACEHOLDER)
            db.add_tiles_to_pack_record(pack_name, grid.total)
    except Exception as exc:  # noqa: BLE001
        logger.exception("이모지 팩 생성/추가 실패")
        if charge_id:
            try:
                await bot.refund_star_payment(user_id=user_id, telegram_payment_charge_id=charge_id)
            except Exception:  # noqa: BLE001
                logger.exception("환불 실패")
            await _update_status(
                context, job, f"❌ 이모지 팩 생성에 실패했습니다: {_esc(str(exc))}\n결제하신 Stars는 환불해드렸어요."
            )
        else:
            await _update_status(context, job, f"❌ 이모지 팩 생성에 실패했습니다: {_esc(str(exc))}")
        _cleanup_job_files(job)
        return

    _cleanup_job_files(job)

    link = f"https://t.me/addemoji/{pack_name}"
    admin_note = "\n\n<blockquote>👑 관리자 계정 — 횟수 제한 무제한</blockquote>" if _is_admin(user_id) else ""
    await _update_status(
        context,
        job,
        "🎉 <b>완성!</b>\n\n"
        f"📦 팩: <b>{_esc(title)}</b>\n"
        f"🔢 <b>{grid.cols}x{grid.rows}</b> ({grid.total}개) 추가됨\n"
        f"🔗 {link}"
        f"{admin_note}",
    )
