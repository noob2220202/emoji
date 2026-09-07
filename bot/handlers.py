import html
import logging
import time
import uuid
from typing import Dict, List, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Message, Update
from telegram.constants import StickerFormat
from telegram.ext import ContextTypes

from . import config, db
from .background_removal import remove_background_image
from .grid import Grid
from .image_split import split_static_image
from .stickers import MAX_PACK_CAPACITY, add_tiles_to_pack, create_new_pack, get_live_pack_state
from .text_emoji import FONTS, STYLES, render_text_emoji

logger = logging.getLogger(__name__)

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
        "chat_id": None,
        "status_message_id": None,
        "stage": None,
        "image_bytes": None,
        "grid": None,
        "tiles": None,
        "pack_candidates": [],
        "target_pack": None,
        "tile_count": None,
        "phrase": None,
        "font_key": None,
        "ts": time.time(),
    }
    return job_id


async def cleanup_pending(context: ContextTypes.DEFAULT_TYPE) -> None:
    """방치된 요청을 주기적으로 정리한다."""
    now = time.time()
    stale_ids = [jid for jid, job in _jobs.items() if now - job["ts"] > _JOB_TTL_SECONDS]
    for jid in stale_ids:
        job = _jobs.pop(jid, None)
        if job and _awaiting.get(job["user_id"]) == jid:
            _awaiting.pop(job["user_id"], None)


def _esc(text: str) -> str:
    return html.escape(text or "")


def _is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_USER_IDS


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


# ---------------------------------------------------------------------------
# 기본 명령어
# ---------------------------------------------------------------------------


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "✨ <b>이모지 팩 제작소</b>에 오신 걸 환영해요!\n\n"
        "📸 사진을 보내주시면 자동으로 격자로 잘라서\n"
        "텔레그램 <b>커스텀(프리미엄) 이모지 팩</b>으로 만들어 드려요.\n\n"
        "<blockquote>💡 화질을 살리려면 사진이 아니라 <b>파일(문서)</b>로 압축 없이 보내주세요.</blockquote>\n\n"
        "✍️ <b>/text 문구</b> 로 글자를 꾸며서 이모지로 만들 수도 있어요 (예: <code>/text 펭구 화이팅</code>)\n\n"
        "<b>💰 요금 안내</b>\n"
        f"이모지 팩 1회 제작: <b>{config.STAR_PRICE} ⭐</b> · 하루 무료 <b>{config.FREE_USES_PER_DAY}회</b>\n"
        "🕛 무료 횟수는 매일 <u>자정(KST)</u>에 초기화돼요.\n\n"
        "📦 기존에 만든 팩이 있으면 이어서 추가할 수도 있어요!\n\n"
        "지금 바로 사진을 보내거나 /text 를 입력해보세요 🚀"
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    packs = db.list_user_packs(user.id)
    lines = ["📊 <b>내 현황</b>\n"]
    if _is_admin(user.id):
        lines.append("👑 관리자 계정 — 횟수 제한 <b>무제한</b>")
    else:
        left = db.remaining_free_quota(user.id)
        lines.append(f"🎁 오늘 남은 무료 횟수 — <b>{left}회</b>")
    lines.append("")
    if packs:
        lines.append("📦 <b>내 이모지 팩</b>")
        for p in packs:
            lines.append(f"🖼 {_esc(p.title)} — {p.tile_count}/{MAX_PACK_CAPACITY}개")
    else:
        lines.append("아직 만든 이모지 팩이 없어요. 사진을 보내보세요!")
    await update.message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# 사진 수신 -> 배경 제거 여부 질문
# ---------------------------------------------------------------------------


async def _ask_bg_choice(message: Message, job_id: str) -> None:
    if config.OFFER_BACKGROUND_REMOVAL:
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🖼 원본 그대로", callback_data=f"bg:plain:{job_id}"),
                    InlineKeyboardButton("✂️ 배경 제거(누끼) 후", callback_data=f"bg:nukki:{job_id}"),
                ]
            ]
        )
        text = "🖌 <b>배경을 제거(누끼)</b>할까요?"
    else:
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("▶️ 다음", callback_data=f"bg:plain:{job_id}")]]
        )
        text = "이모지 팩을 만들까요?"
    await _send_status(message, _jobs[job_id], text, keyboard)


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    photo = update.message.photo[-1]
    file = await photo.get_file()
    data = await file.download_as_bytearray()
    job_id = _new_job(user.id, user.first_name or "사용자")
    _jobs[job_id]["raw_bytes"] = bytes(data)
    await _ask_bg_choice(update.message, job_id)


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    document = update.message.document
    mime = document.mime_type or ""
    if not mime.startswith("image/"):
        await update.message.reply_text("🤔 이미지 파일만 지원해요. 사진으로 보내주세요.")
        return
    user = update.effective_user
    file = await document.get_file()
    data = await file.download_as_bytearray()
    job_id = _new_job(user.id, user.first_name or "사용자")
    _jobs[job_id]["raw_bytes"] = bytes(data)
    await _ask_bg_choice(update.message, job_id)


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

    image_bytes = job.pop("raw_bytes", None)
    if mode == "nukki":
        await _update_status(context, job, "✂️ 배경을 제거하는 중...")
        try:
            image_bytes = remove_background_image(image_bytes)
        except Exception as exc:  # noqa: BLE001
            logger.exception("배경 제거 실패")
            _jobs.pop(job_id, None)
            await _update_status(context, job, f"❌ 배경 제거에 실패했습니다: {_esc(str(exc))}")
            return

    job["image_bytes"] = image_bytes
    await _ask_pack_selection(context, job_id)


