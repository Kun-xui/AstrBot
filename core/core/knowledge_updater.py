import asyncio
import os
import json
import time
from datetime import datetime
from astrbot.api import logger


class KnowledgeUpdater:
    def __init__(self, plugin_ref=None):
        self._plugin = plugin_ref
        self._enabled = False
        self._search_topics: list[str] = []
        self._update_interval_hours = 24
        self._task: asyncio.Task | None = None
        self._last_update_time = 0
        self._knowledge_file: str = ""

    def configure(self, enabled: bool = False, topics: list | None = None,
                  interval_hours: int = 24):
        self._enabled = enabled
        self._search_topics = topics or []
        self._update_interval_hours = max(1, interval_hours)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _get_knowledge_path(self) -> str:
        if not self._plugin or not self._plugin._active_role:
            return ""
        role_dir = self._plugin._role_config.get("_role_dir", "") if self._plugin._role_config else ""
        if not role_dir:
            return ""
        return os.path.join(role_dir, "knowledge_base.json")

    def load_knowledge(self) -> list[str]:
        path = self._get_knowledge_path()
        if not path or not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("facts", [])
        except Exception:
            return []

    def save_knowledge(self, facts: list[str]):
        path = self._get_knowledge_path()
        if not path:
            return
        data = {
            "updated_at": time.time(),
            "facts": facts
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"[KnowledgeUpdater] 知识库已保存: {path}, {len(facts)}条")
        except Exception as e:
            logger.error(f"[KnowledgeUpdater] 保存知识库失败: {e}")

    async def start(self):
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run())
        self._task.add_done_callback(lambda _: logger.info("[KnowledgeUpdater] 调度器已退出"))
        logger.info("[KnowledgeUpdater] 调度器已启动")

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def trigger_now(self) -> bool:
        try:
            return await self._do_update()
        except Exception as e:
            logger.error(f"[KnowledgeUpdater] 手动触发失败: {e}")
            return False

    async def _run(self):
        await asyncio.sleep(10)
        await self._do_update()
        while True:
            try:
                await asyncio.sleep(max(60, self._update_interval_hours * 3600))
                if not self._enabled:
                    continue
                await self._do_update()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[KnowledgeUpdater] 调度异常: {e}")
                await asyncio.sleep(3600)

    async def _do_update(self) -> bool:
        if not self._plugin or not self._plugin._enabled or not self._plugin._active_role:
            return False
        role_cfg = self._plugin._role_config
        if not role_cfg:
            return False

        now = time.time()
        if now - self._last_update_time < self._update_interval_hours * 3600 * 0.5:
            logger.info("[KnowledgeUpdater] 距上次更新不到一半间隔，跳过")
            return True
        self._last_update_time = now

        role_name = role_cfg.get("name", "角色")
        persona = role_cfg.get("persona", "")
        topics = self._search_topics or [f"{role_name} 最新动态", f"{role_name} 作品更新"]

        logger.info(f"[KnowledgeUpdater] 开始更新角色知识库: {role_name}, 主题: {topics}")

        provider = self._plugin._get_llm_provider()
        if not provider:
            logger.warning("[KnowledgeUpdater] 无 LLM Provider，无法更新")
            return False

        existing_facts = self.load_knowledge()
        search_results = []
        search_ok = True
        try:
            search_query = "; ".join(topics[:3])
            search_prompt = (
                f"请模拟联网搜索以下主题的最新信息(2025-2026年):\n{search_query}\n\n"
                f"请以JSON数组格式返回搜索结果，每条包含 title, snippet, url 字段。"
                f"请基于你对{role_name}相关作品的了解，提供真实可信的信息。"
            )
            sr = await provider.text_chat(search_prompt)
            sr_text = sr.completion_text if hasattr(sr, 'completion_text') else str(sr)
            search_results.append(sr_text[:2000] if sr_text else "")
        except Exception as e:
            logger.warning(f"[KnowledgeUpdater] 搜索失败，使用已有知识: {e}")
            search_ok = False

        if search_ok:
            summary_prompt = (
                f"你是一个知识助手。以下是关于角色「{role_name}」的最新搜索结果和已有知识。\n\n"
                f"角色背景:\n{persona[:500]}\n\n"
                f"搜索结果:\n{chr(10).join(search_results[:3])}\n\n"
                f"已有知识:\n{chr(10).join(existing_facts[-20:] if existing_facts else ['无'])}\n\n"
                f"请提炼出3-5条关于这个角色或其相关作品的最新事实/知识，"
                f"每条一行，以「- 」开头。只输出事实，不要额外说明。"
            )
            try:
                summary = await provider.text_chat(summary_prompt)
                summary_text = summary.completion_text if hasattr(summary, 'completion_text') else str(summary)
                if summary_text:
                    new_facts = []
                    for line in summary_text.strip().split("\n"):
                        stripped = line.strip()
                        if stripped.startswith("-") or stripped.startswith("·"):
                            stripped = stripped.lstrip("-· ").strip()
                            if stripped and len(stripped) > 4:
                                new_facts.append(stripped)
                    if new_facts:
                        all_facts = existing_facts + new_facts
                        if len(all_facts) > 50:
                            all_facts = all_facts[-50:]
                        self.save_knowledge(all_facts)
                        logger.info(f"[KnowledgeUpdater] 知识库更新完成: +{len(new_facts)}条")
                        return True
            except Exception as e:
                logger.error(f"[KnowledgeUpdater] 摘要失败: {e}")

        if existing_facts:
            self.save_knowledge(existing_facts)
            return True

        return False

    def get_knowledge_context(self) -> str:
        facts = self.load_knowledge()
        if not facts:
            return ""
        lines = []
        for i, f in enumerate(facts):
            lines.append(f"[背景知识{i+1}] {f}")
        return "\n".join(lines)
