import logging
import os
import tempfile
import time
import uuid
from typing import Dict

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import StickerFormat
from telegram.ext import ContextTypes

from . import config
from .background_removal import remove_background_image, remove_background_video
from .grid import Grid
from .image_split import split_static_image
from .stickers import create_emoji_pack
from .video_split import split_animated

logger = logging.getLogger(__name__)

_MIME_EXT = {"image/gif": ".gif", "video/mp4": ".mp4", "video/webm": ".webm"}

# 배경 제거 여부를 버튼으로 물어보는 동안, 원본 데이터를 잠깐 들고 있기 위한 저장소.
# value: {"kind": "image"|"video", "payload": bytes|str(경로), "ts": float}
_pending: Dict[str, dict] = {}
_PENDING_TTL_SECONDS = 15 * 60


def _store_pending(kind: str, payload) -> str:
    req_id = uuid.uuid4().hex[:12]
    _pending[req_id] = {"kind": kind, "payload": payload, "ts": time.time()}
    return req_id


def _pop_pending(req_id: str):
    return _pending.pop(req_id, None)


async def cleanup_pending(context: ContextTypes.DEFAULT_TYPE) -> None:
    """버튼을 누르지 않고 방치된 요청을 주기적으로 정리한다(임시 영상 파일 포함)."""
    now = time.time()
    stale_ids = [rid for rid, entry in _pending.items() if now - entry["ts"] > _PENDING_TTL_SECONDS]
    for rid in stale_ids:
        entry = _pending.pop(rid, None)
        if entry and entry["kind"] == "video":
            path = entry["payload"]
            if isinstance(path, str) and os.path.exists(path):
                os.remove(path)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "안녕하세요! 이미지나 GIF/영상을 보내주시면 자동으로 격자로 잘라서\n"
        "텔레그램 커스텀(프리미엄) 이모지 팩으로 만들어 드려요.\n\n"
        "- 이미지: 사진 또는 파일(문서)로 전송\n"
        "- GIF/영상: 최대 3초 분량만 이모지로 사용됩니다\n"
        "- 원하면 배경 제거(누끼) 후 만들 수도 있어요\n\n"
        "화질을 최대한 살리려면 사진이 아니라 '파일'(문서)로 압축 없이 보내주세요."
    )


async def _ask_choice(update: Update, kind: str, payload) -> None:
    req_id = _store_pending(kind, payload)
    if config.OFFER_BACKGROUND_REMOVAL:
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🖼 원본 그대로", callback_data=f"proceed:plain:{req_id}"),
                    InlineKeyboardButton(
                        "✂️ 배경 제거(누끼) 후", callback_data=f"proceed:nukki:{req_id}"
                    ),
                ]
            ]
        )
        await update.message.reply_text("어떻게 이모지 팩을 만들까요?", reply_markup=keyboard)
    else:
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("이모지 팩 만들기", callback_data=f"proceed:plain:{req_id}")]]
        )
        await update.message.reply_text("이모지 팩을 만들까요?", reply_markup=keyboard)


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    photo = update.message.photo[-1]
    file = await photo.get_file()
    data = await file.download_as_bytearray()
    await _ask_choice(update, "image", bytes(data))


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    document = update.message.document
    mime = document.mime_type or ""
    file = await document.get_file()

    if mime.startswith("image/") and mime != "image/gif":
        data = await file.download_as_bytearray()
        await _ask_choice(update, "image", bytes(data))
        return

    if mime in _MIME_EXT:
        with tempfile.NamedTemporaryFile(suffix=_MIME_EXT[mime], delete=False) as tmp:
            path = tmp.name
        await file.download_to_drive(path)
        await _ask_choice(update, "video", path)
        return

    await update.message.reply_text("지원하지 않는 파일 형식이에요. 이미지 또는 GIF/영상을 보내주세요.")


async def on_animation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    animation = update.message.animation
    file = await animation.get_file()
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        path = tmp.name
    await file.download_to_drive(path)
    await _ask_choice(update, "video", path)


async def on_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    video = update.message.video
    file = await video.get_file()
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        path = tmp.name
    await file.download_to_drive(path)
    await _ask_choice(update, "video", path)