# ---------------------------------------------------------------------------
# /text 글자 이모지화
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
    return InlineKeyboardMarkup(rows)


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
    return InlineKeyboardMarkup(rows)


async def _ask_font_choice(message: Message, job_id: str) -> None:
    await _send_status(message, _jobs[job_id], "🎨 폰트를 선택해주세요.", _font_keyboard(job_id))


async def text_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    job_id = _new_job(user.id, user.first_name or "사용자")

    phrase = " ".join(context.args).strip() if context.args else ""
    if phrase:
        _jobs[job_id]["phrase"] = phrase
        await _ask_font_choice(update.message, job_id)
        return

    _jobs[job_id]["stage"] = "await_phrase"
    _awaiting[user.id] = job_id
    await update.message.reply_text("✍️ 이모지로 만들 문구를 입력해주세요. (예: 펭구 화이팅)")


async def on_font_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        _, job_id, font_key = query.data.split(":", 2)
    except ValueError:
        return

    job = _jobs.get(job_id)
    if job is None:
        await query.edit_message_text("⌛ 요청이 만료됐어요. /text 로 다시 시작해주세요.")
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
        await query.edit_message_text("⌛ 요청이 만료됐어요. /text 로 다시 시작해주세요.")
        return

    await _update_status(context, job, "🖌 이미지를 만드는 중...")
    try:
        image_bytes = render_text_emoji(job["phrase"], job["font_key"], style_key)
    except Exception as exc:  # noqa: BLE001
        logger.exception("글자 이모지 렌더링 실패")
        _jobs.pop(job_id, None)
        await _update_status(context, job, f"❌ 이미지 생성에 실패했습니다: {_esc(str(exc))}")
        return

    job["image_bytes"] = image_bytes
    await _ask_pack_selection(context, job_id)


# ---------------------------------------------------------------------------
# 팩 선택(신규/기존) -> 조각 수 입력
# ---------------------------------------------------------------------------


async def _ask_pack_selection(context: ContextTypes.DEFAULT_TYPE, job_id: str) -> None:
    job = _jobs[job_id]
    candidates = db.list_user_packs(job["user_id"])
    job["pack_candidates"] = candidates

    buttons: List[List[InlineKeyboardButton]] = [
        [InlineKeyboardButton("🆕 새 팩 만들기", callback_data=f"packsel:new:{job_id}")]
    ]
    for idx, p in enumerate(candidates):
        label = f"📦 {p.title} ({p.tile_count}/{MAX_PACK_CAPACITY})"[:64]
        buttons.append([InlineKeyboardButton(label, callback_data=f"packsel:use:{job_id}:{idx}")])

    await _update_status(context, job, "📦 어느 이모지 팩에 넣을까요?", InlineKeyboardMarkup(buttons))


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
        await _update_status(context, job, "📝 새 팩 이름을 입력해주세요. (예: 펭구팩)")
        return

    idx = int(parts[3])
    candidates = job["pack_candidates"]
    if idx >= len(candidates):
        await _update_status(context, job, "⌛ 선택이 만료됐어요. 다시 시도해주세요.")
        return
    candidate = candidates[idx]
    job["target_pack"] = {"mode": "existing", "pack_name": candidate.pack_name, "title": candidate.title}
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
    )


# ---------------------------------------------------------------------------
# 일반 텍스트 메시지 라우팅 (팩 이름 / 조각 수 / 글자 이모지 문구 입력)
# ---------------------------------------------------------------------------


