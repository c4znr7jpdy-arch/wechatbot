# -*- coding: utf8 -*-

import json
import sys
import os
import os.path
import inspect
import copy
import signal
import threading
import asyncio
from functools import wraps
from ctypes import WinDLL, create_string_buffer, WINFUNCTYPE
import logging
import ctypes
import time
from ctypes import wintypes
import subprocess
import httpx
import websockets
import json as _json
import re
import uuid as _uuid
from html import unescape

# 253 读取wx消息
# 563 向wx发送消息

# ============================ 配置和常量 ============================

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('wechat_service.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger('WeChatService')

# 常量定义
PAGE_READWRITE = 0x04
FILE_MAP_ALL_ACCESS = 0x000F001F
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
SHARED_MEM_SIZE = 33  # 明确共享内存大小为33字节


# ============================ AstrBot WebSocket Client ============================

class AstrBotWsClient:
    """WebSocket Client，连接 AstrBot 的 WebSocket Server，支持自动重连"""

    def __init__(self, host="127.0.0.1", port=6199, send_text_fn=None, send_image_fn=None, send_emoji_fn=None, send_video_fn=None, send_voice_fn=None, get_friend_list_fn=None, get_group_list_fn=None):
        self.host = host
        self.port = port
        self.url = f"ws://{host}:{port}/ws"  # AstrBot aiocqhttp 反向 WS 路径
        self.send_text_fn = send_text_fn  # helper_send_text 回调
        self.send_image_fn = send_image_fn  # helper_send_image 回调
        self.send_emoji_fn = send_emoji_fn  # helper_send_emoji 回调（type=11254 CDN表情包）
        self.send_video_fn = send_video_fn  # helper_send_video 回调
        self.send_voice_fn = send_voice_fn  # helper_send_voice 回调
        self.get_friend_list_fn = get_friend_list_fn  # helper_get_friend_list 回调
        self.get_group_list_fn = get_group_list_fn  # helper_get_group_list 回调
        self._ws = None
        self._running = False
        self._closed = False
        self._user_id_to_wxid = {}  # 映射 user_id -> 原始 wxid (str)
        self._group_id_to_wxid = {}  # 映射 group_id -> 原始 room_wxid (str)
        self.send_link_card_fn = None  # 卡片发送回调
        self._reconnect_delay = 3  # 重连延迟秒数
        self._bot_wxid = None  # 机器人自己的 wxid
        self._bot_nickname = ""  # 机器人昵称
        self._loop = None  # Bug2修复: 保存事件循环引用，供跨线程调度
        self.pending_requests = {} # 异步请求追踪
        self._service_handler = None  # 引用 WeChatServiceHandler，用于 API 查询
        self._msg_id_counter = 0  # V11 message_id 自增计数器
        self._heartbeat_task = None  # 心跳任务引用
        self._pending_send = {}  # trace -> {"to_wxid": ..., "type": ...}
        self._last_sent_msg = {}  # to_wxid -> latest msg_info
        self._sent_msg_queues = {}  # to_wxid -> [msg_info, ...]
        self._last_send_to_wxid = ""
        self._last_send_type = ""  # 最近一次发送的消息类型（text/image 等），用于 11047 兜底
        self._image_recall_fifo = []  # bot 图片发送 FIFO，等待 11047 绑定 newMsgId，供精准撤回
        self._connecting = False
        self._receive_task = None
        self._reconnect_task = None

    def _next_msg_id(self) -> int:
        """生成自增的 V11 message_id"""
        self._msg_id_counter += 1
        return self._msg_id_counter

    def _resolve_group_wxid(self, group_id) -> str:
        group_id_str = str(group_id or "")
        if not group_id_str:
            return ""
        to_wxid = self._group_id_to_wxid.get(group_id_str, "")
        if to_wxid:
            return to_wxid
        if group_id_str.endswith("@chatroom"):
            return group_id_str
        chatroom_key = f"{group_id_str}@chatroom"
        return self._group_id_to_wxid.get(chatroom_key, chatroom_key)

    def _remember_sent_msg(self, to_wxid: str, msg_info: dict):
        """Record a sent WeChat message so delayed recalls do not overwrite each other."""
        if not to_wxid or not msg_info:
            return
        self._last_sent_msg[to_wxid] = msg_info
        self._sent_msg_queues.setdefault(to_wxid, []).append(msg_info)

    def _peek_recall_msg(self, to_wxid: str, msg_type: str = ""):
        queue = self._sent_msg_queues.get(to_wxid) or []
        if msg_type:
            for item in reversed(queue):
                if isinstance(item, dict) and item.get("type") == msg_type:
                    return item
            return None
        if queue:
            return queue[-1]
        return self._last_sent_msg.get(to_wxid)

    def _mark_recall_done(self, to_wxid: str, msg_info):
        queue = self._sent_msg_queues.get(to_wxid) or []
        if queue and queue[0] is msg_info:
            queue.pop(0)
        elif queue and msg_info in queue:
            queue.remove(msg_info)
        if queue:
            self._last_sent_msg[to_wxid] = queue[-1]
        else:
            self._sent_msg_queues.pop(to_wxid, None)
            self._last_sent_msg.pop(to_wxid, None)

    def _v12_segs_to_v11(self, segments: list) -> list:
        """将 V12 消息段转换为 V11 格式"""
        import os, base64
        v11 = []
        for seg in segments:
            st = seg.get("type", "")
            sd = seg.get("data", {})
            if st == "mention":
                v11.append({"type": "at", "data": {"qq": sd.get("user_id", "")}})
            elif st == "voice":
                fp = sd.get("file_id") or sd.get("file") or ""
                if fp.startswith("file:///"):
                    fp = fp[8:]
                if fp and os.path.exists(fp):
                    with open(fp, "rb") as f:
                        fp = f"base64://{base64.b64encode(f.read()).decode()}"
                v11.append({"type": "record", "data": {"file": fp}})
            elif st == "image":
                fp = sd.get("file_id") or sd.get("file") or sd.get("url") or ""
                if fp and os.path.exists(fp):
                    with open(fp, "rb") as f:
                        fp = f"base64://{base64.b64encode(f.read()).decode()}"
                elif fp and not fp.startswith(("http://", "https://", "base64://", "file://")):
                    fp = f"file:///{fp.replace(os.sep, '/')}"
                v11.append({"type": "image", "data": {"file": fp}})
            elif st == "video":
                fp = sd.get("file") or sd.get("file_id") or ""
                if fp.startswith("file:///"):
                    fp = fp[8:]
                # 视频直接发路径，不做 base64 编码（文件太大）
                v11.append({"type": "video", "data": {"file": fp}})
            elif st == "text":
                v11.append({"type": "text", "data": {"text": sd.get("text", "")}})
            else:
                v11.append(seg)
        return v11

    def _v11_segs_to_v12(self, segments: list) -> list:
        """将 AstrBot 发来的 V11 段转回内部格式"""
        converted = []
        for seg in segments:
            st = seg.get("type", "")
            sd = seg.get("data", {})
            if st == "at":
                converted.append({"type": "mention", "data": {"user_id": sd.get("qq", sd.get("user_id", ""))}})
            elif st == "record":
                converted.append({"type": "voice", "data": {"file_id": sd.get("file", ""), "file": sd.get("file", "")}})
            else:
                converted.append(seg)
        return converted

    async def connect(self):
        """连接到 AstrBot WebSocket Server，延时发送 connect 事件（等登录拿到真实 wxid）"""
        import os

        if self._running or self._connecting:
            return
        self._connecting = True
        try:
            while not self._closed:
                logger.info(f"连接 AstrBot WebSocket Server: {self.url}")
                try:
                    self._ws = await websockets.connect(
                        self.url,
                        proxy=None,
                        additional_headers={
                            "X-Self-ID": self._bot_wxid or "wechat_bot",
                            "X-Client-Role": "Universal",
                        },
                        ping_interval=None,
                        max_size=None,  # Remove default 1MiB limit; base64 image reports can exceed it
                    )
                    logger.info("AstrBot WebSocket 连接成功")

                    self._running = True
                    if self._receive_task and not self._receive_task.done():
                        self._receive_task.cancel()
                    self._receive_task = asyncio.create_task(self._receive_loop())

                    # 已有 wxid（热重连），直接发 connect
                    if self._bot_wxid:
                        await self.send_connect_event(self._bot_wxid)
                    else:
                        logger.info("连接就绪（等待登录后发送 connect 事件）")
                    return  # 连接成功，退出循环
                except Exception as e:
                    logger.error(f"连接 AstrBot 失败: {e}, {self._reconnect_delay}秒后重连...")
                    self._running = False
                    self._ws = None
                    await asyncio.sleep(self._reconnect_delay)
        finally:
            self._connecting = False

    async def send_connect_event(self, wxid: str):
        """登录后发送 V11 connect 事件（携带真实 wxid）"""
        if not self._ws:
            return
        import time as _time
        connect_event = {
            "time": int(_time.time()),
            "self_id": wxid,
            "post_type": "meta_event",
            "meta_event_type": "lifecycle",
            "sub_type": "connect",
        }
        await self._ws.send(_json.dumps(connect_event))
        logger.info(f"已发送 V11 connect meta_event, wxid={wxid}")
        # 启动心跳
        if not getattr(self, '_heartbeat_task', None):
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def reconnect_with_wxid(self):
        """登录后用真实 wxid 重连，确保 X-Self-ID 正确"""
        logger.info(f"用真实 wxid={self._bot_wxid} 重连 WebSocket...")
        # 关闭旧连接
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._running = False
        # 取消旧心跳
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        # 短暂等待后重连
        await asyncio.sleep(0.5)
        await self.connect()

    async def _heartbeat_loop(self):
        """V11 心跳，每 30 秒发送一次"""
        import time as _time
        while self._running and not self._closed:
            try:
                if self._ws and self._bot_wxid:
                    heartbeat = {
                        "time": int(_time.time()),
                        "self_id": self._bot_wxid,
                        "post_type": "meta_event",
                        "meta_event_type": "heartbeat",
                        "status": {"good": True, "online": True},
                        "interval": 30000,
                    }
                    await self._ws.send(_json.dumps(heartbeat))
            except Exception:
                pass
            await asyncio.sleep(30)
        # 循环结束，清除任务引用，重连后可重新创建
        self._heartbeat_task = None

    async def _receive_loop(self):
        """接收 AstrBot 发回的消息"""
        logger.info("Receive loop started")
        try:
            while self._ws is not None:
                logger.info("等待接收消息...")
                try:
                    raw_message = await self._ws.recv()
                    logger.info(f"收到消息: {raw_message[:200] if raw_message else 'None'}")
                    if raw_message is None:
                        logger.info("收到空消息，继续等待")
                        continue
                    response = await self._handle_message(raw_message)
                    # 如果有响应数据，发送回 AstrBot
                    if response is not None:
                        await self._ws.send(_json.dumps(response))
                        logger.info(f"已发送响应: {response}")
                except websockets.exceptions.ConnectionClosed:
                    logger.warning("AstrBot WebSocket 连接已关闭")
                    break
                except websockets.exceptions.InvalidMessage:
                    logger.error("无效的 WebSocket 消息")
                    break
                except BaseException as e:
                    logger.error(f"接收消息异常: {type(e).__name__}: {e}")
                    break
        except BaseException as e:
            logger.error(f"Receive loop 异常: {type(e).__name__}: {e}")
        finally:
            logger.info("Receive loop 结束")
            self._running = False
            self._ws = None
            current_task = asyncio.current_task()
            if current_task is self._receive_task:
                self._receive_task = None
            if not self._closed:
                if not self._reconnect_task or self._reconnect_task.done():
                    self._reconnect_task = asyncio.create_task(self._auto_reconnect())

    async def _auto_reconnect(self):
        """自动重连（带异常保护，确保不会因意外异常停止重连）"""
        while not self._closed:
            await asyncio.sleep(self._reconnect_delay)
            if self._closed or self._running:
                return
            try:
                logger.info("尝试重新连接 AstrBot...")
                await self.connect()
                if self._running:
                    return  # 连接成功
            except Exception as e:
                logger.error(f"自动重连异常: {e}, 继续重试...")

    async def _handle_message(self, raw_message: str):
        """处理 AstrBot 发回的消息，返回要发送的响应（如果有）"""
        import os as os_module  # Bug10修复: 提前导入，避免作用域泄漏
        try:
            data = _json.loads(raw_message)

            # AstrBot sends events and API responses differently
            # Events have post_type, API responses have echo/action
            if "post_type" in data:
                # This is an event from AstrBot (like heartbeat)
                logger.debug(f"收到 AstrBot 事件: {data.get('post_type')}")
                return None

            # API call/response
            echo = data.get("echo", "")
            retcode = data.get("retcode", -1)
            action = data.get("action", "")
            msg_data = data.get("params", {})

            # AstrBot 发送的 API 请求带有 action 字段
            is_api_request = bool(action)

            # 处理 send_message 请求（V12 标准动作名）
            if action in ("send_message", "send_msg") and is_api_request:
                messages = msg_data.get("message", [])
                text_content, image_data, sub_type = self._extract_text_and_image(messages) if messages else ("", None, None)

                # V12: 直接使用 user_id / group_id 作为 wxid（不再依赖 echo 映射）
                # Bug9修复: 事件中 user_id/group_id 已经是原始 wxid
                group_id = msg_data.get("group_id", "")
                user_id = msg_data.get("user_id", "")

                # 确定发送目标: 优先从映射表找，否则直接用 ID（V12中ID就是wxid）
                if group_id:
                    to_wxid = self._resolve_group_wxid(group_id)
                elif user_id:
                    to_wxid = self._user_id_to_wxid.get(user_id, user_id)
                else:
                    to_wxid = None

                auto_recall_delay = int(msg_data.get("auto_recall_delay", 0) or 0)

                # 发送消息
                sent = False
                if text_content and to_wxid:
                    if self.send_text_fn:
                        self.send_text_fn(to_wxid=to_wxid, content=text_content)
                        logger.info(f"AI 回复 -> {to_wxid}: {text_content[:100]}")
                        sent = True

                # 发送图片
                if image_data and to_wxid:
                    logger.info(f"图片数据: type={type(image_data).__name__}, sub_type={sub_type}, value={str(image_data)[:120]}")
                    if self._is_emoji_sub_type(sub_type):
                        # 表情包走 CDN 表情包接口 (type=11254)
                        if isinstance(image_data, bytes):
                            import tempfile
                            with tempfile.NamedTemporaryFile(suffix='.gif', delete=False) as tmp:
                                tmp.write(image_data)
                                image_path = tmp.name
                            try:
                                self._send_emoji(to_wxid, image_path)
                                logger.info(f"AI 表情包回复: {image_path}")
                                sent = True
                            finally:
                                try:
                                    os_module.remove(image_path)
                                except:
                                    pass
                        elif isinstance(image_data, str) and os_module.path.exists(image_data):
                            self._send_emoji(to_wxid, image_data)
                            logger.info(f"AI 表情包回复 (path): {image_data}")
                            sent = True
                        elif isinstance(image_data, str):
                            logger.warning(f"表情包路径不存在: {image_data}")
                    elif isinstance(image_data, bytes):
                        import tempfile
                        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                            tmp.write(image_data)
                            image_path = tmp.name
                        try:
                            self._send_image(to_wxid, image_path, auto_recall_delay=auto_recall_delay)
                            logger.info(f"AI 图片回复: {image_path}")
                            sent = True
                        finally:
                            try:
                                os_module.remove(image_path)
                            except:
                                pass
                    elif isinstance(image_data, str) and os_module.path.exists(image_data):
                        self._send_image(to_wxid, image_data, auto_recall_delay=auto_recall_delay)
                        logger.info(f"AI 图片回复 (path): {image_data}")
                        sent = True
                    elif isinstance(image_data, str):
                        logger.warning(f"图片路径不存在: {image_data}")

                # 发送视频
                video_path = self._extract_video(messages)
                if video_path and to_wxid:
                    if isinstance(video_path, str) and os_module.path.exists(video_path):
                        if self.send_video_fn:
                            self.send_video_fn(to_wxid, video_path)
                            logger.info(f"AI 视频回复 -> {to_wxid}: {video_path}")
                            sent = True
                        else:
                            logger.warning("send_video_fn 未设置，无法发送视频")
                    else:
                        logger.warning(f"视频文件不存在: {video_path}")

                # 发送语音（用于验证 DLL 是否支持 silk/slik 语音气泡）
                voice_path = self._extract_voice(messages)
                if voice_path and to_wxid:
                    if isinstance(voice_path, str) and os_module.path.exists(voice_path):
                        if self.send_voice_fn:
                            self.send_voice_fn(to_wxid, voice_path)
                            logger.info(f"AI 语音回复 -> {to_wxid}: {voice_path}")
                            sent = True
                        else:
                            logger.warning("send_voice_fn 未设置，无法发送语音")
                    else:
                        logger.warning(f"语音文件不存在: {voice_path}")

                # 发送卡片
                card_data = self._extract_link_card(messages)
                
                if card_data and to_wxid:
                    if self.send_link_card_fn:
                        self.send_link_card_fn(
                            to_wxid=to_wxid, 
                            title=card_data.get("title", ""),
                            desc=card_data.get("desc", ""),
                            url=card_data.get("url", "https://weixin.qq.com"),
                            image_url=card_data.get("image_url", "https://img.jbzj.com/file_images/Illustrator/201702/2017020411591786.png")
                        )
                        logger.info(f"AI 发送链接卡片 -> {to_wxid}")
                        sent = True

                return {"retcode": 0, "status": "ok", "data": {"message_id": 0}, "echo": echo}

            # V11: send_group_msg / send_private_msg / forward messages
            if action in ("send_group_msg", "send_private_msg", "send_group_forward_msg", "send_private_forward_msg") and is_api_request:
                messages = msg_data.get("message", [])
                auto_recall_delay = int(msg_data.get("auto_recall_delay", 0) or 0)
                if action in ("send_group_forward_msg", "send_private_forward_msg"):
                    text_content = self._extract_forward_text(messages)
                    image_data = None
                else:
                    text_content, image_data, sub_type = self._extract_text_and_image(messages) if messages else ("", None, None)
                if action in ("send_group_msg", "send_group_forward_msg"):
                    group_id = msg_data.get("group_id", "")
                    to_wxid = self._resolve_group_wxid(group_id)
                else:
                    user_id = msg_data.get("user_id", "")
                    to_wxid = self._user_id_to_wxid.get(str(user_id), str(user_id))
                sent = False
                if text_content and to_wxid and self.send_text_fn:
                    self.send_text_fn(to_wxid=to_wxid, content=text_content)
                    logger.info(f"V11 回复 -> {to_wxid}: {text_content[:100]}")
                    sent = True
                if image_data and to_wxid:
                    if self._is_emoji_sub_type(sub_type):
                        if isinstance(image_data, bytes):
                            import tempfile
                            with tempfile.NamedTemporaryFile(suffix='.gif', delete=False) as tmp:
                                tmp.write(image_data)
                                image_path = tmp.name
                            try:
                                self._send_emoji(to_wxid, image_path)
                                sent = True
                            finally:
                                try: os_module.remove(image_path)
                                except: pass
                        elif isinstance(image_data, str) and os_module.path.exists(image_data):
                            self._send_emoji(to_wxid, image_data)
                            sent = True
                    elif isinstance(image_data, bytes):
                        import tempfile
                        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                            tmp.write(image_data)
                            image_path = tmp.name
                        try:
                            self._send_image(to_wxid, image_path, auto_recall_delay=auto_recall_delay)
                            sent = True
                        finally:
                            try: os_module.remove(image_path)
                            except: pass
                    elif isinstance(image_data, str) and os_module.path.exists(image_data):
                        self._send_image(to_wxid, image_data, auto_recall_delay=auto_recall_delay)
                        sent = True
                video_path = self._extract_video(messages)
                if video_path and to_wxid and os_module.path.exists(video_path):
                    if self.send_video_fn:
                        self.send_video_fn(to_wxid, video_path)
                        sent = True
                voice_path = self._extract_voice(messages)
                if voice_path and to_wxid and os_module.path.exists(voice_path):
                    if self.send_voice_fn:
                        self.send_voice_fn(to_wxid, voice_path)
                        sent = True
                # wechat_link_card / OneBot music
                card_data = self._extract_link_card(messages)
                if card_data and to_wxid and self.send_link_card_fn:
                    self.send_link_card_fn(to_wxid=to_wxid, title=card_data.get("title", ""), desc=card_data.get("desc", ""), url=card_data.get("url", ""), image_url=card_data.get("image_url", ""))
                    sent = True
                return {"retcode": 0, "status": "ok", "data": {"message_id": 0}, "echo": echo}

            # V11: get_login_info
            if action == "get_login_info" and is_api_request:
                return {"retcode": 0, "status": "ok", "data": {"user_id": self._bot_wxid or "", "nickname": self._bot_nickname or ""}, "echo": echo}

            # V11: get_stranger_info
            if action == "get_stranger_info" and is_api_request:
                uid = str(msg_data.get("user_id", ""))
                info = self._get_cached_user_info(uid) if hasattr(self, '_get_cached_user_info') else {}
                return {"retcode": 0, "status": "ok", "data": {"user_id": uid, "nickname": info.get("nickname", uid), "sex": "unknown"}, "echo": echo}

            # V11: get_msg (stub)
            if action == "get_msg" and is_api_request:
                return {"retcode": 0, "status": "ok", "data": {"message_id": msg_data.get("message_id", 0), "message": [], "raw_message": ""}, "echo": echo}

            # V11: can_send_image / can_send_record
            if action in ("can_send_image", "can_send_record") and is_api_request:
                return {"retcode": 0, "status": "ok", "data": {"yes": True}, "echo": echo}

            # Bug10修复: 处理 upload_file 请求 (OneBot V12)
            if action == "upload_file" and is_api_request:
                logger.info(f"收到 upload_file API 请求: type={msg_data.get('type')}")
                file_type = msg_data.get("type")
                file_id = ""
                if file_type == "data":
                    data = msg_data.get("data")
                    file_bytes = b""
                    if isinstance(data, str):
                        import base64
                        try:
                            # 尝试 base64 解码 (V12 JSON 标准)
                            file_bytes = base64.b64decode(data)
                        except Exception:
                            file_bytes = data.encode('utf-8')
                    elif isinstance(data, bytes):
                        file_bytes = data

                    if file_bytes:
                        import tempfile, os as os_module
                        fd, path = tempfile.mkstemp(suffix=".png")
                        with os_module.fdopen(fd, 'wb') as f:
                            f.write(file_bytes)
                        file_id = path
                elif file_type == "path":
                    path = msg_data.get("path") or msg_data.get("file")
                    if path and os_module.path.exists(path):
                        file_id = path
                        logger.info(f"upload_file path: {file_id}")
                    else:
                        logger.warning(f"upload_file path 不存在: {path}")

                if file_id:
                    return {"status": "ok", "retcode": 0, "data": {"file_id": file_id}, "message": "", "echo": echo}
                else:
                    return {"status": "failed", "retcode": 10002, "data": None, "message": "upload failed", "echo": echo}

            # Bug3+6修复 (ASYNC): 处理 get_friend_list 请求（V12 API）
            if action == "get_friend_list" and is_api_request:
                logger.info("收到 get_friend_list API 请求")
                if self.get_friend_list_fn and getattr(self, "_loop", None):
                    import uuid, asyncio
                    trace_id = str(uuid.uuid4())
                    future = self._loop.create_future()
                    self.pending_requests[trace_id] = future
                    self.get_friend_list_fn(trace_id)
                    try:
                        result_data = await asyncio.wait_for(future, timeout=10.0)
                        v12_friends = []
                        for friend in result_data:
                            v12_friends.append({
                                "user_id": str(friend.get("wxid", "")),
                                "user_name": str(friend.get("nickname", "")),
                                "user_remark": str(friend.get("remark", ""))
                            })
                        return {"status": "ok", "retcode": 0, "data": v12_friends, "message": "", "echo": echo}
                    except asyncio.TimeoutError:
                        logger.error("获取好友列表超时")
                        self.pending_requests.pop(trace_id, None)
                    except Exception as e:
                        logger.error(f"获取好友列表异常: {e}")
                        self.pending_requests.pop(trace_id, None)
                return {"status": "ok", "retcode": 0, "data": [], "message": "", "echo": echo}

            # 处理 get_user_info 请求（V12 API，用于获取指定wxid头像信息）
            if action == "get_user_info" and is_api_request:
                user_id = msg_data.get("user_id", "")
                if getattr(self, "get_user_info_fn", None) and getattr(self, "_loop", None) and user_id:
                    import uuid, asyncio
                    trace_id = str(uuid.uuid4())
                    future = self._loop.create_future()
                    self.pending_requests[trace_id] = future
                    self.get_user_info_fn(trace_id, user_id)
                    try:
                        result_data = await asyncio.wait_for(future, timeout=5.0)
                        avatar_url = str(result_data.get("avatar", ""))
                        nickname = str(result_data.get("nickname", ""))
                        
                        v12_user = {
                            "user_id": user_id,
                            "user_name": nickname,
                            "user_displayname": "",
                            "avatar": avatar_url
                        }
                        return {"status": "ok", "retcode": 0, "data": v12_user, "message": "", "echo": echo}
                    except asyncio.TimeoutError:
                        logger.error(f"获取用户信息超时: {user_id}")
                        self.pending_requests.pop(trace_id, None)
                    except Exception as e:
                        logger.error(f"获取用户信息异常: {e}")
                        self.pending_requests.pop(trace_id, None)
                return {"status": "ok", "retcode": 0, "data": None, "message": "", "echo": echo}

            # 处理 get_group_member_info 请求（V12 API）
            if action == "get_group_member_info" and is_api_request:
                group_id = msg_data.get("group_id", "")
                user_id = msg_data.get("user_id", "")
                if getattr(self, "get_group_member_info_fn", None) and getattr(self, "_loop", None) and group_id and user_id:
                    import uuid, asyncio
                    trace_id = str(uuid.uuid4())
                    future = self._loop.create_future()
                    self.pending_requests[trace_id] = future
                    self.get_group_member_info_fn(trace_id, group_id)
                    try:
                        result_data = await asyncio.wait_for(future, timeout=5.0)
                        # data: {"member_list": [...]}
                        avatar_url = ""
                        nickname = ""
                        member_sex = 0
                        if result_data and "member_list" in result_data:
                            for mem in result_data["member_list"]:
                                if mem.get("wxid") == user_id:
                                    avatar_url = str(mem.get("avatar", ""))
                                    nickname = str(mem.get("nickname", ""))
                                    member_sex = mem.get("sex", 0)
                                    break

                        v12_member = {
                            "user_id": user_id,
                            "user_name": nickname,
                            "user_displayname": "",
                            "avatar": avatar_url,
                            "sex": member_sex
                        }
                        return {"status": "ok", "retcode": 0, "data": v12_member, "message": "", "echo": echo}
                    except asyncio.TimeoutError:
                        logger.error(f"获取群成员信息超时: {group_id} {user_id}")
                        self.pending_requests.pop(trace_id, None)
                    except Exception as e:
                        logger.error(f"获取群成员信息异常: {e}")
                        self.pending_requests.pop(trace_id, None)
                return {"status": "ok", "retcode": 0, "data": None, "message": "", "echo": echo}

            # 处理 get_user_info 请求（V12 API）
            if action == "get_user_info" and is_api_request:
                user_id = msg_data.get("user_id", "")
                if getattr(self, "get_user_info_fn", None) and getattr(self, "_loop", None) and user_id:
                    import uuid, asyncio
                    trace_id = str(uuid.uuid4())
                    future = self._loop.create_future()
                    self.pending_requests[trace_id] = future
                    self.get_user_info_fn(trace_id, user_id)
                    try:
                        result_data = await asyncio.wait_for(future, timeout=5.0)
                        # V12 User Object
                        v12_user = {
                            "user_id": str(result_data.get("wxid", user_id)),
                            "user_name": str(result_data.get("nickname", "")),
                            "user_displayname": str(result_data.get("remark", "")),
                            # 插件可以通过这层拿到头像
                            "avatar": str(result_data.get("avatar", ""))
                        }
                        return {"status": "ok", "retcode": 0, "data": v12_user, "message": "", "echo": echo}
                    except asyncio.TimeoutError:
                        logger.error(f"获取好友/用户信息超时: {user_id}")
                        self.pending_requests.pop(trace_id, None)
                    except Exception as e:
                        logger.error(f"获取用户信息异常: {e}")
                        self.pending_requests.pop(trace_id, None)
                return {"status": "ok", "retcode": 0, "data": None, "message": "", "echo": echo}

            # 处理 get_group_member_info 请求（V12 API）
            if action == "get_group_member_info" and is_api_request:
                group_id = msg_data.get("group_id", "")
                user_id = msg_data.get("user_id", "")
                if getattr(self, "get_group_member_info_fn", None) and getattr(self, "_loop", None) and group_id and user_id:
                    import uuid, asyncio
                    trace_id = str(uuid.uuid4())
                    future = self._loop.create_future()
                    self.pending_requests[trace_id] = future
                    self.get_group_member_info_fn(trace_id, group_id)
                    try:
                        result_data = await asyncio.wait_for(future, timeout=5.0)
                        # data: {"member_list": [...]}
                        avatar_url = ""
                        nickname = ""
                        member_sex = 0
                        if result_data and "member_list" in result_data:
                            for mem in result_data["member_list"]:
                                if mem.get("wxid") == user_id:
                                    avatar_url = str(mem.get("avatar", ""))
                                    nickname = str(mem.get("nickname", ""))
                                    member_sex = mem.get("sex", 0)
                                    break

                        v12_member = {
                            "user_id": user_id,
                            "user_name": nickname,
                            "user_displayname": "",
                            "avatar": avatar_url,
                            "sex": member_sex
                        }
                        return {"status": "ok", "retcode": 0, "data": v12_member, "message": "", "echo": echo}
                    except asyncio.TimeoutError:
                        logger.error(f"获取群成员信息超时: {group_id} {user_id}")
                        self.pending_requests.pop(trace_id, None)
                    except Exception as e:
                        logger.error(f"获取群成员信息异常: {e}")
                        self.pending_requests.pop(trace_id, None)
                return {"status": "ok", "retcode": 0, "data": None, "message": "", "echo": echo}

            # 处理 get_user_info 请求（V12 API）
            if action == "get_user_info" and is_api_request:
                user_id = msg_data.get("user_id", "")
                if getattr(self, "get_user_info_fn", None) and getattr(self, "_loop", None) and user_id:
                    import uuid, asyncio
                    trace_id = str(uuid.uuid4())
                    future = self._loop.create_future()
                    self.pending_requests[trace_id] = future
                    self.get_user_info_fn(trace_id, user_id)
                    try:
                        result_data = await asyncio.wait_for(future, timeout=5.0)
                        # V12 User Object
                        v12_user = {
                            "user_id": str(result_data.get("wxid", user_id)),
                            "user_name": str(result_data.get("nickname", "")),
                            "user_displayname": str(result_data.get("remark", "")),
                            # 插件可以通过这层拿到头像
                            "avatar": str(result_data.get("avatar", ""))
                        }
                        return {"status": "ok", "retcode": 0, "data": v12_user, "message": "", "echo": echo}
                    except asyncio.TimeoutError:
                        logger.error(f"获取好友/用户信息超时: {user_id}")
                        self.pending_requests.pop(trace_id, None)
                    except Exception as e:
                        logger.error(f"获取用户信息异常: {e}")
                        self.pending_requests.pop(trace_id, None)
                return {"status": "ok", "retcode": 0, "data": None, "message": "", "echo": echo}

            # 处理 get_group_list 请求（V12 API）
            if action == "get_group_list" and is_api_request:
                logger.info("收到 get_group_list API 请求")
                if getattr(self, "get_group_list_fn", None) and getattr(self, "_loop", None):
                    import uuid, asyncio
                    trace_id = str(uuid.uuid4())
                    future = self._loop.create_future()
                    self.pending_requests[trace_id] = future
                    self.get_group_list_fn(trace_id)
                    try:
                        result_data = await asyncio.wait_for(future, timeout=10.0)
                        v12_groups = []
                        for group in result_data:
                            v12_groups.append({
                                "group_id": str(group.get("wxid", "")),
                                "group_name": str(group.get("nickname", ""))
                            })
                        return {"status": "ok", "retcode": 0, "data": v12_groups, "message": "", "echo": echo}
                    except asyncio.TimeoutError:
                        logger.error("获取群组列表超时")
                        self.pending_requests.pop(trace_id, None)
                    except Exception as e:
                        logger.error(f"获取群组列表异常: {e}")
                        self.pending_requests.pop(trace_id, None)
                return {"status": "ok", "retcode": 0, "data": [], "message": "", "echo": echo}

            # 处理 get_status 请求（V12 API）
            if action == "get_status" and is_api_request:
                return {"status": "ok", "retcode": 0, "data": {"good": True, "online": True}, "message": "", "echo": echo}

            # 处理 get_version 请求（V12 API）
            if action == "get_version" and is_api_request:
                return {
                    "status": "ok", "retcode": 0,
                    "data": {"impl": "wechat", "version": "1.0.0", "onebot_version": "11"},
                    "message": "",
                    "echo": echo
                }

            # 处理 get_self_info 请求（V12 API）
            if action == "get_self_info" and is_api_request:
                bot_nickname = getattr(self, '_bot_nickname', '') or ''
                return {
                    "status": "ok", "retcode": 0,
                    "data": {"user_id": self._bot_wxid or "", "nickname": bot_nickname},
                    "message": "",
                    "echo": echo
                }

            # 获取文件助手最近消息（自定义动作）
            if action == "get_filehelper_messages" and is_api_request:
                count = int(msg_data.get("count", 2))
                if self._service_handler:
                    msgs = self._service_handler.get_filehelper_msgs(count)
                    return {"status": "ok", "retcode": 0, "data": msgs, "message": "", "echo": echo}
                return {"status": "failed", "retcode": 10002, "data": [], "message": "handler unavailable", "echo": echo}

            if action == "refresh_group_members" and is_api_request:
                room_wxid = str(msg_data.get("room_wxid", "") or msg_data.get("group_id", ""))
                if self._service_handler:
                    result = self._service_handler.refresh_group_members(room_wxid or None)
                    return {"status": "ok", "retcode": 0, "data": result, "message": "", "echo": echo}
                return {"status": "failed", "retcode": 10002, "data": {}, "message": "handler unavailable", "echo": echo}

            if action == "get_group_member_cache" and is_api_request:
                room_wxid = str(msg_data.get("room_wxid", "") or msg_data.get("group_id", ""))
                if room_wxid:
                    members = _GROUP_MEMBER_CACHE.get(room_wxid, {})
                    return {"status": "ok", "retcode": 0, "data": {"room_wxid": room_wxid, "member_count": len(members), "members": members}, "message": "", "echo": echo}
                summary = {room: len(members) for room, members in _GROUP_MEMBER_CACHE.items()}
                return {"status": "ok", "retcode": 0, "data": {"group_count": len(summary), "groups": summary}, "message": "", "echo": echo}

            if action == "get_group_member_aliases" and is_api_request:
                room_wxid = str(msg_data.get("room_wxid", "") or msg_data.get("group_id", ""))
                wxid = str(msg_data.get("wxid", "") or msg_data.get("user_id", ""))
                if room_wxid and wxid:
                    aliases = _get_member_call_aliases(room_wxid, wxid)
                    return {"status": "ok", "retcode": 0, "data": {"room_wxid": room_wxid, "wxid": wxid, "aliases": aliases}, "message": "", "echo": echo}
                return {"status": "ok", "retcode": 0, "data": _get_group_alias_summary(room_wxid), "message": "", "echo": echo}

            if action == "set_group_member_aliases" and is_api_request:
                room_wxid = str(msg_data.get("room_wxid", "") or msg_data.get("group_id", ""))
                wxid = str(msg_data.get("wxid", "") or msg_data.get("user_id", ""))
                aliases = msg_data.get("aliases", [])
                if not room_wxid or not wxid:
                    return {"status": "failed", "retcode": 10003, "data": {}, "message": "room_wxid and wxid are required", "echo": echo}
                saved = _set_member_call_aliases(room_wxid, wxid, aliases)
                return {"status": "ok", "retcode": 0, "data": {"room_wxid": room_wxid, "wxid": wxid, "aliases": saved}, "message": "", "echo": echo}

            if action == "delete_group_member_aliases" and is_api_request:
                room_wxid = str(msg_data.get("room_wxid", "") or msg_data.get("group_id", ""))
                wxid = str(msg_data.get("wxid", "") or msg_data.get("user_id", ""))
                if not room_wxid or not wxid:
                    return {"status": "failed", "retcode": 10003, "data": {}, "message": "room_wxid and wxid are required", "echo": echo}
                _set_member_call_aliases(room_wxid, wxid, [])
                return {"status": "ok", "retcode": 0, "data": {"room_wxid": room_wxid, "wxid": wxid, "aliases": []}, "message": "", "echo": echo}

            # 发送转发聊天记录（自定义动作）
            if action == "send_forward_record" and is_api_request:
                to_wxid = msg_data.get("to_wxid", "")
                title = msg_data.get("title", "聊天记录")
                messages = msg_data.get("messages", [])
                fwd_type = int(msg_data.get("forward_type", 11044))
                if self._service_handler and hasattr(self._service_handler, 'service'):
                    ok = self._service_handler.service.helper_send_forward_record(
                        to_wxid, title, messages, fwd_type
                    )
                    return {"status": "ok" if ok else "failed", "retcode": 0 if ok else 10002, "data": None, "message": "", "echo": echo}
                return {"status": "failed", "retcode": 10002, "data": None, "message": "handler unavailable", "echo": echo}

            # 发送 silk/slik 语音文件（自定义动作，用于验证 DLL 语音发送能力）
            if action == "send_voice" and is_api_request:
                to_wxid = msg_data.get("to_wxid") or msg_data.get("user_id") or msg_data.get("group_id") or ""
                file_path = msg_data.get("file") or msg_data.get("file_id") or msg_data.get("path") or msg_data.get("slik_file") or msg_data.get("silk_file") or ""
                msg_type = int(msg_data.get("msg_type") or os.environ.get("WECHAT_VOICE_SEND_TYPE", "11044"))
                if self._service_handler and hasattr(self._service_handler, 'service'):
                    ok = self._service_handler.service.helper_send_voice(to_wxid, file_path, msg_type)
                    return {"status": "ok" if ok else "failed", "retcode": 0 if ok else 10002, "data": None, "message": "", "echo": echo}
                return {"status": "failed", "retcode": 10002, "data": None, "message": "handler unavailable", "echo": echo}

            # 发送原始 XML（type=11214），用于探测 voicemsg raw_msg 是否可转发
            if action == "send_raw_xml" and is_api_request:
                to_wxid = msg_data.get("to_wxid") or msg_data.get("user_id") or msg_data.get("group_id") or ""
                content = msg_data.get("content") or ""
                send_type = int(msg_data.get("send_type") or os.environ.get("WECHAT_RAW_XML_SEND_TYPE", "11214"))
                if content == "__last_voice__" and self._service_handler:
                    content = self._service_handler.get_last_voice_raw_msg()
                if content == "__last_appmsg__" and self._service_handler:
                    content = self._service_handler.get_last_appmsg_raw_msg()
                if self._service_handler and hasattr(self._service_handler, 'service'):
                    ok = self._service_handler.service.helper_send_raw_xml(to_wxid, content, send_type)
                    return {"status": "ok" if ok else "failed", "retcode": 0 if ok else 10002, "data": None, "message": "", "echo": echo}
                return {"status": "failed", "retcode": 10002, "data": None, "message": "handler unavailable", "echo": echo}

            # 读取最近一次收到的 11061 appmsg 原始 XML，便于分析音乐卡片结构
            if action == "get_last_appmsg_raw_xml" and is_api_request:
                content = self._service_handler.get_last_appmsg_raw_msg() if self._service_handler else ""
                return {"status": "ok", "retcode": 0, "data": {"content": content}, "message": "", "echo": echo}

            # CDN 上传文件 (type=11229)，返回 file_id/aes_key 等
            if action == "cdn_upload" and is_api_request:
                import asyncio as _asyncio
                file_path = msg_data.get("file_path") or msg_data.get("file") or ""
                file_type = int(msg_data.get("file_type", 5))
                trace_id = str(_uuid.uuid4())
                if self._service_handler and hasattr(self._service_handler, 'service'):
                    # 注册 pending 等待 CDN 回调
                    future = self._loop.create_future() if self._loop else None
                    if future:
                        self.pending_requests[trace_id] = future
                    ok = self._service_handler.service.helper_cdn_upload(file_path, file_type, trace_id)
                    if not ok:
                        self.pending_requests.pop(trace_id, None)
                        return {"status": "failed", "retcode": 10002, "data": None, "message": "cdn_upload send failed", "echo": echo}
                    # 等待 DLL 回调结果
                    if future:
                        try:
                            result = await _asyncio.wait_for(future, timeout=15)
                            return {"status": "ok", "retcode": 0, "data": result, "message": "", "echo": echo}
                        except _asyncio.TimeoutError:
                            self.pending_requests.pop(trace_id, None)
                            return {"status": "failed", "retcode": 10003, "data": None, "message": "cdn_upload timeout", "echo": echo}
                    return {"status": "ok", "retcode": 0, "data": None, "message": "sent (no await)", "echo": echo}
                return {"status": "failed", "retcode": 10002, "data": None, "message": "handler unavailable", "echo": echo}

            # 通用 CDN 发送 (指定 type 和 CDN 字段)
            if action == "cdn_send" and is_api_request:
                to_wxid = msg_data.get("to_wxid") or msg_data.get("user_id") or msg_data.get("group_id") or ""
                send_type = int(msg_data.get("send_type", 11235))
                cdn_data = msg_data.get("cdn_data", {})
                if self._service_handler and hasattr(self._service_handler, 'service'):
                    ok = self._service_handler.service.helper_cdn_send(to_wxid, cdn_data, send_type)
                    return {"status": "ok" if ok else "failed", "retcode": 0 if ok else 10002, "data": None, "message": "", "echo": echo}
                return {"status": "failed", "retcode": 10002, "data": None, "message": "handler unavailable", "echo": echo}

            if action == "get_guild_list" and is_api_request:
                return {
                    "status": "ok", "retcode": 0,
                    "data": [],
                    "message": "",
                    "echo": echo
                }

            # V11: delete_msg（撤回消息）
            if action == "delete_msg" and is_api_request:
                to_wxid = msg_data.get("to_wxid", "")
                msg_server_id = str(msg_data.get("msg_server_id", ""))
                if to_wxid and msg_server_id:
                    ok = self._service_handler.service.helper_recall_msg(to_wxid, msg_server_id) if self._service_handler else False
                    return {"status": "ok" if ok else "failed", "retcode": 0 if ok else 10002, "data": None, "message": "", "echo": echo}
                return {"status": "failed", "retcode": 10003, "data": None, "message": "need to_wxid and msg_server_id", "echo": echo}

            # Custom: recall the next queued bot message in this conversation.
            if action == "recall_last_msg" and is_api_request:
                to_wxid = msg_data.get("to_wxid", "")
                if not to_wxid:
                    group_id = str(msg_data.get("group_id", ""))
                    user_id = str(msg_data.get("user_id", ""))
                    if group_id:
                        to_wxid = self._resolve_group_wxid(group_id)
                    elif user_id:
                        to_wxid = self._user_id_to_wxid.get(user_id, user_id)
                if not to_wxid:
                    return {"status": "failed", "retcode": 10003, "data": None, "message": "need to_wxid", "echo": echo}
                msg_type = str(msg_data.get("msg_type", "") or msg_data.get("type", ""))
                msg_info = self._peek_recall_msg(to_wxid, msg_type)
                if not msg_info:
                    suffix = f" type={msg_type}" if msg_type else ""
                    return {"status": "failed", "retcode": 10004, "data": None, "message": f"no msg_server_id for {to_wxid}{suffix}", "echo": echo}
                new_msgid = msg_info.get("new_msgid", "") if isinstance(msg_info, dict) else str(msg_info)
                client_msgid = msg_info.get("client_msgid", 0) if isinstance(msg_info, dict) else 0
                create_time = msg_info.get("create_time", 0) if isinstance(msg_info, dict) else 0
                ok = self._service_handler.service.helper_recall_msg(to_wxid, new_msgid, client_msgid, create_time) if self._service_handler else False
                if ok:
                    self._mark_recall_done(to_wxid, msg_info)
                return {"status": "ok" if ok else "failed", "retcode": 0 if ok else 10002, "data": None, "message": "", "echo": echo}

            # Custom: schedule auto-recall of the last queued bot message.
            if action == "auto_recall_last_msg" and is_api_request:
                to_wxid = msg_data.get("to_wxid", "")
                if not to_wxid:
                    group_id = str(msg_data.get("group_id", ""))
                    user_id = str(msg_data.get("user_id", ""))
                    if group_id:
                        to_wxid = self._resolve_group_wxid(group_id)
                    elif user_id:
                        to_wxid = self._user_id_to_wxid.get(user_id, user_id)
                delay = int(msg_data.get("delay", 20))
                msg_type = str(msg_data.get("msg_type", "") or msg_data.get("type", ""))
                if not to_wxid:
                    return {"status": "failed", "retcode": 10003, "data": None, "message": "need to_wxid", "echo": echo}
                msg_info = self._peek_recall_msg(to_wxid, msg_type)
                trace = msg_info.get("trace", "") if isinstance(msg_info, dict) else ""
                if self._service_handler:
                    self._service_handler.service._schedule_auto_recall(to_wxid, trace, delay, msg_type)
                state = "matched" if msg_info else "pending"
                logger.info(f"[auto_recall] scheduled: to={to_wxid}, delay={delay}s, type={msg_type}, state={state}")
                return {"status": "ok", "retcode": 0, "data": None, "message": "", "echo": echo}

            # 其他未知 API 请求，返回 unsupported
            if is_api_request:
                logger.info(f"收到未处理的 API 请求: {action}")
                return {"status": "failed", "retcode": 10002, "data": None, "message": f"unsupported action: {action}", "echo": echo}

            # 其他消息（可能是事件或响应）
            if echo:
                if retcode != 0:
                    logger.warning(f"AstrBot API 返回错误: retcode={retcode}")
                return None

            return None

        except Exception as e:
            logger.error(f"解析 AstrBot 消息失败: {e}")

    def _extract_text_and_image(self, messages) -> tuple:
        """从 OneBot 消息段中提取纯文本、图片数据和子类型（兼容 V11/V12 格式）
        返回 (text, image_data, sub_type)"""
        if isinstance(messages, str):
            return messages, None, None
        if isinstance(messages, dict):
            messages = [messages]
        parts = []
        image_data = None
        sub_type = None
        if isinstance(messages, list):
            for seg in messages:
                if isinstance(seg, dict):
                    seg_type = seg.get("type", "")
                    seg_data = seg.get("data", {})
                    if seg_type == "text":
                        text = seg_data.get("text", "")
                        if "<think>" in text or "</think>" in text:
                            import re
                            text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
                        parts.append(text)
                    elif seg_type == "image":
                        image_data = seg_data.get("file_id") or seg_data.get("file") or seg_data.get("image_id") or seg_data.get("url")
                        sub_type = (
                            seg_data.get("sub_type")
                            or seg_data.get("subType")
                            or seg_data.get("subtype")
                            or seg.get("sub_type")
                            or seg.get("subType")
                            or seg.get("subtype")
                        )
                        # V11 base64:// 格式：解码保存到临时文件
                        if image_data and isinstance(image_data, str) and image_data.startswith("base64://"):
                            import base64, tempfile
                            try:
                                raw = base64.b64decode(image_data[9:])
                                suffix = ".jpg"
                                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                                tmp.write(raw)
                                tmp.close()
                                image_data = tmp.name
                            except Exception:
                                pass
                        # V11 file:/// 格式：转为本地路径
                        elif image_data and isinstance(image_data, str) and image_data.startswith("file:///"):
                            image_data = image_data[8:].replace("/", os.sep)
                        if sub_type is None and self._is_meme_manager_image_path(image_data):
                            sub_type = "emoji"
                elif isinstance(seg, str):
                    if not (seg.startswith("<think>") or "</think>" in seg):
                        parts.append(seg)
        result = "".join(parts).strip()
        return result, image_data, sub_type

    @staticmethod
    def _is_emoji_sub_type(sub_type) -> bool:
        if sub_type is None:
            return False
        normalized = str(sub_type).strip().lower()
        return normalized in {"emoji", "sticker", "mface", "face", "1"}

    @staticmethod
    def _is_meme_manager_image_path(image_data) -> bool:
        if not isinstance(image_data, str) or not image_data:
            return False
        normalized = image_data.replace("\\", "/").lower()
        return "/plugin_data/meme_manager/memes/" in normalized

    def _extract_link_card(self, messages) -> dict | None:
        if isinstance(messages, dict):
            messages = [messages]
        if not isinstance(messages, list):
            return None

        for seg in messages:
            if not isinstance(seg, dict):
                continue
            seg_type = seg.get("type")
            seg_data = seg.get("data", {}) or {}
            if seg_type == "wechat_link_card":
                return seg_data
            if seg_type != "music":
                continue

            music_type = str(seg_data.get("type", "")).lower()
            song_id = str(seg_data.get("id", "") or "")
            title = seg_data.get("title") or "网易云音乐"
            desc = seg_data.get("content") or seg_data.get("desc") or "点击播放音乐"
            url = seg_data.get("url") or ""
            image_url = (
                seg_data.get("image")
                or seg_data.get("image_url")
                or seg_data.get("cover")
                or "https://s1.music.126.net/style/favicon.ico"
            )

            if not url and music_type in {"163", "netease", "netease_cloud"} and song_id:
                url = f"https://music.163.com/song?id={song_id}"
            if not url:
                continue
            return {
                "title": title,
                "desc": desc,
                "url": url,
                "image_url": image_url,
            }
        return None

    def _extract_forward_text(self, messages) -> str:
        """从 OneBot 合并转发节点中提取普通文本"""
        if isinstance(messages, dict):
            messages = [messages]
        parts = []
        if isinstance(messages, list):
            for node in messages:
                if not isinstance(node, dict):
                    continue
                node_data = node.get("data", {})
                content = node_data.get("content", "")
                text, _, _ = self._extract_text_and_image(content)
                if text:
                    parts.append(text)
        return "\n\n".join(parts).strip()

    def _extract_video(self, messages) -> str | None:
        """从 OneBot V12 消息段中提取视频文件路径"""
        if isinstance(messages, dict):
            messages = [messages]
        if isinstance(messages, list):
            for seg in messages:
                if isinstance(seg, dict) and seg.get("type") == "video":
                    seg_data = seg.get("data", {})
                    video_path = seg_data.get("file") or seg_data.get("file_id") or seg_data.get("path")
                    if video_path and isinstance(video_path, str):
                        if video_path.startswith("file:///"):
                            video_path = video_path[8:]
                        elif video_path.startswith("file://"):
                            video_path = video_path[7:]
                    if video_path:
                        return video_path
        return None

    def _extract_voice(self, messages) -> str | None:
        """从 OneBot 消息段中提取语音文件路径（兼容 V11 record / V12 voice）"""
        if isinstance(messages, dict):
            messages = [messages]
        if isinstance(messages, list):
            for seg in messages:
                if isinstance(seg, dict) and seg.get("type") in ("voice", "audio", "record"):
                    seg_data = seg.get("data", {})
                    voice_path = (
                        seg_data.get("file")
                        or seg_data.get("file_id")
                        or seg_data.get("path")
                        or seg_data.get("slik_file")
                        or seg_data.get("silk_file")
                    )
                    # V11 base64:// 格式：解码保存到临时文件
                    if voice_path and isinstance(voice_path, str) and voice_path.startswith("base64://"):
                        import base64, tempfile
                        try:
                            raw = base64.b64decode(voice_path[9:])
                            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".silk")
                            tmp.write(raw)
                            tmp.close()
                            voice_path = tmp.name
                        except Exception:
                            pass
                    elif voice_path and isinstance(voice_path, str) and voice_path.startswith("file:///"):
                        voice_path = voice_path[8:].replace("/", os.sep)
                    if voice_path:
                        return voice_path
        return None

    def _send_image(self, to_wxid: str, image_path: str, auto_recall_delay: int = 0):
        """发送图片消息（type=11040）"""
        import os as os_module
        if not os_module.path.exists(image_path):
            logger.warning(f"图片文件不存在: {image_path}")
            return False

        if self.send_image_fn:
            try:
                result = self.send_image_fn(to_wxid, image_path, auto_recall_delay=auto_recall_delay)
                logger.info(f"图片发送结果: {result}")
                return result
            except Exception as e:
                logger.error(f"发送图片失败: {e}")
                return False
        else:
            logger.warning("send_image_fn 未设置，无法发送图片")
            return False

    def _send_emoji(self, to_wxid: str, image_path: str):
        """发送表情包消息（type=11254 CDN表情包）"""
        import os as os_module
        if not os_module.path.exists(image_path):
            logger.warning(f"表情包文件不存在: {image_path}")
            return False

        if self.send_emoji_fn:
            try:
                result = self.send_emoji_fn(to_wxid, image_path)
                logger.info(f"表情包发送结果: {result}")
                return result
            except Exception as e:
                logger.error(f"发送表情包失败: {e}")
                return False
        else:
            logger.warning("send_emoji_fn 未设置，无法发送表情包")
            return False

    async def send_event(self, event_data: dict):
        """发送事件到 AstrBot"""
        if not self._ws or not self._running:
            logger.warning("AstrBot 未连接，消息未发送")
            return
        try:
            # Bug9修复: 保存映射关系（直接用 user_id/group_id，不再依赖 echo）
            user_id = event_data.get("user_id", "")
            group_id = event_data.get("group_id", "")
            if user_id:
                self._user_id_to_wxid[user_id] = user_id  # V12中 wxid 就是 user_id
            if group_id:
                self._group_id_to_wxid[group_id] = group_id

            await self._ws.send(_json.dumps(event_data))
        except Exception as e:
            logger.error(f"发送消息到 AstrBot 失败: {e}")

    def set_handler(self, handler):
        """设置 WeChatServiceHandler 引用"""
        self._service_handler = handler

    def is_connected(self):
        # 至少检查 websocket 对象是否存在，这是最可靠的
        return self._ws is not None

    async def close(self):
        """关闭连接"""
        self._closed = True
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None