async def on_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    try:
        _, mode, req_id = query.data.split(":", 2)
    except ValueError:
        return

    entry = _pop_pending(req_id)
    if entry is None:
        await query.edit_message_text("요청이 만료되었어요. 이미지를 다시 보내주세요.")
        return

    status = query.message
    kind = entry["kind"]
    payload = entry["payload"]

    if kind == "image":
        await _process_image(update, context, status, payload, remove_bg=(mode == "nukki"))
        return

    # kind == "video"
    path = payload
    try:
        await _process_video(update, context, status, path, remove_bg=(mode == "nukki"))
    finally:
        if os.path.exists(path):
            os.remove(path)


async def _process_image(
    update: Update, context: ContextTypes.DEFAULT_TYPE, status: Message, image_bytes: bytes, remove_bg: bool
) -> None:
    if remove_bg:
        await status.edit_text("배경을 제거하는 중...")
        try:
            image_bytes = remove_background_image(image_bytes)
        except Exception as exc:  # noqa: BLE001
            logger.exception("배경 제거 실패")
            await status.edit_text(f"배경 제거에 실패했습니다: {exc}")
            return

    await status.edit_text("이미지를 분석하고 이모지로 쪼개는 중...")
    try:
        grid, tiles = split_static_image(image_bytes, config.TARGET_TILE_PX, config.MAX_TILES)
    except Exception as exc:  # noqa: BLE001
        logger.exception("이미지 분할 실패")
        await status.edit_text(f"이미지 처리 중 오류가 발생했습니다: {exc}")
        return

    flat = [tile for row in tiles for tile in row]
    await _create_and_reply(update, context, status, flat, grid, StickerFormat.STATIC)


async def _process_video(
    update: Update, context: ContextTypes.DEFAULT_TYPE, status: Message, file_path: str, remove_bg: bool
) -> None:
    source_path = file_path
    nukki_path = None
    if remove_bg:
        await status.edit_text("배경을 제거하는 중... (프레임이 많으면 시간이 걸려요)")
        nukki_path = file_path + ".nukki.webm"
        try:
            remove_background_video(file_path, nukki_path, config.MAX_VIDEO_DURATION)
        except Exception as exc:  # noqa: BLE001
            logger.exception("영상 배경 제거 실패")
            await status.edit_text(f"배경 제거에 실패했습니다: {exc}")
            return
        source_path = nukki_path

    await status.edit_text("GIF/영상을 분석하고 이모지로 쪼개는 중... (시간이 좀 걸릴 수 있어요)")
    try:
        with tempfile.TemporaryDirectory() as out_dir:
            try:
                grid, tile_paths = split_animated(
                    source_path,
                    out_dir,
                    config.TARGET_TILE_PX,
                    config.MAX_TILES,
                    config.MAX_VIDEO_DURATION,
                    config.MAX_VIDEO_BYTES,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("영상 분할 실패")
                await status.edit_text(f"영상 처리 중 오류가 발생했습니다: {exc}")
                return
            flat = [p for row in tile_paths for p in row]
            await _create_and_reply(update, context, status, flat, grid, StickerFormat.VIDEO)
    finally:
        if nukki_path and os.path.exists(nukki_path):
            os.remove(nukki_path)


async def _create_and_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    status: Message,
    flat_tiles,
    grid: Grid,
    sticker_format: str,
) -> None:
    if grid.total > 200:
        await status.edit_text("이미지가 너무 커서 텔레그램 이모지 팩 최대 개수(200개)를 초과합니다.")
        return

    bot = context.bot
    me = await bot.get_me()
    user = update.effective_user
    title = f"{user.first_name}의 이모지 팩"[:64]

    await status.edit_text(f"{grid.cols}x{grid.rows} ({grid.total}개)로 분할 완료. 이모지 팩 생성 중...")
    try:
        name = await create_emoji_pack(
            bot, user.id, me.username, title, flat_tiles, sticker_format.value, config.EMOJI_PLACEHOLDER
        )
    except Exception as exc:  # noqa: BLE001 - 사용자에게 실패 사유를 그대로 보여주기 위함
        logger.exception("이모지 팩 생성 실패")
        await status.edit_text(f"이모지 팩 생성에 실패했습니다: {exc}")
        return

    link = f"https://t.me/addemoji/{name}"
    await status.edit_text(f"완성! {grid.cols}x{grid.rows} ({grid.total}개) 이모지 팩이 만들어졌어요.\n{link}")
