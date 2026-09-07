import asyncio
import html
import logging
import os
import shutil
import tempfile
import time
import uuid
from typing import Dict, List, Optional

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

# 진행 중인 요청(배경 제거 -> 팩 선택 -> 조각 수 -> 대기열/결제 -> 제작)을 잠깐 들고 있는 저장소.
_jobs: Dict[str, dict] = {}
# 다음에 오는 일반 텍스트 메시지를 "이 job의 입력"으로 받아야 하는 유저 목록.
_awaiting: Dict[int, str] = {}
_JOB_TTL_SECONDS = 15 * 60

# 처리 대기열: 결제(또는 관리자) 건은 _priority_queue, 무료 건은 _normal_queue에 FIFO로 쌓인다.
# 슬롯이 비면 항상 _priority_queue를 먼저 비운다.
_priority_queue: List[str] = []
_normal_queue: List[str] = []
_active_count = 0


def _new_job(user_id: int, first_name: str) -> str:
    job_id = uuid.uuid4().hex[:10]
    _jobs[job_id] = {
        "user_id": user_id,
        "first_name": first_name,
        "kind": None,  # "photo" | "video" | "text" - 어느 입력으로 시작됐는지
        "chat_id": None,
        "status_message_id": None,
        "stage": None,
        "bg_mode": None,  # "nukki" | "plain" (photo/video 전용, 실제 제거는 처리 시작 시점에 수행)
        "anim_mode": None,  # "static" | "gif" (text 전용)
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
        "priority": None,  # "high" | "normal" - 대기열에서의 우선순위
        "consume_quota": False,  # 처리 시작 시점에 무료 횟수를 소비해야 하는지
        "_queue_ahead": None,
        "_processing": False,
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


def _remove_from_queues(job_id: str) -> None:
    if job_id in _priority_queue:
        _priority_queue.remove(job_id)
    if job_id in _normal_queue:
        _normal_queue.remove(job_id)


def _discard_job(job_id: str) -> None:
    """job을 완전히 정리한다(대기 텍스트 등록, 대기열, 임시 파일 포함)."""
    job = _jobs.pop(job_id, None)
    if job is None:
        return
    if _awaiting.get(job["user_id"]) == job_id:
        _awaiting.pop(job["user_id"], None)
    _remove_from_queues(job_id)
    _cleanup_job_files(job)


async def cleanup_pending(context: ContextTypes.DEFAULT_TYPE) -> None:
    """방치된 요청을 주기적으로 정리한다(임시 파일 포함). 이미 처리(제작)가 시작된
    job은 파일을 다른 코루틴이 쓰고 있을 수 있으므로 건드리지 않는다."""
    now = time.time()
    stale_ids = [
        jid
        for jid, job in _jobs.items()
        if not job.get("_processing") and now - job["ts"] > _JOB_TTL_SECONDS
    ]
    any_queued = any(jid in _priority_queue or jid in _normal_queue for jid in stale_ids)
    for jid in stale_ids:
        _discard_job(jid)
    if any_queued:
        await _refresh_queue_positions(context)


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
        await query.edit_message_text("⌛ 이미 종료되었거나 만료된 요청입니다.")
        return

    was_queued = job_id in _priority_queue or job_id in _normal_queue
    _discard_job(job_id)
    await _update_status(
        context,
        job,
        "❌ <b>요청을 취소했습니다.</b>\n사진, GIF/영상을 보내거나 문구를 입력하시면 다시 시작할 수 있습니다.",
    )
    if was_queued:
        await _refresh_queue_positions(context)


# ---------------------------------------------------------------------------
# 기본 명령어
# ---------------------------------------------------------------------------


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "✨ <b>이모지 팩 제작소</b>\n\n"
        "사진, GIF/영상 또는 문구를 보내주시면 텔레그램 <b>커스텀(프리미엄) 이모지 팩</b>을\n"
        "제작해 드립니다.\n"
        "(예시: <code>펭구 화이팅</code>)\n\n"
        "<blockquote>💡 화질 유지를 위해 사진은 <b>파일(문서)</b>로 압축 없이 전송해 주십시오.</blockquote>\n\n"
        "<b>💰 이용 안내</b>\n"
        f"▸ 제작 1건당 <b>{config.STAR_PRICE} ⭐ Stars</b>\n"
        "▸ 매일 무료로도 이용 가능합니다\n"
        "▸ 기존에 만든 팩에 이어서 추가할 수 있습니다\n"
        "▸ 대기열이 있을 경우, 결제 시 우선 처리됩니다\n\n"
        "지금 바로 사진, GIF/영상을 보내거나 원하는 문구를 입력해 주십시오."
    )


# ---------------------------------------------------------------------------
# 사진/영상 수신 -> 배경 제거 여부 질문
# ---------------------------------------------------------------------------


async def _ask_bg_choice(message: Message, job_id: str) -> None:
    if config.OFFER_BACKGROUND_REMOVAL:
        rows = [
            [
                InlineKeyboardButton("🖼 원본 유지", callback_data=f"bg:plain:{job_id}"),
                InlineKeyboardButton("✂️ 배경 제거 후 진행", callback_data=f"bg:nukki:{job_id}"),
            ]
        ]
        text = "🖌 <b>배경을 제거하시겠습니까?</b>"
    else:
        rows = [[InlineKeyboardButton("▶️ 시작", callback_data=f"bg:plain:{job_id}")]]
        text = "🚀 <b>이모지 팩 제작을 시작하시겠습니까?</b>"
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

    await update.message.reply_text("🤔 지원하지 않는 파일 형식입니다. 이미지 또는 GIF/영상을 보내 주십시오.")


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
        await query.edit_message_text("⌛ 요청이 만료되었습니다. 사진을 다시 보내 주십시오.")
        return

    # 실제 배경 제거는 무겁기 때문에(rembg/ffmpeg) 여기서 바로 실행하지 않고 선택만
    # 기록해둔 뒤, 동시 처리 대기열에 들어가는 시점(_produce_tiles)에 수행한다.
    job["bg_mode"] = "nukki" if mode == "nukki" else "plain"
    job["sticker_format"] = StickerFormat.VIDEO if job["kind"] == "video" else StickerFormat.STATIC
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
    await _send_status(message, _jobs[job_id], "🎨 <b>폰트를 선택해 주십시오.</b>", _font_keyboard(job_id))


async def on_font_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        _, job_id, font_key = query.data.split(":", 2)
    except ValueError:
        return

    job = _jobs.get(job_id)
    if job is None:
        await query.edit_message_text("⌛ 요청이 만료되었습니다. 문구를 다시 입력해 주십시오.")
        return

    job["font_key"] = font_key
    await _update_status(context, job, "🎨 <b>색상 스타일을 선택해 주십시오.</b>", _style_keyboard(job_id))


async def on_style_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        _, job_id, style_key = query.data.split(":", 2)
    except ValueError:
        return

    job = _jobs.get(job_id)
    if job is None:
        await query.edit_message_text("⌛ 요청이 만료되었습니다. 문구를 다시 입력해 주십시오.")
        return

    job["style_key"] = style_key
    await _update_status(
        context, job, "🖼 정지 이미지와 🎬 움직이는 GIF 중 선택해 주십시오.", _anim_keyboard(job_id)
    )


async def on_anim_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        _, job_id, mode = query.data.split(":", 2)
    except ValueError:
        return

    job = _jobs.get(job_id)
    if job is None:
        await query.edit_message_text("⌛ 요청이 만료되었습니다. 문구를 다시 입력해 주십시오.")
        return

    # 글자 이모지화는 세로를 항상 이모지 한 칸(1행)으로 고정한다 - 가로는 문구 길이에
    # 맞춰 자동으로 늘어나므로, 사진과 달리 "조각 개수"를 따로 물어볼 필요가 없다.
    # 실제 렌더링/분할은 무겁기 때문에 여기서 바로 하지 않고 대기열에 들어가는 시점
    # (_produce_tiles)에 수행한다.
    job["anim_mode"] = mode
    job["sticker_format"] = StickerFormat.STATIC if mode == "static" else StickerFormat.VIDEO
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

    await _update_status(context, job, "📦 <b>이모지 팩을 선택해 주십시오.</b>", _with_cancel(rows, job_id))


async def on_pack_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    if len(parts) < 3:
        return
    mode, job_id = parts[1], parts[2]

    job = _jobs.get(job_id)
    if job is None:
        await query.edit_message_text("⌛ 요청이 만료되었습니다. 다시 시도해 주십시오.")
        return

    if mode == "new":
        job["stage"] = "await_pack_name"
        _awaiting[job["user_id"]] = job_id
        await _update_status(
            context,
            job,
            "📝 <b>새로 만들 팩의 이름을 입력해 주십시오.</b>\n<blockquote>예시: 펭구팩</blockquote>",
            _with_cancel([], job_id),
        )
        return

    idx = int(parts[3])
    candidates = job["pack_candidates"]
    if idx >= len(candidates):
        await _update_status(context, job, "⌛ 선택이 만료되었습니다. 다시 시도해 주십시오.")
        return
    candidate = candidates[idx]
    job["target_pack"] = {"mode": "existing", "pack_name": candidate.pack_name, "title": candidate.title}
    await _after_target_chosen(context, job_id)


async def _after_target_chosen(context: ContextTypes.DEFAULT_TYPE, job_id: str) -> None:
    """팩 대상(새 팩/기존 팩)이 정해진 뒤 다음 단계로 이어간다.

    사진/업로드 영상은 아직 조각 수를 물어봐야 하고, 글자 이모지화는 세로 1칸 고정이라
    조각 수를 물어볼 필요 없이 바로 대기열/결제 단계로 넘어간다.
    """
    job = _jobs[job_id]
    if job["kind"] == "text":
        await _finish_choices_and_enqueue(context, job_id)
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
        f"🔢 <b>원하는 조각(이모지) 개수를 입력해 주십시오.</b>\n<blockquote>권장 범위: {lo}~{hi}개</blockquote>",
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
            await update.message.reply_text("숫자로 입력해 주십시오. (예: 8)")
            return
        if count < 1 or count > config.MAX_TILES:
            await update.message.reply_text(f"1~{config.MAX_TILES} 사이의 숫자를 입력해 주십시오.")
            return
        _awaiting.pop(user.id, None)
        job["tile_count"] = count
        await _finish_choices_and_enqueue(context, job_id)
        return


# ---------------------------------------------------------------------------
# 선택 완료 -> 우선순위 결정 -> 대기열 등록/결제
# ---------------------------------------------------------------------------


async def _finish_choices_and_enqueue(context: ContextTypes.DEFAULT_TYPE, job_id: str) -> None:
    """사용자가 모든 선택(배경 제거 여부/팩 대상/조각 수 또는 폰트·색상·모드)을 마친
    뒤 호출된다. 관리자거나 오늘 무료 횟수가 남아있으면 곧바로(무료로) 처리 대기열에
    등록하고, 그렇지 않으면 결제부터 요청한다. 실제 배경 제거/분할/제작은 아직 시작하지
    않고, 대기열에서 순서가 되었을 때(_run_processing)에야 시작한다."""
    job = _jobs[job_id]
    user_id = job["user_id"]

    if _is_admin(user_id):
        await _enter_processing_queue(context, job_id, priority="high", consume_quota=False)
        return

    if db.has_free_quota(user_id):
        await _enter_processing_queue(context, job_id, priority="normal", consume_quota=True)
        return

    await _update_status(
        context,
        job,
        "🚫 <b>오늘의 무료 제작 횟수를 모두 사용하셨습니다.</b>\n"
        "<u>내일 다시 무료로 이용하실 수 있습니다.</u>\n\n"
        f"지금 바로 제작하시려면 아래에서 <b>{config.STAR_PRICE} ⭐ Stars</b>를 결제해 주십시오.",
    )
    await context.bot.send_invoice(
        chat_id=job["chat_id"],
        title="이모지 팩 제작",
        description="이모지 팩 제작 서비스 1건입니다. (사진/GIF/영상/문구 공통)",
        payload=f"emojijob:{job_id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice("이모지 팩 제작", config.STAR_PRICE)],
    )


# ---------------------------------------------------------------------------
# 처리 대기열: 최대 config.MAX_CONCURRENT_JOBS건을 동시에 처리하고, 나머지는
# 우선순위(_priority_queue: 결제/관리자 -> _normal_queue: 무료) 순으로 대기한다.
# ---------------------------------------------------------------------------


async def _enter_processing_queue(
    context: ContextTypes.DEFAULT_TYPE, job_id: str, *, priority: str, consume_quota: bool
) -> None:
    job = _jobs[job_id]
    job["priority"] = priority
    job["consume_quota"] = consume_quota
    if priority == "high":
        _priority_queue.append(job_id)
    else:
        _normal_queue.append(job_id)

    _try_dispatch(context)

    if job_id in _priority_queue or job_id in _normal_queue:
        ahead = (_priority_queue + _normal_queue).index(job_id)
        job["_queue_ahead"] = ahead
        await _show_queue_status(context, job, job_id, ahead)


