from io import BytesIO

import pytest
from PIL import Image

from bot.background_removal import remove_background_image


def _make_png(width: int, height: int) -> bytes:
    im = Image.new("RGB", (width, height), (10, 200, 10))
    buf = BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def test_remove_background_image_returns_rgba_png():
    # rembg 모델 다운로드(최초 1회, 인터넷 필요)가 안 되는 환경이면 건너뛴다.
    try:
        out = remove_background_image(_make_png(64, 64))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"rembg 모델을 사용할 수 없어 건너뜀: {exc}")

    im = Image.open(BytesIO(out))
    assert im.mode == "RGBA"
    assert im.size == (64, 64)
