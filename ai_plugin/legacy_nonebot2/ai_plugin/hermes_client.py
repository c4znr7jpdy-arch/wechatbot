"""
Hermes API Client — 替代原有 AIHandler 的 AI 对话逻辑
通过 /v1/chat/completions 调用 Hermes Agent，支持流式逐句发送
"""
import os
import json
import asyncio
import httpx
from typing import AsyncGenerator

from nonebot import logger

from .message_buffer import get_buffer, format_perception_context


HERMES_API_URL = os.getenv("HERMES_API_URL", "http://localhost:8642")
HERMES_API_KEY = os.getenv("HERMES_API_KEY", "Abcd1234")
HERMES_API_TIMEOUT = int(os.getenv("HERMES_API_TIMEOUT", "300"))
HERMES_PERCEPTION_BUFFER = int(os.getenv("HERMES_PERCEPTION_BUFFER", "15"))


class HermesClient:
    def __init__(self):
        self.api_url = HERMES_API_URL
        self.api_key = HERMES_API_KEY
        self.timeout = HERMES_API_TIMEOUT

    def _session_id(self, user_id: str, chat_id: str) -> str:
        return f"nonebot:{chat_id}:{user_id}"

    async def chat_stream(
        self,
        user_id: str,
        text: str,
        chat_id: str,
        is_group: bool = False,
        quoted_text: str | None = None,
        force_long: bool = False,
    ) -> AsyncGenerator[str, None]:
        """流式调用 Hermes，逐句 yield（按换行拆分）"""
        session_id = self._session_id(user_id, chat_id)

        perception_ctx = ""
        if is_group:
            recent = get_buffer().get_recent(chat_id, limit=HERMES_PERCEPTION_BUFFER)
            perception_ctx = format_perception_context(recent)

        messages = []
        if perception_ctx:
            messages.append({
                "role": "system",
                "content": f"以下是群聊中最近的对话记录，供你了解上下文：\n\n{perception_ctx}",
            })
        if force_long:
            messages.append({
                "role": "system",
                "content": (
                    "【格式覆盖】本次回复必须使用科普模式：每句话20-30个字，包含完整信息，"
                    "严格只回复6条。不要拆成碎片短句。每一条都是一个完整的长句。"
                ),
            })
        if quoted_text:
            messages.append({"role": "user", "content": f"[引用消息] {quoted_text}"})
        messages.append({"role": "user", "content": f"[发言人:{user_id}] {text}"})

        payload = {
            "model": "hermes-agent",
            "messages": messages,
            "stream": True,
            "metadata": {
                "user_id": user_id,
                "group_id": chat_id if is_group else "",
                "adapter_name": "OneBot V12",
                "is_private": not is_group,
            },
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Hermes-Session-Id": session_id,
        }

        buffer = ""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self.api_url}/v1/chat/completions",
                    json=payload,
                    headers=headers,
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")
                            if not content:
                                continue
                            buffer += content
                            # 按换行拆分，每行作为一条独立消息发出
                            while "\n" in buffer:
                                line_text, buffer = buffer.split("\n", 1)
                                line_text = line_text.strip()
                                if line_text:
                                    yield line_text
                        except (json.JSONDecodeError, IndexError, KeyError):
                            continue

            # 发送剩余内容
            if buffer.strip():
                yield buffer.strip()

        except httpx.TimeoutException:
            logger.error(f"[HERMES] 流式请求超时 ({self.timeout}s)")
            yield "超时了，再问一次"
        except httpx.HTTPStatusError as e:
            logger.error(f"[HERMES] HTTP {e.response.status_code}")
            yield "服务暂时不可用"
        except Exception as e:
            logger.exception(f"[HERMES] 流式请求失败: {e}")
            yield "出了点问题"

    async def chat(
        self,
        user_id: str,
        text: str,
        chat_id: str,
        is_group: bool = False,
        quoted_text: str | None = None,
    ) -> str:
        """非流式调用（兼容旧接口）"""
        lines = []
        async for line in self.chat_stream(
            user_id=user_id, text=text, chat_id=chat_id,
            is_group=is_group, quoted_text=quoted_text,
        ):
            lines.append(line)
        return "\n".join(lines)


_client: HermesClient | None = None


def get_hermes_client() -> HermesClient:
    global _client
    if _client is None:
        _client = HermesClient()
        logger.info(f"[HERMES] Client 已初始化: {HERMES_API_URL}")
    return _client
