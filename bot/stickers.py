"""텔레그램 커스텀 이모지 스티커 세트 생성/추가를 담당한다."""

import random
import string
from typing import List, Optional, Sequence, Tuple, Union

from telegram import Bot, InputSticker

# 한 번의 createNewStickerSet 호출에 넣을 수 있는 최대 스티커 개수(텔레그램 제한).
CREATE_BATCH_SIZE = 50

# 텔레그램 커스텀 이모지 팩 하나에 들어갈 수 있는 최대 스티커 개수.
MAX_PACK_CAPACITY = 200


def make_pack_name(user_id: int, bot_username: str) -> str:
    """텔레그램 스티커 세트 이름 규칙(영문/숫자/밑줄, botusername으로 끝남)에 맞는
    고유한 이름을 만든다."""
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"e{user_id}{suffix}_by_{bot_username}"


def _to_input_sticker(item: Union[bytes, str], sticker_format: str, emoji: str) -> InputSticker:
    # 파일 경로면 핸들을 열어둔 채 넘기지 않고 바로 읽어서 닫는다(타일이 최대 200개까지
    # 쌓일 수 있어 파일 디스크립터가 새는 것을 방지).
    if isinstance(item, (bytes, bytearray)):
        data = item
    else:
        with open(item, "rb") as f:
            data = f.read()
    return InputSticker(sticker=data, emoji_list=[emoji], format=sticker_format)


async def create_new_pack(
    bot: Bot,
    user_id: int,
    bot_username: str,
    title: str,
    tiles: Sequence[Union[bytes, str]],
    sticker_format: str,
    emoji: str,
) -> str:
    """tiles(PNG 바이트 또는 webm 파일 경로 리스트)로 새 커스텀 이모지 팩을 만들고
    이름을 반환한다."""
    if not tiles:
        raise ValueError("생성할 타일이 없습니다")

    name = make_pack_name(user_id, bot_username)
    first_batch = tiles[:CREATE_BATCH_SIZE]
    rest = tiles[CREATE_BATCH_SIZE:]

    await bot.create_new_sticker_set(
        user_id=user_id,
        name=name,
        title=title,
        stickers=[_to_input_sticker(t, sticker_format, emoji) for t in first_batch],
        sticker_type="custom_emoji",
    )

    for item in rest:
        await bot.add_sticker_to_set(
            user_id=user_id,
            name=name,
            sticker=_to_input_sticker(item, sticker_format, emoji),
        )

    return name


async def add_tiles_to_pack(
    bot: Bot,
    user_id: int,
    pack_name: str,
    tiles: Sequence[Union[bytes, str]],
    sticker_format: str,
    emoji: str,
) -> None:
    """이미 존재하는 이모지 팩에 타일들을 이어서 추가한다."""
    for item in tiles:
        await bot.add_sticker_to_set(
            user_id=user_id,
            name=pack_name,
            sticker=_to_input_sticker(item, sticker_format, emoji),
        )


async def get_live_pack_state(bot: Bot, pack_name: str) -> Optional[Tuple[str, int]]:
    """텔레그램 서버 기준 실제 (포맷, 현재 스티커 개수)를 반환한다. 로컬 DB가 실제와
    어긋났을 수 있으므로(예: 사용자가 텔레그램 앱에서 직접 스티커를 지운 경우) 팩에
    추가하기 직전에 이 값으로 다시 확인한다. 팩이 더 이상 존재하지 않으면 None."""
    try:
        sticker_set = await bot.get_sticker_set(pack_name)
    except Exception:  # noqa: BLE001 - 팩이 삭제됐거나 조회 실패
        return None

    stickers = sticker_set.stickers
    if not stickers:
        fmt = "static"
    elif stickers[0].is_video:
        fmt = "video"
    elif stickers[0].is_animated:
        fmt = "animated"
    else:
        fmt = "static"
    return fmt, len(stickers)
