"""
MiniMax Embedding Store - SQLite-based vector storage for conversations and user profiles.
"""
from __future__ import annotations

import json
import pickle
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx
import numpy as np


class EmbeddingStore:
    """
    SQLite-based embedding store using MiniMax API for vectorization.
    Stores conversations and user profiles with embeddings as pickled numpy arrays.
    """

    def __init__(self, db_path: str = "data/embeddings.db", api_key: Optional[str] = None):
        self.db_path = db_path
        self.api_key = api_key
        self._is_memory = db_path in (":memory:", "file::memory:")
        if not self._is_memory:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            if not self._is_memory:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA busy_timeout=5000")
        return self._conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    group_id TEXT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    embedding BLOB,
                    timestamp TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_profiles (
                    user_id TEXT PRIMARY KEY,
                    traits TEXT,
                    likes TEXT,
                    hobbies TEXT,
                    speaking_style TEXT,
                    catchphrases TEXT,
                    embedding BLOB,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_timestamp ON conversations(timestamp)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS style_corpus (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    topic TEXT,
                    source TEXT DEFAULT 'douyin',
                    embedding BLOB,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_style_topic ON style_corpus(topic)")

    async def _get_embedding(self, text: str) -> Optional[np.ndarray]:
        """Get embedding vector from MiniMax API."""
        if not self.api_key or not text or not text.strip():
            return None

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.minimaxi.com/v1/embeddings",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "embo",
                        "input": text,
                    },
                )
                response.raise_for_status()
                data = response.json()
                embedding = data["data"][0]["embedding"]
                return np.array(embedding, dtype=np.float32)
        except Exception:
            return None

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """Compute cosine similarity between two vectors."""
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))

    async def add_conversation(
        self,
        user_id: str,
        content: str,
        group_id: Optional[str] = None,
        role: str = "user",
    ) -> int:
        """Add a conversation and return its ID."""
        embedding = await self._get_embedding(content)
        embedding_blob = pickle.dumps(embedding) if embedding is not None else None
        timestamp = datetime.now().isoformat()

        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO conversations (user_id, group_id, role, content, embedding, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, group_id, role, content, embedding_blob, timestamp),
            )
            return cursor.lastrowid

    def get_conversations(
        self,
        user_id: str,
        group_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get recent conversations for a user."""
        with self._get_conn() as conn:
            if group_id:
                rows = conn.execute(
                    """
                    SELECT id, user_id, group_id, role, content, timestamp
                    FROM conversations
                    WHERE user_id = ? AND group_id = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (user_id, group_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, user_id, group_id, role, content, timestamp
                    FROM conversations
                    WHERE user_id = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (user_id, limit),
                ).fetchall()

            return [
                {
                    "id": row[0],
                    "user_id": row[1],
                    "group_id": row[2],
                    "role": row[3],
                    "content": row[4],
                    "timestamp": row[5],
                }
                for row in rows
            ]

    async def search_conversations(
        self,
        query: str,
        user_id: Optional[str] = None,
        group_id: Optional[str] = None,
        limit: int = 5,
        user_profile: dict = None,
    ) -> list[dict[str, Any]]:
        """Search conversations using cosine similarity on embeddings."""
        # 如果 query 太短，尝试结合 user_profile 扩展
        if len(query) < 10 and user_profile:
            extra_parts = []
            if user_profile.get("likes"):
                extra_parts.extend(user_profile["likes"][:3])
            if user_profile.get("traits"):
                extra_parts.extend(user_profile["traits"][:2])
            if extra_parts:
                extended_query = f"{query} {' '.join(extra_parts)}"
                query_embedding = await self._get_embedding(extended_query)
            else:
                query_embedding = await self._get_embedding(query)
        else:
            query_embedding = await self._get_embedding(query)

        with self._get_conn() as conn:
            if user_id and group_id:
                sql = "SELECT id, user_id, group_id, role, content, embedding, timestamp FROM conversations WHERE user_id = ? AND group_id = ? ORDER BY timestamp DESC LIMIT 50"
                rows = conn.execute(sql, (user_id, group_id)).fetchall()
            elif user_id:
                sql = "SELECT id, user_id, group_id, role, content, embedding, timestamp FROM conversations WHERE user_id = ? ORDER BY timestamp DESC LIMIT 50"
                rows = conn.execute(sql, (user_id,)).fetchall()
            else:
                sql = "SELECT id, user_id, group_id, role, content, embedding, timestamp FROM conversations ORDER BY timestamp DESC LIMIT 50"
                rows = conn.execute(sql).fetchall()

        # Fallback: if no query_embedding (API down), use keyword matching instead of blind recent messages
        if query_embedding is None:
            return self.search_conversations_by_keywords(
                query=query,
                user_id=user_id,
                group_id=group_id,
                limit=limit
            )

        # Normal embedding-based search
        results = []
        for row in rows:
            if row[5] is None:
                continue
            stored_embedding = pickle.loads(row[5])
            similarity = self._cosine_similarity(query_embedding, stored_embedding)
            if similarity < 0.3:
                continue
            results.append({
                "id": row[0],
                "user_id": row[1],
                "group_id": row[2],
                "role": row[3],
                "content": row[4],
                "timestamp": row[6],
                "similarity": similarity,
            })

        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:limit]

    async def set_user_profile(
        self,
        user_id: str,
        traits: Optional[list[str]] = None,
        likes: Optional[list[str]] = None,
        hobbies: Optional[list[str]] = None,
        speaking_style: Optional[list[str]] = None,
        catchphrases: Optional[list[str]] = None,
    ) -> bool:
        """Set or update a user profile."""
        now = datetime.now().isoformat()

        # Build combined text for embedding
        parts = []
        if traits:
            parts.extend(traits)
        if likes:
            parts.extend(likes)
        if hobbies:
            parts.extend(hobbies)
        if speaking_style:
            parts.extend(speaking_style)
        if catchphrases:
            parts.extend(catchphrases)
        combined_text = " ".join(parts)
        embedding = await self._get_embedding(combined_text)
        embedding_blob = pickle.dumps(embedding) if embedding is not None else None

        with self._get_conn() as conn:
            # Get existing profile to preserve values not being updated
            existing = conn.execute(
                "SELECT traits, likes, hobbies, speaking_style, catchphrases FROM user_profiles WHERE user_id = ?",
                (user_id,),
            ).fetchone()

            final_traits = json.dumps(traits) if traits is not None else (existing[0] if existing else None)
            final_likes = json.dumps(likes) if likes is not None else (existing[1] if existing else None)
            final_hobbies = json.dumps(hobbies) if hobbies is not None else (existing[2] if existing else None)
            final_speaking_style = json.dumps(speaking_style) if speaking_style is not None else (existing[3] if existing else None)
            final_catchphrases = json.dumps(catchphrases) if catchphrases is not None else (existing[4] if existing else None)

            conn.execute(
                """
                INSERT OR REPLACE INTO user_profiles
                (user_id, traits, likes, hobbies, speaking_style, catchphrases, embedding, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    final_traits,
                    final_likes,
                    final_hobbies,
                    final_speaking_style,
                    final_catchphrases,
                    embedding_blob,
                    now,
                ),
            )
        return True

    def get_user_profile(self, user_id: str) -> Optional[dict[str, Any]]:
        """Get user profile by user_id."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM user_profiles WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if row is None:
                return None

            return {
                "user_id": row[0],
                "traits": json.loads(row[1]) if row[1] else [],
                "likes": json.loads(row[2]) if row[2] else [],
                "hobbies": json.loads(row[3]) if row[3] else [],
                "speaking_style": json.loads(row[4]) if row[4] else [],
                "catchphrases": json.loads(row[5]) if row[5] else [],
                "updated_at": row[7],
            }

    def get_conversation_turns(
        self,
        user_id: str,
        group_id: str = None,
        limit: int = 6
    ) -> list[dict[str, Any]]:
        """获取最近 N 轮对话（用户+AI 交替的完整轮次）"""
        with self._get_conn() as conn:
            if group_id:
                rows = conn.execute(
                    """
                    SELECT id, user_id, group_id, role, content, timestamp
                    FROM conversations
                    WHERE user_id = ? AND group_id = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (user_id, group_id, limit * 3),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, user_id, group_id, role, content, timestamp
                    FROM conversations
                    WHERE user_id = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (user_id, limit * 3),
                ).fetchall()

        # 确保是用户和 AI 交替的完整轮次
        turns = []
        for row in reversed(rows):
            role = row[3]
            if not turns and role != "user":
                continue  # 从用户消息开始
            turns.append({
                "id": row[0],
                "user_id": row[1],
                "group_id": row[2],
                "role": role,
                "content": row[4],
                "timestamp": row[5],
            })
            if len(turns) >= limit * 2:
                break

        return turns[-limit:] if len(turns) >= limit else turns

    def get_group_recent_messages(
        self,
        group_id: str,
        limit: int = 10,
        exclude_user_id: str = None,
    ) -> list[dict[str, Any]]:
        """获取群聊中其他人的最近消息，让 AI 理解多人对话上下文"""
        with self._get_conn() as conn:
            if exclude_user_id:
                rows = conn.execute(
                    """
                    SELECT id, user_id, group_id, role, content, timestamp
                    FROM conversations
                    WHERE group_id = ? AND user_id != ? AND role = 'user'
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (group_id, exclude_user_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, user_id, group_id, role, content, timestamp
                    FROM conversations
                    WHERE group_id = ? AND role = 'user'
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (group_id, limit),
                ).fetchall()

        return [
            {
                "id": row[0],
                "user_id": row[1],
                "group_id": row[2],
                "role": row[3],
                "content": row[4],
                "timestamp": row[5],
            }
            for row in reversed(rows)
        ]

    def search_conversations_by_keywords(
        self,
        query: str,
        user_id: str = None,
        group_id: str = None,
        limit: int = 5
    ) -> list[dict[str, Any]]:
        """关键词匹配搜索（embedding 不可用时的 fallback）"""
        if not query or not query.strip():
            return []

        query_words = set(query.lower().split())

        with self._get_conn() as conn:
            if user_id and group_id:
                sql = "SELECT id, user_id, group_id, role, content, timestamp FROM conversations WHERE user_id = ? AND group_id = ? ORDER BY timestamp DESC LIMIT 100"
                rows = conn.execute(sql, (user_id, group_id)).fetchall()
            elif user_id:
                sql = "SELECT id, user_id, group_id, role, content, timestamp FROM conversations WHERE user_id = ? ORDER BY timestamp DESC LIMIT 100"
                rows = conn.execute(sql, (user_id,)).fetchall()
            else:
                sql = "SELECT id, user_id, group_id, role, content, timestamp FROM conversations ORDER BY timestamp DESC LIMIT 100"
                rows = conn.execute(sql).fetchall()

        scored = []
        for row in rows:
            content = row[4]
            if not content:
                continue
            content_words = set(content.lower().split())
            # Jaccard 相似度
            intersection = query_words & content_words
            if intersection:
                score = len(intersection) / max(len(query_words | content_words), 1)
                scored.append((score, {
                    "id": row[0],
                    "user_id": row[1],
                    "group_id": row[2],
                    "role": row[3],
                    "content": content,
                    "timestamp": row[5],
                    "similarity": score,
                }))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [msg for _, msg in scored[:limit]]

    async def add_style_example(self, content: str, topic: str = None, source: str = "douyin") -> int:
        """添加一条风格语料"""
        embedding = await self._get_embedding(content)
        embedding_blob = pickle.dumps(embedding) if embedding is not None else None
        timestamp = datetime.now().isoformat()
        with self._get_conn() as conn:
            cursor = conn.execute(
                "INSERT INTO style_corpus (content, topic, source, embedding, created_at) VALUES (?, ?, ?, ?, ?)",
                (content, topic, source, embedding_blob, timestamp),
            )
            return cursor.lastrowid

    async def add_style_batch(self, items: list[dict]) -> int:
        """批量添加风格语料 items: [{"content": ..., "topic": ..., "source": ...}]"""
        count = 0
        for item in items:
            await self.add_style_example(
                content=item["content"],
                topic=item.get("topic"),
                source=item.get("source", "douyin"),
            )
            count += 1
        return count

    async def search_style_corpus(self, query: str, limit: int = 3) -> list[dict]:
        """语义检索风格语料"""
        query_embedding = await self._get_embedding(query)
        if query_embedding is None:
            return self._search_style_by_keywords(query, limit)

        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT id, content, topic, source, embedding FROM style_corpus WHERE embedding IS NOT NULL"
            ).fetchall()

        results = []
        for row in rows:
            stored_embedding = pickle.loads(row[4])
            similarity = self._cosine_similarity(query_embedding, stored_embedding)
            if similarity < 0.2:
                continue
            results.append({
                "id": row[0],
                "content": row[1],
                "topic": row[2],
                "source": row[3],
                "similarity": similarity,
            })

        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:limit]

    def _search_style_by_keywords(self, query: str, limit: int = 3) -> list[dict]:
        """关键词匹配风格语料（embedding 不可用时的 fallback）"""
        query_words = set(query.lower().split())
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT id, content, topic, source FROM style_corpus ORDER BY id DESC LIMIT 200"
            ).fetchall()

        scored = []
        for row in rows:
            content = row[1]
            if not content:
                continue
            content_words = set(content.lower().split())
            intersection = query_words & content_words
            if intersection:
                score = len(intersection) / max(len(query_words | content_words), 1)
                scored.append((score, {
                    "id": row[0],
                    "content": row[1],
                    "topic": row[2],
                    "source": row[3],
                    "similarity": score,
                }))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:limit]]

    def get_style_corpus_count(self) -> int:
        """获取语料总数"""
        with self._get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM style_corpus").fetchone()
            return row[0] if row else 0
