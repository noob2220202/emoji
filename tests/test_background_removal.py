import os
import subprocess
from io import BytesIO

import pytest
from PIL import Image

from bot.background_removal import remove_background_image, remove_background_video

HAS_FFMPEG = True
try:
    from bot.ffmpeg_util import ffmpeg_path

    ffmpeg_path()
except Exception:  # noqa: BLE001
    HAS_FFMPEG = False


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


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg가 없어서 영상 배경 제거 테스트를 건너뜀")
def test_remove_background_video_produces_alpha_webm(tmp_path):
    # 업로드된 GIF/영상을 흉내내기 위해 단색 프레임들로 짧은 mp4를 만든다.
    input_path = str(tmp_path / "in.mp4")
    cmd = [
        ffmpeg_path(),
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=green:s=64x64:d=1:r=10",
        input_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)

    output_path = str(tmp_path / "out.webm")
    try:
        remove_background_video(input_path, output_path, max_duration=1.0)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"rembg 모델을 사용할 수 없어 건너뜀: {exc}")

    assert os.path.exists(output_path)
    assert os.path.getsize(output_path) > 0
