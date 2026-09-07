from bot.grid import compute_grid, compute_single_row_grid


def test_square_image_hits_target_count_exactly_when_it_divides_evenly():
    grid = compute_grid(1000, 1000, target_tile_count=16, max_tiles=200)
    assert grid.cols == grid.rows == 4
    assert grid.total == 16
    assert grid.tile_size == 250


def test_wide_image_produces_more_columns_than_rows():
    grid = compute_grid(2000, 400, target_tile_count=12, max_tiles=200)
    assert grid.cols > grid.rows


def test_tall_image_produces_more_rows_than_columns():
    grid = compute_grid(400, 2000, target_tile_count=12, max_tiles=200)
    assert grid.rows > grid.cols


def test_higher_target_count_yields_more_tiles_for_the_same_image():
    small = compute_grid(1200, 1200, target_tile_count=9, max_tiles=200)
    big = compute_grid(1200, 1200, target_tile_count=100, max_tiles=200)
    assert big.total > small.total


def test_respects_max_tiles_even_when_target_count_is_higher():
    grid = compute_grid(5000, 5000, target_tile_count=200, max_tiles=64)
    assert grid.total <= 64


def test_target_count_of_one_yields_a_single_tile_for_a_reasonable_aspect_ratio():
    grid = compute_grid(1000, 1000, target_tile_count=1, max_tiles=200)
    assert (grid.cols, grid.rows) == (1, 1)


def test_extreme_wide_aspect_ratio_respects_max_tiles():
    grid = compute_grid(5000, 50, target_tile_count=12, max_tiles=20)
    assert grid.total <= 20
    assert grid.cols >= 1 and grid.rows >= 1


def test_extreme_tall_aspect_ratio_terminates_and_reports_overflow():
    # 회귀 테스트: cols가 1까지 줄어드는 건 빠른데 rows는 계속 커지는 극단적으로
    # 길쭉한(세로로 긴) 이미지에서 예전 구현은 무한 루프에 빠질 수 있었다.
    # cols=1에 도달하면 더 줄일 수 없으므로, max_tiles를 넘더라도 즉시 반환해야 한다
    # (호출하는 쪽에서 초과 여부를 확인해서 사용자에게 안내한다).
    grid = compute_grid(50, 100_000, target_tile_count=12, max_tiles=200)
    assert grid.cols == 1
    assert grid.total > 200


def test_padded_dimensions_never_smaller_than_original():
    for width, height in [(80, 60), (333, 111), (1000, 1000), (2000, 400), (400, 2000), (37, 53)]:
        grid = compute_grid(width, height, target_tile_count=12, max_tiles=200)
        assert grid.padded_width >= width
        assert grid.padded_height >= height


def test_single_row_grid_always_has_exactly_one_row():
    for width, height in [(1265, 378), (100, 100), (5000, 300), (60, 400), (1, 1)]:
        grid = compute_single_row_grid(width, height)
        assert grid.rows == 1
        assert grid.tile_size == height
        assert grid.padded_width >= width
        assert grid.padded_height == height  # 세로는 원본과 정확히 같아야 한다(잘림/여백 없음)


def test_single_row_grid_column_count_matches_width_ratio():
    # tile_size == height 이므로 cols는 ceil(width/height)여야 한다.
    grid = compute_single_row_grid(1000, 100)
    assert grid.cols == 10

    grid = compute_single_row_grid(1001, 100)
    assert grid.cols == 11  # 딱 안 나눠떨어지면 한 칸 더 늘어나 전체를 담는다


def test_single_row_grid_rejects_non_positive_dimensions():
    import pytest

    with pytest.raises(ValueError):
        compute_single_row_grid(0, 100)
    with pytest.raises(ValueError):
        compute_single_row_grid(100, 0)
