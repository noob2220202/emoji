import importlib

import pytest


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """각 테스트마다 격리된 SQLite 파일로 bot.db를 다시 임포트해서 돌려준다."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    import bot.config as config

    importlib.reload(config)
    import bot.db as db

    importlib.reload(db)
    db.init_db()
    return db


def test_free_quota_allows_up_to_daily_limit_then_blocks(fresh_db):
    db = fresh_db
    user_id = 111
    assert db.try_consume_free_quota(user_id, "image") is True
    assert db.try_consume_free_quota(user_id, "image") is True
    # 기본 하루 무료 이미지 횟수는 2회
    assert db.try_consume_free_quota(user_id, "image") is False


def test_gif_quota_is_tracked_separately_from_image(fresh_db):
    db = fresh_db
    user_id = 222
    assert db.try_consume_free_quota(user_id, "gif") is True
    assert db.try_consume_free_quota(user_id, "gif") is False
    # gif 소진과 무관하게 image는 그대로 사용 가능해야 한다
    assert db.try_consume_free_quota(user_id, "image") is True


def test_remaining_free_quota_reflects_usage(fresh_db):
    db = fresh_db
    user_id = 333
    img_left, gif_left = db.remaining_free_quota(user_id)
    assert (img_left, gif_left) == (2, 1)
    db.try_consume_free_quota(user_id, "image")
    img_left, gif_left = db.remaining_free_quota(user_id)
    assert (img_left, gif_left) == (1, 1)


def test_quota_is_per_user(fresh_db):
    db = fresh_db
    db.try_consume_free_quota(1, "image")
    db.try_consume_free_quota(1, "image")
    assert db.try_consume_free_quota(1, "image") is False
    # 다른 유저는 영향을 받지 않는다
    assert db.try_consume_free_quota(2, "image") is True


def test_pack_lifecycle_record_list_and_add(fresh_db):
    db = fresh_db
    db.record_new_pack("pack1_by_bot", owner_id=1, title="테스트팩", sticker_format="static", tile_count=50)

    packs = db.list_user_packs(1)
    assert len(packs) == 1
    assert packs[0].pack_name == "pack1_by_bot"
    assert packs[0].tile_count == 50

    db.add_tiles_to_pack_record("pack1_by_bot", 30)
    updated = db.get_pack("pack1_by_bot")
    assert updated.tile_count == 80


def test_list_user_packs_filters_by_format(fresh_db):
    db = fresh_db
    db.record_new_pack("static_pack_by_bot", owner_id=1, title="정지", sticker_format="static", tile_count=10)
    db.record_new_pack("video_pack_by_bot", owner_id=1, title="영상", sticker_format="video", tile_count=10)

    static_only = db.list_user_packs(1, "static")
    assert [p.pack_name for p in static_only] == ["static_pack_by_bot"]

    video_only = db.list_user_packs(1, "video")
    assert [p.pack_name for p in video_only] == ["video_pack_by_bot"]


def test_other_users_packs_are_not_visible(fresh_db):
    db = fresh_db
    db.record_new_pack("p1_by_bot", owner_id=1, title="유저1", sticker_format="static", tile_count=10)
    assert db.list_user_packs(2) == []
