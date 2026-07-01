# -*- coding: utf-8 -*-
"""
NoneBot2 入口文件
运行方式: python bot.py
"""
import sys
import os
from pathlib import Path

# 设置工作目录
os.chdir(Path(__file__).parent)

# 加载 .env 文件
from dotenv import load_dotenv
load_dotenv(".env.prod", override=True)

import nonebot
from nonebot.adapters.onebot import V12Adapter

if __name__ == "__main__":
    nonebot.init(
        driver="~fastapi",
        host="127.0.0.1",
        port=18765
    )

    # 注册 OneBot V12 适配器
    nonebot.get_driver().register_adapter(V12Adapter)
    nonebot.load_from_toml("pyproject.toml")

    # 配置 V12 适配器（如果需要）
    # V12Adapter 需要配置 access_token 才能接受连接
    # V12Adapter(self_id="wechat_bot", access_token=os.getenv("NB2_ACCESS_TOKEN", ""))

    # 传递超时配置给 uvicorn
    nonebot.run(timeout_keep_alive=120)
