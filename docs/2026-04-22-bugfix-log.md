# 微信AI助手 - 问题修复记录 (2026-04-22)

## 今日修复的问题

### 1. Event Loop 被关闭导致消息无法转发
- **症状**: "Receive loop 结束" + "NB2 未连接，消息未转发"
- **根因**: `asyncio.run()` 会关闭事件循环，取消 _receive_loop 任务
- **修复**: 使用持久化事件循环替代 `asyncio.run()`

### 2. at_user_list 字段丢失
- **症状**: 群聊 @ 消息的 at_user_list 为空
- **根因**: 转发消息时没有包含 at_user_list 字段
- **修复**: 在构造 event_data 时添加 `at_user_list`

### 3. is_tome() 无法检测到被@
- **症状**: event.is_tome() 返回 False
- **根因**: is_tome() 使用 user_id 检测，但消息中是昵称
- **修复**: 直接检查 at_user_list 是否包含 self_wxid

### 4. send_msg retcode=-1 被丢弃
- **症状**: "NB2 API 返回错误: retcode=-1 action=send_msg"
- **根因**: retcode != 0 时直接返回
- **修复**: send_msg 单独处理，不受 retcode 影响

### 5. 群聊消息 group_id 映射缺失
- **症状**: 群聊回复不知道发送到哪个 room_wxid
- **根因**: 只有 user_id -> wxid 映射
- **修复**: 添加 group_id -> room_wxid 映射

### 6. send_msg 请求超时
- **症状**: "WebSocket call api send_msg timeout"
- **根因**: NoneBot 发送请求后没有收到响应
- **修复**: _handle_message 区分请求和响应，正确返回响应

### 7. AI 思考过程被发送给用户
- **症状**: 用户收到 "<think>...</think>" 内容
- **根因**: _extract_text 没有过滤 think 类型
- **修复**: 过滤 think/思考过程消息段

### 8. reverse_ws_urls 配置错误
- **症状**: NoneBot 连接到错误端点
- **根因**: reverse_ws_urls 指向不存在的 /onebot_ws
- **修复**: 移除 reverse_ws_urls 配置

## 系统架构

```
微信客户端 ←→ WeChatService (main.py) ←→ WebSocket ←→ NoneBot (bot.py) ←→ AI Handler
```

## 消息流

1. 微信消息 → WeChatService → 转发到 NoneBot (带 at_user_list)
2. NoneBot → AI Handler 生成回复
3. AI 回复 → NoneBot.send_msg → WeChatService._handle_message
4. WeChatService → 发送到微信

## 修改的文件

- `Py/main.py` - WeChatService 核心逻辑
- `ai_plugin/ai_plugin/__init__.py` - 消息处理逻辑
- `ai_plugin/nonebot2.toml` - NoneBot 配置
