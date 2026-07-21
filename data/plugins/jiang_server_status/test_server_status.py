from __future__ import annotations

import asyncio
import unittest

from .alarm import AlarmEvaluator, format_alarm_message
from .monitor import (
    ServerConfig,
    ServerStatus,
    ServiceStatus,
    extract_server_query,
    format_status_text,
    format_uptime,
    parse_server_configs,
    parse_service_checks,
    select_servers,
    status_from_http_payload,
)


class MonitorTests(unittest.TestCase):
    def test_parse_template_list_and_select_server(self):
        configs = parse_server_configs(
            [
                {
                    "__template_key": "local",
                    "id": "main",
                    "name": "主服务器",
                    "location": "本机",
                    "disk_paths": ["C:\\", "E:\\"],
                    "service_checks": "AstrBot|127.0.0.1|6185",
                },
                {
                    "__template_key": "tcp",
                    "id": "edge",
                    "name": "边缘节点",
                    "host": "127.0.0.1",
                    "port": 80,
                },
            ]
        )
        self.assertEqual([item.server_id for item in configs], ["main", "edge"])
        self.assertEqual(configs[0].services[0].name, "AstrBot")
        self.assertEqual(select_servers(configs, "边缘节点")[0].server_id, "edge")
        self.assertEqual(len(select_servers(configs, "")), 2)

    def test_service_parser_ignores_invalid_lines(self):
        checks = parse_service_checks(
            "正常|localhost|6185\n坏行\n端口错误|localhost|99999\n# 注释"
        )
        self.assertEqual([(item.name, item.port) for item in checks], [("正常", 6185)])

    def test_parse_ssh_server(self):
        config = parse_server_configs(
            [
                {
                    "__template_key": "ssh",
                    "id": "linux",
                    "name": "Linux 节点",
                    "host": "example.internal",
                    "port": 2222,
                    "username": "monitor",
                    "identity_file": "C:/keys/monitor_ed25519",
                    "verify_host_key": True,
                }
            ]
        )[0]
        self.assertEqual(config.kind, "ssh")
        self.assertEqual(config.ssh_user, "monitor")
        self.assertEqual(config.port, 2222)
        self.assertTrue(config.verify_host_key)

    def test_extract_query(self):
        self.assertEqual(extract_server_query("/服务器 main"), "main")
        self.assertEqual(extract_server_query("\\服务器状态 主服务器"), "主服务器")
        self.assertEqual(extract_server_query("/服务器"), "")

    def test_http_payload_normalization(self):
        config = ServerConfig("http", "remote", "远程服务器", "上海")
        status = status_from_http_payload(
            config,
            {
                "status": "online",
                "cpu": {"percent": 35, "count": 8},
                "memory": {"used_gb": 4, "total_gb": 8},
                "disk_used_bytes": 50 * 1024**3,
                "disk_total_bytes": 100 * 1024**3,
                "uptime_seconds": 90061,
                "services": [{"name": "Web", "online": True}],
            },
            18.5,
        )
        self.assertTrue(status.online)
        self.assertEqual(status.cpu_percent, 35)
        self.assertEqual(status.memory_percent, 50)
        self.assertEqual(status.disk_percent, 50)
        self.assertEqual(format_uptime(status.uptime_seconds), "1 天 1 小时")

    def test_http_string_boolean_is_not_treated_as_truthy(self):
        config = ServerConfig("http", "remote", "远程服务器", "上海")
        status = status_from_http_payload(
            config,
            {
                "online": "false",
                "services": [{"name": "Web", "online": "false"}],
            },
            20,
        )
        self.assertFalse(status.online)
        self.assertFalse(status.services[0].online)

    def test_tcp_status_does_not_expose_host(self):
        async def handle_client(reader, writer):
            writer.close()
            await writer.wait_closed()

        async def run_check():
            server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            try:
                from .monitor import collect_server_status

                config = ServerConfig(
                    "tcp", "private", "私有节点", "内网", host="127.0.0.1", port=port
                )
                return await collect_server_status(config)
            finally:
                server.close()
                await server.wait_closed()

        status = asyncio.run(run_check())
        self.assertTrue(status.online)
        self.assertNotIn("127.0.0.1", status.detail)
        self.assertIn("TCP", status.detail)


