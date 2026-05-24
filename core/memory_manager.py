import os
import json
import time
import aiohttp
from astrbot.api import logger


class MemoryManager:
    def __init__(self, base_dir: str, short_term_size: int = 10, medium_interval: int = 20,
                 long_term_enabled: bool = True):
        self.base_dir = base_dir
        self.short_term_size = short_term_size
        self.medium_interval = medium_interval
        self.long_term_enabled = long_term_enabled
        self._role_name = ""
        self._short_term: list[dict] = []
        self._medium_summaries: list[str] = []
        self._long_term_facts: list[str] = []
        self._raw_history: list[dict] = []
        self._message_count_since_summary = 0

    def set_role(self, role_name: str):
        self._role_name = role_name
        self._short_term = []
        self._medium_summaries = []
        self._long_term_facts = []
        self._raw_history = []
        self._message_count_since_summary = 0
        self._load_memory()

    @property
    def role_dir(self) -> str:
        return os.path.join(self.base_dir, self._role_name) if self._role_name else self.base_dir

    def _load_memory(self):
        os.makedirs(self.role_dir, exist_ok=True)
        short_path = os.path.join(self.role_dir, "short_term.json")
        medium_path = os.path.join(self.role_dir, "medium_summaries.json")
        long_path = os.path.join(self.role_dir, "long_term_facts.json")
        raw_path = os.path.join(self.role_dir, "raw_history.json")
        try:
            if os.path.exists(short_path):
                with open(short_path, "r", encoding="utf-8") as f:
                    self._short_term = json.load(f)
        except Exception:
            self._short_term = []
        try:
            if os.path.exists(medium_path):
                with open(medium_path, "r", encoding="utf-8") as f:
                    self._medium_summaries = json.load(f)
        except Exception:
            self._medium_summaries = []
        try:
            if os.path.exists(long_path):
                with open(long_path, "r", encoding="utf-8") as f:
                    self._long_term_facts = json.load(f)
        except Exception:
            self._long_term_facts = []
        try:
            if os.path.exists(raw_path):
                with open(raw_path, "r", encoding="utf-8") as f:
                    self._raw_history = json.load(f)
        except Exception:
            self._raw_history = []

    def _save_short_term(self):
        path = os.path.join(self.role_dir, "short_term.json")
        os.makedirs(self.role_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._short_term, f, ensure_ascii=False, indent=2)

    def _save_medium(self):
        path = os.path.join(self.role_dir, "medium_summaries.json")
        os.makedirs(self.role_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._medium_summaries, f, ensure_ascii=False, indent=2)

    def _save_long_term(self):
        path = os.path.join(self.role_dir, "long_term_facts.json")
        os.makedirs(self.role_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._long_term_facts, f, ensure_ascii=False, indent=2)

    def _save_raw_history(self):
        path = os.path.join(self.role_dir, "raw_history.json")
        os.makedirs(self.role_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._raw_history, f, ensure_ascii=False, indent=2)

    def add_message(self, sender: str, content: str):
        entry = {
            "sender": sender,
            "content": content,
            "time": time.time()
        }
        self._short_term.append(entry)
        if len(self._short_term) > self.short_term_size:
            self._short_term = self._short_term[-self.short_term_size:]
        self._save_short_term()
        self._raw_history.append(entry)
        self._save_raw_history()
        self._message_count_since_summary += 1

    def needs_summary(self) -> bool:
        return self._message_count_since_summary >= self.medium_interval

    def add_summary(self, summary: str):
        self._medium_summaries.append(summary)
        if len(self._medium_summaries) > 20:
            self._medium_summaries = self._medium_summaries[-20:]
        self._save_medium()
        self._message_count_since_summary = 0

    def add_long_term_fact(self, fact: str):
        if not self.long_term_enabled:
            return
        self._long_term_facts.append(fact)
        if len(self._long_term_facts) > 50:
            self._long_term_facts = self._long_term_facts[-50:]
        self._save_long_term()

    def get_short_term_context(self) -> str:
        if not self._short_term:
            return ""
        lines = []
        for entry in self._short_term:
            lines.append(f"{entry['sender']}: {entry['content']}")
        return "\n".join(lines)

    def get_medium_context(self) -> str:
        if not self._medium_summaries:
            return ""
        lines = []
        for i, s in enumerate(self._medium_summaries):
            lines.append(f"[记忆摘要{i + 1}] {s}")
        return "\n".join(lines)

    def get_long_term_context(self) -> str:
        if not self._long_term_facts:
            return ""
        lines = []
        for i, fact in enumerate(self._long_term_facts):
            lines.append(f"[长期事实{i + 1}] {fact}")
        return "\n".join(lines)

    def build_context_block(self) -> str:
        parts = []
        lt = self.get_long_term_context()
        if lt:
            parts.append(f"# 关于用户的重要事实\n{lt}")
        mt = self.get_medium_context()
        if mt:
            parts.append(f"# 近期对话摘要\n{mt}")
        parts.append("# 最近对话")
        st = self.get_short_term_context()
        if st:
            parts.append(st)
        return "\n\n".join(parts)

    def clear_short_term(self):
        self._short_term = []
        self._message_count_since_summary = 0
        self._save_short_term()

    def clear_medium(self):
        self._medium_summaries = []
        self._message_count_since_summary = 0
        self._save_medium()

    def clear_long_term(self):
        self._long_term_facts = []
        self._save_long_term()

    def clear_raw_history(self):
        self._raw_history = []
        self._save_raw_history()

    def clear_all(self):
        self.clear_short_term()
        self.clear_medium()
        self.clear_long_term()
        self._raw_history = []
        self._save_raw_history()

    def get_raw_history(self) -> list[dict]:
        return self._raw_history.copy()

    def get_stats(self) -> dict:
        return {
            "short_term_count": len(self._short_term),
            "medium_summary_count": len(self._medium_summaries),
            "long_term_fact_count": len(self._long_term_facts),
            "raw_history_count": len(self._raw_history),
            "messages_since_summary": self._message_count_since_summary,
            "short_term": self._short_term.copy(),
            "medium_summaries": self._medium_summaries.copy(),
            "long_term_facts": self._long_term_facts.copy(),
            "raw_history": self._raw_history.copy(),
        }
