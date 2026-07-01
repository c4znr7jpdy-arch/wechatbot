"""
多站点批量签到模块 — 每日自动签到 Gemini/FreeAPI/韭菜盒子等
"""
import httpx

ACCOUNTS = [
    {
        "name": "GemAI",
        "url": "https://api.gemai.cc/api/user/checkin",
        "userId": "183778",
        "cookie": "session=MTc4MjM3NTg0MXxEWDhFQVFMX2dBQUJFQUVRQUFEX2xQLUFBQVVHYzNSeWFXNW5EQVFBQW1sa0EybHVkQVFGQVAwRm04UUdjM1J5YVc1bkRBb0FDSFZ6WlhKdVlXMWxCbk4wY21sdVp3d0tBQWhETWpBd01qUXhPUVp6ZEhKcGJtY01CZ0FFY205c1pRTnBiblFFQWdBQ0JuTjBjbWx1Wnd3SUFBWnpkR0YwZFhNRGFXNTBCQUlBQWdaemRISnBibWNNQndBRlozSnZkWEFHYzNSeWFXNW5EQWtBQjJSbFptRjFiSFE9fEGluo0tvPkFpo3OGYD46LV4WPeTqfQy8UPedLGEJrff",
    },
    {
        "name": "FreeAPI (DGBMC)",
        "url": "https://freeapi.dgbmc.top/api/user/checkin",
        "userId": "20",
        "cookie": "session=MTc4MTUyMTI3N3xEWDhFQVFMX2dBQUJFQUVRQUFEX2tmLUFBQVVHYzNSeWFXNW5EQVlBQkhKdmJHVURhVzUwQkFJQUFnWnpkSEpwYm1jTUNBQUdjM1JoZEhWekEybHVkQVFDQUFJR2MzUnlhVzVuREFjQUJXZHliM1Z3Qm5OMGNtbHVad3dKQUFka1pXWmhkV3gwQm5OMGNtbHVad3dFQUFKcFpBTnBiblFFQWdBb0JuTjBjbWx1Wnd3S0FBaDFjMlZ5Ym1GdFpRWnpkSEpwYm1jTUNnQUlRVEl3TURJME1Uaz18wjSHOQQO3AqYpOtUp1B35E4L2jZHgB9zw2lHN-w9lrI=",
    },
]


async def checkin_all() -> str:
    """执行所有站点签到，返回格式化结果文本"""
    lines = ["每日签到结果："]
    async with httpx.AsyncClient(timeout=15.0) as client:
        for acct in ACCOUNTS:
            try:
                url = acct["url"]
                from urllib.parse import urlparse
                parsed = urlparse(url)
                headers = {
                    "accept": "application/json, text/plain, */*",
                    "content-length": "0",
                    "cookie": acct["cookie"],
                    "new-api-user": acct["userId"],
                    "origin": f"{parsed.scheme}://{parsed.hostname}",
                    "referer": f"{parsed.scheme}://{parsed.hostname}/console/personal",
                    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
                }
                resp = await client.post(url, headers=headers)
                data = resp.json()
                if data.get("success"):
                    date = (data.get("data") or {}).get("checkin_date", "未知")
                    lines.append(f"✅ {acct['name']}: 签到成功 ({date})")
                else:
                    msg = data.get("message", "未知错误")
                    lines.append(f"❌ {acct['name']}: {msg}")
            except Exception as e:
                lines.append(f"❌ {acct['name']}: 请求失败 ({e})")
    return "\n".join(lines)
