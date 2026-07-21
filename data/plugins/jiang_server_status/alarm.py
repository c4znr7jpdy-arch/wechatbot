"""Debounced server alarm state transitions and message formatting."""

from __future__ import annotations

import time
from dataclasses import dataclass

from .monitor import ServerStatus, format_uptime


@dataclass(slots=True)
class AlarmState:
    failures: int = 0
    recoveries: int = 0
    down: bool = False
    down_since: float | None = None
    last_alert_at: float | None = None


@dataclass(slots=True)
class AlarmEvent:
    kind: str
    status: ServerStatus
    consecutive_count: int
    down_seconds: float = 0.0


class AlarmEvaluator:
    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        recovery_threshold: int = 2,
        repeat_seconds: float = 3600,
    ) -> None:
        self.failure_threshold = max(1, int(failure_threshold))
        self.recovery_threshold = max(1, int(recovery_threshold))
        self.repeat_seconds = max(0.0, float(repeat_seconds))
        self.states: dict[str, AlarmState] = {}

    def update(self, status: ServerStatus, *, now: float | None = None) -> AlarmEvent | None:
        current = time.monotonic() if now is None else float(now)
        state = self.states.setdefault(status.server_id, AlarmState())

        if status.online:
            state.failures = 0
            if not state.down:
                state.recoveries = 0
                return None
            state.recoveries += 1
            if state.recoveries < self.recovery_threshold:
                return None
            duration = max(0.0, current - (state.down_since or current))
            event = AlarmEvent("recovered", status, state.recoveries, duration)
            self.states[status.server_id] = AlarmState()
            return event

        state.recoveries = 0
        if state.down:
            if (
                self.repeat_seconds > 0
                and state.last_alert_at is not None
                and current - state.last_alert_at >= self.repeat_seconds
            ):
                state.last_alert_at = current
                duration = max(0.0, current - (state.down_since or current))
                return AlarmEvent("still_down", status, state.failures, duration)
            return None

        state.failures += 1
        if state.failures < self.failure_threshold:
            return None
        state.down = True
        state.down_since = current
        state.last_alert_at = current
        return AlarmEvent("down", status, state.failures)

    def retain(self, server_ids: set[str]) -> None:
        self.states = {
            server_id: state
            for server_id, state in self.states.items()
            if server_id in server_ids
        }


def format_alarm_message(event: AlarmEvent) -> str:
    status = event.status
    checked_at = status.checked_at.strftime("%Y-%m-%d %H:%M:%S")
    if event.kind == "recovered":
        latency = (
            f"{status.latency_ms:.0f} ms" if status.latency_ms is not None else "正常"
        )
        return "\n".join(
            [
                "服务器恢复通知",
                f"服务器：{status.name}",
                "状态：已恢复在线",
                f"离线时长：{format_uptime(event.down_seconds)}",
                f"当前响应：{latency}",
                f"时间：{checked_at}",
            ]
        )

    title = "服务器离线告警" if event.kind == "down" else "服务器持续离线提醒"
    lines = [
        title,
        f"服务器：{status.name}",
        "状态：无法连接",
        f"检查结果：{status.detail or '探针不可达'}",
    ]
    if event.kind == "down":
        lines.append(f"判定条件：连续 {event.consecutive_count} 次失败")
    else:
        lines.append(f"持续时间：{format_uptime(event.down_seconds)}")
    lines.append(f"时间：{checked_at}")
    return "\n".join(lines)