def _try_dispatch(context: ContextTypes.DEFAULT_TYPE) -> None:
    """빈 슬롯이 있는 만큼 대기열에서 다음 작업을 꺼내 백그라운드로 처리를 시작한다."""
    global _active_count
    dispatched_any = False
    while _active_count < config.MAX_CONCURRENT_JOBS and (_priority_queue or _normal_queue):
        job_id = _priority_queue.pop(0) if _priority_queue else _normal_queue.pop(0)
        if job_id not in _jobs:
            continue
        _active_count += 1
        dispatched_any = True
        asyncio.create_task(_run_processing(context, job_id))
    if dispatched_any:
        asyncio.create_task(_refresh_queue_positions(context))


async def _refresh_queue_positions(context: ContextTypes.DEFAULT_TYPE) -> None:
    """대기 순서가 바뀌었을 수 있는 나머지 대기열 job들의 상태 메시지를 갱신한다."""
    combined = _priority_queue + _normal_queue
    for idx, job_id in enumerate(combined):
        job = _jobs.get(job_id)
        if job is None:
            continue
        if job.get("_queue_ahead") == idx:
            continue
        job["_queue_ahead"] = idx
        try:
            await _show_queue_status(context, job, job_id, idx)
        except Exception:  # noqa: BLE001
            logger.exception("대기열 상태 갱신 실패")


async def _show_queue_status(context: ContextTypes.DEFAULT_TYPE, job: dict, job_id: str, ahead: int) -> None:
    if ahead == 0:
        position_line = "곧 처리가 시작됩니다."
    else:
        position_line = f"회원님 앞에 <b>{ahead}명</b>이 대기하고 있습니다."

    if job.get("priority") == "high":
        text = f"🕒 <b>우선 처리 대기열에 등록되었습니다.</b>\n\n{position_line}"
        rows: List[List[InlineKeyboardButton]] = []
    else:
        text = (
            "🕒 <b>처리 대기열에 등록되었습니다.</b>\n\n"
            f"{position_line}\n\n"
            f"⚡ 지금 <b>{config.STAR_PRICE} ⭐ Stars</b>를 결제하시면 대기 없이 우선 처리됩니다."
        )
        rows = [
            [
                InlineKeyboardButton(
                    f"⭐ {config.STAR_PRICE} Stars 결제하고 우선 처리", callback_data=f"queueskip:{job_id}"
                )
            ]
        ]
    await _update_status(context, job, text, _with_cancel(rows, job_id))


async def on_queue_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        _, job_id = query.data.split(":", 1)
    except ValueError:
        return

    job = _jobs.get(job_id)
    if job is None:
        await query.edit_message_text("⌛ 이미 종료되었거나 만료된 요청입니다.")
        return

    if job.get("priority") == "high":
        await query.answer("이미 우선 처리 대상으로 등록되어 있습니다.", show_alert=True)
        return

    await context.bot.send_invoice(
        chat_id=job["chat_id"],
        title="우선 처리 결제",
        description="대기열을 건너뛰고 우선 처리합니다.",
        payload=f"queueskip:{job_id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice("우선 처리", config.STAR_PRICE)],
    )


# ---------------------------------------------------------------------------
# 결제 처리
# ---------------------------------------------------------------------------


async def on_pre_checkout_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.pre_checkout_query
    payload = query.invoice_payload or ""
    job_id: Optional[str] = None
    if payload.startswith("emojijob:") or payload.startswith("queueskip:"):
        job_id = payload.split(":", 1)[1]
    if job_id is None or job_id not in _jobs:
        await query.answer(ok=False, error_message="요청이 만료되었습니다. 처음부터 다시 시도해 주십시오.")
        return
    await query.answer(ok=True)


async def _refund_and_notify_expired(context: ContextTypes.DEFAULT_TYPE, update: Update) -> None:
    payment = update.message.successful_payment
    try:
        await context.bot.refund_star_payment(
            user_id=update.effective_user.id,
            telegram_payment_charge_id=payment.telegram_payment_charge_id,
        )
    except Exception:  # noqa: BLE001
        logger.exception("환불 실패")
    await update.message.reply_text(
        "⚠️ <b>결제는 완료되었으나 요청이 만료되어 처리할 수 없습니다.</b>\n"
        "결제하신 Stars는 환불해 드렸습니다. 다시 시도해 주십시오."
    )


