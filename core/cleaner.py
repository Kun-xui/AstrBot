import os
import json
import yaml
import shutil
import tempfile
import aiohttp
from astrbot.api import logger


class Cleaner:
    def __init__(self, provider_getter):
        self._get_provider = provider_getter
        self._prompt = "你是一个数据清洗助手。请提取以下对话中与角色设定相关的信息，忽略用户隐私和聊天痕迹。"

    def set_prompt(self, prompt: str):
        if prompt and prompt.strip():
            self._prompt = prompt.strip()

    def strip_personal_data(self, memory_stats: dict) -> dict:
        short = []
        for msg in memory_stats.get("short_term", []):
            sender = msg.get("sender", "")
            if sender.lower() in ("user", "使用者", "", "astrbot"):
                short.append({"sender": "user", "content": msg.get("content", "")})
            else:
                short.append(msg)
        raw_history = []
        for msg in memory_stats.get("raw_history", []):
            sender = msg.get("sender", "")
            if sender.lower() in ("user", "使用者", ""):
                raw_history.append({"sender": "user", "content": msg.get("content", ""), "time": msg.get("time", 0)})
            elif sender.lower() == "astrbot":
                raw_history.append({"sender": "role", "content": msg.get("content", ""), "time": msg.get("time", 0)})
            else:
                raw_history.append(msg)
        role_facts = []
        for fact in memory_stats.get("role_facts", []):
            role_facts.append(fact)
        return {
            "short_term": short,
            "medium_summaries": memory_stats.get("medium_summaries", []),
            "role_facts": role_facts,
            "raw_history": raw_history,
        }

    async def clean_memory(self, memory_stats: dict, role_config: dict) -> dict:
        content_to_clean = {
            "role_name": role_config.get("name", ""),
            "short_term": memory_stats.get("short_term", []),
            "medium_summaries": memory_stats.get("medium_summaries", []),
            "long_term_facts": memory_stats.get("long_term_facts", []),
        }
        provider = self._get_provider()
        if provider is None:
            logger.warning("无法获取 LLM Provider，跳过清洗，直接导出原始数据")
            return content_to_clean
        payload = json.dumps(content_to_clean, ensure_ascii=False, indent=2)
        try:
            result = await provider.text_chat(
                f"{self._prompt}\n\n以下是需要清洗的数据:\n{payload}",
                system_prompt=self._prompt,
            )
            logger.info("记忆清洗完成")
            return {"cleaned": True, "result": result}
        except Exception as e:
            logger.error(f"记忆清洗失败: {e}")
            return content_to_clean

    async def export_clean_zip(self, role_dir: str, memory_stats: dict,
                                 role_config: dict, include_raw: bool = False,
                                 raw_history: list | None = None) -> str | None:
        tmp_dir = tempfile.mkdtemp(prefix="roleplay_export_")
        role_name = role_config.get("name", "role")
        zip_name = f"{role_name}_cleaned.zip"
        zip_path = os.path.join(tempfile.gettempdir(), zip_name)
        try:
            export_role_dir = os.path.join(tmp_dir, role_name)
            os.makedirs(export_role_dir, exist_ok=True)
            for item in os.listdir(role_dir):
                src = os.path.join(role_dir, item)
                dst = os.path.join(export_role_dir, item)
                if item in ("data", "memory", "__pycache__"):
                    continue
                if os.path.isdir(src):
                    if "memory" in item.lower():
                        continue
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
            cfg_path = os.path.join(export_role_dir, "config.yaml")
            if os.path.exists(cfg_path):
                import yaml
                with open(cfg_path, "r", encoding="utf-8") as f:
                    exp_cfg = yaml.safe_load(f) or {}
                exp_cfg.pop("user_birthday", None)
                exp_cfg.pop("special_dates", None)
                exp_cfg.pop("user_name", None)
                exp_cfg.pop("_role_dir", None)
                with open(cfg_path, "w", encoding="utf-8") as f:
                    yaml.dump(exp_cfg, f, allow_unicode=True, default_flow_style=False)
            cleaned = await self.clean_memory(memory_stats, role_config)
            cleaned_path = os.path.join(export_role_dir, "_cleaned_info.json")
            with open(cleaned_path, "w", encoding="utf-8") as f:
                json.dump(cleaned, f, ensure_ascii=False, indent=2)
            if include_raw and raw_history:
                raw_path = os.path.join(export_role_dir, "_raw_history.json")
                with open(raw_path, "w", encoding="utf-8") as f:
                    json.dump(raw_history, f, ensure_ascii=False, indent=2)
            shutil.make_archive(
                os.path.splitext(zip_path)[0],
                "zip",
                tmp_dir
            )
            final_path = os.path.join(tempfile.gettempdir(), zip_name)
            logger.info(f"清洗导出完成: {final_path}")
            return final_path
        except Exception as e:
            logger.error(f"导出清洗ZIP失败: {e}")
            return None
        finally:
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass
