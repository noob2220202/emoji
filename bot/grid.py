"""원본 이미지/영상 크기를 보고 정사각형 타일 그리드(열x행)를 자동으로 계산한다.

핵심 아이디어:
- 텔레그램 커스텀 이모지는 반드시 100x100 정사각형이어야 한다.
- 원본을 일정한 크기(target_tile_px)의 정사각형 타일로 나눈 뒤, 각 타일을
  100x100으로 리사이즈해서 하나의 이모지로 만든다.
- 열 개수는 "원본 가로 길이 / 목표 타일 픽셀"로, 타일 실제 크기는 그 열 개수로
  가로를 정확히 나눈 값으로 정하고, 행 개수는 그 타일 크기로 세로를 나눠서 정한다.
  이렇게 하면 타일이 항상 정사각형이 되어 리사이즈해도 원본 비율이 왜곡되지 않는다.
- 타일 개수가 max_tiles를 넘으면 목표 타일 픽셀을 키워가며(=타일 개수를 줄여가며)
  다시 계산한다.
"""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Grid:
    cols: int
    rows: int
    tile_size: int  # 원본 이미지 기준 타일 한 변의 픽셀 크기

    @property
    def total(self) -> int:
        return self.cols * self.rows

    @property
    def padded_width(self) -> int:
        return self.tile_size * self.cols

    @property
    def padded_height(self) -> int:
        return self.tile_size * self.rows


def compute_grid(width: int, height: int, target_tile_px: int = 120, max_tiles: int = 100) -> Grid:
    if width <= 0 or height <= 0:
        raise ValueError("width와 height는 0보다 커야 합니다")
    if target_tile_px <= 0:
        raise ValueError("target_tile_px는 0보다 커야 합니다")
    if max_tiles <= 0:
        raise ValueError("max_tiles는 0보다 커야 합니다")

    scale = 1.0
    while True:
        cols = max(1, round(width / (target_tile_px * scale)))
        # ceil을 써야 tile_size*cols/rows(패딩된 캔버스 크기)가 항상 원본 크기 이상이
        # 되어 원본이 잘려나가지 않는다(round는 잘림이 생길 수 있음).
        tile_size = max(1, math.ceil(width / cols))
        rows = max(1, math.ceil(height / tile_size))
        if cols * rows <= max_tiles or (cols == 1 and rows == 1):
            return Grid(cols=cols, rows=rows, tile_size=tile_size)
        scale *= 1.15
