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
    assert db.try_consume_free_quota(user_id) is True
    assert db.try_consume_free_quota(user_id) is True
    # 기본 하루 무료 횟수는 2회
    assert db.try_consume_free_quota(user_id) is False


def test_remaining_free_quota_reflects_usage(fresh_db):
    db = fresh_db
    user_id = 333
    assert db.remaining_free_quota(user_id) == 2
    db.try_consume_free_quota(user_id)
    assert db.remaining_free_quota(user_id) == 1


def test_quota_is_per_user(fresh_db):
    db = fresh_db
    db.try_consume_free_quota(1)
    db.try_consume_free_quota(1)
    assert db.try_consume_free_quota(1) is False
    # 다른 유저는 영향을 받지 않는다
    assert db.try_consume_free_quota(2) is True


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


def test_other_users_packs_are_not_visible(fresh_db):
    db = fresh_db
    db.record_new_pack("p1_by_bot", owner_id=1, title="유저1", sticker_format="static", tile_count=10)
    assert db.list_user_packs(2) == []
