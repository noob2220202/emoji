"""GIF/영상을 타일 그리드로 잘라 텔레그램 영상 이모지 규격(WEBM, VP9, 100x100,
3초 이하, 256KB 이하)에 맞는 조각들을 만든다.

영상 길이/해상도 정보는 OpenCV로 읽고, 실제 크롭/스케일/인코딩은 ffmpeg로 한다.
"""

import os
import shutil
import subprocess
from typing import List, Tuple

import cv2

from .grid import Grid, compute_grid

EMOJI_SIZE = 100
# 파일 크기가 목표치를 넘으면 CRF(품질)를 낮춰가며 재인코딩을 시도한다.
CRF_ATTEMPTS = (30, 34, 38, 42, 46, 50)


def _ffmpeg_path() -> str:
    path = shutil.which("ffmpeg")
    if path:
        return path
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def probe_video(path: str) -> Tuple[int, int, float, float]:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError("영상 파일을 열 수 없습니다")
    try:
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    finally:
        cap.release()
    if width <= 0 or height <= 0:
        raise RuntimeError("영상 해상도를 읽을 수 없습니다")
    duration = frame_count / fps if fps else 0.0
    return width, height, fps, duration


def split_animated(
    input_path: str,
    out_dir: str,
    target_tile_px: int = 120,
    max_tiles: int = 100,
    max_duration: float = 3.0,
    max_bytes: int = 256 * 1024,
) -> Tuple[Grid, List[List[str]]]:
    width, height, _fps, duration = probe_video(input_path)
    grid = compute_grid(width, height, target_tile_px, max_tiles)
    clip_duration = min(duration, max_duration) if duration > 0 else max_duration

    ffmpeg = _ffmpeg_path()
    tile_paths: List[List[str]] = []
    for r in range(grid.rows):
        row: List[str] = []
        for c in range(grid.cols):
            x, y = c * grid.tile_size, r * grid.tile_size
            out_path = os.path.join(out_dir, f"tile_{r}_{c}.webm")
            vf = (
                f"pad={grid.padded_width}:{grid.padded_height}:0:0:black,"
                f"crop={grid.tile_size}:{grid.tile_size}:{x}:{y},"
                f"scale={EMOJI_SIZE}:{EMOJI_SIZE}:flags=lanczos,fps=30"
            )
            _encode_with_size_limit(ffmpeg, input_path, vf, clip_duration, out_path, max_bytes)
            row.append(out_path)
        tile_paths.append(row)
    return grid, tile_paths


def _encode_with_size_limit(
    ffmpeg: str, input_path: str, vf: str, duration: float, out_path: str, max_bytes: int
) -> None:
    last_error = None
    for crf in CRF_ATTEMPTS:
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            input_path,
            "-t",
            f"{duration:.3f}",
            "-vf",
            vf,
            "-an",
            "-c:v",
            "libvpx-vp9",
            "-crf",
            str(crf),
            "-b:v",
            "0",
            "-pix_fmt",
            "yuva420p",
            out_path,
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
        except subprocess.CalledProcessError as exc:
            last_error = exc
            continue
        if os.path.getsize(out_path) <= max_bytes:
            return
    if last_error is not None and not os.path.exists(out_path):
        raise RuntimeError(
            f"ffmpeg 인코딩 실패: {last_error.stderr.decode(errors='ignore')[-500:]}"
        )
    # 마지막 시도 결과가 용량 초과라도 일단 사용한다 (텔레그램이 거부하면 상위에서 에러 처리됨).
