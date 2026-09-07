import logging
import os
import tempfile

from telegram import Update
from telegram.constants import StickerFormat
from telegram.ext import ContextTypes

from . import config
from .grid import Grid
from .image_split import split_static_image
from .stickers import create_emoji_pack
from .video_split import split_animated

logger = logging.getLogger(__name__)

_MIME_EXT = {"image/gif": ".gif", "video/mp4": ".mp4", "video/webm": ".webm"}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "안녕하세요! 이미지나 GIF/영상을 보내주시면 자동으로 격자로 잘라서\n"
        "텔레그램 커스텀(프리미엄) 이모지 팩으로 만들어 드려요.\n\n"
        "- 이미지: 사진 또는 파일(문서)로 전송\n"
        "- GIF/영상: 최대 3초 분량만 이모지로 사용됩니다\n\n"
        "화질을 최대한 살리려면 사진이 아니라 '파일'(문서)로 압축 없이 보내주세요."
    )


async def _create_and_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    status_message,
    flat_tiles,
    grid: Grid,
    sticker_format: str,
) -> None:
    if grid.total > 200:
        await status_message.edit_text(
            "이미지가 너무 커서 텔레그램 이모지 팩 최대 개수(200개)를 초과합니다."
        )
        return

    bot = context.bot
    me = await bot.get_me()
    user = update.effective_user
    title = f"{user.first_name}의 이모지 팩"[:64]

    await status_message.edit_text(
        f"{grid.cols}x{grid.rows} ({grid.total}개)로 분할 완료. 이모지 팩 생성 중..."
    )
    try:
        name = await create_emoji_pack(
            bot, user.id, me.username, title, flat_tiles, sticker_format, config.EMOJI_PLACEHOLDER
        )
    except Exception as exc:  # noqa: BLE001 - 사용자에게 실패 사유를 그대로 보여주기 위함
        logger.exception("이모지 팩 생성 실패")
        await status_message.edit_text(f"이모지 팩 생성에 실패했습니다: {exc}")
        return

    link = f"https://t.me/addemoji/{name}"
    await status_message.edit_text(
        f"완성! {grid.cols}x{grid.rows} ({grid.total}개) 이모지 팩이 만들어졌어요.\n{link}"
    )


async def handle_static_image(
    update: Update, context: ContextTypes.DEFAULT_TYPE, image_bytes: bytes
) -> None:
    status = await update.message.reply_text("이미지를 분석하고 이모지로 쪼개는 중...")
    try:
        grid, tiles = split_static_image(image_bytes, config.TARGET_TILE_PX, config.MAX_TILES)
    except Exception as exc:  # noqa: BLE001
        logger.exception("이미지 분할 실패")
        await status.edit_text(f"이미지 처리 중 오류가 발생했습니다: {exc}")
        return
    flat = [tile for row in tiles for tile in row]
    await _create_and_reply(update, context, status, flat, grid, StickerFormat.STATIC)


async def handle_animated(update: Update, context: ContextTypes.DEFAULT_TYPE, file_path: str) -> None:
    status = await update.message.reply_text(
        "GIF/영상을 분석하고 이모지로 쪼개는 중... (시간이 좀 걸릴 수 있어요)"
    )
    with tempfile.TemporaryDirectory() as out_dir:
        try:
            grid, tile_paths = split_animated(
                file_path,
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


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    photo = update.message.photo[-1]
    file = await photo.get_file()
    data = await file.download_as_bytearray()
    await handle_static_image(update, context, bytes(data))


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    document = update.message.document
    mime = document.mime_type or ""
    file = await document.get_file()

    if mime.startswith("image/") and mime != "image/gif":
        data = await file.download_as_bytearray()
        await handle_static_image(update, context, bytes(data))
        return

    if mime in _MIME_EXT:
        with tempfile.NamedTemporaryFile(suffix=_MIME_EXT[mime], delete=False) as tmp:
            path = tmp.name
        await file.download_to_drive(path)
        try:
            await handle_animated(update, context, path)
        finally:
            os.remove(path)
        return

    await update.message.reply_text("지원하지 않는 파일 형식이에요. 이미지 또는 GIF/영상을 보내주세요.")


async def on_animation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    animation = update.message.animation
    file = await animation.get_file()
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        path = tmp.name
    await file.download_to_drive(path)
    try:
        await handle_animated(update, context, path)
    finally:
        os.remove(path)


async def on_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    video = update.message.video
    file = await video.get_file()
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        path = tmp.name
    await file.download_to_drive(path)
    try:
        await handle_animated(update, context, path)
    finally:
        os.remove(path)