_astrbot_ws_client = None

def get_astrbot_ws_client():
    """获取 AstrBot WebSocket Client 单例"""
    global _astrbot_ws_client
    return _astrbot_ws_client

def set_astrbot_ws_client(client: AstrBotWsClient):
    """设置 AstrBot WebSocket Client 实例"""
    global _astrbot_ws_client
    _astrbot_ws_client = client


# ============================ 共享内存管理 ============================

class SharedMemoryManager:
    """共享内存管理器"""

    def __init__(self):
        # 加载系统库
        self.kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        self.ntdll = ctypes.WinDLL('ntdll', use_last_error=True)
        self._setup_api_types()

    def _setup_api_types(self):
        """设置API参数类型"""
        self.kernel32.CreateFileMappingA.argtypes = [
            wintypes.HANDLE,
            wintypes.LPCVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_char_p
        ]
        self.kernel32.CreateFileMappingA.restype = wintypes.HANDLE

        self.kernel32.MapViewOfFile.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_size_t
        ]
        self.kernel32.MapViewOfFile.restype = wintypes.LPVOID

        self.ntdll.memmove.argtypes = [
            wintypes.LPVOID,
            wintypes.LPCVOID,
            ctypes.c_size_t
        ]
        self.ntdll.memmove.restype = wintypes.LPVOID

        # 释放资源函数
        self.kernel32.UnmapViewOfFile.argtypes = [wintypes.LPVOID]
        self.kernel32.UnmapViewOfFile.restype = wintypes.BOOL
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL

    def create_and_write_shared_memory(self):
        """创建并写入共享内存"""
        # 1. 创建共享内存（33字节）
        h_map = self.kernel32.CreateFileMappingA(
            INVALID_HANDLE_VALUE,
            None,
            PAGE_READWRITE,
            0,
            SHARED_MEM_SIZE,
            b"windows_shell_global__"
        )

        if not h_map or h_map == wintypes.HANDLE(0).value:
            error_code = ctypes.get_last_error()
            logger.error(f"创建映射文件失败，错误码: {error_code}")
            return False

        try:
            # 2. 映射到内存
            data_addr = self.kernel32.MapViewOfFile(
                h_map,
                FILE_MAP_ALL_ACCESS,
                0, 0, 0
            )

            if not data_addr:
                error_code = ctypes.get_last_error()
                logger.error(f"映射到内存失败，错误码: {error_code}")
                return False

            # 3. 准备并写入数据
            key_str = "3101b223dca7715b0154924f0eeeee20"
            key_bytes = key_str.encode('ascii')  # 编码后为32字节

            # 补充1个字节，确保总长度为33
            if len(key_bytes) == 32:
                key_bytes += b'\x00'

            # 验证长度
            if len(key_bytes) != SHARED_MEM_SIZE:
                logger.error(f"数据长度错误，应为{SHARED_MEM_SIZE}字节，实际为{len(key_bytes)}字节")
                return False

            # 4. 写入共享内存
            self.ntdll.memmove(data_addr, key_bytes, len(key_bytes))
            logger.info("共享内存写入成功")
            return True

        finally:
            pass


