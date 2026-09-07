"""텔레그램 커스텀 이모지 스티커 세트 생성/추가를 담당한다."""

import random
import string
from typing import List, Sequence, Union

from telegram import Bot, InputSticker

# 한 번의 createNewStickerSet 호출에 넣을 수 있는 최대 스티커 개수(텔레그램 제한).
CREATE_BATCH_SIZE = 50


def make_pack_name(user_id: int, bot_username: str) -> str:
    """텔레그램 스티커 세트 이름 규칙(영문/숫자/밑줄, botusername으로 끝남)에 맞는
    고유한 이름을 만든다."""
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"e{user_id}{suffix}_by_{bot_username}"


def _to_input_sticker(item: Union[bytes, str], sticker_format: str, emoji: str) -> InputSticker:
    data = item if isinstance(item, (bytes, bytearray)) else open(item, "rb")
    return InputSticker(sticker=data, emoji_list=[emoji], format=sticker_format)


async def create_emoji_pack(
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
