import os
import json
import time
import glob
from datetime import datetime
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
        self._user_facts: list[str] = []
        self._role_facts: list[str] = []
        self._message_count_since_summary = 0
        self._raw_index: dict = {}
        self._current_chunk: list[dict] = []
        self._current_chunk_key: str = ""

    def set_role(self, role_name: str):
        self._role_name = role_name
        self._short_term = []
        self._medium_summaries = []
        self._user_facts = []
        self._role_facts = []
        self._message_count_since_summary = 0
        self._raw_index = {}
        self._current_chunk = []
        self._current_chunk_key = ""
        self._load_all()

    @property
    def role_dir(self) -> str:
        return os.path.join(self.base_dir, self._role_name) if self._role_name else self.base_dir

    @property
    def raw_history_dir(self) -> str:
        return os.path.join(self.role_dir, "raw_history")

    def _this_month_key(self) -> str:
        return datetime.now().strftime("%Y%m")

    def _load_all(self):
        os.makedirs(self.role_dir, exist_ok=True)
        self._short_term = self._load_json("short_term.json", [])
        self._medium_summaries = self._load_json("medium_summaries.json", [])
        self._user_facts = self._load_json("user_facts.json", [])
        self._role_facts = self._load_json("role_facts.json", [])
        self._message_count_since_summary = self._load_json("msg_count.json", 0)
        self._load_raw_index()

    def _load_raw_index(self):
        os.makedirs(self.raw_history_dir, exist_ok=True)
        existing = sorted(glob.glob(os.path.join(self.raw_history_dir, "chunk_*.json")))
        chunks = {}
        for path in existing:
            key = os.path.splitext(os.path.basename(path))[0].replace("chunk_", "")
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    chunks[key] = len(data)
                else:
                    chunks[key] = 0
            except Exception:
                chunks[key] = 0
        self._raw_index = chunks
        old_file = os.path.join(self.role_dir, "raw_history.json")
        if not chunks and os.path.exists(old_file):
            try:
                with open(old_file, "r", encoding="utf-8") as f:
                    old_data = json.load(f)
                if isinstance(old_data, list) and old_data:
                    migrated = {}
                    for entry in old_data:
                        ts = entry.get("time", 0)
                        dt = datetime.fromtimestamp(ts) if ts > 0 else datetime.now()
                        month_key = dt.strftime("%Y%m")
                        migrated.setdefault(month_key, []).append(entry)
                    for mk, entries in migrated.items():
                        chunk_path = os.path.join(self.raw_history_dir, f"chunk_{mk}.json")
                        with open(chunk_path, "w", encoding="utf-8") as f:
                            json.dump(entries, f, ensure_ascii=False, indent=2)
                        self._raw_index[mk] = len(entries)
                    backup_path = old_file + ".bak"
                    os.rename(old_file, backup_path)
                    logger.info(f"[Memory] 已从 raw_history.json 迁移 {len(old_data)} 条 → {len(migrated)} 个月份分块")
            except Exception as e:
                logger.warning(f"[Memory] 旧格式迁移失败: {e}")
        this_month = self._this_month_key()
        if this_month in self._raw_index:
            self._current_chunk = self._load_chunk(this_month)
        else:
            self._current_chunk = []
        self._current_chunk_key = this_month
        total = sum(self._raw_index.values())
        if self._raw_index:
            logger.info(f"[Memory] raw_history: {total}条 / {len(self._raw_index)}个分块 (最早{min(self._raw_index.keys())})")

    def _load_json(self, filename: str, default):
        path = os.path.join(self.role_dir, filename)
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return default

    def _save_json(self, filename: str, data):
        path = os.path.join(self.role_dir, filename)
        os.makedirs(self.role_dir, exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[Memory] 保存 {filename} 失败: {e}")

    def _save_current_chunk(self):
        if not self._current_chunk_key:
            return
        path = os.path.join(self.raw_history_dir, f"chunk_{self._current_chunk_key}.json")
        os.makedirs(self.raw_history_dir, exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._current_chunk, f, ensure_ascii=False, indent=2)
            self._raw_index[self._current_chunk_key] = len(self._current_chunk)
        except Exception as e:
            logger.error(f"[Memory] 保存 raw_history 分块失败: {e}")

    def _load_chunk(self, key: str) -> list[dict]:
        path = os.path.join(self.raw_history_dir, f"chunk_{key}.json")
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning(f"[Memory] 读取分块 {key} 失败: {e}")
        return []

    def _save_all(self):
        self._save_json("short_term.json", self._short_term)
        self._save_json("medium_summaries.json", self._medium_summaries)
        self._save_json("user_facts.json", self._user_facts)
        self._save_json("role_facts.json", self._role_facts)
        self._save_json("msg_count.json", self._message_count_since_summary)
        self._save_current_chunk()

    def add_message(self, sender: str, content: str):
        entry = {
            "sender": sender,
            "content": content,
            "time": time.time()
        }
        self._short_term.append(entry)
        if len(self._short_term) > self.short_term_size:
            self._short_term = self._short_term[-self.short_term_size:]
        this_month = self._this_month_key()
        if this_month != self._current_chunk_key:
            self._save_current_chunk()
            self._current_chunk_key = this_month
            if this_month in self._raw_index:
                self._current_chunk = self._load_chunk(this_month)
            else:
                self._current_chunk = []
        self._current_chunk.append(entry)
        self._message_count_since_summary += 1
        self._save_all()

    def add_user_message(self, content: str):
        self.add_message("user", content)

    def add_bot_message(self, content: str):
        self.add_message(self._role_name, content)

    def needs_summary(self) -> bool:
        return self._message_count_since_summary >= self.medium_interval

    def add_summary(self, summary: str):
        self._medium_summaries.append(summary)
        if len(self._medium_summaries) > 30:
            self._medium_summaries = self._medium_summaries[-30:]
        self._message_count_since_summary = 0
        self._save_all()

    def add_user_fact(self, fact: str):
        if not self.long_term_enabled:
            return
        self._user_facts.append(fact)
        if len(self._user_facts) > 100:
            self._user_facts = self._user_facts[-100:]
        self._save_json("user_facts.json", self._user_facts)

    def add_role_fact(self, fact: str):
        if not self.long_term_enabled:
            return
        self._role_facts.append(fact)
        if len(self._role_facts) > 100:
            self._role_facts = self._role_facts[-100:]
        self._save_json("role_facts.json", self._role_facts)

    def get_short_term_context(self) -> str:
        if not self._short_term:
            return ""
        lines = []
        for entry in self._short_term:
            s = entry.get("sender", "")
            c = entry.get("content", "")
            label = s if s else "?"
            lines.append(f"{label}: {c}")
        return "\n".join(lines)

    def get_medium_context(self) -> str:
        if not self._medium_summaries:
            return ""
        lines = []
        for i, s in enumerate(self._medium_summaries):
            lines.append(f"[摘要{i + 1}] {s}")
        return "\n".join(lines)

    def get_user_facts_context(self) -> str:
        if not self._user_facts:
            return ""
        lines = []
        for i, fact in enumerate(self._user_facts):
            lines.append(f"- {fact}")
        return "\n".join(lines)

    def get_role_facts_context(self) -> str:
        if not self._role_facts:
            return ""
        lines = []
        for i, fact in enumerate(self._role_facts):
            lines.append(f"- {fact}")
        return "\n".join(lines)

    def build_context_block(self) -> str:
        parts = []
        uf = self.get_user_facts_context()
        if uf:
            parts.append(f"# 关于用户的事实（仅你可见，绝对保密）\n{uf}")
        rf = self.get_role_facts_context()
        if rf:
            parts.append(f"# 你学到的角色知识\n{rf}")
        mt = self.get_medium_context()
        if mt:
            parts.append(f"# 近期对话摘要\n{mt}")
        st = self.get_short_term_context()
        if st:
            parts.append(f"# 最近对话\n{st}")
        return "\n\n".join(parts)

    def _all_chunks_sorted(self) -> list[str]:
        return sorted(self._raw_index.keys())

    def query_raw_history(self, keyword: str = "", limit: int = 50,
                          sender: str = "", before_ts: float = 0,
                          after_ts: float = 0, month: str = "") -> list[dict]:
        if month:
            chunks_to_check = [month] if month in self._raw_index else []
        elif before_ts or after_ts:
            chunks_to_check = []
            for ck in self._all_chunks_sorted():
                try:
                    year = int(ck[:4])
                    mo = int(ck[4:6])
                    chunk_start = datetime(year, mo, 1).timestamp()
                    if mo == 12:
                        chunk_end = datetime(year + 1, 1, 1).timestamp()
                    else:
                        chunk_end = datetime(year, mo + 1, 1).timestamp()
                except Exception:
                    chunks_to_check.append(ck)
                    continue
                if after_ts and chunk_end <= after_ts:
                    continue
                if before_ts and chunk_start >= before_ts:
                    continue
                chunks_to_check.append(ck)
        else:
            chunks_to_check = self._all_chunks_sorted()

        results = []
        for ck in reversed(chunks_to_check):
            chunk = self._load_chunk(ck)
            for entry in reversed(chunk):
                if sender and entry.get("sender", "") != sender:
                    continue
                if keyword and keyword.lower() not in entry.get("content", "").lower():
                    continue
                if before_ts and entry.get("time", 0) >= before_ts:
                    continue
                if after_ts and entry.get("time", 0) <= after_ts:
                    continue
                results.append(entry)
                if limit > 0 and len(results) >= limit:
                    return results
        return results

    def get_raw_chunk_info(self) -> dict:
        chunks = []
        total = 0
        for ck in self._all_chunks_sorted():
            count = self._raw_index.get(ck, 0)
            chunks.append({"month": ck, "count": count, "file": f"chunk_{ck}.json"})
            total += count
        return {
            "total": total,
            "chunks": chunks,
            "directory": self.raw_history_dir,
        }

    def clear_short_term(self):
        self._short_term = []
        self._message_count_since_summary = 0
        self._save_all()

    def clear_medium(self):
        self._medium_summaries = []
        self._message_count_since_summary = 0
        self._save_all()

    def clear_user_facts(self):
        self._user_facts = []
        self._save_json("user_facts.json", [])

    def clear_role_facts(self):
        self._role_facts = []
        self._save_json("role_facts.json", [])

    def clear_raw_history(self):
        self._current_chunk = []
        self._raw_index = {}
        self._current_chunk_key = self._this_month_key()
        for path in glob.glob(os.path.join(self.raw_history_dir, "chunk_*.json")):
            try:
                os.remove(path)
            except Exception:
                pass

    def clear_all(self):
        self.clear_short_term()
        self.clear_medium()
        self.clear_user_facts()
        self.clear_role_facts()
        self.clear_raw_history()

    def get_raw_history(self) -> list[dict]:
        results = []
        for ck in self._all_chunks_sorted():
            chunk = self._load_chunk(ck)
            results.extend(chunk)
        return results

    def get_stats(self) -> dict:
        total_raw = sum(self._raw_index.values())
        return {
            "short_term_count": len(self._short_term),
            "medium_summary_count": len(self._medium_summaries),
            "user_fact_count": len(self._user_facts),
            "role_fact_count": len(self._role_facts),
            "raw_history_count": total_raw,
            "raw_chunk_count": len(self._raw_index),
            "messages_since_summary": self._message_count_since_summary,
            "short_term": self._short_term.copy(),
            "medium_summaries": self._medium_summaries.copy(),
            "user_facts": self._user_facts.copy(),
            "role_facts": self._role_facts.copy(),
        }

    def get_export_safe_data(self) -> dict:
        role_only = {
            "short_term": self._short_term.copy(),
            "medium_summaries": self._medium_summaries.copy(),
            "role_facts": self._role_facts.copy(),
        }
        return role_only

_SENTINEL = True