# ============================ 消息类型定义 ============================

class MessageType:
    """消息类型常量"""
    MT_DEBUG_LOG = 11024
    MT_USER_LOGIN = 11025
    MT_USER_LOGOUT = 11026
    MT_SEND_TEXTMSG = 11036


# ============================ 回调系统 ============================

# 全局回调列表
_GLOBAL_CONNECT_CALLBACK_LIST = []
_GLOBAL_RECV_CALLBACK_LIST = []
_GLOBAL_CLOSE_CALLBACK_LIST = []


def CONNECT_CALLBACK(in_class=False):
    """连接回调装饰器"""

    def decorator(f):
        wraps(f)
        if in_class:
            f._wx_connect_handled = True
        else:
            _GLOBAL_CONNECT_CALLBACK_LIST.append(f)
        return f

    return decorator


def RECV_CALLBACK(in_class=False):
    """接收消息回调装饰器"""

    def decorator(f):
        wraps(f)
        if in_class:
            f._wx_recv_handled = True
        else:
            _GLOBAL_RECV_CALLBACK_LIST.append(f)
        return f

    return decorator


def CLOSE_CALLBACK(in_class=False):
    """关闭连接回调装饰器"""

    def decorator(f):
        wraps(f)
        if in_class:
            f._wx_close_handled = True
        else:
            _GLOBAL_CLOSE_CALLBACK_LIST.append(f)
        return f

    return decorator


def add_callback_handler(callbackHandler):
    """添加回调处理器"""
    for dummy, handler in inspect.getmembers(callbackHandler, callable):
        if hasattr(handler, '_wx_connect_handled'):
            _GLOBAL_CONNECT_CALLBACK_LIST.append(handler)
        elif hasattr(handler, '_wx_recv_handled'):
            _GLOBAL_RECV_CALLBACK_LIST.append(handler)
        elif hasattr(handler, '_wx_close_handled'):
            _GLOBAL_CLOSE_CALLBACK_LIST.append(handler)

# 专门拦截群成员变化的缓存
_GROUP_MEMBER_CACHE = {}  # format: {room_wxid: {wxid: {"nickname": str, "display_name": str, "avatar": str}}}
_USER_ALIAS_CACHE = None
_GROUP_MEMBER_CACHE_FILE = os.path.join(os.path.dirname(__file__), "data", "group_members.json")
_GROUP_MEMBER_ALIASES_CACHE = None
_GROUP_MEMBER_ALIASES_FILE = os.path.join(os.path.dirname(__file__), "data", "group_member_aliases.json")

