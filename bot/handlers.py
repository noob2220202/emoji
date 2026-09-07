import html
import logging
import os
import shutil
import tempfile
import time
import uuid
from typing import Dict, List, Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    Update,
)
from telegram.constants import StickerFormat
from telegram.ext import ContextTypes

from . import config, db
from .background_removal import remove_background_image, remove_background_video
from .grid import Grid
from .image_split import split_static_image
from .stickers import MAX_PACK_CAPACITY, add_tiles_to_pack, create_new_pack, get_live_pack_state
from .video_split import split_animated

logger = logging.getLogger(__name__)

_MIME_EXT = {"image/gif": ".gif", "video/mp4": ".mp4", "video/webm": ".webm"}

# 진행 중인 요청(배경 제거 선택 -> 분할 -> 팩 선택 -> 결제)을 잠깐 들고 있는 저장소.
_jobs: Dict[str, dict] = {}
_JOB_TTL_SECONDS = 15 * 60


def _new_job(user_id: int, first_name: str, kind: str, raw) -> str:
    job_id = uuid.uuid4().hex[:10]
    _jobs[job_id] = {
        "user_id": user_id,
        "first_name": first_name,
        "kind": kind,  # "image" | "video"
        "raw": raw,  # bytes(image) 또는 파일 경로(video, 분할 전 임시 원본)
        "tile_dir": None,  # video 타일들이 들어있는 임시 디렉터리(있으면 통째로 삭제)
        "grid": None,
        "tiles": None,
        "sticker_format": None,
        "pack_candidates": [],
        "ts": time.time(),
    }
    return job_id


async def cleanup_pending(context: ContextTypes.DEFAULT_TYPE) -> None:
    """방치된 요청을 주기적으로 정리한다(임시 영상 파일 포함)."""
    now = time.time()
    stale_ids = [jid for jid, job in _jobs.items() if now - job["ts"] > _JOB_TTL_SECONDS]
    for jid in stale_ids:
        job = _jobs.pop(jid, None)
        if job:
            _cleanup_job_files(job)


def _cleanup_job_files(job: dict) -> None:
    """video 작업에서 남은 임시 원본 파일/타일 디렉터리를 정리한다(image는 메모리 바이트라 없음)."""
    if job.get("kind") != "video":
        return
    raw = job.get("raw")
    if isinstance(raw, str) and os.path.exists(raw):
        os.remove(raw)
    tile_dir = job.get("tile_dir")
    if tile_dir and os.path.exists(tile_dir):
        shutil.rmtree(tile_dir, ignore_errors=True)


def _esc(text: str) -> str:
    return html.escape(text or "")


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "✨ <b>이모지 팩 제작소</b>에 오신 걸 환영해요!\n\n"
        "📸 사진이나 🎞 GIF/영상을 보내주시면 자동으로 격자로 잘라서\n"
        "텔레그램 <b>커스텀(프리미엄) 이모지 팩</b>으로 만들어 드려요.\n\n"
        "<blockquote>💡 화질을 살리려면 사진이 아니라 <b>파일(문서)</b>로 압축 없이 보내주세요.</blockquote>\n\n"
        "<b>💰 요금 안내</b>\n"
        f"🖼 이미지 1회 처리: <b>{config.STAR_PRICE_IMAGE} ⭐</b> · 하루 무료 <b>{config.FREE_IMAGE_PER_DAY}회</b>\n"
        f"🎞 GIF/영상 1회 처리: <b>{config.STAR_PRICE_GIF} ⭐</b> · 하루 무료 <b>{config.FREE_GIF_PER_DAY}회</b>\n"
        "🕛 무료 횟수는 매일 <u>자정(KST)</u>에 초기화돼요.\n\n"
        "📦 기존에 만든 팩이 있으면 이어서 추가할 수도 있어요!\n\n"
        "지금 바로 사진이나 GIF를 보내보세요 🚀"
    )


