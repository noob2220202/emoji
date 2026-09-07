"""원본 이미지/영상 크기를 보고 정사각형 타일 그리드(열x행)를 자동으로 계산한다.

핵심 아이디어:
- 텔레그램 커스텀 이모지는 반드시 100x100 정사각형이어야 한다.
- 사용자가 이해하기 쉬운 기준은 "픽셀 크기"가 아니라 "총 몇 조각으로 나뉘는지"이므로,
  목표 조각 개수(target_tile_count)와 원본 가로세로 비율로부터 열/행 개수를 역산한다.
  (조각이 너무 많으면 다 이어붙였을 때 메시지 자체가 커지고, 너무 적으면 각 조각이
  뭉텅뭉텅 커 보이므로 이 개수가 실질적인 "결과물 크기" 조절 다이얼이다.)
- 열 개수를 정한 뒤, 그 열 개수로 가로를 정확히 나눈 값을 타일 크기로 삼고 그 타일
  크기로 세로를 나눠서 행 개수를 정한다. 이렇게 하면 타일이 항상 정사각형이 되어
  100x100으로 리사이즈해도 원본 비율이 왜곡되지 않는다.
- 그 결과 개수가 max_tiles(텔레그램 한도에 맞춘 안전장치)를 넘으면 목표 개수를
  줄여가며 다시 계산한다.
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


def _grid_for_cols(width: int, height: int, cols: int) -> Grid:
    cols = max(1, cols)
    # ceil을 써야 tile_size*cols/rows(패딩된 캔버스 크기)가 항상 원본 크기 이상이
    # 되어 원본이 잘려나가지 않는다(round는 잘림이 생길 수 있음).
    tile_size = max(1, math.ceil(width / cols))
    rows = max(1, math.ceil(height / tile_size))
    return Grid(cols=cols, rows=rows, tile_size=tile_size)


def compute_single_row_grid(width: int, height: int) -> Grid:
    """세로는 무조건 타일 1칸(rows=1)이 되도록 그리드를 계산한다(글자 이모지화 전용).

    타일 크기를 원본 높이 그대로 쓰기 때문에 세로 방향으로는 자르거나 덧대는 부분이
    전혀 없고, 가로로는 원본 폭 전체를 담을 수 있을 만큼만 자동으로 열이 늘어난다.
    """
    if width <= 0 or height <= 0:
        raise ValueError("width와 height는 0보다 커야 합니다")
    tile_size = height
    cols = max(1, math.ceil(width / tile_size))
    return Grid(cols=cols, rows=1, tile_size=tile_size)


def compute_grid(width: int, height: int, target_tile_count: int = 12, max_tiles: int = 200) -> Grid:
    if width <= 0 or height <= 0:
        raise ValueError("width와 height는 0보다 커야 합니다")
    if target_tile_count <= 0:
        raise ValueError("target_tile_count는 0보다 커야 합니다")
    if max_tiles <= 0:
        raise ValueError("max_tiles는 0보다 커야 합니다")

    aspect = width / height
    initial_cols = max(1, round(math.sqrt(target_tile_count * aspect)))

    scale = 1.0
    while True:
        cols = max(1, round(initial_cols / scale))
        grid = _grid_for_cols(width, height, cols)
        # cols가 커질수록(가로) 또는 tile_size가 커질수록(세로, rows) total은 단조
        # 감소하다가 cols=1에서 바닥을 찍는다. cols=1인데도 max_tiles를 넘으면
        # (예: 극단적으로 길쭉한 이미지) 더 줄일 방법이 없으므로 그대로 반환한다 -
        # 무한 루프를 방지하고, 초과 여부는 호출하는 쪽에서 확인한다.
        if grid.total <= max_tiles or cols == 1:
            return grid
        scale *= 1.15
