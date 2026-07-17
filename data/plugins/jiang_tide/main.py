"""AstrBot 青岛潮汐查询插件。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, filter

from .renderer import render_beach_card, render_hourly_card
from .tide import (
    TZ,
    TideClient,
    format_beach_forecast,
    format_hourly_forecast,
    parse_command,
)


logger = logging.getLogger("jiang_tide")


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        self.context = context
        self.client = TideClient()

    @staticmethod
    def _image_or_text(event: AstrMessageEvent, render, text: str):
        """图片渲染异常时降级发送原始文本。"""
        try:
            return event.image_result(render())
        except Exception:
            logger.exception("潮汐卡片渲染失败，已降级为文本")
            return event.plain_result(text)

    @filter.command(
        "潮汐",
        alias={
            "今天潮汐",
            "今日潮汐",
            "明天潮汐",
            "明日潮汐",
            "后天潮汐",
            "青岛潮汐",
            "青岛涨潮退潮时间",
            "涨潮退潮",
            "涨退潮",
            "涨潮",
            "退潮",
        },
    )
    async def tide(self, event: AstrMessageEvent):
        """查询青岛沿岸或指定海水浴场的高潮、低潮时间。"""
        try:
            command = parse_command(
                event.get_message_str(), allow_wake_normalized=True
            )
        except ValueError as exc:
            event.stop_event()
            yield event.plain_result(str(exc))
            return
        if command is None:
            return

        event.stop_event()
        now = datetime.now(TZ)
        try:
            if command.location:
                forecasts, stale = await self.client.fetch_beaches()
                forecast = next(
                    (item for item in forecasts if item.location == command.location), None
                )
                requested_date = (
                    now.date() + timedelta(days=command.date_offset)
                    if command.date_offset is not None
                    else None
                )
                if forecast and (requested_date is None or requested_date == forecast.forecast_date):
                    text = format_beach_forecast(forecast, stale=stale)
                    yield self._image_or_text(
                        event,
                        lambda: render_beach_card(forecast, stale=stale),
                        text,
                    )
                    return

                points, hourly_stale = await self.client.fetch_hourly()
                target = requested_date or now.date()
                note = (
                    f"\n提示：{command.location.replace('（青岛）', '')}接口目前只发布"
                    f" {forecast.forecast_date:%m月%d日} 的精确潮时，本次显示青岛沿岸预报。"
                    if forecast
                    else "\n提示：该浴场暂无单独预报，本次显示青岛沿岸预报。"
                )
                message = format_hourly_forecast(points, target, now, stale=hourly_stale) + note
                card_note = note.removeprefix("\n提示：")
                yield self._image_or_text(
                    event,
                    lambda: render_hourly_card(
                        points,
                        target,
                        now,
                        stale=hourly_stale,
                        extra_note=card_note,
                    ),
                    message,
                )
                return

            target = now.date() + timedelta(days=command.date_offset or 0)
            points, stale = await self.client.fetch_hourly()
            text = format_hourly_forecast(points, target, now, stale=stale)
            yield self._image_or_text(
                event,
                lambda: render_hourly_card(points, target, now, stale=stale),
                text,
            )
        except Exception as exc:
            logger.exception("获取青岛潮汐失败: %s", exc)
            yield event.plain_result("青岛官方潮汐数据暂时获取失败，稍后再试")
