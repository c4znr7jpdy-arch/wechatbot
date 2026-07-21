"""Configuration parsing and server metric collection."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import platform
import re
import socket
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

import httpx
import psutil


COMMAND_NAMES = ("服务器状态", "主机状态", "服务器", "主机")


@dataclass(slots=True)
class ServiceCheck:
    name: str
    host: str
    port: int


@dataclass(slots=True)
class ServerConfig:
    kind: str
    server_id: str
    name: str
    location: str
    enabled: bool = True
    disk_paths: list[str] = field(default_factory=list)
    services: list[ServiceCheck] = field(default_factory=list)
    api_url: str = ""
    api_token: str = ""
    verify_ssl: bool = True
    host: str = ""
    port: int = 0
    ssh_user: str = "root"
    identity_file: str = ""
    verify_host_key: bool = True
    timeout_seconds: float = 5.0


@dataclass(slots=True)
class ServiceStatus:
    name: str
    online: bool
    latency_ms: float | None = None


@dataclass(slots=True)
class ServerStatus:
    server_id: str
    name: str
    location: str
    kind: str
    online: bool
    degraded: bool = False
    detail: str = ""
    latency_ms: float | None = None
    hostname: str = ""
    os_name: str = ""
    cpu_percent: float | None = None
    cpu_count: int | None = None
    memory_percent: float | None = None
    memory_used_bytes: float | None = None
    memory_total_bytes: float | None = None
    disk_percent: float | None = None
    disk_used_bytes: float | None = None
    disk_total_bytes: float | None = None
    upload_bytes_per_second: float | None = None
    download_bytes_per_second: float | None = None
    uptime_seconds: float | None = None
    services: list[ServiceStatus] = field(default_factory=list)
    checked_at: datetime = field(default_factory=lambda: datetime.now().astimezone())


def _clamp(value: Any, minimum: float, maximum: float, fallback: float) -> float:
    try:
        return min(max(float(value), minimum), maximum)
    except (TypeError, ValueError):
        return fallback


def _bool_value(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "1", "yes", "on", "up", "online", "ok"}:
            return True
        if normalized in {"false", "0", "no", "off", "down", "offline", "error"}:
            return False
    return default


def _split_paths(value: Any) -> list[str]:
    if isinstance(value, list):
        values = value
    elif isinstance(value, str):
        values = re.split(r"[\r\n,;]+", value)
    else:
        values = []
    return [str(item).strip() for item in values if str(item).strip()]


def parse_service_checks(value: Any) -> list[ServiceCheck]:
    if isinstance(value, list):
        lines: Iterable[Any] = value
    else:
        lines = str(value or "").splitlines()
    checks: list[ServiceCheck] = []
    for raw_line in lines:
        line = str(raw_line).strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 3:
            continue
        try:
            port = int(parts[2])
        except ValueError:
            continue
        if parts[0] and parts[1] and 1 <= port <= 65535:
            checks.append(ServiceCheck(parts[0][:32], parts[1], port))
    return checks[:12]


def parse_server_configs(raw_servers: Any) -> list[ServerConfig]:
    if not isinstance(raw_servers, list):
        return []
    configs: list[ServerConfig] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_servers):
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("__template_key") or raw.get("template") or "local").lower()
        if kind not in {"local", "http", "ssh", "tcp"}:
            continue
        server_id = str(raw.get("id") or f"server-{index + 1}").strip()
        server_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", server_id).strip("-") or f"server-{index + 1}"
        base_id = server_id
        suffix = 2
        while server_id.casefold() in seen_ids:
            server_id = f"{base_id}-{suffix}"
            suffix += 1
        seen_ids.add(server_id.casefold())

        name = str(raw.get("name") or server_id).strip()[:48]
        location = str(raw.get("location") or "未设置位置").strip()[:64]
        config = ServerConfig(
            kind=kind,
            server_id=server_id,
            name=name,
            location=location,
            enabled=_bool_value(raw.get("enabled"), True),
            timeout_seconds=_clamp(raw.get("timeout_seconds", 5), 0.2, 30, 5),
        )
        if kind == "local":
            config.disk_paths = _split_paths(raw.get("disk_paths"))
            config.services = parse_service_checks(raw.get("service_checks"))
        elif kind == "http":
            config.api_url = str(raw.get("api_url") or "").strip()
            config.api_token = str(raw.get("api_token") or "").strip()
            config.verify_ssl = _bool_value(raw.get("verify_ssl"), True)
        elif kind == "ssh":
            config.host = str(raw.get("host") or "").strip()
            config.ssh_user = str(raw.get("username") or "root").strip() or "root"
            config.identity_file = str(raw.get("identity_file") or "").strip()
            config.verify_host_key = _bool_value(raw.get("verify_host_key"), True)
            try:
                config.port = int(raw.get("port") or 22)
            except (TypeError, ValueError):
                config.port = 22
        else:
            config.host = str(raw.get("host") or "").strip()
            try:
                config.port = int(raw.get("port") or 0)
            except (TypeError, ValueError):
                config.port = 0
        configs.append(config)
    return configs


def extract_server_query(message: str) -> str:
    text = (message or "").strip()
    text = re.sub(r"^\[系统身份提示：.*?\]\s*", "", text, flags=re.S)
    text = text.lstrip("/\\").strip()
    for command in sorted(COMMAND_NAMES, key=len, reverse=True):
        if text == command:
            return ""
        if text.startswith(command) and text[len(command) : len(command) + 1].isspace():
            return text[len(command) :].strip()
    return ""


def select_servers(configs: list[ServerConfig], query: str) -> list[ServerConfig]:
    enabled = [item for item in configs if item.enabled]
    normalized = query.strip().casefold()
    if not normalized:
        return enabled
    exact = [
        item
        for item in enabled
        if normalized in {item.server_id.casefold(), item.name.casefold()}
    ]
    if exact:
        return exact
    partial = [
        item
        for item in enabled
        if normalized in item.server_id.casefold() or normalized in item.name.casefold()
    ]
    return partial if len(partial) == 1 else []


def _tcp_check_sync(check: ServiceCheck, timeout: float) -> ServiceStatus:
    started = time.perf_counter()
    try:
        with socket.create_connection((check.host, check.port), timeout=timeout):
            latency = (time.perf_counter() - started) * 1000
            return ServiceStatus(check.name, True, latency)
    except OSError:
        return ServiceStatus(check.name, False)


def _disk_totals(paths: list[str]) -> tuple[float | None, float | None, float | None]:
    candidates = paths or [os.environ.get("SystemDrive", "C:") + os.sep]
    seen_devices: set[str] = set()
    total = 0.0
    used = 0.0
    for path in candidates:
        try:
            usage = psutil.disk_usage(path)
            drive = os.path.splitdrive(os.path.abspath(path))[0].casefold() or os.path.abspath(path)
            if drive in seen_devices:
                continue
            seen_devices.add(drive)
            total += float(usage.total)
            used += float(usage.used)
        except (FileNotFoundError, OSError, PermissionError):
            continue
    if total <= 0:
        return None, None, None
    return used, total, used / total * 100


def _collect_local_sync(config: ServerConfig, sample_seconds: float) -> ServerStatus:
    interval = _clamp(sample_seconds, 0.05, 2.0, 0.35)
    net_before = psutil.net_io_counters()
    started = time.perf_counter()
    cpu_percent = float(psutil.cpu_percent(interval=interval))
    elapsed = max(time.perf_counter() - started, 0.001)
    net_after = psutil.net_io_counters()
    memory = psutil.virtual_memory()
    disk_used, disk_total, disk_percent = _disk_totals(config.disk_paths)

    service_timeout = min(config.timeout_seconds, 1.5)
    services = [_tcp_check_sync(check, service_timeout) for check in config.services]
    degraded = any(not service.online for service in services)
    return ServerStatus(
        server_id=config.server_id,
        name=config.name,
        location=config.location,
        kind="local",
        online=True,
        degraded=degraded,
        detail="部分服务不可达" if degraded else "系统运行正常",
        hostname=socket.gethostname(),
        os_name=f"{platform.system()} {platform.release()}",
        cpu_percent=cpu_percent,
        cpu_count=psutil.cpu_count(logical=True),
        memory_percent=float(memory.percent),
        memory_used_bytes=float(memory.used),
        memory_total_bytes=float(memory.total),
        disk_percent=disk_percent,
        disk_used_bytes=disk_used,
        disk_total_bytes=disk_total,
        upload_bytes_per_second=max(0.0, (net_after.bytes_sent - net_before.bytes_sent) / elapsed),
        download_bytes_per_second=max(0.0, (net_after.bytes_recv - net_before.bytes_recv) / elapsed),
        uptime_seconds=max(0.0, time.time() - psutil.boot_time()),
        services=services,
    )


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _nested(data: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        current: Any = data
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                current = None
                break
            current = current[part]
        if current is not None:
            return current
    return None


def _bytes_value(data: dict[str, Any], prefix: str, field_name: str) -> float | None:
    direct = _number(
        _nested(
            data,
            f"{prefix}_{field_name}_bytes",
            f"{prefix}.{field_name}_bytes",
            f"{prefix}.{field_name}",
        )
    )
    if direct is not None:
        return direct
    gib = _number(
        _nested(data, f"{prefix}_{field_name}_gb", f"{prefix}.{field_name}_gb")
    )
    return gib * 1024**3 if gib is not None else None


def status_from_http_payload(
    config: ServerConfig, payload: dict[str, Any], latency_ms: float
) -> ServerStatus:
    services: list[ServiceStatus] = []
    raw_services = payload.get("services", [])
    if isinstance(raw_services, list):
        for raw in raw_services[:12]:
            if not isinstance(raw, dict):
                continue
            services.append(
                ServiceStatus(
                    name=str(raw.get("name") or "服务")[:32],
                    online=_bool_value(raw.get("online", raw.get("status"))),
                    latency_ms=_number(raw.get("latency_ms")),
                )
            )
    explicit_online = payload.get("online")
    if explicit_online is None:
        explicit_online = str(payload.get("status", "online")).lower() not in {
            "down",
            "offline",
            "error",
        }
    online = _bool_value(explicit_online, True)
    degraded = online and (
        _bool_value(payload.get("degraded"), False)
        or any(not item.online for item in services)
    )
    memory_used = _bytes_value(payload, "memory", "used")
    memory_total = _bytes_value(payload, "memory", "total")
    disk_used = _bytes_value(payload, "disk", "used")
    disk_total = _bytes_value(payload, "disk", "total")
    memory_percent = _number(_nested(payload, "memory_percent", "memory.percent"))
    disk_percent = _number(_nested(payload, "disk_percent", "disk.percent"))
    if memory_percent is None and memory_used is not None and memory_total:
        memory_percent = memory_used / memory_total * 100
    if disk_percent is None and disk_used is not None and disk_total:
        disk_percent = disk_used / disk_total * 100
    return ServerStatus(
        server_id=config.server_id,
        name=config.name,
        location=config.location,
        kind="http",
        online=online,
        degraded=degraded,
        detail=str(payload.get("message") or ("需要关注" if degraded else "指标接口正常"))[:80],
        latency_ms=latency_ms,
        hostname=str(payload.get("hostname") or "")[:64],
        os_name=str(payload.get("os") or payload.get("os_name") or "")[:64],
        cpu_percent=_number(_nested(payload, "cpu_percent", "cpu.percent")),
        cpu_count=int(_number(_nested(payload, "cpu_count", "cpu.count")) or 0) or None,
        memory_percent=memory_percent,
        memory_used_bytes=memory_used,
        memory_total_bytes=memory_total,
        disk_percent=disk_percent,
        disk_used_bytes=disk_used,
        disk_total_bytes=disk_total,
        upload_bytes_per_second=_number(
            _nested(payload, "upload_bytes_per_second", "network.upload_bytes_per_second")
        ),
        download_bytes_per_second=_number(
            _nested(payload, "download_bytes_per_second", "network.download_bytes_per_second")
        ),
        uptime_seconds=_number(_nested(payload, "uptime_seconds", "uptime")),
        services=services,
    )


def _offline_status(config: ServerConfig, detail: str) -> ServerStatus:
    return ServerStatus(
        server_id=config.server_id,
        name=config.name,
        location=config.location,
        kind=config.kind,
        online=False,
        detail=detail,
    )


async def _collect_http(config: ServerConfig) -> ServerStatus:
    if not config.api_url.startswith(("http://", "https://")):
        return _offline_status(config, "指标接口地址无效")
    headers = {"Accept": "application/json"}
    if config.api_token:
        headers["Authorization"] = f"Bearer {config.api_token}"
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            timeout=config.timeout_seconds,
            verify=config.verify_ssl,
            follow_redirects=True,
        ) as client:
            response = await client.get(config.api_url, headers=headers)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            return _offline_status(config, "指标接口返回格式不正确")
        latency = (time.perf_counter() - started) * 1000
        return status_from_http_payload(config, payload, latency)
    except httpx.TimeoutException:
        return _offline_status(config, "连接指标接口超时")
    except httpx.HTTPStatusError as exc:
        return _offline_status(config, f"指标接口返回 HTTP {exc.response.status_code}")
    except (httpx.HTTPError, ValueError):
        return _offline_status(config, "无法读取指标接口")


async def _collect_tcp(config: ServerConfig) -> ServerStatus:
    if not config.host or not 1 <= config.port <= 65535:
        return _offline_status(config, "TCP 探针配置无效")
    started = time.perf_counter()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(config.host, config.port),
            timeout=config.timeout_seconds,
        )
        del reader
        latency = (time.perf_counter() - started) * 1000
        writer.close()
        await writer.wait_closed()
        return ServerStatus(
            server_id=config.server_id,
            name=config.name,
            location=config.location,
            kind="tcp",
            online=True,
            detail=f"TCP {config.port} 服务可访问",
            latency_ms=latency,
            services=[ServiceStatus(f"TCP {config.port}", True, latency)],
        )
    except (TimeoutError, OSError):
        return _offline_status(config, f"TCP {config.port} 服务不可访问")


_SSH_METRICS_SCRIPT = r'''import json
import os
import shutil
import socket
import subprocess
import time

def cpu_snapshot():
    with open("/proc/stat", encoding="utf-8") as handle:
        values = [int(item) for item in handle.readline().split()[1:9]]
    return sum(values), values[3] + values[4]

def network_snapshot():
    received = 0
    sent = 0
    with open("/proc/net/dev", encoding="utf-8") as handle:
        for line in handle:
            if ":" not in line:
                continue
            name, raw = line.split(":", 1)
            if name.strip() == "lo":
                continue
            fields = raw.split()
            received += int(fields[0])
            sent += int(fields[8])
    return received, sent

def service_status(name):
    result = subprocess.run(
        ["systemctl", "is-active", "--quiet", name],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0

cpu_before = cpu_snapshot()
network_before = network_snapshot()
started = time.monotonic()
time.sleep(0.25)
cpu_after = cpu_snapshot()
network_after = network_snapshot()
elapsed = max(time.monotonic() - started, 0.001)
cpu_delta = cpu_after[0] - cpu_before[0]
idle_delta = cpu_after[1] - cpu_before[1]
cpu_percent = 0.0 if cpu_delta <= 0 else (1.0 - idle_delta / cpu_delta) * 100.0

memory = {}
with open("/proc/meminfo", encoding="utf-8") as handle:
    for line in handle:
        key, value = line.split(":", 1)
        memory[key] = int(value.strip().split()[0]) * 1024
memory_total = memory.get("MemTotal", 0)
memory_available = memory.get("MemAvailable", memory.get("MemFree", 0))
memory_used = max(0, memory_total - memory_available)
disk = shutil.disk_usage("/")

os_name = "Linux"
try:
    with open("/etc/os-release", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("PRETTY_NAME="):
                os_name = line.split("=", 1)[1].strip().strip('"')
                break
except OSError:
    pass

payload = {
    "status": "online",
    "hostname": socket.gethostname(),
    "os": os_name,
    "cpu_percent": max(0.0, min(cpu_percent, 100.0)),
    "cpu_count": os.cpu_count(),
    "memory_used_bytes": memory_used,
    "memory_total_bytes": memory_total,
    "disk_used_bytes": disk.used,
    "disk_total_bytes": disk.total,
    "upload_bytes_per_second": max(0.0, (network_after[1] - network_before[1]) / elapsed),
    "download_bytes_per_second": max(0.0, (network_after[0] - network_before[0]) / elapsed),
    "uptime_seconds": float(open("/proc/uptime", encoding="utf-8").read().split()[0]),
    "services": [
        {"name": "Nginx", "online": service_status("nginx")},
        {"name": "SSH", "online": service_status("ssh")},
    ],
}
print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
'''


def _ssh_remote_command() -> str:
    encoded = base64.b64encode(_SSH_METRICS_SCRIPT.encode("utf-8")).decode("ascii")
    return f'python3 -c "import base64;exec(base64.b64decode(\'{encoded}\'))"'


async def _collect_ssh(config: ServerConfig) -> ServerStatus:
    if not config.host or not config.ssh_user or not 1 <= config.port <= 65535:
        return _offline_status(config, "SSH 指标配置无效")

    timeout = _clamp(config.timeout_seconds, 1.0, 30.0, 8.0)
    args = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={max(1, int(timeout))}",
        "-o",
        f"StrictHostKeyChecking={'yes' if config.verify_host_key else 'accept-new'}",
        "-p",
        str(config.port),
    ]
    if config.identity_file:
        args.extend(["-i", config.identity_file])
    args.extend(
        [
            f"{config.ssh_user}@{config.host}",
            _ssh_remote_command(),
        ]
    )

    process_kwargs: dict[str, Any] = {
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
    }
    if os.name == "nt":
        process_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    started = time.perf_counter()
    try:
        process = await asyncio.create_subprocess_exec(*args, **process_kwargs)
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout + 3,
        )
    except FileNotFoundError:
        return _offline_status(config, "本机未安装 OpenSSH 客户端")
    except TimeoutError:
        if "process" in locals() and process.returncode is None:
            process.kill()
            await process.wait()
        return _offline_status(config, "SSH 指标采集超时")
    except OSError:
        return _offline_status(config, "无法启动 SSH 指标采集")

    if process.returncode != 0:
        error_text = stderr.decode("utf-8", errors="ignore").casefold()
        if "permission denied" in error_text:
            detail = "SSH 密钥认证失败"
        elif "host key verification failed" in error_text:
            detail = "SSH 主机指纹校验失败"
        else:
            detail = "SSH 指标采集失败"
        return _offline_status(config, detail)

    try:
        payload = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _offline_status(config, "SSH 指标返回格式不正确")
    if not isinstance(payload, dict):
        return _offline_status(config, "SSH 指标返回格式不正确")

    latency_ms = (time.perf_counter() - started) * 1000
    status = status_from_http_payload(config, payload, latency_ms)
    status.kind = "ssh"
    status.detail = "SSH 指标采集正常" if not status.degraded else "部分系统服务异常"
    return status


async def collect_server_status(
    config: ServerConfig, sample_seconds: float = 0.35
) -> ServerStatus:
    if config.kind == "local":
        return await asyncio.to_thread(_collect_local_sync, config, sample_seconds)
    if config.kind == "http":
        return await _collect_http(config)
    if config.kind == "ssh":
        return await _collect_ssh(config)
    return await _collect_tcp(config)


async def collect_many(
    configs: list[ServerConfig], sample_seconds: float = 0.35
) -> list[ServerStatus]:
    if not configs:
        return []
    return list(
        await asyncio.gather(
            *(collect_server_status(config, sample_seconds) for config in configs)
        )
    )


def format_bytes(value: float | None) -> str:
    if value is None:
        return "--"
    number = max(0.0, float(value))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if number < 1024 or unit == "TB":
            return f"{number:.0f} {unit}" if unit in {"B", "KB"} else f"{number:.1f} {unit}"
        number /= 1024
    return "--"


def format_uptime(seconds: float | None) -> str:
    if seconds is None:
        return "--"
    total_minutes = max(0, int(seconds // 60))
    days, remainder = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(remainder, 60)
    if days:
        return f"{days} 天 {hours} 小时"
    if hours:
        return f"{hours} 小时 {minutes} 分"
    return f"{minutes} 分钟"


def format_status_text(statuses: list[ServerStatus], title: str) -> str:
    divider = "===================="
    lines = [title, divider]
    for index, status in enumerate(statuses):
        if index:
            lines.extend(["", divider])
        state = "离线" if not status.online else ("需关注" if status.degraded else "运行中")
        kind_name = {
            "local": "本机采集",
            "http": "HTTP 指标",
            "ssh": "SSH 指标",
            "tcp": "TCP 探针",
        }.get(status.kind, status.kind)
        lines.append(f"{status.name}  [{state}]")
        lines.append(f"位置：{status.location}")
        if status.os_name:
            lines.append(f"系统：{status.os_name}")

        if not status.online:
            lines.extend(
                [
                    "",
                    f"原因：{status.detail or '当前无法连接服务器'}",
                    f"采集：{kind_name}",
                    f"检查：{status.checked_at:%Y-%m-%d %H:%M:%S}",
                ]
            )
            continue

        lines.extend(["", "资源"])
        if status.cpu_percent is not None:
            cores = f"，{status.cpu_count} 个逻辑核心" if status.cpu_count else ""
            lines.append(f"CPU：{status.cpu_percent:.0f}%{cores}")
        if status.memory_percent is not None:
            lines.append(
                f"内存：{format_bytes(status.memory_used_bytes)} / "
                f"{format_bytes(status.memory_total_bytes)}（{status.memory_percent:.0f}%）"
            )
        if status.disk_percent is not None:
            lines.append(
                f"磁盘：{format_bytes(status.disk_used_bytes)} / "
                f"{format_bytes(status.disk_total_bytes)}（{status.disk_percent:.0f}%）"
            )

        if (
            status.upload_bytes_per_second is not None
            or status.download_bytes_per_second is not None
        ):
            lines.extend(
                [
                    "",
                    "网络",
                    f"上传：{format_bytes(status.upload_bytes_per_second)}/s",
                    f"下载：{format_bytes(status.download_bytes_per_second)}/s",
                ]
            )

        lines.extend(["", "运行"])
        if status.uptime_seconds is not None:
            lines.append(f"时长：{format_uptime(status.uptime_seconds)}")
        response = (
            "本机"
            if status.kind == "local"
            else (
                f"{status.latency_ms:.0f} ms"
                if status.latency_ms is not None
                else "未记录"
            )
        )
        lines.append(f"响应：{response}")
        lines.append(f"采集：{kind_name}")

        if status.services:
            lines.extend(["", "服务"])
            lines.extend(
                f"{item.name}：{'正常' if item.online else '异常'}"
                for item in status.services
            )

        if status.degraded and status.detail:
            lines.extend(["", f"说明：{status.detail}"])
        lines.extend(["", f"更新：{status.checked_at:%Y-%m-%d %H:%M:%S}"])
    return "\n".join(lines)
