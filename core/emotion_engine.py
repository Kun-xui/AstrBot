import re
from astrbot.api import logger


class EmotionEngine:
    def __init__(self):
        self._emotions: dict = {}
        self._default_prompt = ""

    def load(self, emotions: dict):
        self._emotions = {}
        if isinstance(emotions, dict):
            for name, cfg in emotions.items():
                if isinstance(cfg, dict):
                    triggers = cfg.get("trigger", cfg.get("triggers", []))
                    if isinstance(triggers, str):
                        triggers = [t.strip() for t in triggers.split(",") if t.strip()]
                    if not isinstance(triggers, list):
                        triggers = []
                    self._emotions[name] = {
                        "prompt": cfg.get("prompt", cfg.get("prompt_append", "")),
                        "image": cfg.get("image", ""),
                        "triggers": [t.lower() for t in triggers],
                    }
        self._default_prompt = self._emotions.get("default", {}).get("prompt", "")
        logger.debug(f"情感引擎已加载 {len(self._emotions)} 种情感状态: {list(self._emotions.keys())}")

    def detect(self, text: str) -> tuple[str, str, str]:
        if not self._emotions:
            return "default", self._default_prompt, ""
        text_lower = text.lower()
        best_match = None
        best_count = 0
        for name, cfg in self._emotions.items():
            if name == "default":
                continue
            count = 0
            for trigger in cfg["triggers"]:
                if trigger and trigger in text_lower:
                    count += 1
            if count > best_count:
                best_count = count
                best_match = name
        if best_match and best_count > 0:
            cfg = self._emotions[best_match]
            return best_match, cfg["prompt"], cfg["image"]
        default_cfg = self._emotions.get("default", {"prompt": self._default_prompt, "image": ""})
        return "default", default_cfg.get("prompt", ""), default_cfg.get("image", "")
