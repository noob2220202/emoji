from io import BytesIO

import pytest
from PIL import Image

from bot.text_emoji import FONTS, STYLES, render_text_emoji


def test_all_font_and_style_combinations_render_without_error():
    for font_key in FONTS:
        for style_key in STYLES:
            data = render_text_emoji("펭구 화이팅", font_key, style_key)
            im = Image.open(BytesIO(data))
            assert im.format == "PNG"
            assert im.width > 0 and im.height > 0


def test_rendered_image_has_visible_content():
    data = render_text_emoji("펭구", "black_han_sans", "gold_premium")
    im = Image.open(BytesIO(data)).convert("L")
    # 배경과 글자색이 다르므로 픽셀 값에 어느 정도 분산이 있어야 한다(완전 단색이면 실패).
    extrema = im.getextrema()
    assert extrema[1] - extrema[0] > 20


def test_unknown_font_raises():
    with pytest.raises(ValueError):
        render_text_emoji("테스트", "no_such_font", "gold_premium")


def test_unknown_style_raises():
    with pytest.raises(ValueError):
        render_text_emoji("테스트", "jua", "no_such_style")


def test_empty_phrase_raises():
    with pytest.raises(ValueError):
        render_text_emoji("   ", "jua", "gold_premium")


def test_long_phrase_is_truncated_not_errored():
    data = render_text_emoji("가" * 200, "jua", "gold_premium")
    im = Image.open(BytesIO(data))
    assert im.width > 0
