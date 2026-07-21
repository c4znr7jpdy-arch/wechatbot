# 服务器状态

通过 `/服务器` 发送分区清晰的实时服务器状态文本。当前运行时配置只监控阿里云 `MyViture`。

- 位置：中国香港
- 系统：Ubuntu 22.04
- 规格：2 核、2 GiB 内存、40 GiB 磁盘
- 采集：通过现有 SSH 密钥只读获取系统指标；目标 IP 只保存在 Git 忽略的运行时配置中

## 命令

- `/服务器`：查看全部已启用服务器
- `/服务器 myviture`：按服务器 ID 查看
- `/服务器 MyViture`：按名称查看
- `/服务器 列表`：查看可用 ID 与名称
- `/服务器告警状态`：管理员查看后台告警任务
- `/服务器告警测试`：管理员测试私聊推送通道

状态消息按“资源、网络、运行、服务、更新时间”分区输出。离线时只显示故障原因与检查时间，不发送图片，也不会用 `--` 填充不可用指标。

## 报警推送

默认每 30 秒通过 SSH 检查一次服务器。连续 3 次失败后向管理员 `fengchenhao002` 私聊发送离线告警；离线后连续 2 次成功会发送恢复通知；持续离线每 60 分钟再次提醒。短时单次失败不会推送，消息中不会包含服务器公网 IP。

推送目标使用完整的 AstrBot `unified_msg_origin`，可在 WebUI 中继续添加私聊或群聊会话。

## 添加服务器

在 AstrBot WebUI 的“服务器状态”插件配置中编辑“服务器列表”。支持：

- 本机监控：采集 CPU、内存、磁盘、网络、运行时长和自定义端口。
- HTTP 指标接口：读取远程服务器的完整系统指标。
- SSH 系统指标：无需部署 Agent，使用现有密钥读取 Linux `/proc`、系统盘和 systemd 服务状态。
- TCP 在线探针：只检查指定主机端口与连接延迟。

HTTP 指标接口返回一个 JSON 对象，支持平铺字段或 `cpu`、`memory`、`disk`、`network` 嵌套字段。最小可用示例：

```json
{
  "status": "online",
  "hostname": "server-01",
  "os": "Ubuntu 24.04",
  "cpu_percent": 32.5,
  "cpu_count": 8,
  "memory_used_bytes": 8589934592,
  "memory_total_bytes": 17179869184,
  "disk_used_bytes": 214748364800,
  "disk_total_bytes": 536870912000,
  "upload_bytes_per_second": 1048576,
  "download_bytes_per_second": 5242880,
  "uptime_seconds": 864000,
  "services": [
    {"name": "Web", "online": true, "latency_ms": 12.4}
  ]
}
```

也可使用 `memory.used_gb`、`memory.total_gb`、`disk.used_gb`、`disk.total_gb`。配置 Bearer Token 后，请求会附带 `Authorization: Bearer <token>`。
