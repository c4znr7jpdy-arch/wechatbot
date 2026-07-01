"""
新闻热点模块 — /新闻 命令，获取抖音热榜
"""
from datetime import datetime

import httpx
from nonebot import logger

API_ID = "10012789"
API_KEY = "8eb14a1232332764a246f46b062c715f"

# apihz 负载均衡节点
_API_HOSTS = [
    "https://cn.apihz.cn",
    "http://81.69.163.176",
    "http://101.35.2.25",
    "http://124.222.204.22",
    "http://101.34.207.105",
    "http://43.142.65.209",
    "http://81.68.85.14",
]


async def fetch_hot_news(count: int = 20) -> list[dict]:
    """获取抖音热榜"""
    for host in _API_HOSTS:
        try:
            url = f"{host}/api/xinwen/douyin.php"
            params = {"id": API_ID, "key": API_KEY}
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, params=params)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                if data.get("code") != 200:
                    logger.warning(f"[NEWS] apihz 错误: {data.get('msg', '')}")
                    continue
                items = data.get("data", [])
                if not items:
                    continue
                # 按热度排序，取前 count 条
                items.sort(key=lambda x: x.get("hot_value", 0), reverse=True)
                return items[:count]
        except Exception as e:
            logger.warning(f"[NEWS] {host} 请求失败: {e}")
            continue
    raise RuntimeError("所有 apihz 节点均不可用")


def format_news(items: list[dict]) -> str:
    """格式化热榜列表"""
    if not items:
        return "暂无热榜数据"
    now = datetime.now().strftime("%Y/%m/%d %H:%M")
    lines = [f"📰 今日热点 {now}"]
    for item in items:
        t = item.get("title", "").strip()
        if not t:
            continue
        lines.append(f"#{t}")
    return "\n".join(lines)
