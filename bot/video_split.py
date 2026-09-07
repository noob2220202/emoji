"""애니메이션 배너(WEBM)를 타일 그리드로 잘라 텔레그램 영상 이모지 규격(WEBM, VP9,
100x100, 3초 이하, 256KB 이하)에 맞는 조각들을 만든다.

입력은 사용자가 올린 영상이 아니라 text_emoji.py가 PIL 프레임을 이어붙여 만든
자체 생성 애니메이션 배너다. 해상도/길이/프레임 수를 우리가 직접 정하기 때문에
임의의 업로드 영상보다 다뤄야 할 변수가 적다.
"""

import os
import subprocess
from typing import List, Tuple

import cv2

from .ffmpeg_util import ffmpeg_path
from .grid import Grid, compute_grid, compute_single_row_grid

EMOJI_SIZE = 100
# 파일 크기가 목표치를 넘으면 CRF(품질)를 낮춰가며 재인코딩을 시도한다.
CRF_ATTEMPTS = (30, 34, 38, 42, 46, 50)


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
    target_tile_count: int = 12,
    max_tiles: int = 200,
    max_duration: float = 3.0,
    max_bytes: int = 256 * 1024,
) -> Tuple[Grid, List[List[str]]]:
    width, height, _fps, duration = probe_video(input_path)
    grid = compute_grid(width, height, target_tile_count, max_tiles)
    tiles = _slice_video(input_path, out_dir, grid, duration, max_duration, max_bytes)
    return grid, tiles


def split_animated_single_row(
    input_path: str,
    out_dir: str,
    max_duration: float = 3.0,
    max_bytes: int = 256 * 1024,
) -> Tuple[Grid, List[List[str]]]:
    """세로 1칸(rows=1) 고정으로, 가로는 원본 폭에 맞춰 자동으로 나눈다
    (글자 이모지화 전용 - 항상 한 줄짜리 움직이는 배너 형태로 나온다)."""
    width, height, _fps, duration = probe_video(input_path)
    grid = compute_single_row_grid(width, height)
    tiles = _slice_video(input_path, out_dir, grid, duration, max_duration, max_bytes)
    return grid, tiles


def _slice_video(
    input_path: str,
    out_dir: str,
    grid: Grid,
    duration: float,
    max_duration: float,
    max_bytes: int,
) -> List[List[str]]:
    clip_duration = min(duration, max_duration) if duration > 0 else max_duration

    ffmpeg = ffmpeg_path()
    tile_paths: List[List[str]] = []
    for r in range(grid.rows):
        row: List[str] = []
        for c in range(grid.cols):
            x, y = c * grid.tile_size, r * grid.tile_size
            out_path = os.path.join(out_dir, f"tile_{r}_{c}.webm")
            # pad 대상 크기가 입력과 정확히 같으면(둘 중 한 변이라도) 일부 ffmpeg 빌드에서
            # "Padded dimensions cannot be smaller than input dimensions" 오류를 내는
            # 버그가 있어(실제로는 작지 않은데도), 1px 여유를 둬서 항상 "더 크게" 만든다.
            # crop 좌표는 원본 기준 절대 좌표라 이 여유분의 영향을 받지 않는다.
            vf = (
                # black@0.0: 입력에 알파 채널이 있으면 패딩 영역이 투명하게 채워지고,
                # 없으면 그냥 불투명한 검정으로 채워진다.
                f"pad={grid.padded_width + 1}:{grid.padded_height + 1}:0:0:black@0.0,"
                f"crop={grid.tile_size}:{grid.tile_size}:{x}:{y},"
                f"scale={EMOJI_SIZE}:{EMOJI_SIZE}:flags=lanczos,fps=30"
            )
            _encode_with_size_limit(ffmpeg, input_path, vf, clip_duration, out_path, max_bytes)
            row.append(out_path)
        tile_paths.append(row)
    return tile_paths


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
            result = subprocess.run(cmd, capture_output=True)
        except OSError as exc:
            raise RuntimeError(f"ffmpeg 실행 실패: {exc}") from exc
        if result.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            last_error = result.stderr.decode(errors="ignore")
            continue
        if os.path.getsize(out_path) <= max_bytes:
            return
    if last_error is not None:
        raise RuntimeError(f"ffmpeg 인코딩 실패: {last_error[-500:]}")
    # 마지막 시도 결과가 용량 초과라도 일단 사용한다 (텔레그램이 거부하면 상위에서 에러 처리됨).
