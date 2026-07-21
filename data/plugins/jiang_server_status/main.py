"""AstrBot server status command plugin."""

from __future__ import annotations

import asyncio
import contextlib

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.core import AstrBotConfig
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain

from .alarm import AlarmEvaluator, format_alarm_message
from .monitor import (
    collect_many,
    extract_server_query,
    format_status_text,
    parse_server_configs,
    select_servers,
)

ADMIN_WXID = "fengchenhao002"


class Main(star.Star):
    def __init__(self, context: star.Context, config: AstrBotConfig) -> None:
        self.context = context
        self.config = config
        self._alarm_task: asyncio.Task | None = None
        self._stopping = False
        self._alarm_evaluator = self._build_alarm_evaluator()
        self._ensure_alarm_task()

    def _build_alarm_evaluator(self) -> AlarmEvaluator:
        try:
            failure_threshold = int(self.config.get("alarm_failure_threshold", 3))
        except (TypeError, ValueError):
            failure_threshold = 3
        try:
            recovery_threshold = int(self.config.get("alarm_recovery_threshold", 2))
        except (TypeError, ValueError):
            recovery_threshold = 2
        try:
            repeat_minutes = float(self.config.get("alarm_repeat_minutes", 60))
        except (TypeError, ValueError):
            repeat_minutes = 60
        return AlarmEvaluator(
            failure_threshold=max(1, min(failure_threshold, 10)),
            recovery_threshold=max(1, min(recovery_threshold, 10)),
            repeat_seconds=max(0, min(repeat_minutes, 1440)) * 60,
        )

    def _alarm_enabled(self) -> bool:
        return bool(self.config.get("alarm_enabled", True))

    def _alarm_targets(self) -> list[str]:
        raw_targets = self.config.get("alarm_target_sessions", [])
        if not isinstance(raw_targets, list):
            return []
        targets: list[str] = []
        for raw in raw_targets:
            target = str(raw).strip()
            if target.count(":") >= 2 and target not in targets:
                targets.append(target)
        return targets

    def _alarm_interval_seconds(self) -> int:
        try:
            value = int(self.config.get("alarm_interval_seconds", 30))
        except (TypeError, ValueError):
            value = 30
        return max(10, min(value, 3600))

    def _ensure_alarm_task(self) -> None:
        if self._stopping or not self._alarm_enabled():
            return
        if self._alarm_task and not self._alarm_task.done():
            return
        try:
            self._alarm_task = asyncio.get_running_loop().create_task(
                self._alarm_loop(),
                name="jiang-server-status-alarm",
            )
            logger.info(
                "服务器报警任务已启动: interval=%ss targets=%s",
                self._alarm_interval_seconds(),
                len(self._alarm_targets()),
            )
        except RuntimeError:
            self._alarm_task = None

    async def initialize(self) -> None:
        logger.info("初始化服务器报警: enabled=%s", self._alarm_enabled())
        self._ensure_alarm_task()
        logger.info(
            "服务器报警初始化完成: running=%s",
            bool(self._alarm_task and not self._alarm_task.done()),
        )

    @filter.on_platform_loaded()
    async def on_platform_loaded(self):
        self._ensure_alarm_task()

    @filter.on_plugin_loaded()
    async def on_plugin_loaded(self, metadata):
        del metadata
        self._ensure_alarm_task()

    async def terminate(self) -> None:
        self._stopping = True
        if self._alarm_task and not self._alarm_task.done():
            self._alarm_task.cancel()
        if self._alarm_task:
            with contextlib.suppress(asyncio.CancelledError):
                await self._alarm_task
        self._alarm_task = None

    async def _send_alarm_text(self, text: str) -> int:
        sent = 0
        for target in self._alarm_targets():
            try:
                if await self.context.send_message(target, MessageChain([Plain(text)])):
                    sent += 1
                else:
                    logger.warning("服务器告警目标不可用: %s", target)
            except Exception as exc:
                logger.warning("发送服务器告警失败: target=%s error=%s", target, exc)
        return sent

    async def _check_alarms_once(self) -> int:
        configs = [
            item
            for item in parse_server_configs(self.config.get("servers", []))
            if item.enabled
        ]
        self._alarm_evaluator.retain({item.server_id for item in configs})
        if not configs:
            return 0
        statuses = await collect_many(configs, sample_seconds=0.1)
        sent = 0
        for status in statuses:
            alarm_event = self._alarm_evaluator.update(status)
            if alarm_event:
                delivered = await self._send_alarm_text(format_alarm_message(alarm_event))
                sent += delivered
                log_method = logger.info if alarm_event.kind == "recovered" else logger.warning
                log_method(
                    "服务器报警状态变化: server=%s kind=%s delivered=%s",
                    status.name,
                    alarm_event.kind,
                    delivered,
                )
        return sent

    async def _alarm_loop(self) -> None:
        await asyncio.sleep(5)
        while not self._stopping:
            try:
                await self._check_alarms_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("服务器后台告警检查失败")
            await asyncio.sleep(self._alarm_interval_seconds())

    @filter.command(
        "服务器",
        alias={"服务器状态", "主机状态", "主机"},
    )
    async def server_status(self, event: AstrMessageEvent):
        """查看全部服务器或按 ID、名称查看指定服务器。"""
        event.stop_event()
        configs = parse_server_configs(self.config.get("servers", []))
        enabled = [item for item in configs if item.enabled]
        if not enabled:
            yield event.plain_result("还没有启用服务器，请先在插件配置里添加一台")
            return

        query = extract_server_query(event.get_message_str())
        if query.casefold() in {"列表", "list"}:
            lines = ["已配置的服务器："]
            lines.extend(f"- {item.name}（{item.server_id}）" for item in enabled)
            lines.append("\n查看单台：/服务器 服务器ID")
            yield event.plain_result("\n".join(lines))
            return

        selected = select_servers(configs, query)
        if not selected:
            available = "、".join(f"{item.name}({item.server_id})" for item in enabled)
            yield event.plain_result(f"没找到这台服务器。当前可用：{available}")
            return

        try:
            limit = max(1, min(int(self.config.get("max_servers_per_card", 6)), 10))
        except (TypeError, ValueError):
            limit = 6
        selected = selected[:limit]
        try:
            sample_seconds = float(self.config.get("sample_seconds", 0.35))
        except (TypeError, ValueError):
            sample_seconds = 0.35
        title = str(self.config.get("card_title", "我的服务器") or "我的服务器")[:40]

        try:
            statuses = await collect_many(selected, sample_seconds=sample_seconds)
        except Exception as exc:
            logger.exception("采集服务器状态失败: %s", exc)
            yield event.plain_result("服务器状态采集失败，稍后再试")
            return

        yield event.plain_result(format_status_text(statuses, title))

    @filter.command("服务器告警状态")
    async def alarm_status(self, event: AstrMessageEvent):
        """查看服务器后台告警任务配置。"""
        event.stop_event()
        if str(event.get_sender_id() or "") != ADMIN_WXID:
            yield event.plain_result("这个命令只允许管理员使用")
            return
        servers = [
            item.name
            for item in parse_server_configs(self.config.get("servers", []))
            if item.enabled
        ]
        running = bool(self._alarm_task and not self._alarm_task.done())
        lines = [
            "服务器告警状态",
            f"监控：{'、'.join(servers) or '无'}",
            f"后台任务：{'运行中' if running else '未运行'}",
            f"检查间隔：{self._alarm_interval_seconds()} 秒",
            f"离线判定：连续 {self._alarm_evaluator.failure_threshold} 次失败",
            f"恢复判定：连续 {self._alarm_evaluator.recovery_threshold} 次成功",
            f"推送目标：{len(self._alarm_targets())} 个",
        ]
        yield event.plain_result("\n".join(lines))

    @filter.command("服务器告警测试")
    async def alarm_test(self, event: AstrMessageEvent):
        """向配置的管理员会话发送一条测试告警。"""
        event.stop_event()
        if str(event.get_sender_id() or "") != ADMIN_WXID:
            yield event.plain_result("这个命令只允许管理员使用")
            return
        server_names = [
            item.name
            for item in parse_server_configs(self.config.get("servers", []))
            if item.enabled
        ]
        text = "\n".join(
            [
                "服务器告警测试",
                f"服务器：{'、'.join(server_names) or '未配置'}",
                "状态：推送通道正常",
                "说明：这是一条手动测试消息，不代表服务器离线",
            ]
        )
        sent = await self._send_alarm_text(text)
        if sent:
            yield event.plain_result(f"测试消息已发送到 {sent} 个告警会话")
        else:
            yield event.plain_result("测试消息发送失败，请检查告警会话配置")
