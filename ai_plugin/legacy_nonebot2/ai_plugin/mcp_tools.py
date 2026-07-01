"""
MiniMax Token Plan 工具 — 直接调 API（无需 MCP 子进程）
- web_search: POST /v1/coding_plan/search
- understand_image: POST /v1/coding_plan/vlm
"""
import re
import base64
from pathlib import Path

import httpx
from nonebot import logger

from .utils import clean_at

SEARCH_INTENT_PATTERNS = [
    r"搜索.{2,}",
    r"搜一下.{2,}",
    r"查一下.{2,}",
    r"查下.{2,}",
    r"帮我查.{2,}",
    r"(?:最新|最近|今天|现在).{0,6}?(?:新闻|消息|情况|数据|价格|汇率|天气)",
    r"(?:什么是|是什么|是谁).{2,}",
    r"(?:热点|热榜|热搜).{1,}",
    r".{1,}?(?:事件|怎么回事|发生了什么).{0,}$",
    r"(?:告诉我|知道|了解).{2,}?(?:吗|不|一下|关于)",
    r"怎么(?:看|评价|理解).{2,}",
]


def detect_search_intent(text: str) -> str | None:
    """检测是否为联网搜索请求，返回搜索词或 None"""
    for pattern in SEARCH_INTENT_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            query = clean_at(text)
            # 去指令前缀
            for prefix in ["搜索", "查一下", "查下", "帮我查", "搜一下", "搜"]:
                if query.startswith(prefix):
                    query = query[len(prefix):].strip()
                    query = re.sub(r"^[\s：:，,]+", "", query)
                    break
            if query and len(query) >= 3:
                return query
    return None


class SearchClient:
    """MiniMax 联网搜索客户端"""

    def __init__(self, api_key: str, base_url: str = "https://api.minimaxi.com/v1"):
        self.api_key = api_key
        self.base_url = base_url

    async def search(self, query: str) -> list[dict]:
        """执行搜索，返回 [{title, url, snippet}, ...]"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.base_url}/coding_plan/search",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={"q": query},
            )
            if resp.status_code != 200:
                raise RuntimeError(f"搜索失败: {resp.status_code} {resp.text}")

            data = resp.json()
            base = data.get("base_resp", {})
            if base.get("status_code") != 0:
                raise RuntimeError(f"搜索错误: {base.get('status_msg', 'unknown')}")

            results = data.get("organic", [])
            formatted = []
            for r in results:
                formatted.append({
                    "title": r.get("title", ""),
                    "url": r.get("link", ""),
                    "snippet": r.get("snippet", ""),
                })
            return formatted

    def format_context(self, results: list[dict]) -> str:
        """将搜索结果格式化为对话上下文"""
        if not results:
            return "（未搜索到相关结果）"
        lines = ["【联网搜索结果】"]
        for i, r in enumerate(results[:5], 1):
            lines.append(f"{i}. {r['title']}\n   {r['snippet']}\n   来源: {r['url']}")
        return "\n".join(lines)


_search_client: SearchClient | None = None


def init_search_client(api_key: str, base_url: str = "https://api.minimaxi.com/v1") -> SearchClient:
    global _search_client
    _search_client = SearchClient(api_key=api_key, base_url=base_url)
    return _search_client


def get_search_client() -> SearchClient | None:
    return _search_client


# ---- 图片理解 (VLM) ----

class ImageUnderstandingClient:
    """MiniMax VLM 图片理解客户端"""

    def __init__(self, api_key: str, base_url: str = "https://api.minimaxi.com/v1"):
        self.api_key = api_key
        self.base_url = base_url

    async def understand(self, image_path: str, prompt: str = "请描述这张图片的内容") -> str:
        """分析图片，返回文字描述"""
        image_url = _encode_image_data_uri(image_path)
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self.base_url}/coding_plan/vlm",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={"prompt": prompt, "image_url": image_url},
            )
            if resp.status_code != 200:
                raise RuntimeError(f"VLM 图片理解失败: {resp.status_code} {resp.text}")
            data = resp.json()
            base = data.get("base_resp", {})
            if base.get("status_code") != 0:
                raise RuntimeError(f"VLM 图片理解错误: {base.get('status_msg', 'unknown')}")
            content = data.get("content", "")
            if not content:
                raise RuntimeError(f"VLM 未返回内容: {data}")
            return content


def _encode_image_data_uri(image_path: str) -> str:
    """将本地图片编码为 base64 data URI"""
    p = Path(image_path)
    if not p.exists():
        raise FileNotFoundError(f"图片文件不存在: {image_path}")
    ext = p.suffix.lower()
    mime_map = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp",
    }
    mime = mime_map.get(ext, "image/jpeg")
    b64 = base64.b64encode(p.read_bytes()).decode("utf-8")
    return f"data:{mime};base64,{b64}"


_vlm_client: ImageUnderstandingClient | None = None


def init_vlm_client(api_key: str, base_url: str = "https://api.minimaxi.com/v1") -> ImageUnderstandingClient:
    global _vlm_client
    _vlm_client = ImageUnderstandingClient(api_key=api_key, base_url=base_url)
    return _vlm_client


def get_vlm_client() -> ImageUnderstandingClient | None:
    return _vlm_client