def _is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_USER_IDS


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    packs = db.list_user_packs(user.id)
    lines = ["📊 <b>내 현황</b>\n"]
    if _is_admin(user.id):
        lines.append("👑 관리자 계정 — 횟수 제한 <b>무제한</b>")
    else:
        img_left, gif_left = db.remaining_free_quota(user.id)
        lines.append(f"🎁 오늘 남은 무료 횟수 — 🖼 이미지 <b>{img_left}회</b> · 🎞 GIF <b>{gif_left}회</b>")
    lines.append("")
    if packs:
        lines.append("📦 <b>내 이모지 팩</b>")
        for p in packs:
            fmt_icon = "🖼" if p.sticker_format == "static" else "🎞"
            lines.append(f"{fmt_icon} {_esc(p.title)} — {p.tile_count}/{MAX_PACK_CAPACITY}개")
    else:
        lines.append("아직 만든 이모지 팩이 없어요. 사진이나 GIF를 보내보세요!")
    await update.message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# 1단계: 파일 수신 -> 배경 제거 여부 질문
# ---------------------------------------------------------------------------


async def _ask_bg_choice(update: Update, kind: str, raw) -> None:
    user = update.effective_user
    job_id = _new_job(user.id, user.first_name or "사용자", kind, raw)
    if config.OFFER_BACKGROUND_REMOVAL:
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🖼 원본 그대로", callback_data=f"bg:plain:{job_id}"),
                    InlineKeyboardButton("✂️ 배경 제거(누끼) 후", callback_data=f"bg:nukki:{job_id}"),
                ]
            ]
        )
        await update.message.reply_text("🖌 <b>배경을 제거(누끼)</b>할까요?", reply_markup=keyboard)
    else:
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("▶️ 이모지 팩 만들기", callback_data=f"bg:plain:{job_id}")]]
        )
        await update.message.reply_text("이모지 팩을 만들까요?", reply_markup=keyboard)


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    photo = update.message.photo[-1]
    file = await photo.get_file()
    data = await file.download_as_bytearray()
    await _ask_bg_choice(update, "image", bytes(data))


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    document = update.message.document
    mime = document.mime_type or ""
    file = await document.get_file()

    if mime.startswith("image/") and mime != "image/gif":
        data = await file.download_as_bytearray()
        await _ask_bg_choice(update, "image", bytes(data))
        return

    if mime in _MIME_EXT:
        with tempfile.NamedTemporaryFile(suffix=_MIME_EXT[mime], delete=False) as tmp:
            path = tmp.name
        await file.download_to_drive(path)
        await _ask_bg_choice(update, "video", path)
        return

    await update.message.reply_text("🤔 지원하지 않는 파일 형식이에요. 이미지 또는 GIF/영상을 보내주세요.")


async def on_animation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    animation = update.message.animation
    file = await animation.get_file()
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        path = tmp.name
    await file.download_to_drive(path)
    await _ask_bg_choice(update, "video", path)


async def on_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    video = update.message.video
    file = await video.get_file()
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        path = tmp.name
    await file.download_to_drive(path)
    await _ask_bg_choice(update, "video", path)


# ---------------------------------------------------------------------------
# 2단계: 배경 제거 선택 -> 분할 -> 팩 선택 질문
# ---------------------------------------------------------------------------


