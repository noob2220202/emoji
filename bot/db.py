"""사용자별 하루 무료 횟수(자정 초기화)와, 이 봇으로 만든 이모지 팩 목록을 저장하는
가벼운 SQLite 저장소. 텔레그램 Bot API에는 "이 봇이 특정 유저를 위해 만든 팩 목록"을
조회하는 방법이 없기 때문에, 기존 팩에 이어서 추가하려면 이 봇이 직접 기억해둬야 한다.
"""

import os
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional
from zoneinfo import ZoneInfo

from . import config

_TZ = ZoneInfo(config.QUOTA_TIMEZONE)
_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(config.DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _lock, closing(_connect()) as conn, conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS quota_v2 (
                user_id INTEGER NOT NULL,
                quota_date TEXT NOT NULL,
                used INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, quota_date)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS packs (
                pack_name TEXT PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                sticker_format TEXT NOT NULL,
                tile_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )


def today_str() -> str:
    return datetime.now(_TZ).date().isoformat()


def try_consume_free_quota(user_id: int) -> bool:
    """오늘 무료 횟수가 남아 있으면 소비하고 True, 없으면 False를 반환한다."""
    date = today_str()
    with _lock, closing(_connect()) as conn, conn:
        conn.execute("INSERT OR IGNORE INTO quota_v2 (user_id, quota_date) VALUES (?, ?)", (user_id, date))
        row = conn.execute(
            "SELECT used FROM quota_v2 WHERE user_id = ? AND quota_date = ?", (user_id, date)
        ).fetchone()
        if row["used"] >= config.FREE_USES_PER_DAY:
            return False
        conn.execute(
            "UPDATE quota_v2 SET used = used + 1 WHERE user_id = ? AND quota_date = ?", (user_id, date)
        )
        return True


@dataclass(frozen=True)
class PackInfo:
    pack_name: str
    title: str
    sticker_format: str
    tile_count: int


def list_user_packs(owner_id: int, sticker_format: Optional[str] = None) -> List[PackInfo]:
    query = "SELECT pack_name, title, sticker_format, tile_count FROM packs WHERE owner_id = ?"
    params: list = [owner_id]
    if sticker_format is not None:
        query += " AND sticker_format = ?"
        params.append(sticker_format)
    query += " ORDER BY created_at DESC"
    with _lock, closing(_connect()) as conn:
        rows = conn.execute(query, params).fetchall()
    return [PackInfo(r["pack_name"], r["title"], r["sticker_format"], r["tile_count"]) for r in rows]


def record_new_pack(pack_name: str, owner_id: int, title: str, sticker_format: str, tile_count: int) -> None:
    with _lock, closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO packs (pack_name, owner_id, title, sticker_format, tile_count, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (pack_name, owner_id, title, sticker_format, tile_count, datetime.now(_TZ).isoformat()),
        )


def add_tiles_to_pack_record(pack_name: str, added: int) -> None:
    with _lock, closing(_connect()) as conn, conn:
        conn.execute("UPDATE packs SET tile_count = tile_count + ? WHERE pack_name = ?", (added, pack_name))


def get_pack(pack_name: str) -> Optional[PackInfo]:
    with _lock, closing(_connect()) as conn:
        row = conn.execute(
            "SELECT pack_name, title, sticker_format, tile_count FROM packs WHERE pack_name = ?",
            (pack_name,),
        ).fetchone()
    return PackInfo(row["pack_name"], row["title"], row["sticker_format"], row["tile_count"]) if row else None
