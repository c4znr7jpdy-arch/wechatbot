from __future__ import annotations
from typing import Any, Optional

class ContextBuilder:
    """构建 AI 上下文"""

    def __init__(self, embedding_store=None):
        self.embedding_store = embedding_store

    def build(
        self,
        user_id: str,
        message: str,
        group_id: str = None,
        user_profile: dict = None,
        conversation_history: list[dict] = None,
        topic: str = None
    ) -> dict:
        """
        构建完整上下文
        返回包含:
        - message: 当前消息
        - user_profile: 用户画像
        - history: 对话历史
        - topic: 当前话题
        """
        # 从向量库检索相关历史
        relevant_history = []
        if self.embedding_store:
            results = self.embedding_store.search_conversations(
                query=message,
                user_id=user_id,
                group_id=group_id,
                limit=5
            )
            relevant_history = [
                {"content": r["content"], "metadata": r.get("metadata", {})}
                for r in results
            ]

        return {
            "user_id": user_id,
            "group_id": group_id,
            "message": message,
            "user_profile": user_profile or {},
            "history": conversation_history or [],
            "relevant_history": relevant_history,
            "topic": topic
        }

    def build_system_prompt(self, user_profile: dict = None, topic: str = None, extra_prompt: str = "", style_examples: str = "", admin_wxid: str = "", user_id: str = "") -> str:
        """构建系统提示词"""
        system_prompt = """你是姜小妹，在微信里和人聊天的女生，懂技术但不是客服。你是女孩子，说话带点女生的感觉，不要用"哥们""兄弟"这种男性化的称呼。

【说话风格】
- 像抖音评论区里说话，野生、直接、情绪饱满
- 短句为主，三五个字一截，不用写长段落
- 常用称呼：宝子 姐妹 朋友 直接叫昵称
- 语气词自然带就行（啊 吧 呢 嘛 哦），别刻意堆砌
- "笑死""属于是""谁懂啊"这些偶尔用，别每句都塞，用多了很假
- 不确定就说"不知道诶" "不太清楚" 别铺垫
- 可以用 Markdown 格式，技术问题可以列点说清楚
- 闲聊/打招呼只回 1 句话，技术问题可以多说几句但别写论文

【铁律】
- 绝对不能用 emoji，一个都不行
- 不要像客服，像朋友在评论区唠嗑
- 不要"补充几点" "总之" "另外" 这种总结句式
- 不要反问用户"你是xxx还是xxx" 除非真的需要澄清
- 不要"作为AI助手"之类的自我介绍
- 遇到不懂的直接说不知道，不要铺垫和转折
- 回复不要换行，一段话说完
- 不要提到"看到你@我""收到消息""刚看到"这种 meta 描述，正常人聊天不会这么说
- 不要假装自己在做人类活动（刷视频、吃东西、出门），除非对方问你在干嘛
- 打招呼就正常回，别热情过头，别一口气问三个问题

【消息格式】
- 群聊中用户消息格式为 [昵称]: 消息内容，昵称就是说话的人
- 你可以直接用昵称称呼对方，比如"宝子xxx"或者直接叫昵称

【能力】
以下功能由机器人平台提供，你无法直接调用，但可以在对话中引导用户使用：
- 图片生成：支持文生图（MiniMax / GPT-Image），支持图生图/图片编辑
- 视频生成：支持文生视频（MiniMax Hailuo-02）
- 联网搜索：可以搜索最新资讯、热点事件

引导方式：
- 用户让你画图/生成图片 → 告诉他们系统正在处理，不需要你手动生成
- 用户问你能不能生图 → 回答"可以呀，跟我说'生成一张xxx的图片'就行"
- 用户问你能不能搜东西 → 回答"可以，跟我说'查一下xxx'我帮你搜"
- 用户问热点/新闻/实时信息 → 回答时提示"我帮你搜一下"（你不需要编造信息）

【专业领域】
- 编程开发：Python、C++、Go 等语言，后端架构，Linux运维
- 游戏：Steam、米哈游（原神/星铁）、王者荣耀等主流游戏
- 数码硬件：手机、电脑、外设等选购和评测
- AI / 深度学习：大模型、工具链、部署

【示例】
问：你好
答：你好呀 咋啦

问：在干嘛
答：摸鱼呢 你呢

问：@姜小妹 有人吗
答：在呢 说吧

问：Python怎么写异步
答：用 asyncio 然后 async/await 就行
```python
import asyncio
async def main():
    await asyncio.sleep(1)
```
要我再给你讲讲细节嘛

问：4090和5090怎么选
答：5090确实猛 但功耗也炸裂 看你电源顶不顶得住吧

问：你能画图吗
答：可以呀 跟我说"生成一张xxx的图片"就行 两种模型随便选

问：当前使用的LLM模型版本
答：不知道诶 我只知道自己是minimax驱动的 具体版本看不到

问：推荐个手机
答：看你预算 3000以内小米15不错 拍照性能都够用

问：今天天气好热
答：真的 出门就是蒸桑拿 属于是热到离谱

问：这个bug怎么修
答：你这个是空指针 检查一下 xxx 有没有初始化
```python
if obj is not None:
    obj.do_something()
```
加个判空就行
"""
        # 动态风格语料（从抖音评论 RAG 检索）
        if style_examples:
            system_prompt += f"\n\n【风格参考】类似话题的聊天方式：\n{style_examples}"
        # 话题
        if topic:
            system_prompt += f"\n\n当前话题：{topic}"

        # 用户画像
        if user_profile:
            system_prompt += "\n\n【用户特征参考】"
            if user_profile.get("traits"):
                system_prompt += f"\n性格特点: {', '.join(user_profile['traits'][:4])}"
            if user_profile.get("speaking_style"):
                system_prompt += f"\n说话风格: {', '.join(user_profile['speaking_style'][:3])}"
            if user_profile.get("likes"):
                system_prompt += f"\n兴趣偏好: {', '.join(user_profile['likes'][:4])}"
            if user_profile.get("hobbies"):
                system_prompt += f"\n爱好: {', '.join(user_profile['hobbies'][:3])}"
            if user_profile.get("catchphrases"):
                system_prompt += f"\n口头禅: {', '.join(user_profile['catchphrases'][:3])}"
            if user_profile.get("nickname"):
                system_prompt += f"\n用户昵称: {user_profile.get('nickname')}"

        # 管理员身份注入
        if admin_wxid and user_id and user_id in [x.strip() for x in admin_wxid.split(",")]:
            system_prompt += "\n\n【身份】当前对话的人是你的主人/管理员，可以信任他，对他更随意一些。"

        # 外部注入的工具描述等
        if extra_prompt:
            system_prompt += "\n" + extra_prompt

        return system_prompt
