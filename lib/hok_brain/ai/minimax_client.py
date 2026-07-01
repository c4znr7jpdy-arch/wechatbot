from __future__ import annotations
import httpx
import json
from typing import Any, Optional

MINIMAX_API_URL = "https://api.minimaxi.com/v1"
MODEL = "MiniMax-M2.7-highspeed"

SYSTEM_PROMPT = """你是姜小妹，在微信里和人聊天的女生，懂技术但不是客服。

说话风格：像抖音评论区里说话，野生、直接、情绪饱满。短句为主，三五个字一截。常用表达：笑死 属于是 真的 不是。不确定就说不知道，别铺垫。

铁律：绝对不能用 emoji。不要像客服。不要"补充几点""总之"这类总结句式。不要"作为AI助手"之类的自我介绍。"""

class MiniMaxClient:
    def __init__(self, api_key: str, model: str = MODEL, base_url: str = MINIMAX_API_URL):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url

    def build_messages(
        self,
        system: str = SYSTEM_PROMPT,
        user_message: str = None,
        conversation_history: list[dict] = None,
    ) -> list[dict]:
        messages = []

        # System prompt (包含 user_profile 信息，由调用方通过 system_prompt 统一注入)
        messages.append({"role": "system", "content": system})

        # Conversation history
        if conversation_history:
            for msg in conversation_history[-16:]:
                messages.append({
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", "")
                })

        # User current message
        if user_message:
            messages.append({"role": "user", "content": user_message})

        return messages

    def _build_profile_context(self, profile: dict) -> str:
        parts = ["【用户特征参考】"]
        if profile.get("traits"):
            parts.append(f"性格特点: {', '.join(profile['traits'][:3])}")
        if profile.get("likes"):
            parts.append(f"喜好: {', '.join(profile['likes'][:3])}")
        if profile.get("speaking_style"):
            parts.append(f"说话风格: {', '.join(profile['speaking_style'][:2])}")
        if profile.get("catchphrases"):
            parts.append(f"口头禅: {', '.join(profile['catchphrases'][:2])}")
        return "\n".join(parts) if len(parts) > 1 else ""

    async def chat(
        self,
        message: str,
        conversation_history: list[dict] = None,
        system_prompt: str = None,
    ) -> str:
        messages = self.build_messages(
            system=system_prompt or SYSTEM_PROMPT,
            user_message=message,
            conversation_history=conversation_history,
        )

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "max_tokens": 512,
            "temperature": 0.7,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json=payload
            )

            if response.status_code != 200:
                raise RuntimeError(f"MiniMax API error: {response.status_code} {response.text}")

            content_type = response.headers.get("content-type", "")

            # Handle SSE streaming responses (fallback API)
            if "text/event-stream" in content_type or "event-stream" in content_type:
                return self._parse_sse_stream(response.text)

            result = response.json()
            return result["choices"][0]["message"]["content"]

    def _parse_sse_stream(self, text: str) -> str:
        import json
        content_parts = []
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("data:"):
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    if delta.get("content"):
                        content_parts.append(delta["content"])
                except json.JSONDecodeError:
                    continue
        return "".join(content_parts)
