from bot.grid import compute_grid


def test_square_image_produces_square_grid():
    grid = compute_grid(1000, 1000, target_tile_px=100, max_tiles=100)
    assert grid.cols == grid.rows == 10
    assert grid.tile_size == 100


def test_wide_image_produces_more_columns_than_rows():
    grid = compute_grid(2000, 400, target_tile_px=100, max_tiles=100)
    assert grid.cols > grid.rows


def test_tall_image_produces_more_rows_than_columns():
    grid = compute_grid(400, 2000, target_tile_px=100, max_tiles=100)
    assert grid.rows > grid.cols


def test_respects_max_tiles():
    grid = compute_grid(5000, 5000, target_tile_px=50, max_tiles=64)
    assert grid.total <= 64


def test_tiny_image_still_produces_at_least_one_tile():
    grid = compute_grid(10, 10, target_tile_px=120, max_tiles=100)
    assert grid.cols == 1
    assert grid.rows == 1


def test_extreme_aspect_ratio_falls_back_to_single_tile_axis():
    grid = compute_grid(5000, 50, target_tile_px=100, max_tiles=20)
    assert grid.total <= 20
    assert grid.cols >= 1 and grid.rows >= 1