async def on_successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    payment = update.message.successful_payment
    payload = payment.invoice_payload or ""

    if payload.startswith("emojijob:"):
        job_id = payload.split(":", 1)[1]
        job = _jobs.get(job_id)
        if job is None:
            await _refund_and_notify_expired(context, update)
            return
        job["payment_charge_id"] = payment.telegram_payment_charge_id
        await _update_status(
            context, job, "✅ <b>결제가 완료되었습니다.</b>\n\n⚡ 우선 처리 대상으로 등록되었습니다."
        )
        await _enter_processing_queue(context, job_id, priority="high", consume_quota=False)
        return

    if payload.startswith("queueskip:"):
        job_id = payload.split(":", 1)[1]
        job = _jobs.get(job_id)
        if job is None:
            await _refund_and_notify_expired(context, update)
            return
        job["payment_charge_id"] = payment.telegram_payment_charge_id
        job["consume_quota"] = False
        job["priority"] = "high"
        _remove_from_queues(job_id)
        _priority_queue.append(job_id)
        await _update_status(context, job, "✅ <b>결제가 완료되었습니다.</b>\n\n⚡ 우선 처리로 전환되었습니다.")
        _try_dispatch(context)
        if job_id in _priority_queue:
            ahead = (_priority_queue + _normal_queue).index(job_id)
            job["_queue_ahead"] = ahead
            await _show_queue_status(context, job, job_id, ahead)
        return

    await _refund_and_notify_expired(context, update)


# ---------------------------------------------------------------------------
# 실제 처리(배경 제거/분할/렌더링 -> 팩 용량 확인 -> 제작) - 대기열에서 순서가
# 되면 백그라운드 태스크로 실행된다. 무거운 동기 작업(rembg, ffmpeg, Pillow)은
# asyncio.to_thread로 감싸서 이벤트 루프를 막지 않고, 최대 config.MAX_CONCURRENT_JOBS
# 개까지 실제로 동시에 실행될 수 있게 한다.
# ---------------------------------------------------------------------------


async def _run_processing(context: ContextTypes.DEFAULT_TYPE, job_id: str) -> None:
    global _active_count
    try:
        job = _jobs.get(job_id)
        if job is None:
            return
        job["_processing"] = True
        await _update_status(
            context, job, "⚙️ <b>이모지 팩을 제작하고 있습니다...</b>\n잠시만 기다려 주십시오."
        )

        if not await _produce_tiles(context, job_id):
            return

        job = _jobs.get(job_id)
        if job is None:
            return

        if job["target_pack"]["mode"] == "existing":
            if not await _check_existing_pack_capacity(context, job_id):
                return

        job = _jobs.get(job_id)
        if job is None:
            return

        if job.get("consume_quota"):
            db.try_consume_free_quota(job["user_id"])

        await _finalize_job(context, job_id)
    finally:
        _active_count -= 1
        _try_dispatch(context)
        asyncio.create_task(_refresh_queue_positions(context))


