"""정지 이미지를 타일 그리드로 잘라 100x100 PNG 이모지 조각들을 만든다."""

from io import BytesIO
from typing import List, Tuple

from PIL import Image

from .grid import Grid, compute_grid

EMOJI_SIZE = 100


def split_static_image(
    data: bytes, target_tile_count: int = 12, max_tiles: int = 200
) -> Tuple[Grid, List[List[bytes]]]:
    """이미지 바이트를 받아 (Grid, 행렬 형태의 PNG 바이트 리스트)를 반환한다.

    반환되는 tiles[r][c]는 왼쪽 위부터 순서대로, 실제 텔레그램에 이모지를 이어
    붙였을 때 원본 그림이 재구성되도록 정렬되어 있다.
    """
    im = Image.open(BytesIO(data))
    im = im.convert("RGBA")
    width, height = im.size

    grid = compute_grid(width, height, target_tile_count, max_tiles)

    # 원본이 grid.padded_width/height 보다 작을 수 있으므로 투명 배경 캔버스에
    # 왼쪽 위 기준으로 붙여서 정확히 나누어떨어지게 만든다.
    canvas = Image.new("RGBA", (grid.padded_width, grid.padded_height), (0, 0, 0, 0))
    canvas.paste(im, (0, 0))

    tiles: List[List[bytes]] = []
    for r in range(grid.rows):
        row: List[bytes] = []
        for c in range(grid.cols):
            box = (
                c * grid.tile_size,
                r * grid.tile_size,
                (c + 1) * grid.tile_size,
                (r + 1) * grid.tile_size,
            )
            tile = canvas.crop(box).resize((EMOJI_SIZE, EMOJI_SIZE), Image.LANCZOS)
            buf = BytesIO()
            tile.save(buf, format="PNG")
            row.append(buf.getvalue())
        tiles.append(row)
    return grid, tiles
