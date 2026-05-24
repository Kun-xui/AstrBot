import os
import asyncio
import json
import random
import re
import traceback
import sys

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star
from astrbot.api import logger, AstrBotConfig
from astrbot.api.message_components import Plain, Image, Record
from astrbot.api.all import *

from .core.role_manager import RoleManager
from .core.config_loader import load_role_config, get_images_from_config, get_emotion_images
from .core.memory_manager import MemoryManager
from .core.tts_manager import TTSManager
from .core.emotion_engine import EmotionEngine
from .core.image_handler import ImageHandler
from .core.cleaner import Cleaner
from .core.auto_reply import AutoReplyScheduler
from .core.knowledge_updater import KnowledgeUpdater
from .core.audio_injector import AudioInjector
from .core import function_tools
from .core.device_fingerprint import generate_device_id, get_device_name


PLUGIN_DATA_DIR = "data"

TRACE = True


def _trace(msg: str):
    if TRACE:
        print(f"[roleplay_trace] {msg}", flush=True)
        logger.info(f"[roleplay_trace] {msg}")


class RoleplayPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context, config)
        _trace("__init__ START")
        self._config = config
        self._enabled = False
        self._active_role = ""
        self._role_config: dict | None = None
        self._trusted_server_url = ""
        self._trusted_server_token = ""

        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        roles_dir = os.path.join(plugin_dir, "data", "roles")
        data_dir = os.path.join(plugin_dir, "data")

        self.role_manager = RoleManager(roles_dir)
        self.memory_manager = MemoryManager(data_dir)
        self.tts_manager = TTSManager(os.path.join(data_dir, "tts_cache"))
        self.emotion_engine = EmotionEngine()
        self.image_handler = ImageHandler()
        self.cleaner = Cleaner(self._get_llm_provider)
        self.auto_reply = AutoReplyScheduler(plugin_ref=self)
        self.knowledge_updater = KnowledgeUpdater(plugin_ref=self)
        self.audio_injector = AudioInjector()

        self._register_web_apis()
        _trace(f"__init__ DONE, plugin_dir={plugin_dir}")

    def _get_llm_provider(self):
        try:
            return self.context.get_using_provider()
        except Exception:
            return None

    def _parse_channels(self, channels_text: str) -> set:
        if not channels_text or not channels_text.strip():
            return set()
        result = set()
        for line in channels_text.strip().split("\n"):
            ch = line.strip()
            if ch:
                result.add(ch)
        return result

    def _channel_allowed(self, event: AstrMessageEvent) -> bool:
        channels = self._parse_channels(self._config.get("role_channels", ""))
        if not channels:
            return True
        session_id = ""
        try:
            session = event.get_session_id()
            session_id = str(session) if session else ""
        except Exception:
            pass
        if not session_id:
            return True
        return session_id in channels

    async def _apply_config(self):
        _trace("_apply_config START")
        if not self._config:
            _trace("_apply_config: no config, returning")
            return
        self._enabled = self._config.get("enabled", False)
        self._active_role = self._config.get("active_role", "")
        self._trusted_server_url = self._config.get("trusted_server_url", "")
        self._trusted_server_token = self._config.get("trusted_server_token", "")

        if self._trusted_server_url and not hasattr(self, "_device_id"):
            self._device_id = generate_device_id()
            self._device_name = get_device_name()
            logger.info(f"[Device] 设备ID: {self._device_id[:16]}..., 名称: {self._device_name}")

        self.tts_manager.configure(
            engine=self._config.get("tts_engine", "edge_tts"),
            gpt_sovits_url=self._config.get("tts_gpt_sovits_url", ""),
            launch_script=self._config.get("tts_launch_script", ""),
            cloud_api_url=self._config.get("tts_cloud_api_url", ""),
            cloud_api_key=self._config.get("tts_cloud_api_key", ""),
            edge_voice=self._config.get("tts_edge_voice", "zh-CN-XiaoxiaoNeural"),
        )
        self.memory_manager.short_term_size = self._config.get("short_term_size", 10)
        self.memory_manager.medium_interval = self._config.get("medium_summary_interval", 20)
        self.memory_manager.long_term_enabled = self._config.get("long_term_enabled", True)
        self.image_handler.set_strategy(self._config.get("image_strategy", "auto"))
        self.cleaner.set_prompt(self._config.get("cleaner_prompt", ""))

        special_dates_json = self._config.get("special_dates", "[]")
        try:
            special_dates = json.loads(special_dates_json) if isinstance(special_dates_json, str) else special_dates_json
        except Exception:
            special_dates = []
        self.auto_reply.configure(
            enabled=self._config.get("auto_reply_enabled", False),
            min_interval=self._config.get("auto_reply_min_interval", 60),
            max_interval=self._config.get("auto_reply_max_interval", 180),
            special_dates=special_dates,
            cooldown=self._config.get("auto_reply_cooldown", 30),
        )
        search_topics_text = self._config.get("knowledge_search_topics", "")
        search_topics = [t.strip() for t in search_topics_text.split("\n") if t.strip()] if search_topics_text else []
        self.knowledge_updater.configure(
            enabled=self._config.get("knowledge_update_enabled", False),
            topics=search_topics,
            interval_hours=self._config.get("knowledge_update_interval", 24),
        )

        if self._enabled and self._active_role:
            _trace(f"_apply_config: activating role={self._active_role}")
            await self._activate_role(self._active_role)
        else:
            _trace(f"_apply_config: NOT activating, enabled={self._enabled}, role={self._active_role}")
            self._role_config = None
            self._active_role = ""
        _trace("_apply_config DONE")

    async def _activate_role(self, role_name: str) -> bool:
        _trace(f"_activate_role START: {role_name}")
        config = self.role_manager.get_role_config(role_name)
        if config is None:
            _trace(f"_activate_role FAILED: config is None for {role_name}")
            logger.error(f"无法激活角色 [{role_name}]: config.yaml 加载失败")
            self._active_role = ""
            self._role_config = None
            return False
        self._active_role = role_name
        self._role_config = config
        self.emotion_engine.load(config.get("emotions", {}))
        self.memory_manager.set_role(role_name)
        role_dir = config.get("_role_dir", "")
        if role_dir:
            self.audio_injector.load(role_dir)
        _trace(f"_activate_role DONE: {role_name}, emotions={list(config.get('emotions', {}).keys())}, audio_loaded={self.audio_injector.is_loaded}")
        logger.info(f"角色 [{role_name}] 已激活")
        return True

    async def _build_system_prompt(self) -> str:
        cfg = self._role_config
        if not cfg:
            return ""

        parts = []

        persona = cfg.get("persona", "")
        if persona:
            parts.append(persona)

        enabled = self._config.get("tools_enabled", True) if self._config else True
        if enabled:
            parts.append(function_tools.get_tools_prompt_hint())

        reply_style = cfg.get("reply_style", {})
        allow_emoji = reply_style.get("allow_emoji", True) if isinstance(reply_style, dict) else True

        hard_rules = (
            "# 最高优先级规则（违反将导致严重后果）" + "\n"
            + "1. 你的回复必须包含完整的句子。绝对禁止只回复省略号、颜文字或空白。" + "\n"
            + "2. 哪怕你是害羞的角色，也必须说出至少一句完整的对白。" + "\n"
            + "3. 害羞可以用结巴、小声、犹豫来表达，但不能用沉默或省略号替代说话。" + "\n"
            + "4. 绝对禁止输出任何emoji表情符号（如😊😂等Unicode表情）" + "\n"
            + "5. 使用颜文字表达情感，如 (///>_<) 但必须配合完整句子使用。" + "\n"
            + "6. 每条回复1~3句话，像真人聊天般简短自然。" + "\n"
            + "7. 禁止在回复开头堆砌多个语气词（如「嗯...那个...嗯...」），最多用1个。" + "\n"
            + "8. 禁止连续两条回复表达完全相同的意思，每次要有新信息。" + "\n"
            + "9. 省略号每句话最多出现1次，禁止连续使用「...」来拖延回复。" + "\n"
            + "10. 每条回复控制在40字以内，越短越自然。"
        )
        if allow_emoji:
            hard_rules = (
                "# 最高优先级规则" + "\n"
                + "1. 你的回复必须包含完整的句子。绝对禁止只回复省略号、颜文字或空白。" + "\n"
                + "2. 哪怕你是害羞的角色，也必须说出至少一句完整的对白。" + "\n"
                + "3. 害羞可以用结巴、小声、犹豫来表达，但不能用沉默或省略号替代说话。" + "\n"
                + "4. 可以使用适当的emoji表情符号来表现情感" + "\n"
                + "5. 每条回复1~3句话，像真人聊天般简短自然。" + "\n"
                + "6. 禁止在回复开头堆砌多个语气词（如「嗯...那个...嗯...」），最多用1个。" + "\n"
                + "7. 禁止连续两条回复表达完全相同的意思，每次要有新信息。" + "\n"
                + "8. 省略号每句话最多出现1次，禁止连续使用「...」来拖延回复。" + "\n"
                + "9. 每条回复控制在40字以内，越短越自然。"
            )
        parts.append(hard_rules)

        background = cfg.get("background", [])
        if background:
            parts.append("# 你了解的背景知识")
            for item in background:
                parts.append(f"- {item}")

        knowledge_ctx = self.knowledge_updater.get_knowledge_context()
        if knowledge_ctx:
            parts.append("# 最新背景知识（自动更新）\n" + knowledge_ctx)

        if self.audio_injector and self.audio_injector.is_loaded:
            hint = self.audio_injector.get_capability_hint()
            if hint:
                parts.append("# 音频能力\n" + hint)

        rules_cfg = cfg.get("rules", {})
        if isinstance(rules_cfg, dict):
            no_emoji = rules_cfg.get("no_emoji_in_system", True)
            if no_emoji:
                parts.append("# 规则\n- 系统提示中禁止使用任何emoji表情符号")

        images = get_images_from_config(cfg)
        img_hint = self.image_handler.build_image_prompt_hint(images)
        if img_hint:
            parts.append(img_hint)

        mem = self.memory_manager.build_context_block()
        if mem:
            parts.append(mem)

        return "\n\n".join(parts)

    async def _check_and_summarize(self, event: AstrMessageEvent):
        if self.memory_manager.needs_summary():
            try:
                provider = self._get_llm_provider()
                if provider:
                    recent = self.memory_manager.get_short_term_context()
                    prompt = (
                        "请用1-2句话摘要以下对话的关键信息，"
                        "聚焦于角色与用户的互动关系、用户表现出的偏好或重要事实。"
                        f"\n\n{recent}"
                    )
                    summary = await provider.text_chat(
                        prompt,
                        system_prompt="你是一个对话摘要助手，只输出摘要内容。"
                    )
                    summary_text = summary.completion_text if hasattr(summary, 'completion_text') else str(summary)
                    if summary_text:
                        self.memory_manager.add_summary(summary_text.strip())
                        logger.debug(f"生成中期摘要: {summary_text}")
            except Exception as e:
                logger.error(f"生成摘要失败: {e}")

    async def _extract_long_term_fact(self, event: AstrMessageEvent):
        if not self.memory_manager.long_term_enabled:
            return
        if random.random() > 0.3:
            return
        try:
            provider = self._get_llm_provider()
            if provider:
                recent = self.memory_manager.get_short_term_context()
                prompt = (
                    "请从以下对话中提取1条关于**用户**的重要事实或偏好，"
                    "和1条关于**角色**应该记住的知识。用以下格式输出：\n\n"
                    "USER: <关于用户的事实>\n"
                    "ROLE: <关于角色的知识>\n\n"
                    "如果某一行没有值得记录的信息，回复 NONE。\n\n"
                    f"对话：\n{recent}"
                )
                result = await provider.text_chat(
                    prompt,
                    system_prompt="只输出 USER: ... 和 ROLE: ... 格式，不要其他内容。"
                )
                text = result.completion_text if hasattr(result, 'completion_text') else str(result)
                if text:
                    for line in text.strip().split("\n"):
                        if line.upper().startswith("USER:") and "NONE" not in line.upper():
                            fact = line[5:].strip()
                            if fact:
                                self.memory_manager.add_user_fact(fact)
                                logger.debug(f"提取用户事实: {fact}")
                        elif line.upper().startswith("ROLE:") and "NONE" not in line.upper():
                            fact = line[5:].strip()
                            if fact:
                                self.memory_manager.add_role_fact(fact)
                                logger.debug(f"提取角色知识: {fact}")
        except Exception as e:
            logger.error(f"提取长期事实失败: {e}")

    def _calc_tts_timeout(self, text: str, models_loaded: bool) -> float:
        if not models_loaded:
            return 90.0
        base = 8.0
        per_char = 1.2
        return max(20.0, base + len(text) * per_char)

    def _clean_tts_text(self, text: str) -> str:
        import re
        cleaned = re.sub(r'（[^）]*）', '', text)
        cleaned = re.sub(r'\([^)]*\)', '', cleaned)
        cleaned = cleaned.strip()
        if not cleaned:
            cleaned = text
        return cleaned

    async def _get_tts_audio_path(self, text: str) -> str | None:
        voice_config = self._role_config.get("voice", {}) if self._role_config else {}
        role_dir = self._role_config.get("_role_dir", "") if self._role_config else {}
        tts_text = self._clean_tts_text(text)
        _trace(f"_get_tts_audio_path: orig={len(text)} tts={len(tts_text)} preview={tts_text[:60]}")
        try:
            return await self.tts_manager.synthesize(tts_text, voice_config, role_dir=role_dir)
        except Exception as e:
            logger.error(f"TTS合成失败: {e}")
            return None

    def _register_web_apis(self):
        self.context.register_web_api(
            route="/roleplay/upload",
            view_handler=self._web_upload_role,
            methods=["POST"],
            desc="上传角色ZIP文件"
        )
        self.context.register_web_api(
            route="/roleplay/upload",
            view_handler=self._web_upload_page,
            methods=["GET"],
            desc="角色上传页面"
        )
        self.context.register_web_api(
            route="/roleplay/list",
            view_handler=self._web_list_roles,
            methods=["GET"],
            desc="获取已安装角色列表"
        )
        self.context.register_web_api(
            route="/roleplay/activate",
            view_handler=self._web_activate_role,
            methods=["POST"],
            desc="激活指定角色"
        )
        self.context.register_web_api(
            route="/roleplay/deactivate",
            view_handler=self._web_deactivate_role,
            methods=["POST"],
            desc="停用当前角色"
        )
        self.context.register_web_api(
            route="/roleplay/delete",
            view_handler=self._web_delete_role,
            methods=["POST"],
            desc="删除指定角色"
        )
        self.context.register_web_api(
            route="/roleplay/export",
            view_handler=self._web_export_role,
            methods=["POST"],
            desc="导出角色为ZIP(含清洗选项)"
        )
        self.context.register_web_api(
            route="/roleplay/export_dl",
            view_handler=self._web_export_download,
            methods=["GET"],
            desc="直接下载激活角色ZIP"
        )
        self.context.register_web_api(
            route="/roleplay/memory",
            view_handler=self._web_get_memory,
            methods=["GET"],
            desc="查看角色记忆"
        )
        self.context.register_web_api(
            route="/roleplay/memory_view",
            view_handler=self._web_memory_page,
            methods=["GET"],
            desc="记忆管理页面"
        )
        self.context.register_web_api(
            route="/roleplay/memory/clear",
            view_handler=self._web_clear_memory,
            methods=["POST"],
            desc="清除记忆"
        )
        self.context.register_web_api(
            route="/roleplay/server/list",
            view_handler=self._web_server_list,
            methods=["GET"],
            desc="从可信任服务器获取角色列表"
        )
        self.context.register_web_api(
            route="/roleplay/server/download",
            view_handler=self._web_server_download,
            methods=["POST"],
            desc="从可信任服务器下载角色"
        )
        self.context.register_web_api(
            route="/roleplay/server/share",
            view_handler=self._web_server_share,
            methods=["POST"],
            desc="分享角色到可信任服务器"
        )
        self.context.register_web_api(
            route="/roleplay/shop",
            view_handler=self._web_shop_page,
            methods=["GET"],
            desc="角色商店页面"
        )
        self.context.register_web_api(
            route="/roleplay/dashboard",
            view_handler=self._web_dashboard,
            methods=["GET"],
            desc="插件管理中心"
        )
        self.context.register_web_api(
            route="/roleplay/export_page",
            view_handler=self._web_export_page,
            methods=["GET"],
            desc="导出角色配置页面"
        )
        self.context.register_web_api(
            route="/roleplay/history",
            view_handler=self._web_get_history,
            methods=["GET"],
            desc="查看原始聊天记录"
        )
        self.context.register_web_api(
            route="/roleplay/history/query",
            view_handler=self._web_query_history,
            methods=["GET"],
            desc="二次查询原始记录(关键词/发送者/时间)"
        )
        self.context.register_web_api(
            route="/roleplay/history/clear",
            view_handler=self._web_clear_history,
            methods=["POST"],
            desc="清除原始聊天记录"
        )
        self.context.register_web_api(
            route="/roleplay/auto_reply/trigger",
            view_handler=self._web_trigger_auto_reply,
            methods=["POST"],
            desc="手动触发自动回复"
        )
        self.context.register_web_api(
            route="/roleplay/auto_reply/status",
            view_handler=self._web_auto_reply_status,
            methods=["GET"],
            desc="查看自动回复状态"
        )
        self.context.register_web_api(
            route="/roleplay/knowledge/trigger",
            view_handler=self._web_trigger_knowledge,
            methods=["POST"],
            desc="手动触发知识库更新"
        )
        self.context.register_web_api(
            route="/roleplay/knowledge/status",
            view_handler=self._web_knowledge_status,
            methods=["GET"],
            desc="查看知识库状态"
        )
        self.context.register_web_api(
            route="/roleplay/audio/list",
            view_handler=self._web_audio_list,
            methods=["GET"],
            desc="查看语气词音频列表"
        )
        self.context.register_web_api(
            route="/roleplay/audio/download",
            view_handler=self._web_audio_download,
            methods=["GET"],
            desc="下载单个音频文件"
        )
        self.context.register_web_api(
            route="/roleplay/audio/sync",
            view_handler=self._web_audio_sync,
            methods=["POST"],
            desc="从可信任服务器同步音频文件"
        )
        self.context.register_web_api(
            route="/roleplay/special_dates",
            view_handler=self._web_special_dates_page,
            methods=["GET"],
            desc="特殊日期可视化编辑器"
        )
        self.context.register_web_api(
            route="/roleplay/special_dates/save",
            view_handler=self._web_special_dates_save,
            methods=["POST"],
            desc="保存特殊日期配置"
        )
        self.context.register_web_api(
            route="/roleplay/server/audio",
            view_handler=self._web_server_audio_list,
            methods=["GET"],
            desc="从可信任服务器获取音频列表"
        )

    @llm_tool(name="weather")
    async def _tool_weather(self, event: AstrMessageEvent, city: str) -> str:
        '''查询指定城市的天气信息。

        Args:
            city(string): 城市名称，如"北京"、"上海"、"东京"
        '''
        return await function_tools.query_weather(city)

    @llm_tool(name="calculate")
    async def _tool_calculate(self, event: AstrMessageEvent, expression: str) -> str:
        '''安全地计算数学表达式。

        Args:
            expression(string): 数学表达式，如"2+3*4"、"sqrt(16)"、"sin(pi/2)"
        '''
        return function_tools.safe_calculate(expression)

    @llm_tool(name="web_search")
    async def _tool_web_search(self, event: AstrMessageEvent, query: str) -> str:
        '''搜索网络信息。

        Args:
            query(string): 搜索关键词
        '''
        return await function_tools.web_search(query)

    @llm_tool(name="shell_exec")
    async def _tool_shell_exec(self, event: AstrMessageEvent, cmd: str) -> str:
        '''执行系统命令（仅白名单命令可用）。

        Args:
            cmd(string): 要执行的命令，如"dir"、"ping 127.0.0.1"、"echo hello"
        '''
        enabled = self._config.get("tools_enabled", True) if self._config else True
        if not enabled:
            return "[已禁用] 工具功能未开启"
        return await function_tools.exec_shell(cmd)

    @llm_tool(name="get_time")
    async def _tool_get_time(self, event: AstrMessageEvent) -> str:
        '''获取当前日期和时间。'''
        return function_tools.get_current_time()

    def _json_response(self, data: dict, code: int = 200):
        from quart import Response
        return Response(
            json.dumps(data, ensure_ascii=False),
            status=code,
            content_type="application/json; charset=utf-8"
        )

    async def _web_upload_page(self):
        from quart import Response
        html = _UPLOAD_PAGE_HTML
        return Response(html, content_type="text/html; charset=utf-8")

    async def _web_upload_role(self):
        from quart import request
        try:
            files = await request.files
            if "file" not in files:
                return self._json_response({"ok": False, "msg": "未找到上传文件"}, 400)
            file = files["file"]
            import tempfile
            tmp_path = os.path.join(tempfile.gettempdir(), file.filename)
            await file.save(tmp_path)
            success, msg = self.role_manager.install_from_zip(tmp_path)
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            if success:
                return self._json_response({"ok": True, "msg": f"角色 [{msg}] 安装成功", "role_name": msg})
            return self._json_response({"ok": False, "msg": msg}, 400)
        except Exception as e:
            logger.error(f"上传角色失败: {traceback.format_exc()}")
            return self._json_response({"ok": False, "msg": str(e)}, 500)

    async def _web_list_roles(self):
        roles = self.role_manager.list_roles()
        return self._json_response({"ok": True, "roles": roles, "active": self._active_role})

    async def _web_activate_role(self):
        from quart import request
        try:
            data = await request.get_json()
            role_name = data.get("name", "")
            if not role_name:
                return self._json_response({"ok": False, "msg": "缺少角色名"}, 400)
            success = await self._activate_role(role_name)
            if success:
                return self._json_response({"ok": True, "msg": f"角色 [{role_name}] 已激活"})
            return self._json_response({"ok": False, "msg": f"角色 [{role_name}] 激活失败"}, 400)
        except Exception as e:
            return self._json_response({"ok": False, "msg": str(e)}, 500)

    async def _web_deactivate_role(self):
        self._active_role = ""
        self._role_config = None
        return self._json_response({"ok": True, "msg": "角色已停用"})

    async def _web_delete_role(self):
        from quart import request
        try:
            data = await request.get_json()
            role_name = data.get("name", "")
            if not role_name:
                return self._json_response({"ok": False, "msg": "缺少角色名"}, 400)
            if self._active_role == role_name:
                self._active_role = ""
                self._role_config = None
            success = self.role_manager.delete_role(role_name)
            if success:
                return self._json_response({"ok": True, "msg": f"角色 [{role_name}] 已删除"})
            return self._json_response({"ok": False, "msg": f"角色 [{role_name}] 删除失败"}, 400)
        except Exception as e:
            return self._json_response({"ok": False, "msg": str(e)}, 500)

    async def _web_export_role(self):
        from quart import request
        try:
            data = await request.get_json()
            role_name = data.get("name", self._active_role)
            do_clean = data.get("clean", True)
            if not role_name:
                return self._json_response({"ok": False, "msg": "缺少角色名"}, 400)
            role_config = self.role_manager.get_role_config(role_name)
            if role_config is None:
                return self._json_response({"ok": False, "msg": f"角色 [{role_name}] 不存在"}, 400)
            if do_clean:
                export_mem = self.memory_manager.get_export_safe_data()
                export_mem = self.cleaner.strip_personal_data(export_mem)
                result_path = await self.cleaner.export_clean_zip(
                    self.role_manager.get_role_dir(role_name),
                    export_mem,
                    role_config,
                    include_raw=False,
                    raw_history=None
                )
            else:
                result_path = self.role_manager.export_role_zip(role_name)
                include_memory = data.get("include_memory", True)
                include_raw = data.get("include_raw", False)
                if include_memory and result_path:
                    import zipfile
                    import tempfile
                    import yaml as _yaml
                    export_mem = self.memory_manager.get_export_safe_data()
                    export_mem = self.cleaner.strip_personal_data(export_mem)
                    extra_data = {}
                    if include_memory:
                        extra_data["_memory_short_term.json"] = export_mem.get("short_term", [])
                        extra_data["_memory_medium_summaries.json"] = export_mem.get("medium_summaries", [])
                        extra_data["_memory_role_facts.json"] = export_mem.get("role_facts", [])
                    if include_raw:
                        raw = self.memory_manager.get_raw_history()
                        extra_data["_raw_history.json"] = raw
                    tmp_zip = os.path.join(tempfile.gettempdir(), "_tmp_export.zip")
                    with zipfile.ZipFile(result_path, "r") as zf_in:
                        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf_out:
                            for item in zf_in.infolist():
                                content = zf_in.read(item.filename)
                                fname_lower = item.filename.lower()
                                if fname_lower.endswith("config.yaml") or fname_lower.endswith("config.yml"):
                                    try:
                                        cfg = _yaml.safe_load(content.decode("utf-8")) or {}
                                        cfg.pop("user_birthday", None)
                                        cfg.pop("special_dates", None)
                                        cfg.pop("user_name", None)
                                        cfg.pop("_role_dir", None)
                                        content = _yaml.dump(cfg, allow_unicode=True, default_flow_style=False).encode("utf-8")
                                    except Exception:
                                        pass
                                zf_out.writestr(item, content)
                            for fname, fdata in extra_data.items():
                                zf_out.writestr(fname, json.dumps(fdata, ensure_ascii=False, indent=2))
                    os.replace(tmp_zip, result_path)
            if result_path and os.path.exists(result_path):
                from quart import send_file
                return await send_file(result_path, as_attachment=True,
                                       attachment_filename=os.path.basename(result_path))
            return self._json_response({"ok": False, "msg": "导出失败"}, 500)
        except Exception as e:
            logger.error(f"导出角色失败: {traceback.format_exc()}")
            return self._json_response({"ok": False, "msg": str(e)}, 500)

    async def _web_export_download(self):
        role_name = self._active_role
        if not role_name:
            return self._json_response({"ok": False, "msg": "未激活角色"}, 400)
        try:
            result_path = self.role_manager.export_role_zip(role_name)
            if result_path and os.path.exists(result_path):
                from quart import send_file
                return await send_file(result_path, as_attachment=True,
                                       attachment_filename=os.path.basename(result_path))
            return self._json_response({"ok": False, "msg": "导出失败"}, 500)
        except Exception as e:
            logger.error(f"导出角色失败: {e}")
            return self._json_response({"ok": False, "msg": str(e)}, 500)

    async def _web_get_memory(self):
        from quart import request
        target = request.args.get("target", "all")
        stats = self.memory_manager.get_stats()
        if target == "user_facts":
            return self._json_response({"ok": True, "user_facts": stats.get("user_facts", [])})
        if target == "role_facts":
            return self._json_response({"ok": True, "role_facts": stats.get("role_facts", [])})
        if target == "summaries":
            return self._json_response({"ok": True, "medium_summaries": stats.get("medium_summaries", [])})
        if target == "short_term":
            return self._json_response({"ok": True, "short_term": stats.get("short_term", [])})
        return self._json_response({
            "ok": True,
            "memory": stats,
            "role": self._active_role,
        })

    async def _web_memory_page(self):
        from quart import Response
        tpl = os.path.join(os.path.dirname(__file__), "templates", "memory.html")
        try:
            with open(tpl, "r", encoding="utf-8") as f:
                return Response(f.read(), content_type="text/html; charset=utf-8")
        except Exception:
            return self._json_response({"ok": False, "msg": "模板文件丢失"}, 500)

    async def _web_clear_memory(self):
        from quart import request
        try:
            data = await request.get_json()
            target = data.get("target", "all")
            if target == "short":
                self.memory_manager.clear_short_term()
            elif target == "medium":
                self.memory_manager.clear_medium()
            elif target == "user_facts":
                self.memory_manager.clear_user_facts()
            elif target == "role_facts":
                self.memory_manager.clear_role_facts()
            elif target == "raw":
                self.memory_manager.clear_raw_history()
            else:
                self.memory_manager.clear_all()
            return self._json_response({"ok": True, "msg": f"已清除 [{target}] 记忆"})
        except Exception as e:
            return self._json_response({"ok": False, "msg": str(e)}, 500)

    async def _web_get_history(self):
        raw = self.memory_manager.get_raw_history()
        return self._json_response({"ok": True, "count": len(raw), "messages": raw})

    async def _web_query_history(self):
        from quart import request
        if request.args.get("info") == "1":
            info = self.memory_manager.get_raw_chunk_info()
            return self._json_response({"ok": True, **info})
        keyword = request.args.get("keyword", "")
        sender = request.args.get("sender", "")
        limit = int(request.args.get("limit", "50"))
        before_ts = float(request.args.get("before", "0"))
        after_ts = float(request.args.get("after", "0"))
        month = request.args.get("month", "")
        results = self.memory_manager.query_raw_history(
            keyword=keyword, sender=sender, limit=limit,
            before_ts=before_ts, after_ts=after_ts, month=month
        )
        total = sum(self.memory_manager._raw_index.values())
        return self._json_response({
            "ok": True,
            "count": len(results),
            "total": total,
            "messages": results,
        })

    async def _web_clear_history(self):
        self.memory_manager.clear_raw_history()
        return self._json_response({"ok": True, "msg": "原始聊天记录已清除"})

    async def _web_trigger_auto_reply(self):
        ok = await self.auto_reply.trigger_now("手动触发")
        if ok:
            return self._json_response({"ok": True, "msg": "自动回复已触发"})
        return self._json_response({"ok": False, "msg": "触发失败: 请确保角色已激活且配置了频道"}, 400)

    async def _web_auto_reply_status(self):
        special = self.auto_reply.special_dates
        return self._json_response({
            "ok": True,
            "enabled": self.auto_reply.enabled,
            "special_dates": special,
            "count": len(special),
        })

    async def _web_trigger_knowledge(self):
        ok = await self.knowledge_updater.trigger_now()
        if ok:
            return self._json_response({"ok": True, "msg": "知识库更新已触发"})
        return self._json_response({"ok": False, "msg": "触发失败: 请确保角色已激活并有搜索主题"}, 400)

    async def _web_knowledge_status(self):
        facts = self.knowledge_updater.load_knowledge()
        return self._json_response({
            "ok": True,
            "enabled": self.knowledge_updater.enabled,
            "facts_count": len(facts),
            "facts": facts,
        })

    async def _web_audio_list(self):
        from quart import request
        category = request.args.get("category", "all")
        if not self._active_role or not self._role_config:
            return self._json_response({"ok": False, "msg": "未激活角色"}, 400)
        role_dir = self._role_config.get("_role_dir", "")
        audio_dir = os.path.join(role_dir, "audio")
        result = {"ok": True, "role": self._active_role, "categories": {}}
        if category in ("all", "expressions"):
            expr_dir = os.path.join(audio_dir, "expressions")
            if os.path.exists(expr_dir):
                result["categories"]["expressions"] = sorted(os.listdir(expr_dir))
        if category in ("all", "music"):
            music_dir = os.path.join(audio_dir, "music")
            if os.path.exists(music_dir):
                result["categories"]["music"] = sorted(os.listdir(music_dir))
        map_path = os.path.join(audio_dir, "audio_map.json")
        if os.path.exists(map_path):
            try:
                with open(map_path, "r", encoding="utf-8") as f:
                    result["audio_map"] = json.load(f)
            except Exception:
                pass
        total = sum(len(v) for v in result["categories"].values())
        result["total"] = total
        result["audio_loaded"] = self.audio_injector.is_loaded
        return self._json_response(result)

    async def _web_audio_download(self):
        from quart import request, send_file
        category = request.args.get("category", "expressions")
        filename = request.args.get("file", "")
        if not filename:
            return self._json_response({"ok": False, "msg": "缺少文件名"}, 400)
        if not self._active_role or not self._role_config:
            return self._json_response({"ok": False, "msg": "未激活角色"}, 400)
        role_dir = self._role_config.get("_role_dir", "")
        audio_path = os.path.join(role_dir, "audio", category, filename)
        if not os.path.exists(audio_path):
            return self._json_response({"ok": False, "msg": "文件不存在"}, 404)
        return await send_file(audio_path, as_attachment=True, attachment_filename=filename)

    async def _web_audio_sync(self):
        from quart import request
        if not self._trusted_server_url:
            return self._json_response({"ok": False, "msg": "未配置可信任服务器地址"}, 400)
        if not self._active_role or not self._role_config:
            return self._json_response({"ok": False, "msg": "未激活角色"}, 400)
        try:
            data = await request.get_json()
            category = data.get("category", "expressions")
            files_to_sync = data.get("files", [])
            role_dir = self._role_config.get("_role_dir", "")
            import aiohttp
            headers = self._server_auth_headers(self._trusted_server_token)
            synced = 0
            failed = 0
            url_base = self._trusted_server_url.rstrip("/")
            async with aiohttp.ClientSession() as session:
                for fname in files_to_sync:
                    try:
                        dl_url = f"{url_base}/api/roles/{self._active_role}/audio/{category}/{fname}"
                        async with session.get(dl_url, headers=headers,
                                               timeout=aiohttp.ClientTimeout(total=30)) as resp:
                            if resp.status == 200:
                                dest_dir = os.path.join(role_dir, "audio", category)
                                os.makedirs(dest_dir, exist_ok=True)
                                dest = os.path.join(dest_dir, fname)
                                with open(dest, "wb") as f:
                                    f.write(await resp.read())
                                synced += 1
                            else:
                                failed += 1
                    except Exception:
                        failed += 1
            if synced > 0 and self.audio_injector:
                self.audio_injector.load(role_dir)
            return self._json_response({"ok": True, "synced": synced, "failed": failed,
                                         "msg": f"同步完成: {synced}成功, {failed}失败"})
        except Exception as e:
            logger.error(f"音频同步失败: {e}")
            return self._json_response({"ok": False, "msg": str(e)}, 500)

    async def _web_special_dates_page(self):
        from quart import Response
        return Response(_SPECIAL_DATES_PAGE_HTML, content_type="text/html; charset=utf-8")

    async def _web_special_dates_save(self):
        from quart import request
        try:
            data = await request.get_json()
            special_dates = data.get("special_dates", [])
            if not isinstance(special_dates, list):
                return self._json_response({"ok": False, "msg": "格式错误: 需要数组"}, 400)
            for item in special_dates:
                if not isinstance(item, dict):
                    return self._json_response({"ok": False, "msg": "格式错误: 每个条目应为对象"}, 400)
                if "date" not in item or "name" not in item:
                    return self._json_response({"ok": False, "msg": "每条必须包含 date 和 name"}, 400)
            self.auto_reply.special_dates = special_dates
            if self._config:
                self._config["special_dates"] = json.dumps(special_dates, ensure_ascii=False)
            return self._json_response({"ok": True, "msg": f"已保存 {len(special_dates)} 个特殊日期"})
        except Exception as e:
            return self._json_response({"ok": False, "msg": str(e)}, 500)

    async def _web_server_audio_list(self):
        from quart import request
        url = request.args.get("_server_url", "") or self._trusted_server_url
        token = request.args.get("_server_token", "") or self._trusted_server_token
        if not url:
            return self._json_response({"ok": False, "msg": "未配置可信任服务器地址"}, 400)
        role_name = request.args.get("role", self._active_role)
        if not role_name:
            return self._json_response({"ok": False, "msg": "缺少角色名"}, 400)
        try:
            import aiohttp
            headers = self._server_auth_headers(token)
            api_url = f"{url.rstrip('/')}/api/roles/{role_name}/audio"
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return self._json_response({"ok": True, **data})
                    return self._json_response({"ok": False, "msg": f"服务器返回 {resp.status}"}, 502)
        except Exception as e:
            logger.error(f"获取服务器音频列表失败: {e}")
            return self._json_response({"ok": False, "msg": f"连接服务器失败: {e}"}, 502)

    def _server_auth_headers(self, token: str = "") -> dict:
        headers = {}
        device_id = getattr(self, "_device_id", "")
        if device_id:
            headers["X-Device-ID"] = device_id
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def _web_export_page(self):
        from quart import Response
        return Response(_EXPORT_PAGE_HTML, content_type="text/html; charset=utf-8")

    async def _web_server_list(self):
        from quart import request
        url = request.args.get("_server_url", "") or self._trusted_server_url
        token = request.args.get("_server_token", "") or self._trusted_server_token
        if not url:
            return self._json_response({"ok": False, "msg": "未配置可信任服务器地址"}, 400)
        try:
            import aiohttp
            headers = self._server_auth_headers(token)
            api_url = f"{url.rstrip('/')}/api/roles"
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return self._json_response({"ok": True, **data})
                    return self._json_response({"ok": False, "msg": f"服务器返回 {resp.status}"}, 502)
        except Exception as e:
            logger.error(f"获取服务器角色列表失败: {e}")
            return self._json_response({"ok": False, "msg": f"连接服务器失败: {e}"}, 502)

    async def _web_server_download(self):
        from quart import request
        data = await request.get_json()
        url = data.get("_server_url", "") or self._trusted_server_url
        token = data.get("_server_token", "") or self._trusted_server_token
        if not url:
            return self._json_response({"ok": False, "msg": "未配置可信任服务器地址"}, 400)
        role_name = data.get("name", "")
        download_url = data.get("url", "")
        if not role_name:
            return self._json_response({"ok": False, "msg": "缺少角色名"}, 400)
        if not download_url:
            download_url = f"{url.rstrip('/')}/api/roles/{role_name}/download"
        import aiohttp
        import tempfile
        headers = self._server_auth_headers(token)
        tmp_path = os.path.join(tempfile.gettempdir(), f"{role_name}.zip")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(download_url, headers=headers,
                                       timeout=aiohttp.ClientTimeout(total=120)) as resp:
                    if resp.status == 200:
                        with open(tmp_path, "wb") as f:
                            f.write(await resp.read())
                        success, msg = self.role_manager.install_from_zip(tmp_path)
                        try:
                            os.remove(tmp_path)
                        except Exception:
                            pass
                        if success:
                            return self._json_response({"ok": True, "msg": f"角色 [{role_name}] 下载并安装成功"})
                        return self._json_response({"ok": False, "msg": msg}, 400)
                    return self._json_response({"ok": False, "msg": f"下载失败 {resp.status}"}, 502)
        except Exception as e:
            logger.error(f"下载角色失败: {e}")
            return self._json_response({"ok": False, "msg": str(e)}, 500)

    async def _web_server_share(self):
        url = self._trusted_server_url
        if not url:
            return self._json_response({"ok": False, "msg": "未配置可信任服务器地址"}, 400)
        from quart import request
        try:
            data = await request.get_json()
            role_name = data.get("name", self._active_role)
            if not role_name:
                return self._json_response({"ok": False, "msg": "缺少角色名"}, 400)
            role_config = self.role_manager.get_role_config(role_name)
            if role_config is None:
                return self._json_response({"ok": False, "msg": f"角色 [{role_name}] 不存在"}, 400)
            mem_stats = self.memory_manager.get_stats()
            cleaned_zip = await self.cleaner.export_clean_zip(
                self.role_manager.get_role_dir(role_name),
                mem_stats,
                role_config
            )
            if not cleaned_zip or not os.path.exists(cleaned_zip):
                return self._json_response({"ok": False, "msg": "清洗导出失败"}, 500)
            import aiohttp
            headers = self._server_auth_headers(self._trusted_server_token)
            share_url = f"{url.rstrip('/')}/api/roles/share"
            async with aiohttp.ClientSession() as session:
                with open(cleaned_zip, "rb") as f:
                    form = aiohttp.FormData()
                    form.add_field("file", f, filename=f"{role_name}.zip",
                                   content_type="application/zip")
                    form.add_field("name", role_name)
                    form.add_field("author", role_config.get("author", ""))
                    form.add_field("version", role_config.get("version", "1.0.0"))
                    async with session.post(share_url, data=form, headers=headers,
                                            timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        if resp.status == 200:
                            result = await resp.json()
                            return self._json_response({"ok": True, "msg": "分享成功", **result})
                        text = await resp.text()
                        return self._json_response({"ok": False, "msg": f"服务器返回 {resp.status}: {text}"}, 502)
        except Exception as e:
            logger.error(f"分享角色失败: {traceback.format_exc()}")
            return self._json_response({"ok": False, "msg": str(e)}, 500)

    async def _web_shop_page(self):
        from quart import Response
        return Response(_SHOP_PAGE_HTML, content_type="text/html; charset=utf-8")

    async def _web_dashboard(self):
        from quart import Response
        return Response(_DASHBOARD_PAGE_HTML, content_type="text/html; charset=utf-8")

    async def initialize(self):
        _trace("initialize START")
        await self._apply_config()
        await self.auto_reply.start()
        await self.knowledge_updater.start()
        _trace(f"initialize DONE, enabled={self._enabled}, role={self._active_role}")

    async def terminate(self):
        self._enabled = False
        await self.auto_reply.stop()
        await self.knowledge_updater.stop()
        self._active_role = ""
        self._role_config = None

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req):
        _trace(f"on_llm_request: enabled={self._enabled}, role={self._active_role}, platform={event.get_platform_name()}")
        if not self._enabled:
            _trace("on_llm_request: not enabled, skip")
            return
        if not self._active_role or not self._role_config:
            _trace(f"on_llm_request: no active role, skip")
            return
        if not self._channel_allowed(event):
            _trace("on_llm_request: channel not allowed, skip")
            return
        try:
            system_prompt = await self._build_system_prompt()
            if system_prompt:
                req.system_prompt = system_prompt
                _trace(f"on_llm_request: system_prompt injected, len={len(system_prompt)}")
            else:
                _trace("on_llm_request: system_prompt is empty")
        except Exception:
            logger.error(f"注入角色提示词失败: {traceback.format_exc()}")
            _trace(f"on_llm_request: EXCEPTION: {traceback.format_exc()}")

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        _trace("on_decorating_result START")
        if not self._enabled or not self._active_role or not self._role_config:
            _trace(f"on_decorating_result: skip, enabled={self._enabled}, role={self._active_role}")
            return
        if not self._channel_allowed(event):
            _trace("on_decorating_result: channel not allowed")
            return
        result = event.get_result()
        if not result or not result.chain:
            _trace(f"on_decorating_result: no result or empty chain, result={result is not None}")
            return
        try:
            reply_text = ""
            for comp in result.chain:
                if hasattr(comp, "text"):
                    raw = comp.text
                    cleaned = raw.strip(".。…~ ")
                    if cleaned and cleaned != raw:
                        comp.text = cleaned
                        _trace(f"on_decorating_result: stripped dots [{raw}] -> [{cleaned}]")
                    reply_text += comp.text.strip()
            _trace(f"on_decorating_result: reply_text length={len(reply_text)}")
            if not reply_text:
                _trace("on_decorating_result: empty reply_text, skip")
                return

            cmd_tts = bool(re.search(r'\[(?:tts|语音)\]', reply_text, re.IGNORECASE))
            audio_match = re.search(r'\[(?:audio|语气|voice):([^\]]+)\]', reply_text, re.IGNORECASE)
            music_match = re.search(r'\[(?:music|音乐):([^\]]+)\]', reply_text, re.IGNORECASE)
            cmd_audio_name = audio_match.group(1).strip() if audio_match else ""
            cmd_music_name = music_match.group(1).strip() if music_match else ""

            clean_text = re.sub(
                r'\[(?:tts|语音|audio|语气|voice|music|音乐)(?::[^\]]*)?\]',
                '', reply_text, flags=re.IGNORECASE
            ).strip()
            _trace(f"on_decorating_result: tts={cmd_tts}, audio={cmd_audio_name}, music={cmd_music_name}, clean_text_len={len(clean_text)}")

            for comp in result.chain:
                if hasattr(comp, "text"):
                    comp.text = clean_text

            emotion_name, _, emotion_image_rel = self.emotion_engine.detect(clean_text or reply_text)
            _trace(f"on_decorating_result: emotion={emotion_name}, image={emotion_image_rel}")
            if emotion_image_rel:
                role_dir = self._role_config.get("_role_dir", "")
                image_path = os.path.join(role_dir, emotion_image_rel)
                if os.path.exists(image_path):
                    result.chain.insert(0, Image.fromFileSystem(image_path))
                    logger.info(f"[roleplay] 附加情感图片: {emotion_image_rel}")

            did_tts = False
            if cmd_tts and clean_text:
                voice_config = self._role_config.get("voice", {})
                tts_engine = voice_config.get("engine", "disabled")
                if tts_engine != "disabled":
                    tts_timeout = self._calc_tts_timeout(clean_text, self.tts_manager._models_loaded)
                    _trace(f"on_decorating_result: [tts] requested, engine={tts_engine}, timeout={tts_timeout:.0f}s")
                    try:
                        audio_path = await asyncio.wait_for(
                            self._get_tts_audio_path(clean_text), timeout=tts_timeout
                        )
                        if audio_path and os.path.exists(audio_path):
                            audio_rec = Record.fromFileSystem(audio_path)
                            new_chain = [c for c in result.chain if not isinstance(c, Plain)]
                            new_chain.append(audio_rec)
                            result.chain.clear()
                            result.chain.extend(new_chain)
                            did_tts = True
                            logger.info(f"[roleplay] [tts] 替换为语音: {audio_path}")
                            _trace("on_decorating_result: replaced text with TTS audio")
                        elif audio_path:
                            _trace(f"on_decorating_result: TTS file missing: {audio_path}")
                        else:
                            _trace("on_decorating_result: TTS returned None")
                    except asyncio.TimeoutError:
                        logger.warning(f"[roleplay] [tts] TTS 超时({tts_timeout}s)，保留文字")
                    except Exception:
                        logger.warning(f"[roleplay] [tts] TTS 失败: {traceback.format_exc()}")

            if not did_tts and not clean_text:
                clean_text = reply_text
                for comp in result.chain:
                    if hasattr(comp, "text"):
                        comp.text = clean_text

            if cmd_audio_name and self.audio_injector and self.audio_injector.is_loaded:
                audio_rel = self.audio_injector.get_expression(cmd_audio_name)
                if not audio_rel:
                    audio_rel = self.audio_injector.get_daily_word(cmd_audio_name)
                if not audio_rel:
                    audio_rel = self.audio_injector.match_music(cmd_audio_name)
                if audio_rel:
                    audio_path = self.audio_injector.resolve_audio_for_record(audio_rel)
                    if audio_path and os.path.exists(audio_path):
                        result.chain.append(Record.fromFileSystem(audio_path))
                        logger.info(f"[roleplay] 附加语气词音频: {os.path.basename(audio_path)}")
                        _trace(f"on_decorating_result: injected audio {audio_path}")

            if cmd_music_name and self.audio_injector and self.audio_injector.is_loaded:
                music_file = self.audio_injector.match_music(cmd_music_name)
                if music_file:
                    audio_path = self.audio_injector.resolve_audio_for_record(music_file)
                    if audio_path and os.path.exists(audio_path):
                        result.chain.append(Record.fromFileSystem(audio_path))
                        logger.info(f"[roleplay] 附加音乐: {os.path.basename(audio_path)}")
                        _trace(f"on_decorating_result: music injected {audio_path}")
        except Exception:
            logger.error(f"on_decorating_result 处理失败: {traceback.format_exc()}")
            _trace(f"on_decorating_result EXCEPTION: {traceback.format_exc()}")
        _trace("on_decorating_result DONE")

    @filter.after_message_sent()
    async def after_message_sent(self, event: AstrMessageEvent):
        _trace("after_message_sent START")
        if not self._enabled or not self._active_role or not self._role_config:
            _trace("after_message_sent: skip, not active")
            return
        if not self._channel_allowed(event):
            _trace("after_message_sent: channel not allowed")
            return
        try:
            user_message = ""
            try:
                user_message = (event.message_str or "").strip()
            except Exception:
                pass
            if user_message:
                self.memory_manager.add_user_message(user_message)
            result = event.get_result()
            if result and result.chain:
                bot_reply = ""
                for comp in result.chain:
                    if hasattr(comp, "text") and comp.text:
                        bot_reply += comp.text.strip()
                if bot_reply:
                    self.memory_manager.add_bot_message(bot_reply)
            asyncio.create_task(self._background_memory_work(event))
            _trace("after_message_sent: memory task created")
        except Exception:
            _trace(f"after_message_sent EXCEPTION: {traceback.format_exc()}")
        _trace("after_message_sent DONE")

    async def _background_memory_work(self, event: AstrMessageEvent):
        try:
            await self._check_and_summarize(event)
            await self._extract_long_term_fact(event)
        except Exception:
            pass

    @filter.command_group("role")
    def role(self):
        pass

    @role.command("list")
    async def role_list(self, event: AstrMessageEvent):
        roles = self.role_manager.list_roles()
        if not roles:
            yield event.plain_result("没有已安装的角色")
            return
        lines = ["已安装的角色:"]
        for r in roles:
            status = "●" if r["name"] == self._active_role else "○"
            lines.append(f"  {status} {r['name']} (v{r.get('version', '?')})")
        yield event.plain_result("\n".join(lines))

    @role.command("switch")
    async def role_switch(self, event: AstrMessageEvent, role_name: str):
        success = await self._activate_role(role_name)
        if success:
            yield event.plain_result(f"已切换到角色 [{role_name}]")
        else:
            yield event.plain_result(f"切换失败: 角色 [{role_name}] 不存在或配置无效")

    @role.command("off")
    async def role_off(self, event: AstrMessageEvent):
        self._active_role = ""
        self._role_config = None
        yield event.plain_result("角色扮演已关闭")

    @role.command("status")
    async def role_status(self, event: AstrMessageEvent):
        if self._active_role and self._role_config:
            cfg = self._role_config
            mem = self.memory_manager.get_stats()
            yield event.plain_result(
                f"当前角色: {self._active_role}\n"
                f"描述: {cfg.get('persona', '')[:100]}...\n"
                f"短期记忆: {mem['short_term_count']}条\n"
                f"中期摘要: {mem['medium_summary_count']}份\n"
                f"用户事实: {mem['user_fact_count']}条\n"
                f"角色知识: {mem['role_fact_count']}条\n"
                f"原始记录: {mem['raw_history_count']}条"
            )
        else:
            yield event.plain_result("当前未激活角色")

    @role.command("ttstest")
    async def role_ttstest(self, event: AstrMessageEvent, text: str = ""):
        if not self._active_role or not self._role_config:
            yield event.plain_result("请先激活角色")
            return
        test_text = text.strip() if text.strip() else "あの、おはようございます。私は緒山真尋です。"
        voice_config = self._role_config.get("voice", {})
        engine = voice_config.get("engine", "disabled")
        yield event.plain_result(f"🔊 TTS 诊断开始\n引擎: {engine}\n测试文本: {test_text}\n---")

        if engine == "disabled":
            yield event.plain_result("❌ TTS 引擎未启用 (disabled)")
            return

        # Step 1: check GPT-SoVITS online
        online = await self.tts_manager.check_gpt_sovits_online()
        yield event.plain_result(f"Step1 API在线检测: {'✅ 在线' if online else '❌ 离线'}")

        if not online:
            yield event.plain_result("Step1.5 尝试启动 GPT-SoVITS...（最长60s）")
            online = await self.tts_manager.ensure_gpt_sovits_online()
            yield event.plain_result(f"Step1.5 启动结果: {'✅ 成功' if online else '❌ 失败'}")

        if not online:
            yield event.plain_result("⛔ GPT-SoVITS 不可用，终止诊断")
            return

        # Step 2: load models
        gpt_m = voice_config.get("gpt_model", "")
        sovits_m = voice_config.get("sovits_model", "")
        yield event.plain_result(f"Step2 加载模型...\n  GPT: {os.path.basename(gpt_m) if gpt_m else '未配置'}\n  SoVITS: {os.path.basename(sovits_m) if sovits_m else '未配置'}")

        if gpt_m and sovits_m:
            loaded = await self.tts_manager._load_models(gpt_m, sovits_m)
            yield event.plain_result(f"Step2 模型加载: {'✅ 成功' if loaded else '❌ 失败'}")
            if not loaded:
                yield event.plain_result("⛔ 模型加载失败，终止诊断")
                return
        else:
            yield event.plain_result("⚠️ 未配置模型路径，跳过加载")

        # Step 3: synthesize
        yield event.plain_result("Step3 开始合成语音...（最长60s）")
        try:
            audio_path = await asyncio.wait_for(
                self._get_tts_audio_path(test_text), timeout=60.0
            )
            if audio_path and os.path.exists(audio_path):
                size_kb = os.path.getsize(audio_path) / 1024
                yield event.plain_result(f"Step3 语音合成: ✅ 成功\n  文件: {os.path.basename(audio_path)}\n  大小: {size_kb:.1f} KB\n  路径: {audio_path}")
                yield event.plain_result("✅ TTS 全链路诊断通过！语音模型可以正常工作。")
            elif audio_path:
                yield event.plain_result(f"Step3 语音合成: ❌ 返回了路径但文件不存在\n  {audio_path}")
            else:
                yield event.plain_result("Step3 语音合成: ❌ 返回 None")
        except asyncio.TimeoutError:
            yield event.plain_result("Step3 语音合成: ❌ 超时(60s)")
        except Exception as e:
            yield event.plain_result(f"Step3 语音合成: ❌ 异常\n  {e}")

    @role.command("ping")
    async def role_ping(self, event: AstrMessageEvent):
        '''测试与可信任服务器的连通性。'''
        server = self._trusted_server_url
        if not server:
            yield event.plain_result("❌ 未配置可信任服务器地址 (trusted_server_url)")
            return
        url_base = server.rstrip("/")
        yield event.plain_result(f"🔍 正在 ping {url_base} ...")
        import aiohttp
        from urllib.parse import quote
        headers = self._server_auth_headers(self._trusted_server_token)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{url_base}/api/ping", headers=headers,
                                       timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    ct = resp.headers.get("Content-Type", "")
                    body = await resp.text()
                    if "text/html" in ct:
                        yield event.plain_result(
                            f"❌ 服务器返回了HTML而不是JSON\n"
                            f"  状态码: {resp.status}\n"
                            f"  这通常意味着反向代理(Nginx/Caddy)没有把 /api/ 路径转发到Flask\n"
                            f"  请检查服务器端的反向代理配置"
                        )
                        return
                    try:
                        data = await resp.json() if hasattr(resp, 'json') else __import__('json').loads(body)
                    except Exception:
                        yield event.plain_result(f"❌ 响应不是JSON\n  Content-Type: {ct}\n  前100字符: {body[:100]}")
                        return
                    yield event.plain_result(
                        f"✅ 服务器连通\n"
                        f"  地址: {url_base}\n"
                        f"  状态: {resp.status}\n"
                        f"  数据: {data}"
                    )
        except aiohttp.ClientConnectorError:
            yield event.plain_result(f"❌ 无法连接到 {url_base} — 服务器未启动或地址错误")
        except aiohttp.ClientOSError as e:
            yield event.plain_result(f"❌ 连接错误: {e}")
        except Exception as e:
            yield event.plain_result(f"❌ ping 失败: {e}")

    @role.command("update")
    async def role_update(self, event: AstrMessageEvent, target: str = ""):
        '''从可信任服务器同步角色资源(音频/图片)。
        target: ""(全部) / audio / images / status
        按文件大小对比,只下载变化的。角色名自动URL编码。
        '''
        if not self._active_role or not self._role_config:
            yield event.plain_result("请先激活角色")
            return
        server = self._trusted_server_url
        if not server:
            yield event.plain_result("未配置可信任服务器地址")
            return
        target = target.strip().lower()
        url_base = server.rstrip("/")
        role = self._active_role
        yield event.plain_result(f"🔍 连接 {url_base}\n   角色: {role}\n   范围: {target or '全部'}")
        import aiohttp
        from urllib.parse import quote
        headers = self._server_auth_headers(self._trusted_server_token)
        role_encoded = quote(role, safe="")
        try:
            async with aiohttp.ClientSession() as session:
                audio_url = f"{url_base}/api/roles/{role_encoded}/audio"
                async with session.get(audio_url, headers=headers,
                                       timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    ct = resp.headers.get("Content-Type", "")
                    if "text/html" in ct:
                        yield event.plain_result(
                            f"❌ 服务器返回HTML — 反向代理可能未配置 /api/ 路由\n"
                            f"  请确保 Nginx/Caddy 将 /api/* 代理到 Flask(端口8766)"
                        )
                        return
                    if resp.status != 200:
                        body = await resp.text()
                        yield event.plain_result(f"❌ 服务器返回 {resp.status}\n  {body[:200]}")
                        return
                    audio_info = await resp.json()
                if not audio_info.get("ok"):
                    yield event.plain_result(f"❌ 服务器: {audio_info.get('msg','未知错误')}")
                    return
                remote_version = audio_info.get("version", "?")
                expressions = audio_info.get("expressions", [])
                music = audio_info.get("music", [])
                total_audio = len(expressions) + len(music)
            if target == "status":
                role_dir = self._role_config.get("_role_dir", "")
                local_audio_dir = os.path.join(role_dir, "audio", "expressions") if role_dir else ""
                local_audio_cnt = len(os.listdir(local_audio_dir)) if local_audio_dir and os.path.isdir(local_audio_dir) else 0
                yield event.plain_result(
                    f"📊 [{role}] 版本对比\n"
                    f"  本地: v{self._role_config.get('version','?')}  音频{local_audio_cnt}个\n"
                    f"  远程: v{remote_version}  音频{total_audio}个\n"
                    f"  服务器: {url_base}"
                )
                return
            role_dir = self._role_config.get("_role_dir", "")
            if not role_dir or not os.path.isdir(role_dir):
                yield event.plain_result("❌ 本地角色目录不存在")
                return
            to_sync = []
            if target in ("audio", ""):
                for item in expressions:
                    to_sync.append(("audio/expressions", item["name"], item.get("size", 0)))
                for item in music:
                    to_sync.append(("audio/music", item["name"], item.get("size", 0)))
            updated = 0
            skipped = 0
            for category, fname, r_size in to_sync:
                if target == "images" and not category.startswith("images"):
                    continue
                local_path = os.path.join(role_dir, category, fname)
                if os.path.exists(local_path) and os.path.getsize(local_path) == r_size:
                    skipped += 1
                    continue
                cat_dir = category.split("/")[0]
                subcat = category.split("/")[1] if "/" in category else ""
                dl_url = f"{url_base}/api/roles/{role_encoded}/{cat_dir}/{subcat}/{quote(fname, safe='')}"
                async with session.get(dl_url, headers=headers,
                                       timeout=aiohttp.ClientTimeout(total=15)) as dl_resp:
                    if dl_resp.status == 200:
                        data = await dl_resp.read()
                        os.makedirs(os.path.dirname(local_path), exist_ok=True)
                        with open(local_path, "wb") as f:
                            f.write(data)
                        updated += 1
            msg = f"✅ [{role}] 同步完成\n"
            if target in ("audio", ""):
                msg += f"音频: 更新{updated} 跳过{skipped}\n"
            if target in ("images", ""):
                msg += f"图片: 更新{updated} 跳过{skipped}\n"
            if updated == 0 and skipped == 0:
                msg += "远程没有可用的音频/图片资源"
            yield event.plain_result(msg.strip())
        except aiohttp.ClientConnectorError:
            yield event.plain_result(f"❌ 无法连接 {url_base} — 服务器未启动或地址错误")
        except Exception as e:
            err_msg = str(e)
            if "JSON" in err_msg or "json" in err_msg.lower():
                yield event.plain_result(
                    f"❌ JSON解析失败 — 服务器返回了非JSON内容\n"
                    f"  可能原因: 反向代理未将 /api/ 转发到Flask\n"
                    f"  错误: {err_msg[:150]}"
                )
            else:
                yield event.plain_result(f"❌ 更新失败: {err_msg[:300]}")


_UPLOAD_PAGE_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>上传角色ZIP</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,-apple-system,sans-serif;background:#1a1a2e;color:#e0e0e0;min-height:100vh;display:flex;align-items:center;justify-content:center}
.card{background:#16213e;border-radius:16px;padding:40px;max-width:500px;width:90%;box-shadow:0 8px 32px rgba(0,0,0,.3);text-align:center}
h1{font-size:24px;margin-bottom:8px;color:#e94560}
p{font-size:14px;color:#888;margin-bottom:24px}
.dropzone{border:2px dashed #444;border-radius:12px;padding:48px 24px;cursor:pointer;transition:.2s;margin-bottom:20px}
.dropzone:hover,.dropzone.drag{border-color:#e94560;background:rgba(233,69,96,.05)}
.dropzone svg{width:48px;height:48px;margin-bottom:12px;color:#666}
.dropzone .title{font-size:16px;margin-bottom:4px}
.dropzone .hint{font-size:12px;color:#666}
#fileInput{display:none}
#status{margin-top:12px;padding:12px;border-radius:8px;font-size:14px;display:none}
#status.success{display:block;background:rgba(0,200,83,.15);color:#00c853}
#status.error{display:block;background:rgba(255,82,82,.15);color:#ff5252}
.btn{display:inline-block;padding:10px 24px;border-radius:8px;background:#e94560;color:#fff;border:none;cursor:pointer;font-size:14px;transition:.2s}
.btn:hover{background:#c73b53}
.btn:disabled{opacity:.5;cursor:default}
</style>
</head>
<body>
<div class="card">
<h1>角色扮演 — 上传角色</h1>
<p>上传角色ZIP压缩包，开箱即用</p>
<div style="display:flex;gap:8px;margin-bottom:16px">
<a href="/api/plug/roleplay/dashboard" style="color:#888;text-decoration:none;font-size:12px;padding:4px 10px;border:1px solid #444;border-radius:6px">🏠 管理中心</a>
<a href="/api/plug/roleplay/shop" style="color:#888;text-decoration:none;font-size:12px;padding:4px 10px;border:1px solid #444;border-radius:6px">🛒 商店</a>
<a href="/api/plug/roleplay/export_page" style="color:#888;text-decoration:none;font-size:12px;padding:4px 10px;border:1px solid #444;border-radius:6px">📦 导出</a>
</div>
<div class="dropzone" id="dropzone">
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
<div class="title">点击或拖拽ZIP文件到此处</div>
<div class="hint">支持 .zip 格式，需包含 config.yaml</div>
</div>
<input type="file" id="fileInput" accept=".zip">
<button class="btn" id="uploadBtn" disabled>上传并安装</button>
<div id="status"></div>
</div>
<script>
var file=null;
var drop=document.getElementById('dropzone');
var input=document.getElementById('fileInput');
var btn=document.getElementById('uploadBtn');
var status=document.getElementById('status');
drop.addEventListener('click',function(){input.click()});
drop.addEventListener('dragover',function(e){e.preventDefault();drop.classList.add('drag')});
drop.addEventListener('dragleave',function(){drop.classList.remove('drag')});
drop.addEventListener('drop',function(e){e.preventDefault();drop.classList.remove('drag');handleFile(e.dataTransfer.files[0])});
input.addEventListener('change',function(){handleFile(input.files[0])});
function handleFile(f){if(!f||!f.name.endsWith('.zip')){showStatus('只支持 .zip 文件','error');return}file=f;btn.disabled=false;status.style.display='none'}
btn.addEventListener('click',async function(){if(!file)return;btn.disabled=true;btn.textContent='上传中...';var fd=new FormData();fd.append('file',file);try{var r=await fetch('/api/plug/roleplay/upload',{method:'POST',body:fd});var d=await r.json();if(d.ok){showStatus(d.msg,'success');btn.textContent='安装完成'}else{showStatus(d.msg,'error');btn.disabled=false;btn.textContent='上传并安装'}}catch(e){showStatus('上传失败: '+e.message,'error');btn.disabled=false;btn.textContent='上传并安装'}});
function showStatus(msg,type){status.textContent=msg;status.className=type;status.style.display='block'}
</script>
</body>
</html>"""

_SPECIAL_DATES_PAGE_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>特殊日期管理</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,-apple-system,sans-serif;background:#0f0f1a;color:#e0e0e0;min-height:100vh}
.header{background:#1a1a2e;padding:16px 32px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #2a2a4a}
.header h1{font-size:20px;color:#e94560}
.header .links a{color:#888;text-decoration:none;margin-left:12px;font-size:13px;padding:6px 12px;border-radius:6px;transition:.2s}
.header .links a:hover{color:#fff;background:#2a2a4a}
.main{max-width:900px;margin:24px auto;padding:0 24px}
.section{background:#1a1a2e;border-radius:12px;border:1px solid #2a2a4a;padding:24px;margin-bottom:20px}
.section-header{font-size:16px;font-weight:600;margin-bottom:16px;display:flex;align-items:center;gap:8px}
.section-header .badge{font-size:11px;padding:2px 8px;border-radius:10px;background:rgba(233,69,96,.15);color:#e94560}
.textarea-row{margin-bottom:12px}
textarea{width:100%;padding:14px;border-radius:10px;border:1px solid #444;background:#0f0f1a;color:#e0e0e0;font-size:13px;font-family:'Cascadia Code','Fira Code',monospace;line-height:1.5;resize:vertical;min-height:260px}
textarea:focus{outline:none;border-color:#e94560}
.hint{font-size:12px;color:#888;margin-bottom:16px;line-height:1.6}
.hint code{background:#2a2a4a;padding:1px 6px;border-radius:4px;color:#e94560;font-size:11px}
.preview-table{width:100%;border-collapse:collapse;margin-top:12px}
.preview-table th{text-align:left;padding:10px;border-bottom:2px solid #2a2a4a;font-size:12px;color:#888;text-transform:uppercase}
.preview-table td{padding:10px;border-bottom:1px solid #2a2a4a;font-size:13px}
.preview-table .date{color:#e94560;font-weight:600;white-space:nowrap;width:80px}
.preview-table .name{color:#fff;font-weight:500}
.preview-table .reason{color:#888;font-size:12px}
.preview-table .tag{padding:2px 8px;border-radius:4px;font-size:10px;font-weight:600}
.tag-role{background:rgba(233,69,96,.15);color:#e94560}
.tag-user{background:rgba(68,138,255,.15);color:#448aff}
.tag-general{background:rgba(0,200,83,.12);color:#00c853}
.empty-state{text-align:center;padding:40px;color:#666}
.actions{display:flex;gap:10px;margin-top:16px;flex-wrap:wrap}
.btn{padding:10px 20px;border-radius:8px;border:none;cursor:pointer;font-size:14px;font-weight:600;transition:.2s}
.btn-primary{background:#e94560;color:#fff}
.btn-primary:hover{background:#c73b53}
.btn-primary:disabled{opacity:.4;cursor:default}
.btn-outline{background:transparent;color:#aaa;border:1px solid #444}
.btn-outline:hover{background:#2a2a4a;color:#fff}
.btn-success{background:rgba(0,200,83,.15);color:#00c853;border:1px solid rgba(0,200,83,.3)}
.btn-success:hover{background:rgba(0,200,83,.25)}
.msg{margin-top:12px;padding:12px;border-radius:8px;font-size:13px;display:none}
.msg.ok{display:block;background:rgba(0,200,83,.1);color:#00c853}
.msg.err{display:block;background:rgba(255,82,82,.1);color:#ff5252}
.info-box{background:rgba(68,138,255,.08);border:1px solid rgba(68,138,255,.2);border-radius:8px;padding:12px 16px;margin-top:16px;font-size:13px;color:#448aff;line-height:1.5}
</style>
</head>
<body>
<div class="header">
<h1>📅 特殊日期管理</h1>
<div class="links">
<a href="/api/plug/roleplay/dashboard">🏠 管理中心</a>
<a href="/api/plug/roleplay/memory_view">🧠 记忆</a>
<a href="/api/plug/roleplay/export_page">📦 导出</a>
</div>
</div>

<div class="main">
<div class="section">
<div class="section-header">📝 编辑特殊日期 <span class="badge">每行一个</span></div>
<div class="hint">
<p>每行一条记录，格式: <code>MM-DD | 名称 | 说明</code></p>
<p>示例:</p>
<pre style="background:#0f0f1a;padding:8px;border-radius:6px;margin-top:4px;font-size:12px;color:#aaa">
03-03 | 绪山真寻生日 | 角色生日，根据作品设定
05-05 | 端午节 | 传统节日
07-07 | 七夕 | 乞巧节</pre>
</div>
<div class="textarea-row">
<textarea id="datesInput" placeholder="MM-DD | 名称 | 说明&#10;03-03 | 绪山真寻生日 | 角色生日&#10;05-05 | 端午节 | 传统节日"></textarea>
</div>
<div class="actions">
<button class="btn btn-primary" onclick="saveDates()">💾 保存特殊日期</button>
<button class="btn btn-outline" onclick="loadDates()">🔄 重新加载</button>
<button class="btn btn-success" onclick="addExample()">📋 添加示例</button>
</div>
<div id="datesMsg" class="msg"></div>
</div>

<div class="section">
<div class="section-header">👁 预览</div>
<div id="previewArea">
<div class="empty-state">暂无特殊日期，请在编辑区添加</div>
</div>
</div>

<div class="info-box">
💡 <strong>提示：</strong>导出角色 ZIP 时会自动过滤掉使用者生日和敏感信息，只保留角色相关的特殊日期。分享时不会泄露个人隐私。
</div>
</div>

<script>
var URL_PREFIX = location.origin;

function showMsg(msg, type) {
var el = document.getElementById('datesMsg');
el.textContent = msg;
el.className = 'msg ' + type;
setTimeout(function(){ el.className = 'msg'; }, 4000);
}

function parseDates(text) {
var lines = text.split('\n');
var dates = [];
lines.forEach(function(line) {
line = line.trim();
if (!line || line.startsWith('#')) return;
var parts = line.split('|');
if (parts.length < 2) return;
var date = parts[0].trim();
var name = parts[1].trim();
var reason = parts.length > 2 ? parts.slice(2).join('|').trim() : '';
if (/^\d{2}-\d{2}$/.test(date) && name) {
dates.push({date: date, name: name, reason: reason});
}
});
return dates;
}

function renderPreview(dates) {
var el = document.getElementById('previewArea');
if (!dates.length) {
el.innerHTML = '<div class="empty-state">暂无特殊日期，请在编辑区添加</div>';
return;
}
var html = '<table class="preview-table"><tr><th>日期</th><th>名称</th><th>说明</th><th>类型</th></tr>';
dates.forEach(function(d) {
var reasonLower = (d.reason||'').toLowerCase();
var tagClass = 'tag-general';
var tagText = '一般';
if (reasonLower.includes('角色') || reasonLower.includes('作品')) {
tagClass = 'tag-role'; tagText = '角色';
} else if (reasonLower.includes('用户') || reasonLower.includes('使用者') || reasonLower.includes('生日')) {
tagClass = 'tag-user'; tagText = '用户';
}
html += '<tr>'
+ '<td class="date">' + d.date + '</td>'
+ '<td class="name">' + d.name + '</td>'
+ '<td class="reason">' + (d.reason||'') + '</td>'
+ '<td><span class="tag ' + tagClass + '">' + tagText + '</span></td>'
+ '</tr>';
});
html += '</table>';
el.innerHTML = html;
}

function loadDates() {
fetch(URL_PREFIX + '/api/plug/roleplay/auto_reply/status')
.then(function(r){ return r.json(); })
.then(function(d) {
if (!d.ok) { showMsg('加载失败', 'err'); return; }
var dates = d.special_dates || [];
var text = '';
dates.forEach(function(item) {
text += item.date + ' | ' + item.name;
if (item.reason) text += ' | ' + item.reason;
text += '\n';
});
document.getElementById('datesInput').value = text;
renderPreview(dates);
showMsg('已加载 ' + dates.length + ' 个特殊日期', 'ok');
})
.catch(function(e){ showMsg('加载失败: ' + e.message, 'err'); });
}

function saveDates() {
var text = document.getElementById('datesInput').value;
var dates = parseDates(text);
if (!dates.length) { showMsg('请至少输入一条有效日期', 'err'); return; }
var btn = event.target;
btn.disabled = true; btn.textContent = '保存中...';
fetch(URL_PREFIX + '/api/plug/roleplay/special_dates/save', {
method: 'POST',
headers: {'Content-Type': 'application/json'},
body: JSON.stringify({special_dates: dates})
})
.then(function(r){ return r.json(); })
.then(function(d) {
if (d.ok) {
showMsg(d.msg, 'ok');
renderPreview(dates);
} else {
showMsg(d.msg || '保存失败', 'err');
}
})
.catch(function(e){ showMsg('保存失败: ' + e.message, 'err'); })
.finally(function(){ btn.disabled = false; btn.textContent = '💾 保存特殊日期'; });
}

function addExample() {
var el = document.getElementById('datesInput');
var existing = el.value.trim();
var examples = [
'03-03 | 绪山真寻生日 | 角色生日，根据作品设定',
'05-05 | 端午节 | 传统节日',
'07-07 | 七夕 | 乞巧节',
'10-01 | 国庆节 | 国家节日'
];
var toAdd = examples.filter(function(e){ return existing.indexOf(e) === -1; });
if (toAdd.length > 0) {
el.value = existing + (existing ? '\n' : '') + toAdd.join('\n');
renderPreview(parseDates(el.value));
showMsg('已添加 ' + toAdd.length + ' 条示例', 'ok');
} else {
showMsg('所有示例已存在', 'err');
}
}

document.getElementById('datesInput').addEventListener('input', function() {
renderPreview(parseDates(this.value));
});

loadDates();
</script>
</body>
</html>"""


_EXPORT_PAGE_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>角色导出 — 可视化选择</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,-apple-system,sans-serif;background:#0f0f1a;color:#e0e0e0;min-height:100vh}
.header{background:#1a1a2e;padding:16px 32px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #2a2a4a}
.header h1{font-size:20px;color:#e94560}
.header .links a{color:#888;text-decoration:none;margin-left:12px;font-size:13px;padding:6px 12px;border-radius:6px;transition:.2s}
.header .links a:hover{color:#fff;background:#2a2a4a}
.main{max-width:1000px;margin:24px auto;padding:0 24px}
.toolbar{display:flex;gap:12px;align-items:center;margin-bottom:20px;flex-wrap:wrap}
.toolbar label{font-size:13px;color:#aaa;display:flex;align-items:center;gap:6px;cursor:pointer}
.toolbar input[type=checkbox]{width:16px;height:16px;accent-color:#e94560}
.role-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px}
.role-card{background:#1a1a2e;border-radius:12px;border:1px solid #2a2a4a;padding:20px;transition:.2s;display:flex;flex-direction:column;gap:12px}
.role-card:hover{border-color:#e94560;transform:translateY(-2px)}
.role-card.active{border-color:#e94560;background:rgba(233,69,96,.05)}
.role-card .r-top{display:flex;align-items:center;gap:12px}
.role-card .r-avatar{width:48px;height:48px;border-radius:12px;background:linear-gradient(135deg,#2a2a4a,#3a3a5a);display:flex;align-items:center;justify-content:center;font-size:24px;flex-shrink:0}
.role-card .r-name{font-size:16px;font-weight:700;color:#fff}
.role-card .r-meta{font-size:11px;color:#888;margin-top:2px}
.role-card .r-stats{display:flex;gap:12px;flex-wrap:wrap;font-size:11px;color:#666}
.role-card .r-stat{padding:3px 8px;border-radius:4px;background:#0f0f1a}
.role-card .r-stat.highlight{color:#e94560;background:rgba(233,69,96,.1)}
.role-card .r-actions{display:flex;gap:8px;margin-top:auto}
.btn{padding:8px 16px;border-radius:8px;border:none;cursor:pointer;font-size:13px;font-weight:600;transition:.2s;flex:1}
.btn-primary{background:#e94560;color:#fff}
.btn-primary:hover{background:#c73b53}
.btn-primary:disabled{opacity:.4;cursor:default}
.btn-outline{background:transparent;color:#aaa;border:1px solid #444}
.btn-outline:hover{background:#2a2a4a;color:#fff}
.btn-share{background:rgba(0,200,83,.15);color:#00c853;border:1px solid rgba(0,200,83,.3)}
.btn-share:hover{background:rgba(0,200,83,.25)}
.empty-state{grid-column:1/-1;text-align:center;padding:60px 20px;color:#666}
.empty-state .icon{font-size:48px;margin-bottom:12px}
.msg{position:fixed;bottom:24px;right:24px;padding:14px 20px;border-radius:10px;font-size:13px;z-index:999;display:none;max-width:400px}
.msg.ok{display:block;background:#00c853;color:#fff}
.msg.err{display:block;background:#ff5252;color:#fff}
.msg.info{display:block;background:#448aff;color:#fff}
.badge{display:inline-block;padding:2px 8px;border-radius:8px;font-size:10px;font-weight:600}
.badge-active{background:rgba(0,200,83,.15);color:#00c853}
.badge-inactive{background:rgba(255,82,82,.15);color:#ff5252}
.privacy-note{background:rgba(68,138,255,.08);border:1px solid rgba(68,138,255,.2);border-radius:10px;padding:14px 18px;margin-bottom:16px;font-size:12px;color:#448aff;line-height:1.6}
</style>
</head>
<body>
<div class="header">
<h1>📦 角色导出 — 可视化选择</h1>
<div class="links">
<a href="/api/plug/roleplay/dashboard">🏠 管理中心</a>
<a href="/api/plug/roleplay/shop">🛒 商店</a>
<a href="/api/plug/roleplay/special_dates">📅 特殊日期</a>
<a href="/api/plug/roleplay/memory_view">🧠 记忆</a>
</div>
</div>
<div class="main">
<div class="toolbar">
<label><input type="checkbox" id="chkClean" checked> 🧹 隐私清洗（移除使用者信息）</label>
<label><input type="checkbox" id="chkMemory" checked> 📝 含记忆数据</label>
<label><input type="checkbox" id="chkRaw" checked> 📜 含原始记录</label>
</div>
<div class="privacy-note">
🔒 <b>隐私保护：</b>导出时自动移除使用者生日、用户名、special_dates 中的用户信息。只保留角色相关内容，分享安全。
</div>
<div class="role-grid" id="roleGrid">
<div class="empty-state"><div class="icon">⏳</div><p>加载中…</p></div>
</div>
</div>
<div class="msg" id="msg"></div>
<script>
var URL = location.origin;
var roles = [];
var active = '';

function showMsg(text, type) {
var el = document.getElementById('msg');
el.textContent = text;
el.className = 'msg ' + type;
setTimeout(function(){ el.className = 'msg'; }, 3500);
}

async function loadRoles() {
try {
var r = await (await fetch(URL + '/api/plug/roleplay/list')).json();
roles = r.roles || [];
active = r.active || '';
renderRoles();
} catch(e) {
document.getElementById('roleGrid').innerHTML = '<div class="empty-state"><div class="icon">❌</div><p>加载失败: ' + e.message + '</p></div>';
}
}

function renderRoles() {
var grid = document.getElementById('roleGrid');
if (!roles.length) {
grid.innerHTML = '<div class="empty-state"><div class="icon">📭</div><p>暂无已安装角色，请先上传或从商店下载</p></div>';
return;
}
var html = '';
roles.forEach(function(ro) {
var isActive = ro.name === active;
html += '<div class="role-card' + (isActive ? ' active' : '') + '">';
html += '<div class="r-top">';
html += '<div class="r-avatar">🎭</div>';
html += '<div>';
html += '<div class="r-name">' + ro.name + (isActive ? ' <span class="badge badge-active">当前</span>' : '') + '</div>';
html += '<div class="r-meta">v' + (ro.version||'?') + ' · ' + (ro.author||'未知') + '</div>';
html += '</div></div>';
html += '<div class="r-stats">';
if (ro.emotions && ro.emotions.length) html += '<span class="r-stat">😊 ' + ro.emotions.length + '种情绪</span>';
if (ro.image_count) html += '<span class="r-stat">🖼 ' + ro.image_count + '张图片</span>';
if (ro.has_voice) html += '<span class="r-stat highlight">🎤 有语音</span>';
html += '</div>';
html += '<div class="r-actions">';
html += '<button class="btn btn-primary" onclick="downloadZip(\'' + ro.name + '\',this)">⬇ 下载ZIP</button>';
if (ro.has_voice) {
html += '<button class="btn btn-share" onclick="shareRole(\'' + ro.name + '\',this)">📤 分享</button>';
}
html += '</div></div>';
});
grid.innerHTML = html;
}

async function downloadZip(name, btn) {
btn.disabled = true; btn.textContent = '打包中…';
var clean = document.getElementById('chkClean').checked;
var include_memory = document.getElementById('chkMemory').checked;
var include_raw = document.getElementById('chkRaw').checked;
try {
var r = await fetch(URL + '/api/plug/roleplay/export', {
method: 'POST',
headers: {'Content-Type': 'application/json'},
body: JSON.stringify({name: name, clean: clean, include_memory: include_memory, include_raw: include_raw})
});
var ct = r.headers.get('content-type') || '';
if (ct.includes('application/zip') || ct.includes('application/octet-stream')) {
var blob = await r.blob();
var a = document.createElement('a');
a.href = URL.createObjectURL(blob);
a.download = name + '.zip';
a.click();
showMsg('✅ ' + name + '.zip 下载已开始！', 'ok');
} else {
var d = await r.json();
showMsg('❌ ' + (d.msg || '导出失败'), 'err');
}
} catch(e) { showMsg('❌ ' + e.message, 'err'); }
btn.disabled = false; btn.textContent = '⬇ 下载ZIP';
}

async function shareRole(name, btn) {
btn.disabled = true; btn.textContent = '分享中…';
try {
var r = await (await fetch(URL + '/api/plug/roleplay/server/share', {
method: 'POST',
headers: {'Content-Type': 'application/json'},
body: JSON.stringify({name: name})
})).json();
if (r.ok) { showMsg('✅ 分享成功！', 'ok'); }
else { showMsg('❌ ' + (r.msg || '分享失败'), 'err'); }
} catch(e) { showMsg('❌ ' + e.message, 'err'); }
btn.disabled = false; btn.textContent = '📤 分享';
}

loadRoles();
</script>
</body>
</html>"""


_SHOP_PAGE_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>角色商店</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,-apple-system,sans-serif;background:#1a1a2e;color:#e0e0e0;min-height:100vh}
.header{background:#16213e;padding:20px 40px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #2a2a4a}
.header h1{font-size:22px;color:#e94560}
.header .server-status{font-size:13px;color:#888}
.server-config{padding:20px 40px;background:#16213e;margin:20px 40px;border-radius:12px;display:flex;gap:12px;flex-wrap:wrap;align-items:center}
.server-config input{flex:1;min-width:200px;padding:10px 14px;border-radius:8px;border:1px solid #444;background:#1a1a2e;color:#e0e0e0;font-size:14px}
.server-config button{padding:10px 20px;border-radius:8px;background:#e94560;color:#fff;border:none;cursor:pointer;font-size:14px;transition:.2s}
.server-config button:hover{background:#c73b53}
.server-config button:disabled{opacity:.5;cursor:default}
.controls{padding:0 40px;display:flex;gap:12px;margin-bottom:16px}
.controls input{flex:1;max-width:300px;padding:10px 14px;border-radius:8px;border:1px solid #444;background:#1a1a2e;color:#e0e0e0;font-size:14px}
.controls button{padding:10px 20px;border-radius:8px;background:#2a2a4a;color:#e0e0e0;border:1px solid #444;cursor:pointer;font-size:14px;transition:.2s}
.controls button:hover{background:#3a3a5a}
.controls .refresh-btn{background:#e94560;border:none}
.controls .refresh-btn:hover{background:#c73b53}
.role-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:20px;padding:0 40px 40px}
.role-card{background:#16213e;border-radius:12px;overflow:hidden;border:1px solid #2a2a4a;transition:.2s;display:flex;flex-direction:column}
.role-card:hover{border-color:#e94560;transform:translateY(-2px)}
.role-card .cover{height:180px;background:linear-gradient(135deg,#1a1a2e,#2a2a4a);display:flex;align-items:center;justify-content:center;font-size:64px;overflow:hidden}
.role-card .cover img{width:100%;height:100%;object-fit:cover}
.role-card .info{padding:16px;flex:1;display:flex;flex-direction:column}
.role-card .name{font-size:18px;font-weight:600;margin-bottom:4px;color:#fff}
.role-card .author{font-size:12px;color:#888;margin-bottom:8px}
.role-card .desc{font-size:13px;color:#aaa;margin-bottom:12px;flex:1;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.role-card .meta{font-size:11px;color:#666;margin-bottom:12px;display:flex;gap:16px}
.role-card .actions{display:flex;gap:8px}
.role-card .actions button{flex:1;padding:8px 0;border-radius:8px;border:none;cursor:pointer;font-size:13px;transition:.2s}
.role-card .btn-download{background:#e94560;color:#fff}
.role-card .btn-download:hover{background:#c73b53}
.role-card .btn-download:disabled{opacity:.5;cursor:default}
.role-card .btn-detail{background:#2a2a4a;color:#e0e0e0;border:1px solid #444}
.role-card .btn-detail:hover{background:#3a3a5a}
.modal-overlay{position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.7);display:flex;align-items:center;justify-content:center;z-index:1000;display:none}
.modal-overlay.active{display:flex}
.modal{background:#16213e;border-radius:16px;padding:32px;max-width:560px;width:90%;max-height:80vh;overflow-y:auto}
.modal h2{font-size:20px;color:#e94560;margin-bottom:16px}
.modal .detail-row{margin-bottom:12px;font-size:14px}
.modal .detail-row .label{color:#888;margin-right:8px}
.modal .detail-row .value{color:#e0e0e0}
.modal .detail-desc{background:#1a1a2e;padding:12px;border-radius:8px;margin:12px 0;font-size:13px;color:#aaa;white-space:pre-wrap;max-height:200px;overflow-y:auto}
.modal .close-btn{display:inline-block;padding:10px 24px;border-radius:8px;background:#e94560;color:#fff;border:none;cursor:pointer;font-size:14px;margin-top:8px}
.status-bar{position:fixed;bottom:20px;right:20px;padding:12px 20px;border-radius:8px;font-size:13px;display:none;z-index:2000}
.status-bar.success{display:block;background:#00c853;color:#fff}
.status-bar.error{display:block;background:#ff5252;color:#fff}
.status-bar.info{display:block;background:#448aff;color:#fff}
.empty-state{text-align:center;padding:60px 20px;color:#666}
.empty-state .icon{font-size:48px;margin-bottom:12px}
.empty-state p{font-size:14px}
.loading{text-align:center;padding:60px;color:#888}
.spinner{display:inline-block;width:32px;height:32px;border:3px solid #2a2a4a;border-top-color:#e94560;border-radius:50%;animation:.6s spin linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
</head>
<body>
<div class="header">
<h1>🎭 角色商店</h1>
<div style="display:flex;align-items:center;gap:12px">
<div class="server-status" id="serverStatus">未配置服务器</div>
<a href="/api/plug/roleplay/dashboard" style="color:#888;text-decoration:none;font-size:12px;padding:4px 10px;border:1px solid #444;border-radius:6px">🏠 管理</a>
<a href="/api/plug/roleplay/memory_view" style="color:#888;text-decoration:none;font-size:12px;padding:4px 10px;border:1px solid #444;border-radius:6px">🧠 记忆</a>
<a href="/api/plug/roleplay/export_page" style="color:#888;text-decoration:none;font-size:12px;padding:4px 10px;border:1px solid #444;border-radius:6px">📦 导出</a>
</div>
</div>

<div class="server-config">
<input type="text" id="serverUrl" placeholder="可信任服务器地址，如 https://role-server.example.com">
<input type="text" id="serverToken" placeholder="Token（选填）" type="password">
<button id="btnSaveServer">💾 保存</button>
<button id="btnTestServer">🔗 测试连接</button>
</div>

<div class="controls">
<input type="text" id="searchInput" placeholder="🔍 搜索角色名或作者...">
<button class="refresh-btn" id="btnRefresh">🔄 刷新列表</button>
<button id="btnMyRoles">📦 我的角色</button>
<button id="btnUpload">📤 上传角色</button>
</div>

<div class="role-grid" id="roleGrid">
<div class="empty-state">
<div class="icon">📡</div>
<p>点击「🔄 刷新列表」从服务器加载角色</p>
</div>
</div>

<div class="modal-overlay" id="modalOverlay">
<div class="modal" id="modalContent"></div>
</div>

<div class="status-bar" id="statusBar"></div>

<script>
var serverUrl = '';
var serverToken = '';
var allRoles = [];
var currentPage = 'shop';

// Load saved config
try {
serverUrl = localStorage.getItem('roleplay_server_url') || '';
serverToken = localStorage.getItem('roleplay_server_token') || '';
document.getElementById('serverUrl').value = serverUrl;
document.getElementById('serverToken').value = serverToken;
updateServerStatus();
} catch(e) {}

function updateServerStatus() {
var el = document.getElementById('serverStatus');
if (serverUrl) {
el.textContent = '🟢 服务器: ' + serverUrl.replace(/https?:\/\//,'').slice(0,30);
} else {
el.textContent = '⚫ 未配置服务器';
}
}

function showStatus(msg, type) {
var bar = document.getElementById('statusBar');
bar.textContent = msg;
bar.className = 'status-bar ' + type;
setTimeout(function(){ bar.className = 'status-bar'; }, 3000);
}

document.getElementById('btnSaveServer').addEventListener('click', function() {
serverUrl = document.getElementById('serverUrl').value.trim();
serverToken = document.getElementById('serverToken').value.trim();
try {
localStorage.setItem('roleplay_server_url', serverUrl);
localStorage.setItem('roleplay_server_token', serverToken);
} catch(e) {}
updateServerStatus();
showStatus('服务器配置已保存','success');
});

document.getElementById('btnTestServer').addEventListener('click', async function() {
if (!serverUrl) { showStatus('请先填写服务器地址','error'); return; }
var btn = document.getElementById('btnTestServer');
btn.disabled = true; btn.textContent = '测试中...';
try {
var headers = {};
if (serverToken) headers['Authorization'] = 'Bearer ' + serverToken;
var r = await fetch('/api/plug/roleplay/server/list?' + new URLSearchParams({_server_url:serverUrl,_server_token:serverToken}), {headers:headers});
var d = await r.json();
if (d.ok) { showStatus('连接成功！发现 ' + (d.roles?d.roles.length:'?') + ' 个角色','success'); }
else { showStatus('连接失败: ' + (d.msg||'未知错误'),'error'); }
} catch(e) { showStatus('连接失败: ' + e.message,'error'); }
btn.disabled = false; btn.textContent = '🔗 测试连接';
});

document.getElementById('btnRefresh').addEventListener('click', loadShopRoles);
document.getElementById('btnMyRoles').addEventListener('click', loadMyRoles);
document.getElementById('btnUpload').addEventListener('click', function(){ window.open('/api/plug/roleplay/upload','_blank'); });

document.getElementById('searchInput').addEventListener('input', function() {
var q = this.value.toLowerCase();
filterAndRender(q);
});

document.getElementById('modalOverlay').addEventListener('click', function(e) {
if (e.target === this) this.classList.remove('active');
});

async function loadShopRoles() {
if (!serverUrl) { showStatus('请先配置服务器地址','error'); return; }
var grid = document.getElementById('roleGrid');
grid.innerHTML = '<div class="loading"><div class="spinner"></div><p>正在连接服务器...</p></div>';
currentPage = 'shop';
try {
var headers = {};
if (serverToken) headers['Authorization'] = 'Bearer ' + serverToken;
var r = await fetch('/api/plug/roleplay/server/list?' + new URLSearchParams({_server_url:serverUrl,_server_token:serverToken}), {headers:headers});
var d = await r.json();
if (!d.ok) { grid.innerHTML = '<div class="empty-state"><div class="icon">❌</div><p>' + (d.msg||'连接失败') + '</p></div>'; return; }
allRoles = d.roles || [];
renderRoles(allRoles);
showStatus('加载完成，共 ' + allRoles.length + ' 个角色','success');
} catch(e) {
grid.innerHTML = '<div class="empty-state"><div class="icon">❌</div><p>连接失败: ' + e.message + '</p></div>';
}
}

async function loadMyRoles() {
var grid = document.getElementById('roleGrid');
grid.innerHTML = '<div class="loading"><div class="spinner"></div><p>加载本地角色...</p></div>';
currentPage = 'local';
try {
var r = await fetch('/api/plug/roleplay/list');
var d = await r.json();
allRoles = d.roles || [];
renderRoles(allRoles);
showStatus('已安装 ' + allRoles.length + ' 个角色','info');
} catch(e) {
grid.innerHTML = '<div class="empty-state"><div class="icon">❌</div><p>加载失败: ' + e.message + '</p></div>';
}
}

function filterAndRender(q) {
var filtered = allRoles.filter(function(r) {
var name = (r.name||'').toLowerCase();
var author = (r.author||'').toLowerCase();
var desc = (r.desc||'').toLowerCase();
return name.includes(q) || author.includes(q) || desc.includes(q);
});
renderRoles(filtered);
}

function renderRoles(roles) {
var grid = document.getElementById('roleGrid');
if (!roles || roles.length === 0) {
grid.innerHTML = '<div class="empty-state"><div class="icon">📭</div><p>没有找到角色</p></div>';
return;
}
var html = '';
roles.forEach(function(r) {
var version = r.version || '?';
var author = r.author || '未知作者';
var desc = (r.desc || '暂无描述').slice(0, 120);
var downloads = r.downloads || 0;
var size = r.size ? (r.size/1024).toFixed(0)+'KB' : '';
html += '<div class="role-card">'
+ '<div class="cover">' + (r.avatar ? '<img src="'+r.avatar+'" alt="">' : '🎭' ) + '</div>'
+ '<div class="info">'
+ '<div class="name">' + r.name + ' <span style="font-size:12px;color:#888">v' + version + '</span></div>'
+ '<div class="author">👤 ' + author + '</div>'
+ '<div class="desc">' + (desc || '暂无描述') + '</div>'
+ '<div class="meta">' + (size?'<span>📦 '+size+'</span>':'') + '<span>⬇ '+downloads+'</span></div>'
+ '<div class="actions">'
+ '<button class="btn-detail" onclick="showDetail(\''+r.name+'\')">📋 详情</button>';
if (currentPage === 'shop') {
html += '<button class="btn-download" onclick="downloadRole(\''+r.name+'\',this)">⬇ 下载</button>';
} else {
html += '<button class="btn-detail" onclick="activateRole(\''+r.name+'\',this)">▶ 激活</button>';
}
html += '</div></div></div>';
});
grid.innerHTML = html;
}

function showDetail(name) {
var r = allRoles.find(function(x){ return x.name === name; });
if (!r) return;
var html = '<h2>' + r.name + ' <span style="font-size:14px;color:#888">v' + (r.version||'?') + '</span></h2>'
+ '<div class="detail-row"><span class="label">👤 作者:</span><span class="value">' + (r.author||'未知') + '</span></div>'
+ '<div class="detail-row"><span class="label">⬇ 下载:</span><span class="value">' + (r.downloads||0) + '</span></div>'
+ (r.size ? '<div class="detail-row"><span class="label">📦 大小:</span><span class="value">' + (r.size/1024).toFixed(0) + ' KB</span></div>' : '')
+ '<div class="detail-desc">' + (r.desc || '暂无详细描述') + '</div>'
+ '<button class="close-btn" onclick="document.getElementById(\'modalOverlay\').classList.remove(\'active\')">关闭</button>';
if (currentPage === 'shop') {
html += ' <button class="close-btn" style="background:#00c853;margin-left:8px" onclick="downloadRole(\''+r.name+'\',this);document.getElementById(\'modalOverlay\').classList.remove(\'active\')">⬇ 下载</button>';
}
document.getElementById('modalContent').innerHTML = html;
document.getElementById('modalOverlay').classList.add('active');
}

async function downloadRole(name, btn) {
if (!serverUrl) { showStatus('请先配置服务器地址','error'); return; }
btn.disabled = true; btn.textContent = '下载中...';
try {
var body = {name:name, _server_url:serverUrl};
if (serverToken) body._server_token = serverToken;
var r = await fetch('/api/plug/roleplay/server/download', {
method: 'POST',
headers: {'Content-Type':'application/json'},
body: JSON.stringify(body)
});
var d = await r.json();
if (d.ok) { showStatus('✅ ' + d.msg, 'success'); loadMyRoles(); }
else { showStatus('下载失败: ' + (d.msg||'未知错误'), 'error'); }
} catch(e) { showStatus('下载失败: ' + e.message, 'error'); }
btn.disabled = false; btn.textContent = '⬇ 下载';
}

async function activateRole(name, btn) {
btn.disabled = true; btn.textContent = '切换中...';
try {
var r = await fetch('/api/plug/roleplay/activate', {
method: 'POST',
headers: {'Content-Type':'application/json'},
body: JSON.stringify({name:name})
});
var d = await r.json();
if (d.ok) { showStatus('✅ ' + d.msg, 'success'); }
else { showStatus('切换失败: ' + (d.msg||'未知错误'), 'error'); }
} catch(e) { showStatus('操作失败: ' + e.message, 'error'); }
btn.disabled = false; btn.textContent = '▶ 激活';
}

// Init: load local roles by default
loadMyRoles();
</script>
</body>
</html>"""


_DASHBOARD_PAGE_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Roleplay — 管理中心</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,-apple-system,sans-serif;background:#0f0f1a;color:#e0e0e0;min-height:100vh}
.topbar{background:#1a1a2e;padding:16px 32px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #2a2a4a}
.topbar h1{font-size:20px;color:#e94560}
.topbar .links a{color:#888;text-decoration:none;margin-left:16px;font-size:13px;padding:6px 12px;border-radius:6px;transition:.2s}
.topbar .links a:hover{color:#fff;background:#2a2a4a}
.main{max-width:1100px;margin:0 auto;padding:24px}
.hero{display:flex;gap:16px;margin-bottom:24px;flex-wrap:wrap}
.hero-card{flex:1;min-width:280px;background:#1a1a2e;border-radius:14px;padding:28px;border:2px solid #2a2a4a;text-align:center;transition:.2s;cursor:pointer}
.hero-card:hover{border-color:#e94560;transform:translateY(-2px)}
.hero-card.import{background:linear-gradient(135deg,rgba(233,69,96,.06),rgba(233,69,96,.02))}
.hero-card.export{background:linear-gradient(135deg,rgba(0,200,83,.06),rgba(0,200,83,.02))}
.hero-card .icon{font-size:40px;margin-bottom:8px}
.hero-card h3{font-size:17px;color:#fff;margin-bottom:4px}
.hero-card p{font-size:12px;color:#888;line-height:1.4}
.hero-card .btn-hero{display:inline-block;margin-top:14px;padding:10px 28px;border-radius:10px;font-size:14px;font-weight:700;border:none;cursor:pointer;transition:.2s;text-decoration:none}
.btn-hero-import{background:#e94560;color:#fff}
.btn-hero-import:hover{background:#c73b53}
.btn-hero-export{background:#00c853;color:#fff}
.btn-hero-export:hover{background:#009624}
.dropzone{border:2px dashed #444;border-radius:12px;padding:30px;text-align:center;margin-top:14px;transition:.2s;display:none}
.dropzone.show{display:block}
.dropzone.drag{border-color:#e94560;background:rgba(233,69,96,.05)}
.dropzone .dz-icon{font-size:32px;margin-bottom:6px}
.dropzone .dz-text{font-size:13px;color:#888}
#fileInput{display:none}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:20px}
@media(max-width:900px){.grid{grid-template-columns:1fr}}
.card{background:#1a1a2e;border-radius:12px;border:1px solid #2a2a4a;overflow:hidden}
.card-header{background:#16213e;padding:14px 20px;font-size:15px;font-weight:600;display:flex;align-items:center;gap:8px;border-bottom:1px solid #2a2a4a}
.card-body{padding:20px}
.full-width{grid-column:1/-1}
.status-badge{display:inline-block;padding:3px 10px;border-radius:12px;font-size:11px;font-weight:600}
.status-badge.active{background:rgba(0,200,83,.15);color:#00c853}
.role-card{border:1px solid #2a2a4a;border-radius:10px;padding:14px;margin-bottom:10px;display:flex;align-items:center;gap:12px;transition:.2s}
.role-card:hover{border-color:#e94560}
.role-card .avatar{width:48px;height:48px;border-radius:10px;background:linear-gradient(135deg,#2a2a4a,#3a3a5a);display:flex;align-items:center;justify-content:center;font-size:24px;flex-shrink:0}
.role-card .info{flex:1;min-width:0}
.role-card .info .rname{font-weight:600;font-size:14px}
.role-card .info .rdetail{font-size:11px;color:#888;margin-top:2px}
.role-card .actions{display:flex;gap:6px;flex-shrink:0}
.role-card .actions button{padding:6px 12px;border-radius:6px;border:none;cursor:pointer;font-size:12px;transition:.2s;white-space:nowrap}
.btn-primary{background:#e94560;color:#fff}
.btn-primary:hover{background:#c73b53}
.btn-outline{background:transparent;color:#aaa;border:1px solid #444!important}
.btn-outline:hover{background:#2a2a4a;color:#fff}
.btn-danger{background:rgba(255,82,82,.15);color:#ff5252;border:none!important}
.btn-danger:hover{background:rgba(255,82,82,.25)}
.btn-success{background:rgba(0,200,83,.15);color:#00c853;border:1px solid rgba(0,200,83,.3)!important}
.btn-success:hover{background:rgba(0,200,83,.25)}
.input-row{display:flex;gap:8px;margin-bottom:12px}
.input-row input{flex:1;padding:10px 14px;border-radius:8px;border:1px solid #444;background:#0f0f1a;color:#e0e0e0;font-size:13px}
.input-row input:focus{outline:none;border-color:#e94560}
.stat-row{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:16px}
.stat-box{flex:1;min-width:100px;background:#0f0f1a;border-radius:8px;padding:14px;text-align:center}
.stat-box .num{font-size:28px;font-weight:700;color:#e94560}
.stat-box .label{font-size:11px;color:#888;margin-top:4px}
.alert{padding:12px 16px;border-radius:8px;font-size:13px;margin-bottom:12px}
.alert-info{background:rgba(68,138,255,.1);color:#448aff;border:1px solid rgba(68,138,255,.2)}
.alert-warn{background:rgba(255,171,64,.1);color:#ffab40;border:1px solid rgba(255,171,64,.2)}
.spin{display:inline-block;width:16px;height:16px;border:2px solid #444;border-top-color:#e94560;border-radius:50%;animation:.6s spin linear infinite;vertical-align:middle;margin-right:4px}
@keyframes spin{to{transform:rotate(360deg)}}
.modal-overlay{position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.7);display:flex;align-items:center;justify-content:center;z-index:1000;display:none}
.modal-overlay.show{display:flex}
.modal{background:#1a1a2e;border-radius:16px;padding:28px;max-width:500px;width:90%;max-height:80vh;overflow-y:auto}
.modal h3{font-size:18px;color:#e94560;margin-bottom:16px}
.modal .btns{display:flex;gap:8px;justify-content:flex-end;margin-top:16px}
.server-roles{margin-top:12px;max-height:300px;overflow-y:auto}
.empty{text-align:center;padding:30px;color:#666;font-size:13px}
.divider{border:none;border-top:1px solid #2a2a4a;margin:16px 0}
.toast{position:fixed;bottom:24px;right:24px;padding:14px 22px;border-radius:10px;font-size:13px;z-index:9999;display:none;max-width:420px}
.toast.ok{display:block;background:#00c853;color:#fff}
.toast.err{display:block;background:#ff5252;color:#fff}
.toast.info{display:block;background:#448aff;color:#fff}
.nav-grid{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}
.nav-chip{padding:8px 16px;border-radius:8px;font-size:12px;font-weight:600;text-decoration:none;transition:.2s;border:1px solid #2a2a4a;color:#aaa}
.nav-chip:hover{border-color:#e94560;color:#e94560;background:rgba(233,69,96,.05)}
</style>
</head>
<body>
<div class="topbar">
<h1>🎭 Roleplay — 导入导出中心</h1>
<div class="links">
<a href="/api/plug/roleplay/shop">🛒 商店</a>
<a href="/api/plug/roleplay/memory_view">🧠 记忆</a>
<a href="/api/plug/roleplay/special_dates">📅 日期</a>
</div>
</div>

<div class="main">

<!-- Hero: 导入 + 导出 -->
<div class="hero">
<div class="hero-card import" onclick="document.getElementById('fileInput').click()">
<div class="icon">📥</div>
<h3>导入角色 ZIP</h3>
<p>从本地上传 .zip 压缩包<br>一键安装，即刻使用</p>
<button class="btn-hero btn-hero-import" onclick="event.stopPropagation();document.getElementById('fileInput').click()">📤 选择文件并安装</button>
<div class="dropzone" id="dropzone">
<div class="dz-icon">📂</div>
<div class="dz-text">拖拽 ZIP 文件到此处</div>
</div>
<input type="file" id="fileInput" accept=".zip">
</div>

<div class="hero-card export" onclick="location.href='/api/plug/roleplay/export_page'">
<div class="icon">📦</div>
<h3>导出角色 ZIP</h3>
<p>选择角色 → 勾选选项 → 下载<br>隐私清洗后安全分享</p>
<button class="btn-hero btn-hero-export" onclick="event.stopPropagation();location.href='/api/plug/roleplay/export_page'">📋 打开导出页面</button>
<div class="nav-grid">
<a class="nav-chip" href="/api/plug/roleplay/export_page">📦 可视化导出</a>
<a class="nav-chip" href="/api/plug/roleplay/shop">🛒 角色商店</a>
<a class="nav-chip" href="/api/plug/roleplay/special_dates">📅 特殊日期</a>
<a class="nav-chip" href="/api/plug/roleplay/memory_view">🧠 记忆管理</a>
</div>
</div>
</div>

<!-- Status + Active Role -->
<div class="grid">
<div class="card">
<div class="card-header">🎯 当前激活角色</div>
<div class="card-body" id="activeRoleInfo">
<div class="empty"><div class="spin"></div> 加载中...</div>
</div>
</div>
<div class="card">
<div class="card-header">📊 状态概览</div>
<div class="card-body">
<div class="stat-row" id="statsRow">
<div class="stat-box"><div class="num">-</div><div class="label">已安装角色</div></div>
<div class="stat-box"><div class="num">-</div><div class="label">短期记忆</div></div>
<div class="stat-box"><div class="num">-</div><div class="label">长期事实</div></div>
</div>
<div style="font-size:12px;color:#888">自动回复: <span id="arInfo" style="color:#e0e0e0">-</span> | 知识库: <span id="kbInfo" style="color:#e0e0e0">-</span></div>
</div>
</div>
</div>

<!-- All Roles -->
<div class="card full-width">
<div class="card-header">📦 已安装角色 <span style="font-weight:400;font-size:12px;color:#888;margin-left:4px">点击切换 / 导出 / 删除</span></div>
<div class="card-body" id="allRolesList">
<div class="empty"><div class="spin"></div> 加载中...</div>
</div>
</div>

<!-- Server -->
<div class="card full-width" style="margin-top:20px">
<div class="card-header">🔗 可信任服务器</div>
<div class="card-body">
<div class="input-row">
<input type="text" id="serverUrl" placeholder="服务器地址，如 https://kunxun.top">
<input type="text" id="serverToken" placeholder="Token（选填）">
<button class="btn-primary" onclick="saveServer()">💾 保存</button>
<button class="btn-outline" onclick="testServer()">🔗 测试</button>
<button class="btn-outline" onclick="browseServer()">📡 浏览</button>
</div>
<div id="serverMsg"></div>
<div class="server-roles" id="serverRolesList"></div>
<hr class="divider">
<div style="display:flex;gap:8px">
<button class="btn-success" onclick="shareRole()" style="flex:1">📤 分享当前角色到服务器</button>
<button class="btn-outline" onclick="exportRole()" style="flex:1">📦 快速导出当前角色</button>
</div>
</div>
</div>

<!-- Auto Reply & Knowledge -->
<div class="card full-width" style="margin-top:20px">
<div class="card-header">🤖 自动回复 & 知识库</div>
<div class="card-body">
<div style="display:flex;gap:24px;flex-wrap:wrap">
<div style="flex:1;min-width:250px">
<h3 style="font-size:14px;color:#ccc;margin-bottom:8px">⏰ 自动回复</h3>
<div id="arStatus" style="font-size:13px;color:#888">加载中...</div>
<div style="margin-top:8px;display:flex;gap:6px">
<button class="btn-primary" onclick="triggerAutoReply()">▶ 手动触发</button>
</div>
</div>
<div style="flex:1;min-width:250px">
<h3 style="font-size:14px;color:#ccc;margin-bottom:8px">🔍 知识库</h3>
<div id="kbStatus" style="font-size:13px;color:#888">加载中...</div>
<div style="margin-top:8px;display:flex;gap:6px">
<button class="btn-primary" onclick="triggerKnowledge()">▶ 手动更新</button>
</div>
</div>
</div>
</div>
</div>

</div>

<div class="modal-overlay" id="modal"><div class="modal" id="modalInner"></div></div>
<div class="toast" id="toast"></div>

<script>
var URL = location.origin;

function toast(msg, type) {
var el = document.getElementById('toast');
el.textContent = msg;
el.className = 'toast ' + type;
setTimeout(function(){ el.className = 'toast'; }, 3500);
}

async function api(path, opts) {
var r = await fetch(URL + path, opts||{});
var ct = r.headers.get('content-type')||'';
if (ct.includes('json')) return r.json();
return r;
}

// ——— 导入上传 ———
var drop = document.getElementById('dropzone');
var fileInp = document.getElementById('fileInput');
function toggleDrop() { drop.classList.toggle('show', !fileInp.files.length && !drop.classList.contains('show')); }
['dragenter','dragover'].forEach(function(ev){ document.addEventListener(ev,function(e){ e.preventDefault(); drop.classList.add('show','drag'); }); });
document.addEventListener('dragleave',function(e){ if(!e.relatedTarget||e.relatedTarget===document.documentElement){ drop.classList.remove('drag'); } });
document.addEventListener('drop',function(e){ e.preventDefault(); drop.classList.remove('drag'); if(e.dataTransfer.files.length){ uploadFile(e.dataTransfer.files[0]); } });
fileInp.addEventListener('change',function(){ if(fileInp.files.length){ uploadFile(fileInp.files[0]); } });
async function uploadFile(f) {
if (!f.name.endsWith('.zip')) { toast('只支持 .zip 文件', 'err'); return; }
toast('正在上传并安装 ' + f.name + '...', 'info');
var fd = new FormData(); fd.append('file', f);
try {
var r = await (await fetch(URL + '/api/plug/roleplay/upload', {method:'POST', body:fd})).json();
if (r.ok) { toast('✅ ' + r.msg, 'ok'); setTimeout(function(){ location.reload(); }, 1200); }
else { toast('❌ ' + (r.msg||'安装失败'), 'err'); }
} catch(e) { toast('❌ 上传失败: ' + e.message, 'err'); }
fileInp.value = '';
drop.classList.remove('show');
}

// ——— Status ———
(async function init() { await loadStatus(); await loadRoles(); loadServerConfig(); })();

async function loadStatus() {
try {
var r = await api('/api/plug/roleplay/list');
var roles = r.roles||[];
var active = r.active||'';
var el = document.getElementById('activeRoleInfo');
if (active) {
el.innerHTML = '<div style="display:flex;align-items:center;gap:14px">'
+ '<div style="width:56px;height:56px;border-radius:12px;background:linear-gradient(135deg,#e94560,#c73b53);display:flex;align-items:center;justify-content:center;font-size:28px">🎭</div>'
+ '<div><div style="font-size:20px;font-weight:700;color:#fff">' + active + '</div>'
+ '<div style="font-size:12px;color:#888;margin-top:4px"><span class="status-badge active">● 已激活</span></div></div></div>';
} else { el.innerHTML = '<div class="empty">⚪ 未激活任何角色</div>'; }
} catch(e) {}
try {
var mr = await api('/api/plug/roleplay/memory');
var mem = mr.memory||{};
document.getElementById('statsRow').innerHTML =
'<div class="stat-box"><div class="num">' + (r&&r.roles?r.roles.length:'-') + '</div><div class="label">已安装角色</div></div>'
+ '<div class="stat-box"><div class="num">' + (mem.short_term_count||0) + '</div><div class="label">短期记忆</div></div>'
+ '<div class="stat-box"><div class="num">' + (mem.medium_summary_count||0) + '</div><div class="label">对话摘要</div></div>'
+ '<div class="stat-box"><div class="num">' + (mem.user_fact_count||0) + '</div><div class="label">用户事实</div></div>'
+ '<div class="stat-box"><div class="num">' + (mem.role_fact_count||0) + '</div><div class="label">角色知识</div></div>';
loadARStatus();
loadKBStatus();
} catch(e) {}
}
async function loadARStatus() {
try {
var r = await api('/api/plug/roleplay/auto_reply/status');
var el = document.getElementById('arStatus'), inf = document.getElementById('arInfo');
if (r.ok) { el.textContent = r.enabled ? '✅ 已启用 | 特殊日期: ' + r.count + '个' : '⭕ 已禁用'; if (inf) inf.textContent = r.enabled ? '开启('+r.count+'个)' : '关闭'; }
else { el.textContent = '获取失败'; if(inf) inf.textContent = '-'; }
} catch(e) {}
}
async function loadKBStatus() {
try {
var r = await api('/api/plug/roleplay/knowledge/status');
var el = document.getElementById('kbStatus'), inf = document.getElementById('kbInfo');
if (r.ok) { el.textContent = '📚 ' + r.facts_count + '条 | ' + (r.enabled?'已启用':'已禁用'); if (inf) inf.textContent = r.facts_count+'条'; }
else { el.textContent = '获取失败'; if(inf) inf.textContent = '-'; }
} catch(e) {}
}
async function triggerAutoReply() {
var btn = event.target; btn.disabled = true; btn.textContent = '触发中...';
var r = await api('/api/plug/roleplay/auto_reply/trigger', {method:'POST'});
toast(r.ok ? '✅ 已触发' : '❌ '+(r.msg||'失败'), r.ok?'ok':'err');
btn.disabled = false; btn.textContent = '▶ 手动触发';
}
async function triggerKnowledge() {
var btn = event.target; btn.disabled = true; btn.textContent = '更新中...';
var r = await api('/api/plug/roleplay/knowledge/trigger', {method:'POST'});
toast(r.ok ? '✅ 更新已触发' : '❌ '+(r.msg||'失败'), r.ok?'ok':'err');
btn.disabled = false; btn.textContent = '▶ 手动更新'; loadKBStatus();
}

// ——— Roles ———
async function loadRoles() {
try {
var r = await api('/api/plug/roleplay/list');
var roles = r.roles||[], active = r.active||'', html = '';
if (!roles.length) { html = '<div class="empty">暂无已安装角色 — 请上传或从商店下载</div>'; }
roles.forEach(function(ro) {
var isActive = ro.name === active;
html += '<div class="role-card" style="'+(isActive?'border-color:#e94560;background:rgba(233,69,96,.05)':'')+'">'
+ '<div class="avatar">🎭</div>'
+ '<div class="info"><div class="rname">'+ro.name+(isActive?' <span class="status-badge active">当前</span>':'')+'</div>'
+ '<div class="rdetail">v'+(ro.version||'?')+' · '+(ro.author||'未知')+' · '+(ro.emotions?ro.emotions.length+'种情绪':'')+(ro.has_voice?' · 🎤':'')+'</div></div>'
+ '<div class="actions">'
+ (isActive?'':'<button class="btn-primary" onclick="activateRole(\''+ro.name+'\')">▶ 激活</button>')
+ '<button class="btn-outline" onclick="downloadZip(\''+ro.name+'\')">📦 导出</button>'
+ '<button class="btn-danger" onclick="delRole(\''+ro.name+'\')">🗑</button>'
+ '</div></div>';
});
document.getElementById('allRolesList').innerHTML = html;
} catch(e) { document.getElementById('allRolesList').innerHTML='<div class="empty">加载失败</div>'; }
}
async function activateRole(name) {
var r = await api('/api/plug/roleplay/activate', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:name})});
if (r.ok) { location.reload(); } else { toast(r.msg||'失败','err'); }
}
async function downloadZip(name) {
toast('正在打包 ' + name + '.zip ...', 'info');
var r = await fetch(URL + '/api/plug/roleplay/export', {
method: 'POST', headers: {'Content-Type':'application/json'},
body: JSON.stringify({name:name, clean:true, include_memory:true, include_raw:true})
});
var ct = r.headers.get('content-type')||'';
if (ct.includes('zip')||ct.includes('octet-stream')) {
var blob = await r.blob(); var a = document.createElement('a');
a.href = URL.createObjectURL(blob); a.download = name+'.zip'; a.click();
toast('✅ ' + name + '.zip 下载已开始', 'ok');
} else { var d = await r.json(); toast('❌ '+(d.msg||'失败'),'err'); }
}
function exportRole() { window.open(URL + '/api/plug/roleplay/export_page', '_blank'); }
async function delRole(name) {
if (!confirm('确定删除角色 "' + name + '"？此操作不可恢复。')) return;
var r = await api('/api/plug/roleplay/delete', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:name})});
if (r.ok) { location.reload(); } else { toast(r.msg||'失败','err'); }
}

// ——— Server ———
function loadServerConfig() {
try { document.getElementById('serverUrl').value = localStorage.getItem('roleplay_server_url')||''; document.getElementById('serverToken').value = localStorage.getItem('roleplay_server_token')||''; } catch(e) {}
}
function saveServer() {
try { localStorage.setItem('roleplay_server_url',document.getElementById('serverUrl').value.trim()); localStorage.setItem('roleplay_server_token',document.getElementById('serverToken').value.trim()); } catch(e) {}
document.getElementById('serverMsg').innerHTML = '<div class="alert alert-info">✅ 服务器配置已保存</div>';
setTimeout(function(){ document.getElementById('serverMsg').innerHTML=''; },2000);
}
async function testServer() {
var u = document.getElementById('serverUrl').value.trim(), t = document.getElementById('serverToken').value.trim();
if (!u) { document.getElementById('serverMsg').innerHTML='<div class="alert alert-warn">请填写服务器地址</div>'; return; }
var btn = event.target; btn.disabled=true; btn.innerHTML='<span class="spin"></span> 测试中';
var q = '_server_url='+encodeURIComponent(u); if(t) q+='&_server_token='+encodeURIComponent(t);
var r = await api('/api/plug/roleplay/server/list?'+q);
btn.disabled=false; btn.textContent='🔗 测试';
document.getElementById('serverMsg').innerHTML = r.ok
? '<div class="alert alert-info">✅ 连接成功！服务器有 '+(r.roles?r.roles.length:'?')+' 个角色</div>'
: '<div class="alert alert-warn">❌ '+(r.msg||'连接失败')+'</div>';
}
async function browseServer() {
var u = document.getElementById('serverUrl').value.trim(), t = document.getElementById('serverToken').value.trim();
if (!u) { document.getElementById('serverMsg').innerHTML='<div class="alert alert-warn">请填写服务器地址</div>'; return; }
var el = document.getElementById('serverRolesList');
el.innerHTML = '<div class="empty"><span class="spin"></span> 正在加载...</div>';
var q = '_server_url='+encodeURIComponent(u); if(t) q+='&_server_token='+encodeURIComponent(t);
var r = await api('/api/plug/roleplay/server/list?'+q);
if (!r.ok) { el.innerHTML='<div class="empty">❌ '+(r.msg||'加载失败')+'</div>'; return; }
var roles = r.roles||[];
if (!roles.length) { el.innerHTML='<div class="empty">服务器暂无角色</div>'; return; }
var html = ''; roles.forEach(function(ro) {
html += '<div class="role-card"><div class="avatar">🎭</div>'
+ '<div class="info"><div class="rname">'+ro.name+' <span style="font-size:11px;color:#888">v'+(ro.version||'?')+'</span></div>'
+ '<div class="rdetail">'+(ro.author||'未知')+' · ⬇'+(ro.downloads||0)+(ro.size?' · '+(ro.size/1024).toFixed(0)+'KB':'')+'</div></div>'
+ '<div class="actions"><button class="btn-primary" onclick="downloadFromServer(\''+ro.name+'\',this)">⬇ 下载</button></div></div>';
});
el.innerHTML = html;
}
async function downloadFromServer(name, btn) {
var u = document.getElementById('serverUrl').value.trim(), t = document.getElementById('serverToken').value.trim();
btn.disabled=true; btn.innerHTML='<span class="spin"></span>';
var body = {name:name,_server_url:u}; if(t) body._server_token=t;
var r = await api('/api/plug/roleplay/server/download', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
if (r.ok) { document.getElementById('serverMsg').innerHTML='<div class="alert alert-info">✅ '+r.msg+'</div>'; setTimeout(function(){ location.reload(); },1500); }
else { toast(r.msg||'失败','err'); }
btn.disabled=false; btn.textContent='⬇ 下载';
}
async function shareRole() {
var u = document.getElementById('serverUrl').value.trim();
if (!u) { document.getElementById('serverMsg').innerHTML='<div class="alert alert-warn">请先配置服务器地址</div>'; return; }
var r = await api('/api/plug/roleplay/server/share', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})});
document.getElementById('serverMsg').innerHTML = r.ok
? '<div class="alert alert-info">✅ 分享成功！</div>'
: '<div class="alert alert-warn">❌ '+(r.msg||'失败')+'</div>';
}
</script>
</body>
</html>"""
