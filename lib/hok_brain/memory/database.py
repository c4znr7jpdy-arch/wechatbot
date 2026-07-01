from __future__ import annotations
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

@dataclass
class User:
    user_id: str
    nickname: Optional[str] = None
    avatar: Optional[str] = None
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None

@dataclass
class Group:
    group_id: str
    group_name: Optional[str] = None
    create_time: Optional[str] = None

class UserDatabase:
    def __init__(self, db_path: str = "data/bot.db"):
        self.db_path = db_path
        self._is_memory = db_path == ":memory:" or db_path.startswith("file::memory:")
        if not self._is_memory:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = None
        self._init_db()

    def _get_conn(self):
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            if not self._is_memory:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA busy_timeout=5000")
        return self._conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    nickname TEXT,
                    avatar TEXT,
                    first_seen TEXT,
                    last_seen TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS groups (
                    group_id TEXT PRIMARY KEY,
                    group_name TEXT,
                    create_time TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_groups (
                    user_id TEXT,
                    group_id TEXT,
                    join_time TEXT,
                    role TEXT,
                    PRIMARY KEY (user_id, group_id)
                )
            """)

    def insert_user(self, user_id: str, nickname: str = None, avatar: str = None):
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO users (user_id, nickname, avatar, first_seen, last_seen)
                VALUES (?, ?, ?, COALESCE((SELECT first_seen FROM users WHERE user_id=?), ?), ?)
            """, (user_id, nickname, avatar, user_id, now, now))

    def get_user(self, user_id: str) -> Optional[User]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            if row:
                return User(user_id=row[0], nickname=row[1], avatar=row[2], first_seen=row[3], last_seen=row[4])
        return None

    def update_last_seen(self, user_id: str):
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            conn.execute("UPDATE users SET last_seen=? WHERE user_id=?", (now, user_id))

    def insert_group(self, group_id: str, group_name: str = None):
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO groups (group_id, group_name, create_time)
                VALUES (?, ?, ?)
            """, (group_id, group_name, now))

    def get_group(self, group_id: str) -> Optional[Group]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM groups WHERE group_id=?", (group_id,)).fetchone()
            if row:
                return Group(group_id=row[0], group_name=row[1], create_time=row[2])
        return None
