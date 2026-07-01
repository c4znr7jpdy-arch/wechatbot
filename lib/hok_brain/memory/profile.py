from __future__ import annotations
import json
import os
from dataclasses import dataclass, field
from typing import Optional

import httpx


@dataclass
class UserProfile:
    user_id: str
    traits: list[str] = field(default_factory=list)
    likes: list[str] = field(default_factory=list)
    hobbies: list[str] = field(default_factory=list)
    speaking_style: list[str] = field(default_factory=list)
    catchphrases: list[str] = field(default_factory=list)
    message_count: int = 0


_EXTRACT_PROMPT = """你是用户画像分析师。根据下面的聊天记录，提取这个用户的特征。

要求：
- 只提取有明确依据的信息，不要猜测
- 每个字段最多5项，用最精炼的词语（2-6个字）
- 如果某个字段没有信息，返回空数组
- 口头禅必须是用户反复使用的固定表达（至少出现2次），不是随机词语
- 说话风格描述用户的表达习惯（如"喜欢用反问句"、"经常发语音"、"爱用缩写"）
- 忽略命令消息（/开头的）

返回严格的JSON格式：
{"traits": [], "likes": [], "hobbies": [], "speaking_style": [], "catchphrases": []}

聊天记录：
"""


class ProfileExtractor:
    """用 LLM 从对话中提取用户画像"""

    def __init__(self, api_key: str = None, base_url: str = None):
        self.api_key = api_key or os.getenv("MINIMAX_API_KEY", "")
        self.base_url = base_url or os.getenv("MINIMAX_API_BASE", "https://api.minimaxi.com/v1")
        self._msg_buffer: dict[str, list[str]] = {}
        self._trigger_count = 10

    def buffer_message(self, user_id: str, text: str):
        """缓存用户消息，达到阈值时触发提取"""
        if not text or text.startswith("/"):
            return
        if user_id not in self._msg_buffer:
            self._msg_buffer[user_id] = []
        self._msg_buffer[user_id].append(text)

    def should_extract(self, user_id: str) -> bool:
        """是否应该触发画像提取"""
        msgs = self._msg_buffer.get(user_id, [])
        return len(msgs) >= self._trigger_count

    def get_buffered_text(self, user_id: str) -> str:
        """获取缓存的消息文本并清空"""
        msgs = self._msg_buffer.pop(user_id, [])
        return "\n".join(msgs)

    async def extract_with_llm(self, text: str) -> dict:
        """用 LLM 提取画像信息"""
        if not self.api_key:
            return {}

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "MiniMax-M2.7-highspeed",
                        "messages": [
                            {"role": "system", "content": _EXTRACT_PROMPT},
                            {"role": "user", "content": text},
                        ],
                        "stream": False,
                        "max_tokens": 300,
                        "temperature": 0.2,
                    },
                )
                if resp.status_code != 200:
                    return {}
                data = resp.json()
                content = data["choices"][0]["message"]["content"].strip()
                # 提取 JSON 部分
                start = content.find("{")
                end = content.rfind("}") + 1
                if start >= 0 and end > start:
                    result = json.loads(content[start:end])
                    # 验证格式
                    valid = {}
                    for key in ["traits", "likes", "hobbies", "speaking_style", "catchphrases"]:
                        val = result.get(key, [])
                        if isinstance(val, list):
                            valid[key] = [str(v).strip() for v in val if v and len(str(v).strip()) >= 2][:5]
                        else:
                            valid[key] = []
                    return valid
        except Exception:
            pass
        return {}

    def extract(self, text: str) -> dict:
        """兼容旧接口 — 同步版本，返回空（实际提取走 async）"""
        return {}

    def merge_profiles(self, existing: dict, new_info: dict) -> dict:
        """合并新旧画像，去重并限制每个字段最多8项"""
        merged = {}
        for key in ["traits", "likes", "hobbies", "speaking_style", "catchphrases"]:
            old_vals = existing.get(key, []) if existing else []
            new_vals = new_info.get(key, [])
            combined = list(dict.fromkeys(old_vals + new_vals))
            merged[key] = combined[-8:]
        return merged

    def update_profile(self, profile: UserProfile, new_info: dict):
        """更新已有画像"""
        for key in ["likes", "hobbies", "traits", "speaking_style", "catchphrases"]:
            if key in new_info and new_info[key]:
                current = getattr(profile, key)
                updated = list(dict.fromkeys(current + new_info[key]))[-8:]
                setattr(profile, key, updated)
        profile.message_count += 1
