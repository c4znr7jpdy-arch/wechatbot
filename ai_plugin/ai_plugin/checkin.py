"""
多站点批量签到模块 — 每日自动签到 Gemini/FreeAPI/韭菜盒子等
"""
import httpx

ACCOUNTS = [
    {
        "name": "GemAI",
        "url": "https://api.gemai.cc/api/user/checkin",
        "userId": "183778",
        "cookie": "session=MTc4NDE2NDczOXxEWDhFQVFMX2dBQUJFQUVRQUFEX2xQLUFBQVVHYzNSeWFXNW5EQVlBQkhKdmJHVURhVzUwQkFJQUFnWnpkSEpwYm1jTUNBQUdjM1JoZEhWekEybHVkQVFDQUFJR2MzUnlhVzVuREFjQUJXZHliM1Z3Qm5OMGNtbHVad3dKQUFka1pXWmhkV3gwQm5OMGNtbHVad3dFQUFKcFpBTnBiblFFQlFEOUJadkVCbk4wY21sdVp3d0tBQWgxYzJWeWJtRnRaUVp6ZEhKcGJtY01DZ0FJUXpJd01ESTBNVGs9fBv-B8UeNryH-3ynwHO8WPmXIkqsYfs-hecaeF2wQT_8",
    },
    {
        "name": "FreeAPI (DGBMC)",
        "url": "https://freeapi.dgbmc.top/api/user/checkin",
        "userId": "20",
        "cookie": "session=MTc4NDE2NDU3NnxEWDhFQVFMX2dBQUJFQUVRQUFEX2tmLUFBQVVHYzNSeWFXNW5EQVFBQW1sa0EybHVkQVFDQUNnR2MzUnlhVzVuREFvQUNIVnpaWEp1WVcxbEJuTjBjbWx1Wnd3S0FBaEJNakF3TWpReE9RWnpkSEpwYm1jTUJnQUVjbTlzWlFOcGJuUUVBZ0FDQm5OMGNtbHVad3dJQUFaemRHRjBkWE1EYVc1MEJBSUFBZ1p6ZEhKcGJtY01Cd0FGWjNKdmRYQUdjM1J5YVc1bkRBa0FCMlJsWm1GMWJIUT18vGUZsyewNrplzK7ORDef6jgNFnQw_KPYhjA_CYjKD7k=",
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
                    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "cache-control": "no-store",
                    "content-length": "0",
                    "cookie": acct["cookie"],
                    "new-api-user": acct["userId"],
                    "origin": f"{parsed.scheme}://{parsed.hostname}",
                    "referer": f"{parsed.scheme}://{parsed.hostname}/profile",
                    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0",
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