def _load_group_member_cache() -> None:
    try:
        if not os.path.exists(_GROUP_MEMBER_CACHE_FILE):
            return
        with open(_GROUP_MEMBER_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        groups = data.get("groups", data) if isinstance(data, dict) else {}
        if not isinstance(groups, dict):
            return
        for room_wxid, members in groups.items():
            if not isinstance(members, dict):
                continue
            room_cache = _GROUP_MEMBER_CACHE.setdefault(str(room_wxid), {})
            for wxid, info in members.items():
                if isinstance(info, dict):
                    room_cache[str(wxid)] = {
                        "nickname": str(info.get("nickname", "")),
                        "display_name": str(info.get("display_name", "")),
                        "avatar": str(info.get("avatar", "")),
                        "remark": str(info.get("remark", "")),
                    }
        logger.info(f"已加载群成员昵称缓存: {len(_GROUP_MEMBER_CACHE)} 个群")
    except Exception as e:
        logger.warning(f"加载群成员昵称缓存失败: {e}")

def _save_group_member_cache() -> None:
    try:
        os.makedirs(os.path.dirname(_GROUP_MEMBER_CACHE_FILE), exist_ok=True)
        payload = {
            "updated_at": int(time.time()),
            "groups": _GROUP_MEMBER_CACHE,
        }
        tmp_path = _GROUP_MEMBER_CACHE_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp_path, _GROUP_MEMBER_CACHE_FILE)
    except Exception as e:
        logger.warning(f"保存群成员昵称缓存失败: {e}")

def _member_display_name(info: dict) -> str:
    if not isinstance(info, dict):
        return ""
    return info.get("display_name") or info.get("nickname") or info.get("remark") or ""

def _member_identity(info: dict, fallback: dict | None = None) -> dict:
    fallback = fallback if isinstance(fallback, dict) else {}
    info = info if isinstance(info, dict) else {}
    nickname = str(info.get("nickname") or fallback.get("nickname") or "")
    display_name = str(info.get("display_name") or fallback.get("display_name") or "")
    avatar = str(info.get("avatar") or fallback.get("avatar") or "")
    return {
        "nickname": nickname,
        "display_name": display_name,
        "avatar": avatar,
        "remark": str(info.get("remark") or fallback.get("remark") or ""),
    }

def _upsert_group_member(room_wxid: str, member: dict) -> None:
    if not room_wxid or not isinstance(member, dict):
        return
    wxid = str(member.get("wxid", ""))
    if not wxid:
        return
    existing = _GROUP_MEMBER_CACHE.setdefault(room_wxid, {}).get(wxid, {})
    if not isinstance(existing, dict):
        existing = {}
    _GROUP_MEMBER_CACHE[room_wxid][wxid] = {
        "nickname": str(member.get("nickname", existing.get("nickname", "")) or ""),
        "display_name": str(member.get("display_name", existing.get("display_name", "")) or ""),
        "avatar": str(member.get("avatar", existing.get("avatar", "")) or ""),
        "remark": str(member.get("remark", existing.get("remark", "")) or ""),
    }

def _cache_group_list(groups) -> int:
    if isinstance(groups, dict):
        groups = groups.get("data", [])
    if not isinstance(groups, list):
        return 0
    count = 0
    for group in groups:
        if not isinstance(group, dict):
            continue
        room_wxid = str(group.get("wxid", "") or group.get("room_wxid", ""))
        if not room_wxid.endswith("@chatroom"):
            continue
        _GROUP_MEMBER_CACHE.setdefault(room_wxid, {})
        for wxid in group.get("member_list", []) or []:
            if wxid:
                _GROUP_MEMBER_CACHE[room_wxid].setdefault(str(wxid), {})
        count += 1
    if count:
        _save_group_member_cache()
    return count

def _cache_group_members(data: dict) -> tuple[str, int]:
    if not isinstance(data, dict):
        return "", 0
    room_wxid = str(data.get("group_wxid", "") or data.get("room_wxid", ""))
    member_list = data.get("member_list", [])
    if not room_wxid or not isinstance(member_list, list):
        return room_wxid, 0
    _GROUP_MEMBER_CACHE[room_wxid] = {}
    for mem in member_list:
        _upsert_group_member(room_wxid, mem)
    _save_group_member_cache()
    return room_wxid, len(member_list)