async def _produce_tiles(context: ContextTypes.DEFAULT_TYPE, job_id: str) -> bool:
    """배경 제거(선택 시) + 분할/렌더링을 실제로 수행해 job["grid"]/job["tiles"]를
    채운다. 실패하면 job을 정리하고 에러 메시지를 남긴 뒤 False를 반환한다."""
    job = _jobs[job_id]
    kind = job["kind"]

    try:
        if kind == "photo":
            image_bytes = job.pop("raw_bytes", None)
            if job.get("bg_mode") == "nukki":
                image_bytes = await asyncio.to_thread(remove_background_image, image_bytes)
            grid, tiles_matrix = await asyncio.to_thread(
                split_static_image, image_bytes, job["tile_count"], config.MAX_TILES
            )
            tiles = [t for row in tiles_matrix for t in row]

        elif kind == "video":
            path = job["source_video_path"]
            if job.get("bg_mode") == "nukki":
                nukki_path = path + ".nukki.webm"
                await asyncio.to_thread(
                    remove_background_video, path, nukki_path, config.ANIM_TILE_MAX_DURATION
                )
                if os.path.exists(path):
                    os.remove(path)
                path = nukki_path
                job["source_video_path"] = path
            out_dir = tempfile.mkdtemp(prefix="emoji_tiles_")
            job["tile_dir"] = out_dir
            grid, tile_paths = await asyncio.to_thread(
                split_animated,
                path,
                out_dir,
                job["tile_count"],
                config.MAX_TILES,
                config.ANIM_TILE_MAX_DURATION,
                config.ANIM_TILE_MAX_BYTES,
            )
            tiles = [p for row in tile_paths for p in row]
            if job["source_video_path"] and os.path.exists(job["source_video_path"]):
                os.remove(job["source_video_path"])
                job["source_video_path"] = None

        else:  # kind == "text"
            phrase, font_key, style_key = job["phrase"], job["font_key"], job["style_key"]
            if job["anim_mode"] == "static":
                job["image_bytes"] = await asyncio.to_thread(render_text_emoji, phrase, font_key, style_key)
                grid, tiles_matrix = await asyncio.to_thread(
                    split_static_image_single_row, job["image_bytes"]
                )
                tiles = [t for row in tiles_matrix for t in row]
            else:
                video_path = os.path.join(tempfile.gettempdir(), f"text_emoji_{job_id}.webm")
                job["source_video_path"] = video_path
                await asyncio.to_thread(render_text_emoji_animated, phrase, font_key, style_key, video_path)
                out_dir = tempfile.mkdtemp(prefix="emoji_tiles_")
                job["tile_dir"] = out_dir
                grid, tile_paths = await asyncio.to_thread(
                    split_animated_single_row,
                    video_path,
                    out_dir,
                    config.ANIM_TILE_MAX_DURATION,
                    config.ANIM_TILE_MAX_BYTES,
                )
                tiles = [p for row in tile_paths for p in row]
    except Exception as exc:  # noqa: BLE001
        logger.exception("이모지 생성/분할 실패")
        _discard_job(job_id)
        await _update_status(context, job, f"❌ <b>처리 중 오류가 발생했습니다.</b>\n{_esc(str(exc))}")
        return False

    if grid.total > config.MAX_TILES:
        _discard_job(job_id)
        await _update_status(
            context,
            job,
            f"❌ <b>이모지 개수가 최대 한도({config.MAX_TILES}개)를 초과했습니다.</b>\n"
            "문구 또는 조각 수를 줄여서 다시 시도해 주십시오.",
        )
        return False

    job["grid"] = grid
    job["tiles"] = tiles

    if kind == "text":
        await _update_status(context, job, f"✅ <b>{grid.cols}x1</b> ({grid.total}개)로 생성되었습니다.")
    elif grid.total != job.get("tile_count"):
        await _update_status(
            context,
            job,
            f"✅ 요청하신 {job['tile_count']}개와 가장 근접한 "
            f"<b>{grid.cols}x{grid.rows}</b> ({grid.total}개)로 분할했습니다.",
        )
    return True


async def _check_existing_pack_capacity(context: ContextTypes.DEFAULT_TYPE, job_id: str) -> bool:
    job = _jobs[job_id]
    target = job["target_pack"]
    live = await get_live_pack_state(context.bot, target["pack_name"])
    if live is None:
        _discard_job(job_id)
        await _update_status(
            context, job, f"❌ <b>'{_esc(target['title'])}' 팩을 찾을 수 없습니다.</b>\n다시 시도해 주십시오."
        )
        return False
    live_fmt, live_count = live
    if live_fmt != job["sticker_format"].value or live_count + job["grid"].total > MAX_PACK_CAPACITY:
        _discard_job(job_id)
        await _update_status(
            context,
            job,
            f"❌ <b>'{_esc(target['title'])}' 팩에는 더 추가할 수 없습니다.</b> (용량 초과)\n"
            "다른 팩으로 다시 시도해 주십시오.",
        )
        return False
    return True


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
                context,
                job,
                f"❌ <b>이모지 팩 생성에 실패했습니다.</b>\n{_esc(str(exc))}\n"
                "결제하신 Stars는 환불해 드렸습니다.",
            )
        else:
            await _update_status(context, job, f"❌ <b>이모지 팩 생성에 실패했습니다.</b>\n{_esc(str(exc))}")
        _cleanup_job_files(job)
        return

    _cleanup_job_files(job)

    link = f"https://t.me/addemoji/{pack_name}"
    admin_note = (
        "\n\n<blockquote>👑 관리자 계정으로 무제한 이용 중입니다.</blockquote>" if _is_admin(user_id) else ""
    )
    await _update_status(
        context,
        job,
        "🎉 <b>이모지 팩 제작이 완료되었습니다.</b>\n\n"
        f"📦 팩 이름: <b>{_esc(title)}</b>\n"
        f"🔢 이모지 개수: <b>{grid.cols}x{grid.rows}</b> ({grid.total}개)\n"
        f"🔗 {link}"
        f"{admin_note}",
    )