async def on_bg_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    try:
        _, mode, job_id = query.data.split(":", 2)
    except ValueError:
        return

    job = _jobs.get(job_id)
    if job is None:
        await query.edit_message_text("⌛ 요청이 만료됐어요. 이미지를 다시 보내주세요.")
        return

    status_msg = query.message
    remove_bg = mode == "nukki"

    if job["kind"] == "image":
        image_bytes = job["raw"]
        if remove_bg:
            await status_msg.edit_text("✂️ 배경을 제거하는 중...")
            try:
                image_bytes = remove_background_image(image_bytes)
            except Exception as exc:  # noqa: BLE001
                logger.exception("배경 제거 실패")
                _jobs.pop(job_id, None)
                await status_msg.edit_text(f"❌ 배경 제거에 실패했습니다: {_esc(str(exc))}")
                return

        await status_msg.edit_text("🔍 이미지를 분석하고 이모지로 쪼개는 중...")
        try:
            grid, tiles = split_static_image(image_bytes, config.TARGET_TILE_COUNT, config.MAX_TILES)
        except Exception as exc:  # noqa: BLE001
            logger.exception("이미지 분할 실패")
            _jobs.pop(job_id, None)
            await status_msg.edit_text(f"❌ 이미지 처리 중 오류가 발생했습니다: {_esc(str(exc))}")
            return

        job["grid"] = grid
        job["tiles"] = [tile for row in tiles for tile in row]
        job["sticker_format"] = StickerFormat.STATIC
        await _ask_pack_choice(update, context, status_msg, job_id)
        return

    # kind == "video"
    file_path = job["raw"]
    source_path = file_path
    nukki_path = None
    if remove_bg:
        await status_msg.edit_text("✂️ 배경을 제거하는 중... (프레임이 많으면 시간이 걸려요)")
        nukki_path = file_path + ".nukki.webm"
        try:
            remove_background_video(file_path, nukki_path, config.MAX_VIDEO_DURATION)
        except Exception as exc:  # noqa: BLE001
            logger.exception("영상 배경 제거 실패")
            _jobs.pop(job_id, None)
            if os.path.exists(file_path):
                os.remove(file_path)
            await status_msg.edit_text(f"❌ 배경 제거에 실패했습니다: {_esc(str(exc))}")
            return
        source_path = nukki_path

    await status_msg.edit_text("🔍 GIF/영상을 분석하고 이모지로 쪼개는 중... (시간이 좀 걸릴 수 있어요)")
    out_dir = tempfile.mkdtemp(prefix="emoji_tiles_")
    try:
        grid, tile_paths = split_animated(
            source_path,
            out_dir,
            config.TARGET_TILE_COUNT,
            config.MAX_TILES,
            config.MAX_VIDEO_DURATION,
            config.MAX_VIDEO_BYTES,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("영상 분할 실패")
        _jobs.pop(job_id, None)
        shutil.rmtree(out_dir, ignore_errors=True)
        await status_msg.edit_text(f"❌ 영상 처리 중 오류가 발생했습니다: {_esc(str(exc))}")
        return
    finally:
        if nukki_path and os.path.exists(nukki_path):
            os.remove(nukki_path)
        if os.path.exists(file_path):
            os.remove(file_path)

    job["grid"] = grid
    job["tiles"] = [p for row in tile_paths for p in row]
    job["sticker_format"] = StickerFormat.VIDEO
    job["raw"] = None  # 이미 지웠으니 나중에 또 지우지 않도록
    job["tile_dir"] = out_dir
    await _ask_pack_choice(update, context, status_msg, job_id)


# ---------------------------------------------------------------------------
# 3단계: 팩 선택(신규/기존) 질문 -> 무료횟수/결제 처리
# ---------------------------------------------------------------------------


async def _ask_pack_choice(update: Update, context: ContextTypes.DEFAULT_TYPE, status_msg: Message, job_id: str) -> None:
    job = _jobs[job_id]
    grid: Grid = job["grid"]

    if grid.total > MAX_PACK_CAPACITY:
        _jobs.pop(job_id, None)
        await status_msg.edit_text(
            f"❌ 이미지가 너무 커서 텔레그램 이모지 팩 최대 개수({MAX_PACK_CAPACITY}개)를 초과합니다."
        )
        return

    fmt_value = job["sticker_format"].value  # "static" | "video"
    user_id = job["user_id"]
    candidates = [
        p
        for p in db.list_user_packs(user_id, fmt_value)
        if p.tile_count + grid.total <= MAX_PACK_CAPACITY
    ]
    job["pack_candidates"] = candidates

    buttons: List[List[InlineKeyboardButton]] = [
        [InlineKeyboardButton("🆕 새 팩 만들기", callback_data=f"pack:new:{job_id}")]
    ]
    for idx, p in enumerate(candidates):
        label = f"📦 {p.title} ({p.tile_count}/{MAX_PACK_CAPACITY})"
        buttons.append([InlineKeyboardButton(label[:64], callback_data=f"pack:use:{job_id}:{idx}")])

    text = (
        f"✅ <b>{grid.cols}x{grid.rows}</b> ({grid.total}개)로 분할했어요!\n\n"
        "📦 어느 이모지 팩에 넣을까요?"
    )
    await status_msg.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def on_pack_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    if len(parts) < 3:
        return
    mode, job_id = parts[1], parts[2]

    job = _jobs.get(job_id)
    if job is None:
        await query.edit_message_text("⌛ 요청이 만료됐어요. 이미지를 다시 보내주세요.")
        return

    status_msg = query.message

    if mode == "new":
        job["target_pack"] = {"mode": "new"}
    else:
        idx = int(parts[3])
        candidates = job["pack_candidates"]
        if idx >= len(candidates):
            await status_msg.edit_text("⌛ 선택이 만료됐어요. 다시 시도해주세요.")
            return
        candidate = candidates[idx]

        live = await get_live_pack_state(context.bot, candidate.pack_name)
        if live is None:
            await status_msg.edit_text(
                f"❌ '{_esc(candidate.title)}' 팩을 찾을 수 없어요(삭제됐을 수 있어요). 다시 시도해주세요."
            )
            return
        live_fmt, live_count = live
        if live_fmt != job["sticker_format"].value or live_count + job["grid"].total > MAX_PACK_CAPACITY:
            await status_msg.edit_text(
                f"❌ '{_esc(candidate.title)}' 팩에는 더 이상 추가할 수 없어요. 다시 시도해주세요."
            )
            return

        job["target_pack"] = {"mode": "existing", "pack_name": candidate.pack_name, "title": candidate.title}

    await _check_quota_and_proceed(update, context, status_msg, job_id)


# ---------------------------------------------------------------------------
# 4단계: 무료 횟수 확인 -> 없으면 Stars 결제 인보이스 발송
# ---------------------------------------------------------------------------


def _quota_kind(job_kind: str) -> str:
    return "image" if job_kind == "image" else "gif"


def _price_for(job_kind: str) -> int:
    return config.STAR_PRICE_IMAGE if job_kind == "image" else config.STAR_PRICE_GIF


async def _check_quota_and_proceed(
    update: Update, context: ContextTypes.DEFAULT_TYPE, status_msg: Message, job_id: str
) -> None:
    job = _jobs[job_id]
    user_id = job["user_id"]
    kind = _quota_kind(job["kind"])

    if _is_admin(user_id):
        await status_msg.edit_text("👑 관리자 계정이라 무제한으로 처리할게요!\n\n⏳ 이모지 팩 만드는 중...")
        await _finalize_job(context, status_msg, job_id)
        return

    if db.try_consume_free_quota(user_id, kind):
        img_left, gif_left = db.remaining_free_quota(user_id)
        await status_msg.edit_text(
            "🎁 오늘의 무료 횟수로 처리할게요!\n"
            f"<blockquote>남은 무료 — 🖼 {img_left}회 · 🎞 {gif_left}회</blockquote>\n\n"
            "⏳ 이모지 팩 만드는 중..."
        )
        await _finalize_job(context, status_msg, job_id)
        return

    price = _price_for(job["kind"])
    kind_label = "이미지" if job["kind"] == "image" else "GIF/영상"
    await status_msg.edit_text(
        f"⭐ 오늘 무료 횟수를 모두 사용하셨어요.\n<b>{price} Stars</b>로 결제하면 바로 만들어 드릴게요."
    )
    await context.bot.send_invoice(
        chat_id=status_msg.chat_id,
        title=f"이모지 팩 제작 ({kind_label})",
        description=f"{job['grid'].cols}x{job['grid'].rows} ({job['grid'].total}개) 이모지 팩을 만들어요.",
        payload=f"emojijob:{job_id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(f"이모지 팩 제작 ({kind_label})", price)],
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
        # 결제는 됐는데 요청이 이미 만료된 드문 경우: 별을 환불하고 알림.
        try:
            await context.bot.refund_star_payment(
                user_id=update.effective_user.id,
                telegram_payment_charge_id=payment.telegram_payment_charge_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception("환불 실패")
        await update.message.reply_text(
            "⚠️ 결제는 완료됐지만 요청이 만료돼서 처리할 수 없었어요. 결제한 Stars는 환불해드렸어요.\n"
            "다시 사진/GIF를 보내주세요."
        )
        return

    job = _jobs[job_id]
    job["payment_charge_id"] = payment.telegram_payment_charge_id
    status_msg = await update.message.reply_text("✅ 결제 완료!\n\n⏳ 이모지 팩 만드는 중...")
    await _finalize_job(context, status_msg, job_id)


# ---------------------------------------------------------------------------
# 5단계: 실제 스티커 생성/추가
# ---------------------------------------------------------------------------


async def _finalize_job(context: ContextTypes.DEFAULT_TYPE, status_msg: Message, job_id: str) -> None:
    job = _jobs.pop(job_id, None)
    if job is None:
        return

    bot = context.bot
    tiles = job["tiles"]
    sticker_format = job["sticker_format"]
    grid: Grid = job["grid"]
    target = job["target_pack"]
    user_id = job["user_id"]
    charge_id = job.get("payment_charge_id")

    try:
        if target["mode"] == "new":
            me = await bot.get_me()
            title = f"{job['first_name']}의 이모지 팩"[:64]
            pack_name = await create_new_pack(
                bot, user_id, me.username, title, tiles, sticker_format.value, config.EMOJI_PLACEHOLDER
            )
            db.record_new_pack(pack_name, user_id, title, sticker_format.value, grid.total)
        else:
            pack_name = target["pack_name"]
            title = target["title"]
            await add_tiles_to_pack(bot, user_id, pack_name, tiles, sticker_format.value, config.EMOJI_PLACEHOLDER)
            db.add_tiles_to_pack_record(pack_name, grid.total)
    except Exception as exc:  # noqa: BLE001
        logger.exception("이모지 팩 생성/추가 실패")
        if charge_id:
            try:
                await bot.refund_star_payment(user_id=user_id, telegram_payment_charge_id=charge_id)
            except Exception:  # noqa: BLE001
                logger.exception("환불 실패")
            await status_msg.edit_text(
                f"❌ 이모지 팩 생성에 실패했습니다: {_esc(str(exc))}\n결제하신 Stars는 환불해드렸어요."
            )
        else:
            await status_msg.edit_text(f"❌ 이모지 팩 생성에 실패했습니다: {_esc(str(exc))}")
        _cleanup_job_files(job)
        return

    _cleanup_job_files(job)

    link = f"https://t.me/addemoji/{pack_name}"
    if _is_admin(user_id):
        quota_line = "👑 관리자 계정 — 횟수 제한 무제한"
    else:
        img_left, gif_left = db.remaining_free_quota(user_id)
        quota_line = f"🎁 오늘 남은 무료 — 🖼 {img_left}회 · 🎞 {gif_left}회"
    await status_msg.edit_text(
        "🎉 <b>완성!</b>\n\n"
        f"📦 팩: <b>{_esc(title)}</b>\n"
        f"🔢 <b>{grid.cols}x{grid.rows}</b> ({grid.total}개) 추가됨\n"
        f"🔗 {link}\n\n"
        f"<blockquote>{quota_line}</blockquote>"
    )
