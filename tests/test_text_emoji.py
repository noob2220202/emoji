from io import BytesIO

import pytest
from PIL import Image

from bot.text_emoji import EFFECTS, FONTS, STYLES, render_text_emoji, render_text_emoji_animated

HAS_FFMPEG = True
try:
    from bot.ffmpeg_util import ffmpeg_path

    ffmpeg_path()
except Exception:  # noqa: BLE001
    HAS_FFMPEG = False


def test_all_font_and_style_combinations_render_without_error():
    for font_key in FONTS:
        for style_key in STYLES:
            data = render_text_emoji("펭구 화이팅", font_key, style_key)
            im = Image.open(BytesIO(data))
            assert im.format == "PNG"
            assert im.width > 0 and im.height > 0


def test_rendered_image_has_visible_content():
    data = render_text_emoji("펭구", "black_han_sans", "silver")
    im = Image.open(BytesIO(data)).convert("L")
    # 배경과 글자색이 다르므로 픽셀 값에 어느 정도 분산이 있어야 한다(완전 단색이면 실패).
    extrema = im.getextrema()
    assert extrema[1] - extrema[0] > 20


def test_unknown_font_raises():
    with pytest.raises(ValueError):
        render_text_emoji("테스트", "no_such_font", "silver")


def test_unknown_style_raises():
    with pytest.raises(ValueError):
        render_text_emoji("테스트", "jua", "no_such_style")


def test_empty_phrase_raises():
    with pytest.raises(ValueError):
        render_text_emoji("   ", "jua", "silver")


def test_long_phrase_is_truncated_not_errored():
    data = render_text_emoji("가" * 200, "jua", "silver")
    im = Image.open(BytesIO(data))
    assert im.width > 0


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg가 없어서 애니메이션 인코딩 테스트를 건너뜀")
def test_animated_render_produces_a_looping_webm(tmp_path):
    out_path = str(tmp_path / "anim.webm")
    render_text_emoji_animated("펭구", "black_han_sans", "blue", out_path)

    import cv2

    cap = cv2.VideoCapture(out_path)
    assert cap.isOpened()
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    assert width > 0 and height > 0
    assert frame_count > 1, "여러 프레임으로 된 애니메이션이어야 한다"


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg가 없어서 분할 테스트를 건너뜀")
def test_animated_banner_splits_into_small_tiles(tmp_path):
    from bot.video_split import split_animated

    out_path = str(tmp_path / "anim.webm")
    render_text_emoji_animated("화이팅", "jua", "gold", out_path)

    tile_dir = tmp_path / "tiles"
    tile_dir.mkdir()
    grid, tile_paths = split_animated(out_path, str(tile_dir), target_tile_count=6, max_tiles=200)

    flat = [p for row in tile_paths for p in row]
    assert len(flat) == grid.total
    for p in flat:
        import os

        assert os.path.getsize(p) > 0
        assert os.path.getsize(p) <= 256 * 1024


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg가 없어서 애니메이션 인코딩 테스트를 건너뜀")
def test_all_effects_render_without_error(tmp_path):
    import cv2

    for effect_key in EFFECTS:
        out_path = str(tmp_path / f"anim_{effect_key}.webm")
        render_text_emoji_animated("펭구 화이팅", "black_han_sans", "gold", out_path, effect_key)

        cap = cv2.VideoCapture(out_path)
        assert cap.isOpened(), effect_key
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()
        assert frame_count > 1, effect_key


def test_unknown_effect_raises(tmp_path):
    out_path = str(tmp_path / "anim.webm")
    with pytest.raises(ValueError):
        render_text_emoji_animated("테스트", "jua", "silver", out_path, "no_such_effect")


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg가 없어서 분할 테스트를 건너뜀")
def test_animated_banner_single_row_split_has_exactly_one_row(tmp_path):
    import os

    from bot.video_split import split_animated_single_row

    out_path = str(tmp_path / "anim.webm")
    render_text_emoji_animated("펭구 화이팅", "black_han_sans", "silver", out_path)

    tile_dir = tmp_path / "tiles"
    tile_dir.mkdir()
    grid, tile_paths = split_animated_single_row(out_path, str(tile_dir))

    assert grid.rows == 1
    assert len(tile_paths) == 1
    flat = tile_paths[0]
    assert len(flat) == grid.cols
    for p in flat:
        assert os.path.getsize(p) > 0
        assert os.path.getsize(p) <= 256 * 1024
