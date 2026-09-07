from io import BytesIO

from PIL import Image

from bot.image_split import EMOJI_SIZE, split_static_image


def _make_png(width: int, height: int) -> bytes:
    im = Image.new("RGB", (width, height))
    for x in range(width):
        for y in range(height):
            im.putpixel((x, y), (x % 256, y % 256, 0))
    buf = BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def test_split_returns_correct_grid_shape():
    data = _make_png(500, 500)
    grid, tiles = split_static_image(data, target_tile_count=12, max_tiles=200)
    assert len(tiles) == grid.rows
    assert all(len(row) == grid.cols for row in tiles)


def test_each_tile_is_valid_100x100_png():
    data = _make_png(330, 220)
    _grid, tiles = split_static_image(data, target_tile_count=12, max_tiles=200)
    for row in tiles:
        for tile_bytes in row:
            im = Image.open(BytesIO(tile_bytes))
            assert im.format == "PNG"
            assert im.size == (EMOJI_SIZE, EMOJI_SIZE)


def test_non_divisible_dimensions_are_padded_not_cropped_out():
    # 333x111은 그리드 계산 결과와 정확히 나누어떨어지지 않는 경우를 검증한다.
    data = _make_png(333, 111)
    grid, tiles = split_static_image(data, target_tile_count=12, max_tiles=200)
    assert grid.padded_width >= 333
    assert grid.padded_height >= 111
    assert len(tiles) * len(tiles[0]) == grid.total


def test_low_target_count_yields_a_compact_pack():
    data = _make_png(1200, 1200)
    grid, _tiles = split_static_image(data, target_tile_count=10, max_tiles=200)
    assert grid.total <= 16  # 10개 근처의 작은 결과물이어야 한다(예: 3x3, 3x4 등)