class TextFormatterTests(unittest.TestCase):
    def test_online_status_has_clear_sections(self):
        status = ServerStatus(
            "myviture",
            "MyViture",
            "中国香港 · 2核2GiB · 40GiB",
            "ssh",
            True,
            os_name="Ubuntu 22.04.5 LTS",
            cpu_percent=12,
            cpu_count=2,
            memory_percent=64,
            memory_used_bytes=1024**3,
            memory_total_bytes=1.6 * 1024**3,
            disk_percent=43,
            disk_used_bytes=16.8 * 1024**3,
            disk_total_bytes=39 * 1024**3,
            upload_bytes_per_second=1024**2,
            download_bytes_per_second=2 * 1024**2,
            uptime_seconds=8 * 86400 + 5 * 3600,
            latency_ms=1250,
            services=[ServiceStatus("Nginx", True), ServiceStatus("SSH", True)],
        )
        text = format_status_text([status], "我的服务器")
        for section in ("资源", "网络", "运行", "服务"):
            self.assertIn(section, text)
        self.assertIn("CPU：12%，2 个逻辑核心", text)
        self.assertIn("内存：1.0 GB / 1.6 GB（64%）", text)
        self.assertIn("Nginx：正常", text)
        self.assertNotIn("--", text)

    def test_offline_status_omits_empty_metrics(self):
        status = ServerStatus(
            "myviture",
            "MyViture",
            "中国香港",
            "ssh",
            False,
            detail="SSH 指标采集超时",
        )
        text = format_status_text([status], "我的服务器")
        self.assertIn("MyViture  [离线]", text)
        self.assertIn("原因：SSH 指标采集超时", text)
        self.assertNotIn("资源", text)
        self.assertNotIn("--", text)


class AlarmTests(unittest.TestCase):
    @staticmethod
    def _status(online: bool) -> ServerStatus:
        return ServerStatus(
            "myviture",
            "MyViture",
            "中国香港",
            "tcp",
            online,
            detail="TCP 443 服务可访问" if online else "TCP 443 服务不可访问",
            latency_ms=68 if online else None,
        )

    def test_transient_failure_does_not_alert(self):
        evaluator = AlarmEvaluator(failure_threshold=3, recovery_threshold=2)
        self.assertIsNone(evaluator.update(self._status(False), now=0))
        self.assertIsNone(evaluator.update(self._status(True), now=30))
        self.assertIsNone(evaluator.update(self._status(False), now=60))

    def test_down_repeat_and_recovery_transitions(self):
        evaluator = AlarmEvaluator(
            failure_threshold=3,
            recovery_threshold=2,
            repeat_seconds=60,
        )
        self.assertIsNone(evaluator.update(self._status(False), now=0))
        self.assertIsNone(evaluator.update(self._status(False), now=30))
        down = evaluator.update(self._status(False), now=60)
        self.assertEqual(down.kind, "down")
        self.assertIsNone(evaluator.update(self._status(False), now=90))
        repeated = evaluator.update(self._status(False), now=120)
        self.assertEqual(repeated.kind, "still_down")
        self.assertIsNone(evaluator.update(self._status(True), now=150))
        recovered = evaluator.update(self._status(True), now=180)
        self.assertEqual(recovered.kind, "recovered")
        self.assertEqual(recovered.down_seconds, 120)

    def test_alarm_message_does_not_contain_endpoint(self):
        evaluator = AlarmEvaluator(failure_threshold=1)
        event = evaluator.update(self._status(False), now=0)
        message = format_alarm_message(event)
        self.assertIn("MyViture", message)
        self.assertIn("服务器离线告警", message)
        self.assertNotIn("203.0.113.10", message)


class AlarmDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_background_check_sends_down_and_recovery_once(self):
        from .main import Main

        class FakeContext:
            def __init__(self):
                self.messages = []

            async def send_message(self, target, chain):
                self.messages.append((target, chain))
                return True

        async def accept_and_close(reader, writer):
            del reader
            writer.close()
            await writer.wait_closed()

        placeholder = await asyncio.start_server(accept_and_close, "127.0.0.1", 0)
        port = placeholder.sockets[0].getsockname()[1]
        placeholder.close()
        await placeholder.wait_closed()

        config = {
            "alarm_enabled": False,
            "alarm_target_sessions": [
                "aiocqhttp:FriendMessage:fengchenhao002"
            ],
            "alarm_failure_threshold": 3,
            "alarm_recovery_threshold": 2,
            "alarm_repeat_minutes": 60,
            "servers": [
                {
                    "__template_key": "tcp",
                    "id": "test-server",
                    "name": "测试服务器",
                    "location": "本机测试",
                    "host": "127.0.0.1",
                    "port": port,
                    "timeout_seconds": 0.2,
                }
            ],
        }
        context = FakeContext()
        plugin = Main(context, config)
        try:
            self.assertEqual(await plugin._check_alarms_once(), 0)
            self.assertEqual(await plugin._check_alarms_once(), 0)
            self.assertEqual(await plugin._check_alarms_once(), 1)
            self.assertEqual(await plugin._check_alarms_once(), 0)

            server = await asyncio.start_server(accept_and_close, "127.0.0.1", port)
            try:
                self.assertEqual(await plugin._check_alarms_once(), 0)
                self.assertEqual(await plugin._check_alarms_once(), 1)
            finally:
                server.close()
                await server.wait_closed()
        finally:
            await plugin.terminate()

        self.assertEqual(len(context.messages), 2)
        self.assertEqual(
            {target for target, _ in context.messages},
            {"aiocqhttp:FriendMessage:fengchenhao002"},
        )


if __name__ == "__main__":
    unittest.main()
