"""이미지/영상의 배경을 제거(누끼)한다.

정지 이미지는 rembg(U^2-Net 기반 오픈소스 배경 제거 라이브러리, PNG 바이트 입력/출력)를
그대로 사용하고, 영상/GIF는 프레임 단위로 rembg를 돌린 뒤 알파 채널을 가진
WEBM(VP9)으로 다시 인코딩한다. 이렇게 만들어진 알파 영상은 이후 video_split.py의
crop/scale 파이프라인에 그대로 입력으로 넣을 수 있다.
"""

import os
import subprocess
import tempfile
from typing import Optional

import cv2
from PIL import Image
from rembg import new_session, remove

from . import config
from .ffmpeg_util import ffmpeg_path

_session = None


def _get_session():
    global _session
    if _session is None:
        _session = new_session(config.REMBG_MODEL)
    return _session


def remove_background_image(data: bytes) -> bytes:
    """이미지 바이트를 받아 배경이 제거된 PNG(RGBA) 바이트를 반환한다."""
    return remove(data, session=_get_session())


def remove_background_video(
    input_path: str,
    output_path: str,
    max_duration: float,
    fps_cap: int = 30,
) -> None:
    """영상/GIF의 각 프레임에서 배경을 제거하고 알파 채널을 가진 WEBM(VP9)으로 저장한다."""
    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        raise RuntimeError("영상 파일을 열 수 없습니다")

    source_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    out_fps = min(source_fps, fps_cap) if source_fps > 0 else 25.0
    max_frames = max(1, int(max_duration * out_fps))
    frame_interval = max(1.0, source_fps / out_fps)

    session = _get_session()

    with tempfile.TemporaryDirectory() as frame_dir:
        saved = 0
        read_idx = 0
        next_take = 0.0
        try:
            while saved < max_frames:
                ok, frame = cap.read()
                if not ok:
                    break
                if read_idx >= next_take:
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    cut = remove(Image.fromarray(rgb), session=session)
                    cut.save(os.path.join(frame_dir, f"frame_{saved:05d}.png"))
                    saved += 1
                    next_take += frame_interval
                read_idx += 1
        finally:
            cap.release()

        if saved == 0:
            raise RuntimeError("영상에서 프레임을 읽지 못했습니다")

        cmd = [
            ffmpeg_path(),
            "-y",
            "-framerate",
            f"{out_fps:.3f}",
            "-i",
            os.path.join(frame_dir, "frame_%05d.png"),
            "-c:v",
            "libvpx-vp9",
            "-pix_fmt",
            "yuva420p",
            "-crf",
            "30",
            "-b:v",
            "0",
            output_path,
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"배경 제거 영상 인코딩 실패: {exc.stderr.decode(errors='ignore')[-500:]}"
            ) from exc