async def on_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    job_id = _awaiting.get(user.id)
    if job_id is None:
        return
    job = _jobs.get(job_id)
    if job is None:
        _awaiting.pop(user.id, None)
        return

    text = (update.message.text or "").strip()
    stage = job.get("stage")

    if stage == "await_phrase":
        if not text:
            await update.message.reply_text("문구를 입력해주세요.")
            return
        _awaiting.pop(user.id, None)
        job["phrase"] = text
        await _ask_font_choice(update.message, job_id)
        return

    if stage == "await_pack_name":
        if not text:
            await update.message.reply_text("팩 이름을 입력해주세요.")
            return
        _awaiting.pop(user.id, None)
        job["target_pack"] = {"mode": "new", "title": text[:64]}
        await _ask_tile_count(context, job_id)
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
    job = _jobs[job_id]
    await _update_status(context, job, "🔍 이미지를 분석하고 이모지로 쪼개는 중...")
    try:
        grid, tiles = split_static_image(job["image_bytes"], job["tile_count"], config.MAX_TILES)
    except Exception as exc:  # noqa: BLE001
        logger.exception("이미지 분할 실패")
        _jobs.pop(job_id, None)
        await _update_status(context, job, f"❌ 이미지 처리 중 오류가 발생했습니다: {_esc(str(exc))}")
        return

    target = job["target_pack"]
    if target["mode"] == "existing":
        live = await get_live_pack_state(context.bot, target["pack_name"])
        if live is None:
            _jobs.pop(job_id, None)
            await _update_status(
                context, job, f"❌ '{_esc(target['title'])}' 팩을 찾을 수 없어요. 다시 시도해주세요."
            )
            return
        live_fmt, live_count = live
        if live_fmt != "static" or live_count + grid.total > MAX_PACK_CAPACITY:
            _jobs.pop(job_id, None)
            await _update_status(
                context,
                job,
                f"❌ '{_esc(target['title'])}' 팩에는 더 넣을 수 없어요(용량 초과).\n"
                "조각 수를 줄이거나 다른 팩으로 다시 시도해주세요.",
            )
            return

    job["grid"] = grid
    job["tiles"] = [tile for row in tiles for tile in row]

    # 요청한 조각 수는 정사각형 타일 제약 때문에 정확히 맞지 않고 종횡비에 맞는 가장
    # 가까운 값으로 근사될 수 있으므로, 결제/무료 처리 전에 실제 결과를 보여준다.
    if grid.total != job["tile_count"]:
        await _update_status(
            context,
            job,
            f"✅ 요청하신 {job['tile_count']}개와 가장 비슷한 "
            f"<b>{grid.cols}x{grid.rows}</b> ({grid.total}개)로 분할했어요!",
        )

    await _check_quota_and_proceed(context, job_id)


async def _check_quota_and_proceed(context: ContextTypes.DEFAULT_TYPE, job_id: str) -> None:
    job = _jobs[job_id]
    user_id = job["user_id"]

    if _is_admin(user_id):
        await _update_status(context, job, "👑 관리자 계정이라 무제한으로 처리할게요!\n\n⏳ 이모지 팩 만드는 중...")
        await _finalize_job(context, job_id)
        return

    if db.try_consume_free_quota(user_id):
        left = db.remaining_free_quota(user_id)
        await _update_status(
            context,
            job,
            f"🎁 오늘의 무료 횟수로 처리할게요!\n<blockquote>오늘 남은 무료 {left}회</blockquote>\n\n⏳ 이모지 팩 만드는 중...",
        )
        await _finalize_job(context, job_id)
        return

    await _update_status(
        context, job, f"⭐ 오늘 무료 횟수를 모두 사용하셨어요.\n<b>{config.STAR_PRICE} Stars</b>로 결제하면 바로 만들어 드릴게요."
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

    try:
        if target["mode"] == "new":
            me = await bot.get_me()
            title = target["title"][:64]
            pack_name = await create_new_pack(
                bot, user_id, me.username, title, tiles, StickerFormat.STATIC.value, config.EMOJI_PLACEHOLDER
            )
            db.record_new_pack(pack_name, user_id, title, "static", grid.total)
        else:
            pack_name = target["pack_name"]
            title = target["title"]
            await add_tiles_to_pack(
                bot, user_id, pack_name, tiles, StickerFormat.STATIC.value, config.EMOJI_PLACEHOLDER
            )
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
        return

    link = f"https://t.me/addemoji/{pack_name}"
    if _is_admin(user_id):
        quota_line = "👑 관리자 계정 — 횟수 제한 무제한"
    else:
        left = db.remaining_free_quota(user_id)
        quota_line = f"🎁 오늘 남은 무료 {left}회"

    await _update_status(
        context,
        job,
        "🎉 <b>완성!</b>\n\n"
        f"📦 팩: <b>{_esc(title)}</b>\n"
        f"🔢 <b>{grid.cols}x{grid.rows}</b> ({grid.total}개) 추가됨\n"
        f"🔗 {link}\n\n"
        f"<blockquote>{quota_line}</blockquote>",
    )
