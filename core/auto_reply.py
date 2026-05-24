import asyncio
import random
import time
import os
import json
from datetime import datetime, timedelta
from astrbot.api import logger


class AutoReplyScheduler:
    def __init__(self, plugin_ref=None):
        self._plugin = plugin_ref
        self._enabled = False
        self._min_interval_minutes = 60
        self._max_interval_minutes = 180
        self._special_dates: list[dict] = []
        self._user_birthday = ""
        self._next_check_time = 0
        self._task: asyncio.Task | None = None
        self._last_reply_time = 0
        self._cooldown_minutes = 30

    def configure(self, enabled: bool = False, min_interval: int = 60,
                  max_interval: int = 180, special_dates: list | None = None,
                  user_birthday: str = "", cooldown: int = 30):
        self._enabled = enabled
        self._min_interval_minutes = max(10, min_interval)
        self._max_interval_minutes = max(self._min_interval_minutes, max_interval)
        self._special_dates = special_dates or []
        self._user_birthday = user_birthday
        self._cooldown_minutes = cooldown
        logger.info(f"[AutoReply] 配置更新: enabled={enabled}, interval={min_interval}-{max_interval}min, "
                    f"dates={len(self._special_dates)}个")

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def special_dates(self) -> list[dict]:
        return self._special_dates[:]

    def _is_special_date(self) -> tuple[bool, str, str]:
        now = datetime.now()
        today_str = now.strftime("%m-%d")
        for sd in self._special_dates:
            if not sd.get("date"):
                continue
            if sd["date"] == today_str:
                return True, sd.get("name", "特殊日子"), sd.get("reason", "")
        return False, "", ""

    def _random_interval(self) -> int:
        return random.randint(self._min_interval_minutes, self._max_interval_minutes)

    async def start(self):
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run())
        logger.info("[AutoReply] 调度器已启动")

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[AutoReply] 调度器已停止")

    async def trigger_now(self, reason: str = "手动触发") -> bool:
        if not self._plugin or not self._plugin._enabled:
            return False
        try:
            result = await self._generate_reply(reason)
            return result
        except Exception as e:
            logger.error(f"[AutoReply] 手动触发失败: {e}")
            return False

    async def _run(self):
        await asyncio.sleep(5)
        while True:
            try:
                if not self._enabled or not self._plugin or not self._plugin._enabled:
                    await asyncio.sleep(30)
                    continue
                if not self._plugin._active_role:
                    await asyncio.sleep(30)
                    continue
                now = time.time()
                triggered = False
                trigger_reason = ""
                is_special, date_name, date_reason = self._is_special_date()
                if is_special:
                    last_hour_check = getattr(self, "_last_special_check", 0)
                    if now - last_hour_check > 3600:
                        triggered = True
                        trigger_reason = f"特殊日子「{date_name}」: {date_reason}"
                        self._last_special_check = now
                if not triggered and now >= self._next_check_time:
                    triggered = True
                    trigger_reason = "随机时间"
                if triggered:
                    if now - self._last_reply_time < self._cooldown_minutes * 60:
                        self._next_check_time = now + self._random_interval() * 60
                        await asyncio.sleep(30)
                        continue
                    logger.info(f"[AutoReply] 触发自动回复: {trigger_reason}")
                    await self._generate_reply(trigger_reason)
                    self._last_reply_time = now
                    self._next_check_time = now + self._random_interval() * 60
                    next_dt = datetime.fromtimestamp(self._next_check_time)
                    logger.info(f"[AutoReply] 下次随机回复时间: {next_dt.strftime('%Y-%m-%d %H:%M')}")
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[AutoReply] 调度器异常: {e}")
                await asyncio.sleep(60)

    async def _generate_reply(self, reason: str) -> bool:
        if not self._plugin:
            return False
        try:
            role_cfg = self._plugin._role_config
            if not role_cfg:
                return False
            persona = role_cfg.get("persona", "")
            role_name = role_cfg.get("name", "角色")
            is_special, date_name, date_reason = self._is_special_date()
            if is_special:
                hint = (f"今天是特殊日子「{date_name}」（{date_reason}）。"
                        f"请以{role_name}的身份主动发起一段对话，"
                        f"内容应与这个特殊日子相关，可以表达你的心情或者发起相关话题。"
                        f"不要提及'今天是特殊日子'这种元信息，直接自然地说话。"
                        f"控制在30字以内，像一个真实的人发消息。")
            elif "随机时间" in reason:
                time_now = datetime.now()
                hour_tag = "早上" if 6 <= time_now.hour < 9 else "上午" if 9 <= time_now.hour < 12 \
                    else "中午" if 12 <= time_now.hour < 14 else "下午" if 14 <= time_now.hour < 18 \
                    else "傍晚" if 18 <= time_now.hour < 20 else "晚上"
                topics = ["今天天气", "刚做了什么事", "突然想到", "好无聊啊", "今天心情",
                          "最近在看的", "刚吃了", "准备去", "在干嘛"]
                hint = (f"你现在是{role_name}，请在这个{hour_tag}主动发起一段自然对话。"
                        f"可以聊聊{random.choice(topics)}之类的话题。"
                        f"控制在30字以内，像一个真实的人发消息。")
            else:
                hint = f"你现在是{role_name}，请主动发起一段自然对话。控制在30字以内。"
            provider = self._plugin._get_llm_provider()
            if not provider:
                return False
            system = f"你是{role_name}，以下是你的设定:\n{persona}"
            reply = await provider.text_chat(hint, system_prompt=system)
            reply_text = reply.completion_text if hasattr(reply, 'completion_text') else str(reply)
            if not reply_text or len(reply_text.strip()) < 2:
                return False
            reply_text = reply_text.strip()
            logger.info(f"[AutoReply] 自动回复内容: {reply_text[:80]}")
            channels_text = self._plugin._config.get("role_channels", "") if self._plugin._config else ""
            if channels_text and channels_text.strip():
                channels = channels_text.strip().split("\n")
                self._plugin._context.send_message(
                    channels[0].strip(),
                    reply_text
                )
                logger.info(f"[AutoReply] 已发送自动回复到: {channels[0].strip()}")
                return True
            return False
        except Exception as e:
            logger.error(f"[AutoReply] 生成回复失败: {e}")
            return False

    def _compute_next_interval(self) -> int:
        return self._random_interval()
