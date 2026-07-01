"""签到模块 — 调用 checkin.js 执行多站点批量签到"""
import asyncio
import logging
from pathlib import Path

logger = logging.getLogger("checkin")

_CHECKIN_JS = str(Path(__file__).resolve().parent.parent / "checkin.js")


async def checkin_all() -> str:
    """执行 checkin.js 并返回结果文本"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "node", _CHECKIN_JS,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        output = stdout.decode("utf-8", errors="replace").strip()
        if stderr:
            err = stderr.decode("utf-8", errors="replace").strip()
            if err:
                logger.warning(f"checkin.js stderr: {err}")
        return output if output else "签到完成（无输出）"
    except asyncio.TimeoutError:
        return "签到超时（60s）"
    except FileNotFoundError:
        return "未找到 node 或 checkin.js"
    except Exception as e:
        logger.exception(f"签到执行失败: {e}")
        return f"签到失败: {e}"