def _load_user_aliases() -> dict:
    global _USER_ALIAS_CACHE
    if _USER_ALIAS_CACHE is not None:
        return _USER_ALIAS_CACHE
    alias_path = os.path.join(os.path.dirname(__file__), "wechat_user_aliases.json")
    try:
        with open(alias_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _USER_ALIAS_CACHE = {str(k): str(v) for k, v in data.items() if k and v}
    except Exception as e:
        logger.warning(f"加载微信用户别名失败: {e}")
        _USER_ALIAS_CACHE = {}
    return _USER_ALIAS_CACHE

def _load_group_member_aliases() -> dict:
    global _GROUP_MEMBER_ALIASES_CACHE
    if _GROUP_MEMBER_ALIASES_CACHE is not None:
        return _GROUP_MEMBER_ALIASES_CACHE
    try:
        if not os.path.exists(_GROUP_MEMBER_ALIASES_FILE):
            _GROUP_MEMBER_ALIASES_CACHE = {"groups": {}, "global": {}}
            return _GROUP_MEMBER_ALIASES_CACHE
        with open(_GROUP_MEMBER_ALIASES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
        groups = data.get("groups", {})
        global_aliases = data.get("global", {})
        if not isinstance(groups, dict):
            groups = {}
        if not isinstance(global_aliases, dict):
            global_aliases = {}
        _GROUP_MEMBER_ALIASES_CACHE = {"groups": groups, "global": global_aliases}
    except Exception as e:
        logger.warning(f"加载群成员外号失败: {e}")
        _GROUP_MEMBER_ALIASES_CACHE = {"groups": {}, "global": {}}
    return _GROUP_MEMBER_ALIASES_CACHE

def _normalize_aliases(aliases) -> list[str]:
    if isinstance(aliases, str):
        aliases = re.split(r"[\s,，、]+", aliases)
    if not isinstance(aliases, list):
        return []
    normalized = []
    seen = set()
    for alias in aliases:
        value = str(alias or "").strip()
        if not value or value in seen:
            continue
        normalized.append(value)
        seen.add(value)
    return normalized

def _save_group_member_aliases() -> None:
    try:
        data = _load_group_member_aliases()
        os.makedirs(os.path.dirname(_GROUP_MEMBER_ALIASES_FILE), exist_ok=True)
        payload = {
            "updated_at": int(time.time()),
            "groups": data.get("groups", {}),
            "global": data.get("global", {}),
        }
        tmp_path = _GROUP_MEMBER_ALIASES_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp_path, _GROUP_MEMBER_ALIASES_FILE)
    except Exception as e:
        logger.warning(f"保存群成员外号失败: {e}")

def _get_member_call_aliases(room_wxid: str, wxid: str) -> list[str]:
    if not wxid:
        return []
    data = _load_group_member_aliases()
    groups = data.get("groups", {})
    global_aliases = data.get("global", {})
    aliases = []
    if room_wxid and isinstance(groups, dict):
        room_aliases = groups.get(room_wxid, {})
        if isinstance(room_aliases, dict):
            aliases.extend(_normalize_aliases(room_aliases.get(wxid, [])))
    if isinstance(global_aliases, dict):
        aliases.extend(_normalize_aliases(global_aliases.get(wxid, [])))
    return _normalize_aliases(aliases)

def _set_member_call_aliases(room_wxid: str, wxid: str, aliases: list[str]) -> list[str]:
    if not room_wxid or not wxid:
        return []
    normalized = _normalize_aliases(aliases)
    data = _load_group_member_aliases()
    groups = data.setdefault("groups", {})
    room_aliases = groups.setdefault(room_wxid, {})
    if normalized:
        room_aliases[wxid] = normalized
    else:
        room_aliases.pop(wxid, None)
    if not room_aliases:
        groups.pop(room_wxid, None)
    _save_group_member_aliases()
    return normalized

def _get_group_alias_summary(room_wxid: str = "") -> dict:
    data = _load_group_member_aliases()
    groups = data.get("groups", {})
    if room_wxid:
        return groups.get(room_wxid, {}) if isinstance(groups, dict) else {}
    return groups if isinstance(groups, dict) else {}

def _lookup_member_nickname(room_wxid: str, wxid: str) -> str:
    """从群成员缓存中查找昵称，找不到返回空字符串"""
    if room_wxid and wxid and room_wxid in _GROUP_MEMBER_CACHE:
        entry = _GROUP_MEMBER_CACHE[room_wxid].get(wxid)
        if isinstance(entry, dict):
            nickname = _member_display_name(entry)
            if nickname:
                return nickname
    return _load_user_aliases().get(wxid, "")

def _build_sender_info(wxid: str, room_wxid: str = "", fallback_nickname: str = "") -> dict:
    nickname = _lookup_member_nickname(room_wxid, wxid) or fallback_nickname or ""
    return {
        "user_id": wxid,
        "nickname": nickname,
        "card": nickname,
        "wx_nickname": nickname,
        "call_aliases": _get_member_call_aliases(room_wxid, wxid),
    }

def _is_command_message(text: str) -> bool:
    stripped = (text or "").lstrip()
    return stripped.startswith(("/", "\\", "#"))

def _with_sender_identity(text: str, wxid: str, room_wxid: str = "") -> str:
    if not room_wxid or _is_command_message(text):
        return text
    nickname = _lookup_member_nickname(room_wxid, wxid)
    alias_hint = f"，昵称/群名：{nickname}" if nickname else ""
    call_aliases = _get_member_call_aliases(room_wxid, wxid)
    call_alias_hint = f" 可选称呼外号：{'、'.join(call_aliases)}；可以自然选择其中一个称呼，但不要把外号当成身份依据；回复里称呼这个人时，外号和昵称/群名二选一，不要同时出现。" if call_aliases else ""
    if wxid == "fengchenhao002":
        identity_rule = "当前发言者是祈，也是姜小妹的哥哥，可以自然称呼哥。"
    else:
        identity_rule = "当前发言者不是祈，也不是姜小妹的哥哥；不要称呼为祈、祈哥、哥。"
    prefix = (
        f"[系统身份提示：当前发言者 wxid={wxid}{alias_hint}。"
        f"{identity_rule}{call_alias_hint} 回答身份问题时必须以这个 wxid/昵称为准，"
        "不要用群里其他人的记忆替代当前发言者。]\n"
    )
    return prefix + (text or "")

_load_group_member_cache()

# C 回调函数
@WINFUNCTYPE(None, ctypes.c_void_p)
def wechat_connect_callback(client_id):
    """微信连接回调"""
    for func in _GLOBAL_CONNECT_CALLBACK_LIST:
        func(client_id)


@WINFUNCTYPE(None, ctypes.c_long, ctypes.c_char_p, ctypes.c_ulong)
def wechat_recv_callback(client_id, data, length):
    """微信接收消息回调"""
    data = copy.deepcopy(data)
    json_data = data.decode('utf-8')
    dict_data = json.loads(json_data)
    for func in _GLOBAL_RECV_CALLBACK_LIST:
        func(client_id, dict_data.get('type'), dict_data)


@WINFUNCTYPE(None, ctypes.c_ulong)
def wechat_close_callback(client_id):
    """微信关闭连接回调"""
    for func in _GLOBAL_CLOSE_CALLBACK_LIST:
        func(client_id)


# ============================ 回调处理器 ============================

class WeChatServiceHandler:
    """微信服务回调处理器"""

    def __init__(self, service):
        self.service = service
        self.connected_clients = set()
        self._pending_cdn = {}  # trace_id -> {"event": threading.Event, "result": None}
        self._pending_image_quotes = {}  # trace_id -> dict (image quote context waiting for CDN download)
        self._pending_voice_downloads = {}  # trace_id -> dict (voice CDN download context)
        self._pending_group_refresh = {}  # trace_id -> {"event": threading.Event, "data": dict}
        self._last_voice_raw_msg = ""
        self._last_voice_raw_path = os.path.join(os.path.dirname(__file__), "data", "voice", "last_voice_raw.xml")
        self._last_appmsg_raw_msg = ""
        self._last_appmsg_raw_path = os.path.join(os.path.dirname(__file__), "data", "appmsg", "last_11061_raw.xml")
        self._last_appmsg_json_path = os.path.join(os.path.dirname(__file__), "data", "appmsg", "last_11061.json")
        self._last_appmsg_by_type = {}
        self._filehelper_msgs = []  # 缓存发给文件助手及收到的消息 (最多100条)

    def _cache_filehelper_msg(self, sender: str, content: str, direction: str = "in"):
        """缓存文件助手消息"""
        from datetime import datetime
        self._filehelper_msgs.append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "sender": sender,
            "content": content[:500],
            "direction": direction,  # "in"=收到, "out"=发出
        })
        if len(self._filehelper_msgs) > 100:
            self._filehelper_msgs = self._filehelper_msgs[-100:]

    def get_filehelper_msgs(self, count: int = 2) -> list:
        return self._filehelper_msgs[-count:]

    def refresh_group_members(self, room_wxid: str | None = None) -> dict:
        """Trigger 11032 group member refresh; returns current cache summary."""
        if room_wxid:
            self.service.helper_get_group_member_list("", room_wxid)
            return {
                "requested": [room_wxid],
                "cached_groups": len(_GROUP_MEMBER_CACHE),
                "cached_members": len(_GROUP_MEMBER_CACHE.get(room_wxid, {})),
            }

        self.service.helper_get_group_list("")
        rooms = sorted(_GROUP_MEMBER_CACHE.keys())
        for room in rooms:
            self.service.helper_get_group_member_list("", room)
            time.sleep(0.2)
        return {
            "requested": rooms,
            "cached_groups": len(_GROUP_MEMBER_CACHE),
            "cached_members": sum(len(members) for members in _GROUP_MEMBER_CACHE.values()),
        }

    def _request_group_member_refresh(self, room_wxid: str, reason: str = "") -> None:
        if not room_wxid or not hasattr(self.service, "helper_get_group_member_list"):
            return
        try:
            self.service.helper_get_group_member_list("", room_wxid)
            logger.info(f"已触发群成员刷新: room={room_wxid}, reason={reason}")
        except Exception as e:
            logger.error(f"触发群成员刷新失败: room={room_wxid}, reason={reason}, err={e}")

    def _refresh_group_members_sync(self, room_wxid: str, reason: str = "", timeout: float = 2.0) -> bool:
        if not room_wxid or not hasattr(self.service, "helper_get_group_member_list"):
            return False
        trace_id = str(_uuid.uuid4())
        event = threading.Event()
        self._pending_group_refresh[trace_id] = {"event": event, "data": None}
        try:
            ok = self.service.helper_get_group_member_list(trace_id, room_wxid)
            if not ok:
                self._pending_group_refresh.pop(trace_id, None)
                return False
            refreshed = event.wait(timeout)
            if not refreshed:
                logger.warning(f"等待群成员刷新超时: room={room_wxid}, reason={reason}, trace={trace_id}")
            return refreshed
        except Exception as e:
            logger.error(f"同步刷新群成员失败: room={room_wxid}, reason={reason}, err={e}")
            return False
        finally:
            self._pending_group_refresh.pop(trace_id, None)

    @staticmethod
    def _extract_room_wxid(data: dict) -> str:
        if not isinstance(data, dict):
            return ""
        for key in ("room_wxid", "group_wxid", "chatroom_wxid", "chatroom"):
            value = str(data.get(key, "") or "")
            if value.endswith("@chatroom"):
                return value
        for key in ("from_wxid", "to_wxid", "wxid"):
            value = str(data.get(key, "") or "")
            if value.endswith("@chatroom"):
                return value
        return ""

    @staticmethod
    def _extract_member_wxid(data: dict, room_wxid: str = "") -> str:
        if not isinstance(data, dict):
            return ""
        member_list = data.get("member_list")
        if isinstance(member_list, list) and len(member_list) == 1 and isinstance(member_list[0], dict):
            value = str(member_list[0].get("wxid", "") or member_list[0].get("user_id", "") or "")
            if value and value != room_wxid and not value.endswith("@chatroom"):
                return value
        direct_keys = (
            "member_wxid",
            "user_wxid",
            "user_id",
            "target_wxid",
            "from_wxid",
            "wxid",
            "username",
        )
        for key in direct_keys:
            value = str(data.get(key, "") or "")
            if value and value != room_wxid and not value.endswith("@chatroom"):
                return value
        raw = json.dumps(data, ensure_ascii=False)
        matches = [m for m in re.findall(r"wxid_[A-Za-z0-9_]+", raw) if m != room_wxid]
        unique = []
        for match in matches:
            if match not in unique:
                unique.append(match)
        return unique[0] if len(unique) == 1 else ""

    def _send_group_member_update_notice_after_refresh(
        self,
        astrbot_ws,
        bot_wxid: str,
        room_wxid: str,
        wxid: str,
        group_name: str,
        old_entry: dict,
        fallback_member: dict | None = None,
    ) -> None:
        def worker():
            refreshed = self._refresh_group_members_sync(room_wxid, "group member update card", timeout=2.0)
            cached_entry = _GROUP_MEMBER_CACHE.get(room_wxid, {}).get(wxid, {}) or fallback_member or {}
            if not cached_entry:
                logger.warning(
                    f"昵称修改卡片未发送：未拿到成员信息 room={room_wxid}, wxid={wxid}, refreshed={refreshed}"
                )
                return
            old_identity = _member_identity(old_entry)
            new_identity = _member_identity(cached_entry)
            if (
                old_identity["display_name"] == new_identity["display_name"]
                and old_identity["nickname"] == new_identity["nickname"]
            ):
                logger.info(f"昵称修改卡片未发送：刷新前后无变化 room={room_wxid}, wxid={wxid}")
                return
            if not astrbot_ws or not astrbot_ws.is_connected():
                return
            event_data = {
                "time": int(time.time()),
                "self_id": bot_wxid,
                "post_type": "notice",
                "notice_type": "group_member_update",
                "sub_type": "profile",
                "group_id": room_wxid,
                "user_id": wxid,
                "operator_id": wxid,
                "wx_old_nickname": old_identity["nickname"],
                "wx_old_display_name": old_identity["display_name"],
                "wx_nickname": new_identity["nickname"],
                "wx_display_name": new_identity["display_name"],
                "wx_group_name": group_name or "本群",
                "wx_avatar": new_identity["avatar"],
            }
            asyncio.run_coroutine_threadsafe(astrbot_ws.send_event(event_data), astrbot_ws._loop)

        threading.Thread(target=worker, daemon=True).start()

    def _send_group_increase_notice_after_refresh(
        self,
        astrbot_ws,
        bot_wxid: str,
        room_wxid: str,
        wxid: str,
        invite_by: str,
        group_name: str,
        fallback_member: dict,
        fallback_avatar: str = "",
    ) -> None:
        def worker():
            refreshed = self._refresh_group_members_sync(room_wxid, "group increase card", timeout=2.0)
            cached_entry = _GROUP_MEMBER_CACHE.get(room_wxid, {}).get(wxid, {})
            if not refreshed or not cached_entry:
                logger.warning(
                    f"入群卡片未发送：未拿到 11032 成员信息 room={room_wxid}, wxid={wxid}, refreshed={refreshed}"
                )
                return
            member_identity = _member_identity(cached_entry, fallback_member)
            if not astrbot_ws or not astrbot_ws.is_connected():
                return
            event_data = {
                "time": int(time.time()),
                "self_id": bot_wxid,
                "post_type": "notice",
                "notice_type": "group_increase",
                "sub_type": "approve",
                "group_id": room_wxid,
                "user_id": wxid,
                "operator_id": invite_by or 0,
                "wx_nickname": member_identity["nickname"] or fallback_member.get("nickname", ""),
                "wx_display_name": member_identity["display_name"],
                "wx_group_name": group_name,
                "wx_avatar": member_identity["avatar"] or fallback_avatar,
            }
            asyncio.run_coroutine_threadsafe(astrbot_ws.send_event(event_data), astrbot_ws._loop)

        threading.Thread(target=worker, daemon=True).start()

    @CONNECT_CALLBACK(in_class=True)
    def on_connect(self, client_id):
        """客户端连接回调"""
        self.connected_clients.add(client_id)
        logger.info(f"客户端 {client_id} 已连接，当前连接数: {len(self.connected_clients)}")

    @RECV_CALLBACK(in_class=True)
    def on_receive(self, client_id, message_type, dict_data):
        """接收消息回调"""
        data = dict_data.get("data", {}) if isinstance(dict_data, dict) else {}

        if message_type in (11030, 11031):
            cached_count = _cache_group_list(data)
            if cached_count:
                logger.info(f"已缓存群聊列表: {cached_count} 个群")
        elif message_type == 11032 and isinstance(data, dict):
            room_wxid, member_count = _cache_group_members(data)
            if room_wxid and member_count:
                logger.info(f"已从 11032 更新群 {room_wxid} 的 {member_count} 个成员昵称缓存")
            if isinstance(dict_data, dict):
                trace_id = dict_data.get("trace", "")
                pending = self._pending_group_refresh.get(trace_id)
                if pending:
                    pending["data"] = data
                    pending["event"].set()

        # 处理异步请求的回调
        astrbot_ws = get_astrbot_ws_client()
        if astrbot_ws and isinstance(dict_data, dict):
            trace_id = dict_data.get("trace", "")
            if trace_id:
                future = astrbot_ws.pending_requests.pop(trace_id, None)
                if future and not future.done() and astrbot_ws._loop:
                    astrbot_ws._loop.call_soon_threadsafe(future.set_result, data)
            if message_type in (11029, 11030, 11031, 11229):
                return  # 获取列表或用户信息响应/CDN上传响应，完成任务后结束流程，不进入下文分发

        # 记录 CDN 发送响应（用于调试语音发送）
        if message_type in (11232, 11234, 11236):
            logger.info(f"[CDN SEND RESP] type={message_type}, data={json.dumps(data, ensure_ascii=False)[:500]}")
            return

        if message_type == 11032:
            if astrbot_ws and isinstance(dict_data, dict):
                trace_id = dict_data.get("trace", "")
                if trace_id:
                    future = astrbot_ws.pending_requests.pop(trace_id, None)
                    if future and not future.done() and astrbot_ws._loop:
                        astrbot_ws._loop.call_soon_threadsafe(future.set_result, data)
            return

        logger.info(f"收到来自客户端 {client_id} 的消息 - 类型: {message_type}, 数据大小: {len(str(data))}")
        # 处理不同类型的消息
        if message_type == 11098:
            # 群成员新增或改名
            # data: {"room_wxid": ..., "member_list": [{"wxid": "...", "nickname": "...", "invite_by": "..."}], "nickname": "..."(group name)}
            room_wxid = data.get("room_wxid", "")
            group_name = data.get("nickname", "")
            member_list = data.get("member_list", [])
            
            if room_wxid not in _GROUP_MEMBER_CACHE:
                _GROUP_MEMBER_CACHE[room_wxid] = {}
                
            astrbot_ws = get_astrbot_ws_client()
            bot_wxid = astrbot_ws._bot_wxid if astrbot_ws else ""
            
            for member in member_list:
                wxid = member.get("wxid", "")
                nickname = member.get("nickname", "")
                invite_by = member.get("invite_by", "")
                
                old_entry = _GROUP_MEMBER_CACHE[room_wxid].get(wxid)
                old_nickname = _member_display_name(old_entry) if isinstance(old_entry, dict) else old_entry
                old_avatar = old_entry.get("avatar", "") if isinstance(old_entry, dict) else ""
                _upsert_group_member(room_wxid, member)
                member_identity = _member_identity(_GROUP_MEMBER_CACHE[room_wxid].get(wxid), member)
                new_nickname = _lookup_member_nickname(room_wxid, wxid) or nickname
                
                if old_nickname is not None:
                    # 已经在缓存中，说明仅仅是改名
                    if old_nickname != nickname:
                        msg = f"系统通知：群成员 {old_nickname} (WxID: {wxid}) 偷偷把群昵称改成了 {nickname}"
                        if astrbot_ws and astrbot_ws.send_text_fn:
                            astrbot_ws.send_text_fn(to_wxid=room_wxid, content=msg)
                    self._request_group_member_refresh(room_wxid, "11098 member profile update")
                else:
                    # 新人入群
                    if astrbot_ws and astrbot_ws.is_connected():
                        self._send_group_increase_notice_after_refresh(
                            astrbot_ws=astrbot_ws,
                            bot_wxid=bot_wxid,
                            room_wxid=room_wxid,
                            wxid=wxid,
                            invite_by=invite_by,
                            group_name=group_name,
                            fallback_member=member,
                            fallback_avatar=old_avatar,
                        )
            _save_group_member_cache()
        
        elif message_type == 11200:
            # 群内成员修改群昵称/资料等轻量通知，收到后刷新 11032 覆盖本地缓存。
            room_wxid = self._extract_room_wxid(data)
            member_wxid = self._extract_member_wxid(data, room_wxid)
            member_list = data.get("member_list")
            fallback_member = (
                member_list[0]
                if isinstance(member_list, list) and len(member_list) == 1 and isinstance(member_list[0], dict)
                else {}
            )
            if room_wxid:
                astrbot_ws = get_astrbot_ws_client()
                bot_wxid = astrbot_ws._bot_wxid if astrbot_ws else ""
                old_entry = copy.deepcopy(_GROUP_MEMBER_CACHE.get(room_wxid, {}).get(member_wxid, {}))
                if member_wxid and astrbot_ws and astrbot_ws.is_connected():
                    self._send_group_member_update_notice_after_refresh(
                        astrbot_ws=astrbot_ws,
                        bot_wxid=bot_wxid,
                        room_wxid=room_wxid,
                        wxid=member_wxid,
                        group_name=str(data.get("nickname", "") or data.get("group_name", "") or ""),
                        old_entry=old_entry,
                        fallback_member=fallback_member,
                    )
                else:
                    self._request_group_member_refresh(room_wxid, "11200 member display-name update")
                    logger.info(
                        f"[11200] 已刷新但未发修改卡片: room={room_wxid}, member={member_wxid}, "
                        f"raw={json.dumps(data, ensure_ascii=False)[:500]}"
                    )
            else:
                logger.info(f"[11200] 未识别群号，原始数据: {json.dumps(data, ensure_ascii=False)[:500]}")

        elif message_type in (11099, 11101):
            # 群成员删除或离开
            room_wxid = data.get("room_wxid", "")
            group_name = data.get("nickname", "")
            member_list = data.get("member_list", [])
            
            astrbot_ws = get_astrbot_ws_client()
            bot_wxid = astrbot_ws._bot_wxid if astrbot_ws else ""
            
            if member_list:
                for member in member_list:
                    wxid = member.get("wxid", "")
                    nickname = member.get("nickname", "")
                    
                    # 在删除缓存之前，先取出群备注、本身昵称和头像
                    member_identity = _member_identity({}, member)
                    if room_wxid in _GROUP_MEMBER_CACHE and wxid in _GROUP_MEMBER_CACHE[room_wxid]:
                        cached_entry = _GROUP_MEMBER_CACHE[room_wxid][wxid]
                        if isinstance(cached_entry, dict):
                            member_identity = _member_identity(cached_entry, member)
                        del _GROUP_MEMBER_CACHE[room_wxid][wxid]
                        _save_group_member_cache()
                        
                    if astrbot_ws and astrbot_ws.is_connected():
                        import time as _time_dec
                        event_data = {
                            "time": int(_time_dec.time()),
                            "self_id": bot_wxid,
                            "post_type": "notice",
                            "notice_type": "group_decrease",
                            "sub_type": "leave" if message_type == 11099 else "kick",
                            "group_id": room_wxid,
                            "user_id": wxid,
                            "operator_id": data.get("manager_wxid", 0),
                            "wx_nickname": member_identity["nickname"] or nickname,
                            "wx_display_name": member_identity["display_name"],
                            "wx_group_name": group_name,
                            "wx_avatar": member_identity["avatar"],
                        }
                        asyncio.run_coroutine_threadsafe(astrbot_ws.send_event(event_data), astrbot_ws._loop)
            self._request_group_member_refresh(room_wxid, "group decrease after notice")

        elif message_type == MessageType.MT_USER_LOGIN:
            logger.info(f"用户登录: {data}")
            # 从登录消息中提取机器人自己的 wxid 和昵称
            wxid = ""
            nickname = ""
            avatar = ""
            if isinstance(data, dict):
                wxid = data.get("wxid", "")
                nickname = data.get("nickname", "")
                avatar = data.get("avatar", "")
            elif isinstance(data, str):
                import json as json_module
                try:
                    login_data = json_module.loads(data)
                    wxid = login_data.get("wxid", "")
                    nickname = login_data.get("nickname", "")
                    avatar = login_data.get("avatar", "")
                except:
                    pass
            self.service.bot_wxid = wxid
            self.service.bot_nickname = nickname
            self.service.bot_avatar = avatar
            if isinstance(data, dict) and data.get("pid"):
                self.service.target_pid = int(data["pid"])
                logger.info(f"记录目标微信 PID: {self.service.target_pid}")
            logger.info(f"机器人 wxid: {wxid}, 昵称: {nickname}")

            # 同步更新 astrbot_ws_client，重连以注册正确的 X-Self-ID
            astrbot_ws = get_astrbot_ws_client()
            if astrbot_ws:
                astrbot_ws._bot_wxid = wxid
                astrbot_ws._bot_nickname = nickname
                if astrbot_ws._loop:
                    asyncio.run_coroutine_threadsafe(
                        astrbot_ws.reconnect_with_wxid(),
                        astrbot_ws._loop
                    )
            # CDN 初始化（all.md: 收到登录消息后执行一次即可）
            self.service.helper_cdn_init()

            # 启动时预加载所有群成员昵称缓存
            def _prefetch_group_members():
                import time as _pt
                try:
                    # 先获取群列表
                    ok = self.service.helper_get_group_list("")
                    if not ok:
                        return
                    # 等待 11031 响应
                    deadline = _pt.time() + 5
                    while _pt.time() < deadline:
                        _pt.sleep(0.2)
                    # 对每个群请求成员详情（11032）
                    for room_wxid in list(_GROUP_MEMBER_CACHE.keys()) if _GROUP_MEMBER_CACHE else []:
                        self.service.helper_get_group_member_list("", room_wxid)
                        _pt.sleep(0.3)
                    logger.info(f"已触发预加载 {len(_GROUP_MEMBER_CACHE)} 个群的成员昵称缓存")
                except Exception as e:
                    logger.error(f"预加载群成员缓存失败: {e}")

            import threading
            threading.Thread(target=_prefetch_group_members, daemon=True).start()
        elif message_type == MessageType.MT_USER_LOGOUT:
            logger.info(f"用户登出: {data}")
        elif message_type == MessageType.MT_DEBUG_LOG:
            logger.debug(f"调试日志: {data}")
        elif message_type == 11030:
            print("收取群聊列表数据:",data)
        elif message_type == 11046:
            # 聊天消息，转发给 AI 处理
            self._handle_chat_message(data)
        elif message_type == 11048:
            # 语音消息，抓取本地 silk/slik 文件路径并转发给 AstrBot
            self._handle_voice_message(data)
        elif message_type == 11059:
            # 撤回消息通知
            logger.info(f"[撤回] from={data.get('from_wxid', '')} to={data.get('to_wxid', '')} "
                        f"room={data.get('room_wxid', '')} wx_type={data.get('wx_type', '')} "
                        f"raw_msg={data.get('raw_msg', '')}")
        elif message_type == 11061:
            # 引用消息（其他应用消息，wx_sub_type=57），解析后转发给 AI 处理
            self._cache_last_appmsg_raw_msg(data)
            self._handle_quoted_message(data)
        elif message_type == 11054:
            # App/music card raw payload, e.g. Qishui Music. Keep the original XML for source extraction.
            self._cache_last_appmsg_raw_msg(data, message_type=message_type)
        elif message_type == 11230:
            # CDN下载响应
            logger.info(f"[CDN 11230] 响应: error_code={data.get('error_code')}, file_size={data.get('file_size')}, save_path={data.get('save_path', '')[:80]}")
            trace_id = dict_data.get("trace", "")
            if trace_id and trace_id in self._pending_cdn:
                self._pending_cdn[trace_id]["result"] = data
                self._pending_cdn[trace_id]["event"].set()
            if trace_id and trace_id in self._pending_image_quotes:
                ctx = self._pending_image_quotes.pop(trace_id)
                self._forward_image_quote(ctx, data)
            if trace_id and trace_id in self._pending_voice_downloads:
                ctx = self._pending_voice_downloads.pop(trace_id)
                logger.info(
                    f"[VOICE CDN] trace={trace_id}, file_type={ctx.get('file_type')}, "
                    f"error_code={data.get('error_code')}, save_path={data.get('save_path', '')}, "
                    f"file_size={data.get('file_size')}"
                )

        # 处理 11047 发送响应（捕获 newMsgId 用于消息撤回）
        if message_type == 11047:
            logger.info(f"[SEND RESP 11047] data={json.dumps(data, ensure_ascii=False)[:500]}")
            # 11047 是微信“消息事件推送”，群友发的消息也会触发（is_pc=0）。
            # 只处理 bot 自己发出的（is_pc=1），否则别人消息的 msgid 会混进撤回队列导致撤错。
            is_pc = data.get("is_pc", 0)
            if is_pc != 1:
                return
            astrbot_ws = get_astrbot_ws_client()
            if not astrbot_ws:
                return
            trace_id = ""
            if isinstance(dict_data, dict):
                trace_id = dict_data.get("trace", "") or data.get("trace", "")
            new_msg_id = data.get("newMsgId") or data.get("msgid") or data.get("msg_id") or data.get("MsgSvrID") or data.get("msgSvrId")
            client_msgid = data.get("client_msgid", 0)
            create_time = data.get("create_time", 0)
            raw_msg = data.get("raw_msg", "") or ""
            is_image = "<img" in raw_msg
            if not new_msg_id:
                return
            # 图片发送：优先按 trace 绑定；没有 trace 时只在唯一候选下兜底，避免撤错图。
            if is_image and astrbot_ws._image_recall_fifo:
                now_ts = time.time()
                candidates = [
                    entry for entry in astrbot_ws._image_recall_fifo
                    if not entry.get("msgid") and now_ts - entry.get("ts", 0) <= 90
                ]
                entry = None
                if trace_id:
                    for candidate in candidates:
                        if candidate.get("trace") == trace_id:
                            entry = candidate
                            break
                elif len(candidates) == 1:
                    entry = candidates[0]
                if entry:
                    entry["msgid"] = str(new_msg_id)
                    entry["client_msgid"] = client_msgid
                    entry["create_time"] = create_time
                    logger.info(f"[SEND RESP] image bound: trace={entry.get('trace')}, to={entry.get('to_wxid')}, newMsgId={new_msg_id}")
                elif candidates:
                    logger.warning(f"[SEND RESP] image bind skipped: trace={trace_id}, candidates={len(candidates)}, newMsgId={new_msg_id}")
            # 兼容 recall_last_msg 等 API：仍写入共享队列（仅 bot 自身发送）
            if trace_id and trace_id in astrbot_ws._pending_send:
                pending_info = astrbot_ws._pending_send.pop(trace_id)
                to_wxid = pending_info.get("to_wxid", "")
                astrbot_ws._remember_sent_msg(to_wxid, {
                    "new_msgid": str(new_msg_id),
                    "client_msgid": client_msgid,
                    "create_time": create_time,
                    "type": pending_info.get("type", ""),
                    "trace": trace_id,
                })
                logger.info(f"[SEND RESP] trace={trace_id}, to={to_wxid}, newMsgId={new_msg_id}")
            else:
                to_wxid = getattr(astrbot_ws, "_last_send_to_wxid", "")
                if to_wxid:
                    astrbot_ws._remember_sent_msg(to_wxid, {
                        "new_msgid": str(new_msg_id),
                        "client_msgid": client_msgid,
                        "create_time": create_time,
                        "type": getattr(astrbot_ws, "_last_send_type", ""),
                        "trace": "",
                    })
                    logger.info(f"[SEND RESP] to={to_wxid}, newMsgId={new_msg_id}, type={getattr(astrbot_ws, '_last_send_type', '')}")

    def _parse_11061_xml(self, raw_msg: str) -> dict:
        """解析 11061 的 raw_msg XML，提取用户文本和引用消息信息"""
        result = {}
        if not raw_msg:
            return result
        title_match = re.search(r"<title>(.*?)</title>", raw_msg, re.DOTALL)
        if title_match:
            result["user_text"] = title_match.group(1).strip()
        if "<refermsg>" not in raw_msg:
            return result
        type_match = re.search(r"<refermsg>.*?<type>(.*?)</type>", raw_msg, re.DOTALL)
        if type_match:
            result["quote_type"] = int(type_match.group(1))
        content_match = re.search(r"<refermsg>.*?<content>(.*?)</content>", raw_msg, re.DOTALL)
        if content_match:
            result["quote_content"] = content_match.group(1).strip()
        display_match = re.search(r"<refermsg>.*?<displayname>(.*?)</displayname>", raw_msg, re.DOTALL)
        if display_match:
            result["quote_sender"] = display_match.group(1).strip()
        chatusr_match = re.search(r"<refermsg>.*?<chatusr>(.*?)</chatusr>", raw_msg, re.DOTALL)
        if chatusr_match:
            result["quote_sender_wxid"] = chatusr_match.group(1).strip()
        return result

    def _parse_forward_record(self, raw_msg: str) -> str | None:
        """从 raw_msg XML 中提取转发聊天记录的内容（<recorditem>），返回格式化文本"""
        if not raw_msg:
            return None
        if "<recorditem>" not in raw_msg:
            return None
        title_match = re.search(r"<title>(.*?)</title>", raw_msg, re.DOTALL)
        title = title_match.group(1).strip() if title_match else ""
        lines = []
        # 方式1: 解析 <recorditem> -> <dataitems> -> <dataitem>
        dataitems_match = re.search(r"<dataitems>(.*?)</dataitems>", raw_msg, re.DOTALL)
        if dataitems_match:
            items_block = dataitems_match.group(1)
            for item_match in re.finditer(r"<dataitem[^>]*>(.*?)</dataitem>", items_block, re.DOTALL):
                block = item_match.group(1)
                sender_match = re.search(r"<sourcename>(.*?)</sourcename>", block, re.DOTALL)
                content_match = re.search(r"<srcmsgcontent>(.*?)</srcmsgcontent>", block, re.DOTALL)
                sender = sender_match.group(1).strip() if sender_match else ""
                content = content_match.group(1).strip() if content_match else ""
                if content:
                    line = f"{sender}: {content}" if sender else content
                    lines.append(line)
        # 方式2: 如果 dataitems 为空，尝试从 <desc> 提取摘要
        if not lines:
            desc_match = re.search(r"<recorditem>.*?<desc>(.*?)</desc>", raw_msg, re.DOTALL)
            if desc_match:
                desc_text = desc_match.group(1).strip()
                if desc_text:
                    lines.append(desc_text)
        if not lines:
            return None
        header = "【转发聊天记录】"
        if title:
            header += f" {title}"
        return header + "\n" + "\n".join(lines)

    def _parse_image_cdn_info(self, xml_content: str) -> dict:
        """从引用图片的 XML 中提取 CDN 下载参数（多个尺寸的 file_id）"""
        xml_content = unescape(xml_content or "")
        aeskey = re.search(r'aeskey="([^"]*)"', xml_content)
        thumb = re.search(r'cdnthumburl="([^"]*)"', xml_content)
        mid = re.search(r'cdnmidimgurl="([^"]*)"', xml_content)
        big = re.search(r'cdnbigimgurl="([^"]*)"', xml_content)
        aes = aeskey.group(1) if aeskey else ""
        return {
            "aes_key": aes,
            "file_id_thumb": thumb.group(1) if thumb else "",   # file_type=3
            "file_id_mid": mid.group(1) if mid else "",           # file_type=2
            "file_id_big": big.group(1) if big else "",           # file_type=1
        }

    def _cdn_download_sync(self, file_id: str, aes_key: str, file_type: int = 2) -> str | None:
        """同步 CDN 下载图片，返回本地路径或 None"""
        import uuid as _uuid_mod
        save_dir = os.path.join(os.path.dirname(__file__), "data", "quoted_images")
        os.makedirs(save_dir, exist_ok=True)
        trace_id = str(_uuid_mod.uuid4())
        save_path = os.path.join(save_dir, f"quoted_{trace_id[:8]}.jpg")

        event = threading.Event()
        self._pending_cdn[trace_id] = {"event": event, "result": None}
        try:
            if hasattr(self.service, 'helper_cdn_download'):
                self.service.helper_cdn_download(file_id, aes_key, save_path, trace_id, file_type)
            if event.wait(timeout=30):
                result = self._pending_cdn.get(trace_id, {}).get("result", {})
                if result and result.get("error_code") == 0:
                    return result.get("save_path", save_path)
            else:
                logger.warning(f"[CDN] 下载超时: trace={trace_id}")
            return None
        except Exception as e:
            logger.error(f"[CDN] 下载异常: {e}")
            return None
        finally:
            self._pending_cdn.pop(trace_id, None)

    def _handle_quoted_message(self, data):
        """处理 11061 引用消息"""
        try:
            raw_msg = data.get("raw_msg", "")
            room_wxid = data.get("room_wxid", "")
            from_wxid = data.get("from_wxid", "")
            to_wxid = data.get("to_wxid", "")
            msgid = data.get("msgid", str(_uuid.uuid4()))

            parsed = self._parse_11061_xml(raw_msg)
            user_text = parsed.get("user_text", "")
            quote_type = parsed.get("quote_type", 0)
            quote_content = parsed.get("quote_content", "")
            quote_sender = parsed.get("quote_sender", "")
            quote_sender_wxid = parsed.get("quote_sender_wxid", "")

            if not user_text:
                logger.warning(f"[11061] 无法解析用户文本")
                return

            astrbot_ws = get_astrbot_ws_client()
            bot_wxid = (astrbot_ws._bot_wxid if astrbot_ws else "") or os.getenv("BOT_IDENTIFIER", "wechat_bot")
            if from_wxid == bot_wxid or data.get("isSendMsg") == 1:
                return

            is_group = bool(room_wxid)
            chat_id = room_wxid if is_group else from_wxid

            # 群聊中检测是否 @了机器人（11061 无 at_user_list，需从 raw_msg 和 user_text 判断）
            mentioned_bot = False
            if is_group and bot_wxid:
                if bot_wxid in raw_msg:
                    mentioned_bot = True
                bot_nick = getattr(self.service, 'bot_nickname', '')
                if bot_nick and bot_nick in user_text:
                    mentioned_bot = True

            if not astrbot_ws or not astrbot_ws.is_connected():
                logger.warning("AstrBot 未连接，引用消息未转发")
                return

            from datetime import datetime

            if quote_type == 3:
                # 图片引用 → 先发 CDN 下载请求（非阻塞），多级 fallback
                cdn_info = self._parse_image_cdn_info(quote_content)
                fallbacks = []
                if cdn_info["file_id_mid"]:
                    fallbacks.append((cdn_info["file_id_mid"], 2))   # 中图
                if cdn_info["file_id_big"]:
                    fallbacks.append((cdn_info["file_id_big"], 1))   # 原图
                if cdn_info["file_id_thumb"]:
                    fallbacks.append((cdn_info["file_id_thumb"], 3))  # 缩略图
                if cdn_info["aes_key"] and fallbacks:
                    import uuid as _uuid_mod
                    trace_id = str(_uuid_mod.uuid4())
                    save_dir = os.path.join(os.path.dirname(__file__), "data", "quoted_images")
                    os.makedirs(save_dir, exist_ok=True)
                    save_path = os.path.join(save_dir, f"quoted_{trace_id[:8]}.jpg")
                    self._pending_image_quotes[trace_id] = {
                        "user_text": user_text,
                        "from_wxid": from_wxid,
                        "room_wxid": room_wxid,
                        "to_wxid": to_wxid,
                        "msgid": msgid,
                        "quote_sender": quote_sender,
                        "quote_sender_wxid": quote_sender_wxid,
                        "quote_content": quote_content,
                        "is_group": is_group,
                        "chat_id": chat_id,
                        "bot_wxid": bot_wxid,
                        "mentioned_bot": mentioned_bot,
                        "aes_key": cdn_info["aes_key"],
                        "save_path": save_path,
                        "fallbacks": fallbacks,
                        "retry_index": 0,  # 从第一个开始
                    }
                    first_fid, first_ft = fallbacks[0]
                    self.service.helper_cdn_download(
                        first_fid, cdn_info["aes_key"], save_path, trace_id, file_type=first_ft
                    )
                else:
                    logger.warning(
                        f"[11061 IMAGE] 引用图片 CDN 参数不完整: "
                        f"aes_key={bool(cdn_info['aes_key'])}, file_ids={len(fallbacks)}, "
                        f"content_preview={quote_content[:120]}"
                    )
                return
            else:
                # 检查是否为转发聊天记录（无 refermsg，有 recorditem）
                forward_content = self._parse_forward_record(raw_msg)
                if forward_content:
                    quoted_text = forward_content
                    import json as _json_mod
                    quote_meta = _json_mod.dumps({
                        "qt": "text", "qs": quote_sender, "qsw": quote_sender_wxid, "qtxt": forward_content,
                    }, ensure_ascii=False)
                    alt_msg = f"{quote_meta}\n[转发 {quote_sender} 的消息:「{forward_content}」]\n{user_text}"
                    logger.info(f"[11061 FORWARD] 提取转发聊天记录 ({len(forward_content)} 字符): {forward_content[:100]}...")
                else:
                    # 文本引用 → 直接拼接上下文
                    quoted_text = quote_content
                    # 将 quote 数据嵌入 alt_message 首行（JSON），确保通过 pydantic 解析
                    import json as _json_mod
                    quote_meta = _json_mod.dumps({
                        "qt": "text", "qs": quote_sender, "qsw": quote_sender_wxid, "qtxt": quoted_text,
                    }, ensure_ascii=False)
                    alt_msg = f"{quote_meta}\n[引用 {quote_sender} 的消息:「{quoted_text}」]\n{user_text}"

            import time as _time_q
            message_segs = []
            if is_group and mentioned_bot:
                nick = _lookup_member_nickname(chat_id, bot_wxid)
                message_segs.append({"type": "at", "data": {"qq": bot_wxid, "name": nick}})
            event_text = _with_sender_identity(alt_msg, from_wxid, chat_id if is_group else "")
            message_segs.append({"type": "text", "data": {"text": event_text}})

            event_data = {
                "time": int(_time_q.time()),
                "self_id": bot_wxid,
                "post_type": "message",
                "message_type": "group" if is_group else "private",
                "sub_type": "normal" if is_group else "friend",
                "user_id": from_wxid,
                "message_id": astrbot_ws._next_msg_id(),
                "message": message_segs,
                "raw_message": event_text,
                "sender": _build_sender_info(from_wxid, chat_id if is_group else ""),
            }
            if is_group:
                event_data["group_id"] = chat_id

            try:
                if astrbot_ws._loop:
                    future = asyncio.run_coroutine_threadsafe(
                        astrbot_ws.send_event(event_data),
                        astrbot_ws._loop
                    )
                    future.result(timeout=30)
            except Exception as e:
                logger.error(f"转发引用消息到 AstrBot 失败: {e}")

        except Exception as e:
            logger.error(f"处理引用消息失败: {e}")

    def _forward_image_quote(self, ctx: dict, cdn_result: dict):
        """CDN 下载完成后，构造并转发图片引用事件到 AstrBot（失败时自动降级重试）"""
        try:
            if cdn_result.get("error_code") != 0:
                fallbacks = ctx.get("fallbacks", [])
                retry_index = ctx.get("retry_index", 0) + 1
                if retry_index < len(fallbacks):
                    ctx["retry_index"] = retry_index
                    fid, ftype = fallbacks[retry_index]
                    logger.info(f"[CDN] 降级重试: file_type={ftype}, index={retry_index}")
                    new_trace = str(_uuid.uuid4())
                    self._pending_image_quotes[new_trace] = ctx
                    self.service.helper_cdn_download(
                        fid, ctx["aes_key"], ctx["save_path"], new_trace, file_type=ftype
                    )
                    return
                else:
                    logger.warning(f"[CDN] 所有降级均失败，图片可能已过期")

            astrbot_ws = get_astrbot_ws_client()
            if not astrbot_ws or not astrbot_ws.is_connected():
                logger.warning("AstrBot 未连接，图片引用事件未转发")
                return

            local_path = None
            if cdn_result.get("error_code") == 0:
                local_path = cdn_result.get("save_path", "")

            import json as _json_mod
            import time as _time_img
            quote_meta = _json_mod.dumps({
                "qt": "img", "qs": ctx["quote_sender"], "qsw": ctx["quote_sender_wxid"],
                "qp": local_path or "",
            }, ensure_ascii=False)
            alt_msg = f"{quote_meta}\n[引用 {ctx['quote_sender']} 的图片]\n{ctx['user_text']}"

            message_segs = []
            if ctx["is_group"] and ctx.get("mentioned_bot"):
                nick = _lookup_member_nickname(ctx.get("chat_id", ""), ctx["bot_wxid"])
                message_segs.append({"type": "at", "data": {"qq": ctx["bot_wxid"], "name": nick}})
            if local_path and os.path.exists(local_path):
                message_segs.append({"type": "image", "data": {"file": f"file:///{local_path.replace(os.sep, '/')}"}})
            event_text = _with_sender_identity(
                alt_msg, ctx["from_wxid"], ctx["chat_id"] if ctx["is_group"] else ""
            )
            message_segs.append({"type": "text", "data": {"text": event_text}})

            event_data = {
                "time": int(_time_img.time()),
                "self_id": ctx["bot_wxid"],
                "post_type": "message",
                "message_type": "group" if ctx["is_group"] else "private",
                "sub_type": "normal" if ctx["is_group"] else "friend",
                "user_id": ctx["from_wxid"],
                "message_id": astrbot_ws._next_msg_id(),
                "message": message_segs,
                "raw_message": event_text,
                "sender": _build_sender_info(
                    ctx["from_wxid"], ctx["chat_id"] if ctx["is_group"] else ""
                ),
            }
            if ctx["is_group"]:
                event_data["group_id"] = ctx["chat_id"]

            if astrbot_ws._loop:
                future = asyncio.run_coroutine_threadsafe(
                    astrbot_ws.send_event(event_data), astrbot_ws._loop
                )
                future.result(timeout=10)
            logger.info(f"[11061 IMAGE] 图片引用事件已转发, local_path={local_path}")
        except Exception as e:
            logger.error(f"转发图片引用事件失败: {e}")

    def _handle_chat_message(self, data):
        """处理聊天消息，调用 AI 生成回复"""
        try:
            from_wxid = data.get("from_wxid", "")
            to_wxid = data.get("to_wxid", "")
            msg = data.get("msg", "")
            room_wxid = data.get("room_wxid", "")

            if not msg:
                return

            astrbot_ws = get_astrbot_ws_client()
            bot_wxid = (astrbot_ws._bot_wxid if astrbot_ws else "") or os.getenv("BOT_IDENTIFIER", "wechat_bot")

            # 缓存文件助手相关消息
            if "filehelper" in (from_wxid, to_wxid):
                self._cache_filehelper_msg(
                    sender="文件助手" if from_wxid == "filehelper" else from_wxid,
                    content=msg,
                    direction="out" if from_wxid == bot_wxid else "in"
                )

            if from_wxid == bot_wxid or data.get("isSendMsg") == 1:
                return

            is_group = bool(room_wxid)
            chat_id = room_wxid if is_group else from_wxid

            admin_ids = {
                item.strip()
                for item in os.getenv("ADMIN_WXID", "fengchenhao002").split(",")
                if item.strip()
            }
            if msg.strip().startswith("#刷新群成员缓存") and from_wxid in admin_ids:
                target_room = None if "全量" in msg else (room_wxid if is_group else None)
                result = self.refresh_group_members(target_room)
                target = chat_id
                self.service.helper_send_text(
                    target,
                    f"群成员缓存刷新已触发：群={result['cached_groups']}，成员={result['cached_members']}",
                )
                return

            if msg.strip().startswith(("#设置外号", "#查看外号", "#删除外号", "#清除外号")) and from_wxid in admin_ids:
                target = chat_id
                parts = msg.strip().split()
                command = parts[0] if parts else ""
                at_user_list = [str(x) for x in data.get("at_user_list", []) if x]
                at_user_list = [x for x in at_user_list if x != bot_wxid]
                if not is_group:
                    self.service.helper_send_text(target, "外号只支持在群聊里设置。")
                    return

                if command == "#查看外号":
                    target_wxid = parts[1] if len(parts) > 1 else (at_user_list[0] if at_user_list else "")
                    if target_wxid:
                        aliases = _get_member_call_aliases(room_wxid, target_wxid)
                        nickname = _lookup_member_nickname(room_wxid, target_wxid) or target_wxid
                        text = f"{nickname}({target_wxid}) 的外号：{('、'.join(aliases) if aliases else '未设置')}"
                    else:
                        summary = _get_group_alias_summary(room_wxid)
                        if not summary:
                            text = "这个群还没有设置成员外号。"
                        else:
                            lines = ["当前群成员外号："]
                            for wxid, aliases in sorted(summary.items()):
                                nickname = _lookup_member_nickname(room_wxid, wxid) or wxid
                                lines.append(f"{nickname}({wxid})：{'、'.join(_normalize_aliases(aliases))}")
                            text = "\n".join(lines)
                    self.service.helper_send_text(target, text)
                    return

                target_wxid = ""
                alias_start = 1
                if len(parts) > 1 and (parts[1].startswith("wxid_") or parts[1].endswith("@chatroom")):
                    target_wxid = parts[1]
                    alias_start = 2
                elif at_user_list:
                    target_wxid = at_user_list[0]
                    alias_start = 2 if len(parts) > 1 and parts[1].startswith("@") else 1

                if not target_wxid:
                    self.service.helper_send_text(target, "请指定 wxid 或 @一个群成员，例如：#设置外号 wxid_xxx 纸鹤 小鹤")
                    return

                if command in ("#删除外号", "#清除外号"):
                    _set_member_call_aliases(room_wxid, target_wxid, [])
                    nickname = _lookup_member_nickname(room_wxid, target_wxid) or target_wxid
                    self.service.helper_send_text(target, f"已清除 {nickname}({target_wxid}) 的外号。")
                    return

                aliases = _normalize_aliases(parts[alias_start:])
                if not aliases:
                    self.service.helper_send_text(target, "请给出至少一个外号，例如：#设置外号 wxid_xxx 纸鹤 小鹤")
                    return
                saved = _set_member_call_aliases(room_wxid, target_wxid, aliases)
                nickname = _lookup_member_nickname(room_wxid, target_wxid) or target_wxid
                self.service.helper_send_text(target, f"已设置 {nickname}({target_wxid}) 的外号：{'、'.join(saved)}")
                return

            astrbot_ws = get_astrbot_ws_client()
            if astrbot_ws and astrbot_ws.is_connected():
                import uuid

                msgid = data.get("msgid", "")
                if not msgid:
                    msgid = str(uuid.uuid4())

                bot_wxid = astrbot_ws._bot_wxid or os.getenv("BOT_IDENTIFIER", "wechat_bot")

                import time as _time_mod
                if is_group:
                    at_user_list = data.get("at_user_list", [])
                    message_segs = []
                    for at_wxid in at_user_list:
                        nick = _lookup_member_nickname(chat_id, at_wxid)
                        message_segs.append({"type": "at", "data": {"qq": at_wxid, "name": nick}})
                    event_text = _with_sender_identity(msg, from_wxid, chat_id)
                    message_segs.append({"type": "text", "data": {"text": event_text}})

                    event_data = {
                        "time": int(_time_mod.time()),
                        "self_id": bot_wxid,
                        "post_type": "message",
                        "message_type": "group",
                        "sub_type": "normal",
                        "group_id": chat_id,
                        "user_id": from_wxid,
                        "message_id": astrbot_ws._next_msg_id(),
                        "message": message_segs,
                        "raw_message": event_text,
                        "sender": _build_sender_info(from_wxid, chat_id),
                    }
                else:
                    event_data = {
                        "time": int(_time_mod.time()),
                        "self_id": bot_wxid,
                        "post_type": "message",
                        "message_type": "private",
                        "sub_type": "friend",
                        "user_id": from_wxid,
                        "message_id": astrbot_ws._next_msg_id(),
                        "message": [{"type": "text", "data": {"text": msg}}],
                        "raw_message": msg,
                        "sender": {"user_id": from_wxid, "nickname": ""},
                    }

                try:
                    if astrbot_ws._loop:
                        future = asyncio.run_coroutine_threadsafe(
                            astrbot_ws.send_event(event_data),
                            astrbot_ws._loop
                        )
                        future.result(timeout=5)
                    else:
                        logger.warning("astrbot_ws._loop 未设置，无法转发消息")
                except Exception as e:
                    logger.error(f"转发消息到 AstrBot 失败: {e}")
            else:
                logger.warning("AstrBot 未连接，消息未转发")

        except Exception as e:
            logger.error(f"处理聊天消息失败: {e}")

    def _find_voice_path(self, value) -> str:
        """递归查找语音事件中可能的本地文件路径。"""
        if isinstance(value, dict):
            for key, item in value.items():
                key_l = str(key).lower()
                if any(token in key_l for token in ("slik", "silk", "voice", "audio", "file", "path")):
                    found = self._find_voice_path(item)
                    if found:
                        return found
            for item in value.values():
                found = self._find_voice_path(item)
                if found:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = self._find_voice_path(item)
                if found:
                    return found
        elif isinstance(value, str):
            text = value.strip()
            if re.search(r"\.(slik|silk|slk|amr|aud|dat)$", text, re.IGNORECASE):
                return text
            m = re.search(r"[A-Za-z]:\\[^\"<>|?*\r\n]+?\.(?:slik|silk|slk|amr|aud|dat)", text, re.IGNORECASE)
            if m:
                return m.group(0)
        return ""

    def _parse_voice_xml(self, raw_msg: str) -> dict:
        """解析 11048 raw_msg 中的 voicemsg 属性。"""
        import xml.etree.ElementTree as ET
        if not raw_msg:
            return {}
        try:
            root = ET.fromstring(raw_msg)
            voice = root.find(".//voicemsg")
            if voice is None:
                return {}
            return dict(voice.attrib)
        except Exception as e:
            logger.warning(f"[VOICE XML] 解析失败: {e}")
            return {}

    def get_last_voice_raw_msg(self) -> str:
        if self._last_voice_raw_msg:
            return self._last_voice_raw_msg
        try:
            if os.path.exists(self._last_voice_raw_path):
                with open(self._last_voice_raw_path, "r", encoding="utf-8") as f:
                    return f.read().strip()
        except Exception as e:
            logger.warning(f"[VOICE XML] 读取最近语音 raw_msg 失败: {e}")
        return ""

    def get_last_appmsg_raw_msg(self) -> str:
        if self._last_appmsg_raw_msg:
            return self._last_appmsg_raw_msg
        try:
            if os.path.exists(self._last_appmsg_raw_path):
                with open(self._last_appmsg_raw_path, "r", encoding="utf-8") as f:
                    return f.read().strip()
        except Exception as e:
            logger.warning(f"[11061 XML] 读取最近 appmsg raw_msg 失败: {e}")
        return ""

    def _cache_last_voice_raw_msg(self, raw_msg: str):
        if not raw_msg:
            return
        self._last_voice_raw_msg = raw_msg
        try:
            os.makedirs(os.path.dirname(self._last_voice_raw_path), exist_ok=True)
            with open(self._last_voice_raw_path, "w", encoding="utf-8") as f:
                f.write(raw_msg)
        except Exception as e:
            logger.warning(f"[VOICE XML] 保存最近语音 raw_msg 失败: {e}")

    def _cache_last_appmsg_raw_msg(self, data: dict, message_type: int = 11061):
        raw_msg = ""
        if isinstance(data, dict):
            for key in ("raw_msg", "content", "xml", "msg", "raw"):
                value = data.get(key, "")
                if isinstance(value, str) and ("<appmsg" in value or "<msg" in value):
                    raw_msg = value
                    break
        if not raw_msg:
            if isinstance(data, dict):
                logger.info(
                    f"[{message_type} XML] 未发现 raw XML，keys={list(data.keys())}, "
                    f"preview={json.dumps(data, ensure_ascii=False)[:500]}"
                )
            return
        self._last_appmsg_raw_msg = raw_msg
        self._last_appmsg_by_type[message_type] = raw_msg
        raw_path = os.path.join(
            os.path.dirname(__file__), "data", "appmsg", f"last_{message_type}_raw.xml"
        )
        json_path = os.path.join(
            os.path.dirname(__file__), "data", "appmsg", f"last_{message_type}.json"
        )
        try:
            os.makedirs(os.path.dirname(raw_path), exist_ok=True)
            with open(raw_path, "w", encoding="utf-8") as f:
                f.write(raw_msg)
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            if message_type == 11061:
                with open(self._last_appmsg_raw_path, "w", encoding="utf-8") as f:
                    f.write(raw_msg)
                with open(self._last_appmsg_json_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(
                f"[{message_type} XML] 已保存最近 appmsg raw_msg: "
                f"{raw_path} ({len(raw_msg)} chars), preview={raw_msg[:300]}"
            )
        except Exception as e:
            logger.warning(f"[{message_type} XML] 保存最近 appmsg raw_msg 失败: {e}")

    def _probe_voice_cdn_download(self, msgid: str, voice_attrs: dict):
        """尝试用现有 CDN 下载接口拉取语音资源。"""
        voiceurl = voice_attrs.get("voiceurl", "")
        aes_key = voice_attrs.get("aeskey", "")
        if not voiceurl or not aes_key:
            return
        save_dir = os.path.join(os.path.dirname(__file__), "data", "voice")
        os.makedirs(save_dir, exist_ok=True)
        for file_type in (5, 4):
            trace_id = str(_uuid.uuid4())
            save_path = os.path.join(save_dir, f"voice_{msgid}_{file_type}.slik")
            self._pending_voice_downloads[trace_id] = {
                "msgid": msgid,
                "file_type": file_type,
                "save_path": save_path,
            }
            logger.info(
                f"[VOICE CDN] 尝试下载: trace={trace_id}, file_type={file_type}, "
                f"voiceurl={voiceurl[:80]}, save={save_path}"
            )
            self.service.helper_cdn_download(voiceurl, aes_key, save_path, trace_id, file_type=file_type)

    def _handle_voice_message(self, data):
        """处理语音消息，记录 silk/slik 路径并转发给 AstrBot。"""
        try:
            logger.info(f"[VOICE 11048 RAW] {json.dumps(data, ensure_ascii=False)[:2000]}")
            from_wxid = data.get("from_wxid", "")
            to_wxid = data.get("to_wxid", "")
            room_wxid = data.get("room_wxid", "")
            raw_msg = data.get("raw_msg", "")
            if raw_msg:
                self._cache_last_voice_raw_msg(raw_msg)
            voice_path = (
                data.get("slik_file")
                or data.get("silk_file")
                or data.get("voice")
                or data.get("file")
                or data.get("file_path")
                or data.get("path")
                or self._find_voice_path(data)
                or ""
            )
            msgid = data.get("msgid", str(_uuid.uuid4()))
            voice_attrs = self._parse_voice_xml(raw_msg)
            if voice_attrs:
                logger.info(
                    f"[VOICE XML] format={voice_attrs.get('voiceformat')}, "
                    f"length={voice_attrs.get('length')}, voicelength={voice_attrs.get('voicelength')}, "
                    f"aeskey={voice_attrs.get('aeskey')}, voiceurl={voice_attrs.get('voiceurl')}"
                )
                if not voice_path:
                    self._probe_voice_cdn_download(msgid, voice_attrs)
            logger.info(
                f"[VOICE 11048] from={from_wxid}, to={to_wxid}, room={room_wxid}, "
                f"file={voice_path}, exists={os.path.exists(voice_path) if voice_path else False}"
            )

            astrbot_ws = get_astrbot_ws_client()
            if not astrbot_ws or not astrbot_ws.is_connected():
                logger.warning("AstrBot 未连接，语音消息未转发")
                return

            bot_wxid = (astrbot_ws._bot_wxid if astrbot_ws else "") or os.getenv("BOT_IDENTIFIER", "wechat_bot")
            if from_wxid == bot_wxid or data.get("isSendMsg") == 1:
                return

            import time as _time_voice
            is_group = bool(room_wxid)
            # V11: 本地文件转 base64
            voice_file = voice_path
            if voice_path and os.path.exists(voice_path):
                import base64
                with open(voice_path, "rb") as _vf:
                    voice_file = f"base64://{base64.b64encode(_vf.read()).decode()}"
            event_data = {
                "time": int(_time_voice.time()),
                "self_id": bot_wxid,
                "post_type": "message",
                "message_type": "group" if is_group else "private",
                "sub_type": "normal" if is_group else "friend",
                "user_id": from_wxid,
                "message_id": astrbot_ws._next_msg_id(),
                "message": [{"type": "record", "data": {"file": voice_file}}],
                "raw_message": _with_sender_identity("[语音]", from_wxid, room_wxid if is_group else ""),
                "sender": _build_sender_info(from_wxid, room_wxid if is_group else ""),
                "raw_voice": data,
            }
            if is_group:
                event_data["group_id"] = room_wxid

            if astrbot_ws._loop:
                future = asyncio.run_coroutine_threadsafe(
                    astrbot_ws.send_event(event_data),
                    astrbot_ws._loop,
                )
                future.result(timeout=5)
            else:
                logger.warning("astrbot_ws._loop 未设置，无法转发语音消息")
        except Exception as e:
            logger.error(f"处理语音消息失败: {e}")

    @CLOSE_CALLBACK(in_class=True)
    def on_close(self, client_id):
        """客户端断开回调"""
        self.connected_clients.discard(client_id)
        logger.info(f"客户端 {client_id} 已断开，当前连接数: {len(self.connected_clients)}")


# ============================ DLL 加载器 ============================

class NoveLoader:
    """DLL 加载器"""

    # 偏移地址
    _InitWeChatSocket = 0xB080
    _GetUserWeChatVersion = 0xCB80
    _InjectWeChat = 0xCC10
    _SendWeChatData = 0xAF90
    _DestroyWeChat = 0xC540
    _UseUtf8 = 0xC680
    _InjectWeChat2 = 0xCC30
    _InjectWeChatPid = 0xB750
    _InjectWeChatMultiOpen = 0xC780

    def __init__(self, loader_path: str):
        loader_path = os.path.realpath(loader_path)
        if not os.path.exists(loader_path):
            logger.error('libs path error or loader not exist')
            return

        loader_module = WinDLL(loader_path)
        self.loader_module_base = loader_module._handle

        # 使用utf8编码
        self.UseUtf8()

        # 初始化接口回调
        self.InitWeChatSocket(wechat_connect_callback, wechat_recv_callback, wechat_close_callback)

    def __get_non_exported_func(self, offset: int, arg_types, return_type):
        """获取非导出函数"""
        func_addr = self.loader_module_base + offset
        if arg_types:
            func_type = ctypes.WINFUNCTYPE(return_type, *arg_types)
        else:
            func_type = ctypes.WINFUNCTYPE(return_type)
        return func_type(func_addr)

    def add_callback_handler(self, callback_handler):
        """添加回调处理器"""
        add_callback_handler(callback_handler)

    def InitWeChatSocket(self, connect_callback, recv_callback, close_callback):
        """初始化微信Socket"""
        func = self.__get_non_exported_func(
            self._InitWeChatSocket,
            [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p],
            ctypes.c_bool
        )
        return func(connect_callback, recv_callback, close_callback)

    def GetUserWeChatVersion(self) -> str:
        """获取用户微信版本"""
        func = self.__get_non_exported_func(self._GetUserWeChatVersion, None, ctypes.c_bool)
        out = create_string_buffer(20)
        if func(out):
            return out.value.decode('utf-8')
        else:
            return ''

    def InjectWeChat(self, dll_path: str) -> ctypes.c_uint32:
        """注入微信"""
        func = self.__get_non_exported_func(self._InjectWeChat, [ctypes.c_char_p], ctypes.c_uint32)
        return func(self._c_string(dll_path))

    def SendWeChatData(self, client_id: int, message: str) -> ctypes.c_bool:
        """发送微信数据"""
        func = self.__get_non_exported_func(self._SendWeChatData, [ctypes.c_uint32, ctypes.c_char_p], ctypes.c_bool)
        return func(client_id, self._c_string(message))

    def DestroyWeChat(self) -> ctypes.c_bool:
        """销毁微信连接"""
        func = self.__get_non_exported_func(self._DestroyWeChat, None, ctypes.c_bool)
        return func()

    def UseUtf8(self):
        """使用UTF-8编码"""
        func = self.__get_non_exported_func(self._UseUtf8, None, ctypes.c_bool)
        return func()

    def InjectWeChat2(self, dll_path: str, exe_path: str) -> ctypes.c_uint32:
        """注入微信（方式2）"""
        func = self.__get_non_exported_func(self._InjectWeChat2, [ctypes.c_char_p, ctypes.c_char_p], ctypes.c_uint32)
        return func(self._c_string(dll_path), self._c_string(exe_path))

    def InjectWeChatPid(self, pid: int, dll_path: str) -> ctypes.c_uint32:
        """通过PID注入微信"""
        func = self.__get_non_exported_func(self._InjectWeChatPid, [ctypes.c_uint32, ctypes.c_char_p], ctypes.c_uint32)
        return func(pid, self._c_string(dll_path))

    def InjectWeChatMultiOpen(self, dll_path: str, exe_path: str) -> ctypes.c_uint32:
        """多开注入微信"""
        func = self.__get_non_exported_func(self._InjectWeChatMultiOpen, [ctypes.c_char_p, ctypes.c_char_p],
                                            ctypes.c_uint32)
        return func(self._c_string(dll_path), self._c_string(exe_path))

    def _c_string(self, data):
        """转换为C字符串"""
        return ctypes.c_char_p(data.encode('utf-8'))


# ============================ 微信服务管理器 ============================

class WeChatService:
    """微信服务管理器"""

    def __init__(self, loader_path: str, dll_path: str):
        self.loader_path = loader_path
        self.dll_path = dll_path
        self.loader = None
        self.handler = None
        self.is_running = False
        self.should_stop = False
        self.client_id = None
        self.last_heartbeat = time.time()
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        self.reconnect_delay = 10  # 秒
        self.bot_wxid = ""  # 机器人自己的 wxid，从登录消息中获取
        self.bot_nickname = ""  # 机器人昵称
        self.bot_avatar = ""  # 机器人头像
        self.target_pid = None  # 目标微信进程 PID，重连时用于定位同一个微信

    def initialize(self):
        """初始化服务"""
        try:
            logger.info("正在初始化微信服务...")

            # 检查Python架构
            if self._is_64bit():
                logger.error("检测到64位Python，但DLL是32位的。请使用32位Python运行此程序。")
                return False

            # 检查文件是否存在
            if not os.path.exists(self.loader_path):
                logger.error(f"Loader DLL 文件不存在: {self.loader_path}")
                return False

            if not os.path.exists(self.dll_path):
                logger.error(f"Helper DLL 文件不存在: {self.dll_path}")
                return False

            # 创建加载器
            self.loader = NoveLoader(self.loader_path)
            if not self.loader:
                logger.error("创建 NoveLoader 失败")
                return False

            # 创建回调处理器
            self.handler = WeChatServiceHandler(self)
            self.loader.add_callback_handler(self.handler)

            # 关联到 AstrBot，让 API 能查到 handler
            astrbot_ws = get_astrbot_ws_client()
            if astrbot_ws:
                astrbot_ws.set_handler(self.handler)

            logger.info("微信服务初始化成功")
            return True

        except Exception as e:
            logger.error(f"初始化微信服务失败: {e}")
            return False

    def start(self):
        """启动服务"""
        if not self.initialize():
            return False

        self.is_running = True
        self.should_stop = False

        try:
            # 注入微信
            logger.info("正在注入微信...")
            self.client_id = self.loader.InjectWeChat(self.dll_path)

            if self.client_id:
                logger.info(f"成功注入微信，客户端 ID 为: {self.client_id}")
                self.reconnect_attempts = 0

                # 启动心跳监控
                self.start_heartbeat()

                # 启动主服务循环
                self.run_service()
                return True
            else:
                logger.error("注入微信失败")
                return False

        except Exception as e:
            logger.error(f"启动微信服务失败: {e}")
            return False

    def start_with_multi_open(self, dll_path: str, exe_path: str):
        """启动服务，通过多开方式启动微信"""
        if not self.initialize():
            return False

        self.is_running = True
        self.should_stop = False

        try:
            # 多开注入微信（绕过防多开）
            logger.info(f"正在通过多开方式启动微信: {exe_path}")
            self.client_id = self.loader.InjectWeChatMultiOpen(dll_path, exe_path)

            if self.client_id:
                logger.info(f"成功注入微信，客户端 ID 为: {self.client_id}")
                self.reconnect_attempts = 0

                # 启动心跳监控
                self.start_heartbeat()

                # 启动主服务循环
                self.run_service()
                return True
            else:
                logger.error("多开注入微信失败")
                return False

        except Exception as e:
            logger.error(f"启动微信服务失败: {e}")
            return False

    def start_heartbeat(self):
        """启动心跳监控线程"""
        threading.Thread(target=self._heartbeat_monitor, daemon=True).start()
        logger.info("心跳监控已启动")

    def _heartbeat_monitor(self):
        """心跳监控"""
        while self.is_running and not self.should_stop:
            logger.info("心跳监控验证...")
            self.last_heartbeat = time.time()
            time.sleep(60)

    def run_service(self):
        """运行服务主循环"""
        logger.info("微信服务已启动，正在运行...")

        try:
            while self.is_running and not self.should_stop:
                # 检查是否需要重连
                if time.time() - self.last_heartbeat > 120:  # 2分钟无心跳
                    logger.warning("检测到连接超时，尝试重连...")
                    if self.reconnect():
                        continue
                    else:
                        break

                time.sleep(1)

        except KeyboardInterrupt:
            logger.info("收到中断信号，正在停止服务...")
            self.should_stop = True
        except Exception as e:
            logger.error(f"服务运行异常: {e}")
        finally:
            self.stop()

    def reconnect(self):
        """重连服务"""
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            logger.error(f"重连次数超过限制 ({self.max_reconnect_attempts})，停止重连")
            return False

        self.reconnect_attempts += 1
        logger.info(f"尝试重连 ({self.reconnect_attempts}/{self.max_reconnect_attempts})...")

        try:
            # 清理当前连接
            if self.loader:
                self.loader.DestroyWeChat()

            time.sleep(self.reconnect_delay)

            # 优先通过 PID 重连同一个微信进程
            old_wxid = self.bot_wxid
            if self.target_pid:
                logger.info(f"通过 PID {self.target_pid} 重连目标微信 (wxid={old_wxid})...")
                self.client_id = self.loader.InjectWeChatPid(self.target_pid, self.dll_path)
                if not self.client_id:
                    logger.warning(f"PID {self.target_pid} 注入失败，目标进程可能已退出，回退到自动查找")
                    self.client_id = self.loader.InjectWeChat(self.dll_path)
            else:
                self.client_id = self.loader.InjectWeChat(self.dll_path)

            if self.client_id:
                logger.info(f"重连成功，客户端 ID: {self.client_id}")
                self.last_heartbeat = time.time()
                self.reconnect_attempts = 0
                return True
            else:
                logger.error("重连失败")
                return False

        except Exception as e:
            logger.error(f"重连过程中发生异常: {e}")
            return False

    def stop(self):
        """停止服务"""
        logger.info("正在停止微信服务...")
        self.should_stop = True
        self.is_running = False

        try:
            if self.loader:
                self.loader.DestroyWeChat()
                logger.info("微信连接已断开")
        except Exception as e:
            logger.error(f"停止服务时发生异常: {e}")

        logger.info("微信服务已停止")

    def send_message(self, message: str) -> bool:
        """发送消息"""
        if not self.client_id or not self.loader:
            logger.error("服务未连接，无法发送消息")
            return False
        try:
            result = self.loader.SendWeChatData(1, message)
            if result:
                logger.info(f"消息发送成功: {message}")
                return True
            else:
                logger.error(f"消息发送失败: {result}")
                return False
        except Exception as e:
            logger.error(f"发送消息时发生异常: {e}")
            return False

    def _is_64bit(self):
        """检查是否为64位Python"""
        return sys.maxsize > 2 ** 32

    # 下列方法请根据文档自行补充
    # https://www.showdoc.com.cn/2447538212104511
    # 密码：qqq222..

    def helper_get_friend_list(self, trace_id: str = "") -> bool:
        # 获取好友列表
        payload = {"type": 11030, "data": {}}
        if trace_id:
            payload["trace"] = trace_id
        message = json.dumps(payload, ensure_ascii=False)
        logger.info(f"获取好友列表请求: {message}")
        return self.send_message(message)

    def helper_get_group_list(self, trace_id: str = "") -> bool:
        # 获取群聊列表
        payload = {"type": 11031, "data": {"detail": 1}}
        if trace_id:
            payload["trace"] = trace_id
        message = json.dumps(payload, ensure_ascii=False)
        logger.info(f"获取群聊列表请求: {message}")
        return self.send_message(message)

    def helper_get_group_member_list(self, trace_id: str, room_wxid: str) -> bool:
        # 获取群成员列表与信息 (11032)
        payload = {"type": 11032, "data": {"room_wxid": room_wxid}}
        if trace_id:
            payload["trace"] = trace_id
        message = json.dumps(payload, ensure_ascii=False)
        logger.info(f"获取群成员信息请求: {message}")
        return self.send_message(message)

    def helper_get_contact_info(self, trace_id: str, wxid: str) -> bool:
        # 获取好友详细信息
        payload = {"type": 11029, "data": {"wxid": wxid}}
        if trace_id:
            payload["trace"] = trace_id
        message = json.dumps(payload, ensure_ascii=False)
        logger.info(f"获取用户详细信息请求: {message}")
        return self.send_message(message)

    @staticmethod
    def _clean_for_wechat(text: str) -> str:
        text = re.sub(r'\[([^\]]*)\]\([^\)]+\)', r'\1', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def helper_send_text(self, to_wxid: str, content: str, trace: str = "") -> bool:
        content = self._clean_for_wechat(content)
        payload = {"data": {"to_wxid": to_wxid, "content": content}, "type": 11036}
        if not trace:
            trace = str(_uuid.uuid4())
        payload["trace"] = trace
        astrbot_ws = get_astrbot_ws_client()
        if astrbot_ws:
            astrbot_ws._pending_send[trace] = {"to_wxid": to_wxid, "type": "text"}
            astrbot_ws._last_send_to_wxid = to_wxid
            astrbot_ws._last_send_type = "text"
        message = json.dumps(payload, ensure_ascii=False)
        logger.info(f"普通消息发送: {message}")
        if to_wxid == "filehelper" and self.handler:
            self.handler._cache_filehelper_msg(sender=self.bot_nickname or "bot", content=content, direction="out")
        return self.send_message(message)

    def helper_send_image(self, to_wxid: str, file_path: str, trace: str = "", auto_recall_delay: int = 0) -> bool:
        # Bug4修复: 合并重复定义。发送图片消息（type=11040，对应 all.md "发送图片消息"）
        if not os.path.exists(file_path):
            logger.warning(f"图片文件不存在: {file_path}")
            return False
        payload = {"data": {"to_wxid": to_wxid, "file": file_path}, "type": 11040}
        if not trace:
            trace = str(_uuid.uuid4())
        payload["trace"] = trace
        astrbot_ws = get_astrbot_ws_client()
        recall_entry = None
        if astrbot_ws:
            astrbot_ws._pending_send[trace] = {"to_wxid": to_wxid, "type": "image"}
            astrbot_ws._last_send_to_wxid = to_wxid
            astrbot_ws._last_send_type = "image"
            if auto_recall_delay > 0:
                # 只登记需要自动撤回的图片，避免普通图片污染撤回 FIFO。
                fifo = astrbot_ws._image_recall_fifo
                recall_entry = {"trace": trace, "to_wxid": to_wxid, "msgid": "", "ts": time.time(), "recall_delay": auto_recall_delay}
                fifo.append(recall_entry)
                if len(fifo) > 100:
                    del fifo[:len(fifo) - 100]
        message = json.dumps(payload, ensure_ascii=False)
        logger.info(f"图片消息发送: {file_path} -> {to_wxid}")
        if to_wxid == "filehelper" and self.handler:
            self.handler._cache_filehelper_msg(sender=self.bot_nickname or "bot", content=f"[图片] {file_path}", direction="out")
        ok = self.send_message(message)
        if not ok and recall_entry and astrbot_ws and recall_entry in astrbot_ws._image_recall_fifo:
            astrbot_ws._image_recall_fifo.remove(recall_entry)
        if ok and auto_recall_delay > 0:
            self._schedule_auto_recall(to_wxid, trace, auto_recall_delay, "image")
        return ok

    def _schedule_auto_recall(self, to_wxid: str, trace: str, delay: int, msg_type: str = ""):
        """后台定时撤回：delay 秒后尝试撤回该消息，失败重试最多3次"""
        import threading

        def _do_recall():
            import time as _time
            _time.sleep(delay)
            astrbot_ws = get_astrbot_ws_client()
            for attempt in range(3):
                # 在图片撤回 FIFO 中精准匹配（11047 已把 newMsgId 绑回对应 trace）
                entry = None
                if astrbot_ws:
                    fifo = astrbot_ws._image_recall_fifo
                    if trace:
                        for item in fifo:
                            if item.get("trace") == trace:
                                entry = item
                                break
                    if not trace:
                        for item in reversed(fifo):
                            if item.get("to_wxid") == to_wxid and item.get("msgid"):
                                entry = item
                                break
                if not entry or not isinstance(entry, dict):
                    logger.warning(f"[自动撤回] 未找到消息 trace={trace}, to={to_wxid}, type={msg_type}, attempt={attempt+1}")
                    _time.sleep(3)
                    continue
                new_msgid = entry.get("msgid", "")
                if not new_msgid:
                    logger.warning(f"[自动撤回] 消息尚未绑定 newMsgId trace={trace}, attempt={attempt+1}")
                    _time.sleep(3)
                    continue
                ok = self.helper_recall_msg(to_wxid, new_msgid,
                                            entry.get("client_msgid", 0),
                                            entry.get("create_time", 0))
                logger.info(f"[自动撤回] to={to_wxid}, attempt={attempt+1}, ok={ok}")
                if ok:
                    if astrbot_ws and entry in astrbot_ws._image_recall_fifo:
                        astrbot_ws._image_recall_fifo.remove(entry)
                    return
                _time.sleep(3)
            logger.error(f"[自动撤回] 3次重试均失败: to={to_wxid}, trace={trace}")

        threading.Thread(target=_do_recall, daemon=True).start()

    def helper_send_emoji(self, to_wxid: str, file_path: str) -> bool:
        """发送 CDN 表情包消息（type=11254）"""
        if not os.path.exists(file_path):
            logger.warning(f"表情包文件不存在: {file_path}")
            return False
        payload = {"data": {"to_wxid": to_wxid, "path": file_path, "file": file_path}, "type": 11254}
        message = json.dumps(payload, ensure_ascii=False)
        logger.info(f"表情包消息发送: {file_path} -> {to_wxid}")
        if to_wxid == "filehelper" and self.handler:
            self.handler._cache_filehelper_msg(sender=self.bot_nickname or "bot", content=f"[表情包] {file_path}", direction="out")
        return self.send_message(message)

    def helper_send_at_text(self, to_wxid: str, content: str, at_list: list = None) -> bool:
        # 发送群@消息（type=11037，对应 all.md "发送群@消息"）
        if at_list is None:
            at_list = ["notify@all"]
        payload = {
            "data": {
                "to_wxid": to_wxid,
                "content": content,
                "at_list": at_list
            },
            "type": 11037
        }
        message = json.dumps(payload, ensure_ascii=False)
        logger.info(f"@消息发送: {message}")
        return self.send_message(message)

    def helper_send_card(self, to_wxid: str, card_wxid: str) -> bool:
        # 发送卡片（type=11038，对应 all.md "发送名片"）
        payload = {"data": {"to_wxid": to_wxid, "card_wxid": card_wxid}, "type": 11038}
        message = json.dumps(payload, ensure_ascii=False)
        logger.info(f"卡片消息发送: {message}")
        return self.send_message(message)

    def helper_send_url(self, to_wxid: str, title: str, desc: str, url: str, image_url: str) -> bool:
        # 发送链接（type=11039，对应 all.md "发送连接消息"）
        payload = {"data": {"to_wxid": to_wxid, "title": title, "desc": desc, "url": url, "image_url": image_url},
                   "type": 11039}
        message = json.dumps(payload, ensure_ascii=False)
        logger.info(f"链接消息发送: {message}")
        return self.send_message(message)
        
    def helper_send_cdn_link_card(self, to_wxid: str, title: str, desc: str, url: str, image_url: str) -> bool:
        # CDN 链接卡片 (type=11236)
        payload = {
          "data": {
            "to_wxid": to_wxid,
            "title": title,
            "desc": desc,
            "url": url,
            "image_url": image_url
          },
          "type": 11236
        }
        message = json.dumps(payload, ensure_ascii=False)
        logger.info(f"发送CDN链接卡片: {message}")
        return self.send_message(message)

    def helper_send_file(self, to_wxid: str, file_path: str) -> bool:
        # 发送文件消息
        payload = {"data": {"to_wxid": to_wxid, "file": file_path}, "type": 11041}
        message = json.dumps(payload, ensure_ascii=False)
        logger.info(f"文件消息发送: {message}")
        return self.send_message(message)

    def helper_send_video(self, to_wxid: str, file_path: str) -> bool:
        # 发送视频消息
        payload = {"data": {"to_wxid": to_wxid, "file": file_path}, "type": 11042}
        message = json.dumps(payload, ensure_ascii=False)
        logger.info(f"视频消息发送: {message}")
        return self.send_message(message)

    def helper_send_voice(self, to_wxid: str, file_path: str, msg_type: int | None = None) -> bool:
        # 发送语音消息探测。不同 DLL 版本字段/类型可能不同，所以先保持可配置。
        if not os.path.exists(file_path):
            logger.warning(f"语音文件不存在: {file_path}")
            return False
        voice_type = msg_type or int(os.environ.get("WECHAT_VOICE_SEND_TYPE", "11044"))
        payload = {
            "data": {
                "to_wxid": to_wxid,
                "file": file_path,
                "slik_file": file_path,
                "silk_file": file_path,
            },
            "type": voice_type,
        }
        message = json.dumps(payload, ensure_ascii=False)
        logger.info(f"语音消息发送探测(type={voice_type}): {file_path} -> {to_wxid}")
        return self.send_message(message)

    @staticmethod
    def _normalize_11214_content(content: str) -> str:
        """type=11214 expects the inner <appmsg> XML, not the received outer <msg>."""
        content = (content or "").strip()
        if "<appmsg" not in content or content.lstrip().startswith("<appmsg"):
            return content
        match = re.search(r"(<appmsg\b.*?</appmsg>)", content, re.DOTALL)
        return match.group(1).strip() if match else content

    def helper_send_raw_xml(self, to_wxid: str, content: str, send_type: int | None = None) -> bool:
        if not content:
            logger.warning("原始 XML 内容为空，无法发送")
            return False
        raw_type = send_type or int(os.environ.get("WECHAT_RAW_XML_SEND_TYPE", "11214"))
        if raw_type == 11214:
            content = self._normalize_11214_content(content)
        payload = {"type": raw_type, "data": {"to_wxid": to_wxid, "content": content}}
        message = json.dumps(payload, ensure_ascii=False)
        logger.info(f"原始 XML 发送探测(type={raw_type}): to={to_wxid}, content={content[:300]}")
        return self.send_message(message)

    def helper_send_gif(self, to_wxid: str, file_path: str) -> bool:
        # 发送GIF动图消息
        payload = {"data": {"to_wxid": to_wxid, "file": file_path}, "type": 11043}
        message = json.dumps(payload, ensure_ascii=False)
        logger.info(f"GIF 动图消息发送: {message}")
        return self.send_message(message)

    def helper_recall_msg(self, to_wxid: str, msg_server_id: str, client_msgid: int = 0, create_time: int = 0) -> bool:
        """撤回消息（type=11244）。"""
        try:
            new_msgid = int(msg_server_id)
        except (ValueError, TypeError):
            new_msgid = msg_server_id
        payload = {
            "data": {
                "client_msgid": client_msgid,
                "create_time": create_time,
                "to_wxid": to_wxid,
                "new_msgid": new_msgid,
            },
            "type": 11244,
        }
        message = json.dumps(payload, ensure_ascii=False)
        logger.info(f"撤回消息: {message}")
        return self.send_message(message)

    def helper_send_forward_record(self, to_wxid: str, title: str, messages: list, forward_type: int = 11044) -> bool:
        """发送合并转发聊天记录（聊天记录卡片）

        Args:
            to_wxid: 目标wxid
            title: 卡片标题
            messages: [{"sender": "xxx", "content": "xxx", "time": "HH:MM:SS", "direction": "in/out"}, ...]
            forward_type: 尝试的发送类型，默认 11044
        """
        import xml.sax.saxutils as saxutils
        esc = saxutils.escape

        # 构建 dataitems
        items_parts = []
        desc_parts = []
        for i, m in enumerate(messages):
            s = esc(m.get("sender", "未知"))
            c = esc(m.get("content", "")[:500])
            t = esc(m.get("time", ""))
            items_parts.append(
                f"<dataitem datatype=\"1\">"
                f"<dataid>{i+1}</dataid>"
                f"<sourcename>{s}</sourcename>"
                f"<sourcetime>{t}</sourcetime>"
                f"<srcmsgcontent>{c}</srcmsgcontent>"
                f"</dataitem>"
            )
            arrow = "←" if m.get("direction") == "in" else "→"
            desc_parts.append(f"{arrow} {s}: {c[:60]}")

        desc = esc("\n".join(desc_parts))
        dataitems_xml = "".join(items_parts)

        # 构建 appmsg XML（对微信来说 key 字段是 <recorditem>）
        xml = (
            f"<appmsg appid=\"\" sdkver=\"0\">"
            f"<title>{esc(title)}</title>"
            f"<des>{desc}</des>"
            f"<type>5</type>"
            f"<recorditem>"
            f"<title>{esc(title)}</title>"
            f"<desc>{desc}</desc>"
            f"<dataitems>{dataitems_xml}</dataitems>"
            f"</recorditem>"
            f"</appmsg>"
        )

        payload = {
            "type": forward_type,
            "data": {
                "to_wxid": to_wxid,
                "content": xml,
            }
        }
        msg = json.dumps(payload, ensure_ascii=False)
        logger.info(f"发送转发聊天记录 ({len(messages)}条, type={forward_type}): {msg[:200]}...")
        return self.send_message(msg)

    def helper_cdn_init(self) -> bool:
        # CDN初始化 (type=11228)
        payload = {"type": 11228, "data": {}}
        message = json.dumps(payload, ensure_ascii=False)
        logger.info("CDN初始化请求")
        return self.send_message(message)

    def helper_cdn_upload(self, file_path: str, file_type: int = 5, trace_id: str = "") -> bool:
        # CDN上传 (type=11229)
        payload = {
            "type": 11229,
            "data": {
                "file_path": file_path,
                "file_type": file_type,
            }
        }
        if trace_id:
            payload["trace"] = trace_id
        message = json.dumps(payload, ensure_ascii=False)
        logger.info(f"CDN上传: file_type={file_type}, path={file_path}, trace={trace_id}")
        return self.send_message(message)

    def helper_cdn_send(self, to_wxid: str, cdn_data: dict, send_type: int) -> bool:
        # 通用 CDN 发送 (可指定 type)
        payload = {
            "type": send_type,
            "data": {**cdn_data, "to_wxid": to_wxid},
        }
        message = json.dumps(payload, ensure_ascii=False)
        logger.info(f"CDN发送(type={send_type}): to={to_wxid}, data_keys={list(cdn_data.keys())}")
        return self.send_message(message)

    def helper_cdn_download(self, file_id: str, aes_key: str, save_path: str,
                            trace_id: str = "", file_type: int = 2) -> bool:
        # CDN下载 (type=11230)
        payload = {
            "type": 11230,
            "data": {
                "file_id": file_id,
                "file_type": file_type,
                "aes_key": aes_key,
                "save_path": save_path,
            }
        }
        if trace_id:
            payload["trace"] = trace_id
        message = json.dumps(payload, ensure_ascii=False)
        logger.info(f"CDN下载: file_type={file_type}, save={save_path}")
        return self.send_message(message)


# ============================ 主程序 ============================

def setup_signal_handlers(service):
    """设置信号处理器"""

    def signal_handler(signum, frame):
        logger.info(f"收到信号 {signum}，准备停止服务...")
        service.should_stop = True

    signal.signal(signal.SIGINT, signal_handler)
    if hasattr(signal, 'SIGTERM'):
        try:
            signal.signal(signal.SIGTERM, signal_handler)
        except Exception:
            pass
    if hasattr(signal, 'SIGBREAK'):
        try:
            signal.signal(signal.SIGBREAK, signal_handler)
        except Exception:
            pass


def start_debug_control_server(service, host: str = "127.0.0.1", port: int = 18766):
    """Start a localhost-only debug HTTP API for direct DLL send probes."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class DebugControlHandler(BaseHTTPRequestHandler):
        server_version = "WeChatDebugControl/1.0"

        def log_message(self, fmt, *args):
            logger.info("[debug-control] " + fmt, *args)

        def _send_json(self, status: int, payload: dict):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            return json.loads(raw.decode("utf-8"))

        def do_GET(self):
            if self.path.rstrip("/") == "/health":
                self._send_json(200, {
                    "ok": True,
                    "connected": bool(service.client_id and service.loader),
                })
                return
            self._send_json(404, {"ok": False, "error": "not found"})

        def do_POST(self):
            try:
                data = self._read_json()
                path = self.path.split("?", 1)[0].rstrip("/")

                if path == "/send_raw_xml":
                    to_wxid = data.get("to_wxid") or data.get("group_id") or data.get("user_id") or ""
                    content = data.get("content") or ""
                    send_type = int(data.get("send_type") or os.environ.get("WECHAT_RAW_XML_SEND_TYPE", "11214"))
                    if content == "__last_voice__" and service.handler:
                        content = service.handler.get_last_voice_raw_msg()
                    if content == "__last_appmsg__" and service.handler:
                        content = service.handler.get_last_appmsg_raw_msg()
                    ok = service.helper_send_raw_xml(to_wxid, content, send_type)
                    self._send_json(200 if ok else 500, {"ok": ok})
                    return

                if path == "/send_voice":
                    to_wxid = data.get("to_wxid") or data.get("group_id") or data.get("user_id") or ""
                    file_path = data.get("file") or data.get("file_id") or data.get("path") or data.get("slik_file") or data.get("silk_file") or ""
                    msg_type = int(data.get("msg_type") or os.environ.get("WECHAT_VOICE_SEND_TYPE", "11044"))
                    ok = service.helper_send_voice(to_wxid, file_path, msg_type)
                    self._send_json(200 if ok else 500, {"ok": ok})
                    return

                self._send_json(404, {"ok": False, "error": "not found"})
            except Exception as e:
                logger.exception(f"[debug-control] request failed: {e}")
                self._send_json(500, {"ok": False, "error": str(e)})

    try:
        httpd = ThreadingHTTPServer((host, port), DebugControlHandler)
    except OSError as e:
        logger.warning(f"调试控制接口启动失败: http://{host}:{port} ({e})")
        return None

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    logger.info(f"调试控制接口已启动: http://{host}:{port}")
    return httpd


def main():
    """主函数"""
    # 初始化共享内存
    memory_manager = SharedMemoryManager()
    memory_manager.create_and_write_shared_memory()

    time.sleep(3)

    # Bug1修复: 先创建 service，再创建 astrbot_ws（避免 lambda 引用未定义变量）
    # 配置 DLL 路径
    loader_path = "./NoveLoader.dll"
    dll_path = "./NoveHelper.dll"
    wechat_exe_path = r"D:\SofeWare\Weixin\Weixin.exe"

    service = WeChatService(loader_path, dll_path)
    debug_control = start_debug_control_server(service)

    # 连接 AstrBot WebSocket Server
    astrbot_ws = AstrBotWsClient(
        host="127.0.0.1",
        port=6199,
        send_text_fn=lambda to_wxid, content: service.helper_send_text(to_wxid, content),
        send_image_fn=lambda to_wxid, image_path, auto_recall_delay=0: service.helper_send_image(
            to_wxid, image_path, auto_recall_delay=auto_recall_delay
        ),
        send_emoji_fn=lambda to_wxid, image_path: service.helper_send_emoji(to_wxid, image_path),
        send_video_fn=lambda to_wxid, video_path: service.helper_send_video(to_wxid, video_path),
        send_voice_fn=lambda to_wxid, voice_path: service.helper_send_voice(to_wxid, voice_path),
        get_friend_list_fn=lambda t: service.helper_get_friend_list(t),
        get_group_list_fn=lambda t: service.helper_get_group_list(t)
    )
    astrbot_ws.get_user_info_fn = lambda t, wxid: service.helper_get_contact_info(t, wxid)
    astrbot_ws.get_group_member_info_fn = lambda t, room: service.helper_get_group_member_list(t, room)
    astrbot_ws.send_link_card_fn = lambda to_wxid, title, desc, url, image_url: service.helper_send_cdn_link_card(to_wxid, title, desc, url, image_url)
    set_astrbot_ws_client(astrbot_ws)

    # Bug2修复: 保存事件循环引用，供跨线程调度使用
    def run_ws_client():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        astrbot_ws._loop = loop  # 保存循环引用
        loop.run_until_complete(astrbot_ws.connect())
        loop.run_forever()

    ws_thread = threading.Thread(target=run_ws_client, daemon=True)
    ws_thread.start()

    logger.info("正在连接 AstrBot WebSocket Server...")

    # 注册退出时清理
    import atexit
    def cleanup():
        if astrbot_ws and astrbot_ws._loop:
            asyncio.run_coroutine_threadsafe(astrbot_ws.close(), astrbot_ws._loop)
        if debug_control:
            debug_control.shutdown()
    atexit.register(cleanup)

    # 设置信号处理器
    setup_signal_handlers(service)

    # 启动服务（会通过 DLL 多开微信）
    t = threading.Thread(target=service.start_with_multi_open, args=(dll_path, wechat_exe_path))
    t.daemon = True
    t.start()

    # 等待服务线程结束，支持 Ctrl+C 中断
    try:
        while t.is_alive():
            t.join(timeout=1)
            if service.should_stop:
                break
    except KeyboardInterrupt:
        logger.info("程序被用户中断")
        service.should_stop = True


if __name__ == '__main__':
    main()
