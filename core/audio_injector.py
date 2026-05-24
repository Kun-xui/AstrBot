import os
import json
import random
import re
import subprocess
from astrbot.api import logger


class AudioInjector:
    def __init__(self):
        self._audio_map: dict = {}
        self._expressions: dict[str, list[str]] = {}
        self._daily_words: dict[str, list[str]] = {}
        self._music: dict[str, list[str]] = {}
        self._role_dir: str = ""
        self._loaded = False

    def load(self, role_dir: str):
        self._role_dir = role_dir
        self._audio_map = {}
        self._expressions = {}
        self._daily_words = {}
        self._music = {}
        self._loaded = False

        map_path = os.path.join(role_dir, "audio", "audio_map.json")
        if not os.path.exists(map_path):
            logger.debug(f"[AudioInjector] 未找到 audio_map.json: {map_path}")
            return

        try:
            with open(map_path, "r", encoding="utf-8") as f:
                self._audio_map = json.load(f)
        except Exception as e:
            logger.error(f"[AudioInjector] 加载 audio_map.json 失败: {e}")
            return

        audio_dir = os.path.join(role_dir, "audio")
        self._expressions = self._build_file_map(self._audio_map.get("expressions", {}), audio_dir)
        self._daily_words = self._build_file_map(self._audio_map.get("daily_words", {}), audio_dir)
        self._music = self._build_file_map(self._audio_map.get("music", {}), audio_dir)

        existing = sum(len(v) for v in self._expressions.values())
        existing += sum(len(v) for v in self._daily_words.values())
        existing += sum(len(v) for v in self._music.values())
        self._loaded = existing > 0
        logger.info(f"[AudioInjector] 加载完成: 情绪{len(self._expressions)}类, 日常{len(self._daily_words)}类, 音乐{len(self._music)}类, 共{existing}个文件")

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def _build_file_map(self, category: dict, audio_dir: str) -> dict[str, list[str]]:
        result = {}
        for key, files in category.items():
            valid = []
            for f in files:
                full = os.path.join(audio_dir, f)
                if os.path.exists(full):
                    valid.append(f)
            if valid:
                result[key] = valid
        return result

    def get_expression(self, emotion: str) -> str | None:
        if emotion in self._expressions:
            return random.choice(self._expressions[emotion])
        if "default" in self._expressions:
            return random.choice(self._expressions["default"])
        return None

    def match_daily_word(self, text: str) -> str | None:
        text_clean = text.strip()
        for keyword, files in self._daily_words.items():
            if keyword in text_clean:
                candidates = [f for f in files if self._filename_matches(f, keyword)]
                if not candidates:
                    candidates = files
                return random.choice(candidates)
        return None

    def match_music(self, text: str) -> str | None:
        text_clean = text.lower()
        for keyword, files in self._music.items():
            if keyword in text_clean:
                return random.choice(files)
        return None

    def _filename_matches(self, filepath: str, keyword: str) -> bool:
        name = os.path.splitext(os.path.basename(filepath))[0]
        return keyword in name

    def resolve_audio_path(self, relative_path: str) -> str:
        return os.path.join(self._role_dir, relative_path)

    def resolve_audio_for_record(self, relative_path: str) -> str | None:
        full_path = os.path.join(self._role_dir, relative_path)
        if not os.path.exists(full_path):
            return None
        if not full_path.lower().endswith('.mp3'):
            return full_path
        wav_path = os.path.splitext(full_path)[0] + '.wav'
        if os.path.exists(wav_path) and os.path.getsize(wav_path) > 0:
            return wav_path
        ok = self._convert_to_wav(full_path, wav_path)
        if ok:
            return wav_path
        return full_path

    @staticmethod
    def _convert_to_wav(mp3_path: str, wav_path: str) -> bool:
        ffmpeg_candidates = [
            "ffmpeg",
            os.path.expanduser("~/ffmpeg.exe"),
            "C:\\ffmpeg\\bin\\ffmpeg.exe",
            os.path.join(os.path.dirname(__file__), "..", "..", "ffmpeg.exe"),
        ]
        for ff in ffmpeg_candidates:
            try:
                subprocess.run([ff, "-i", mp3_path, "-acodec", "pcm_s16le",
                                "-ar", "24000", "-ac", "1", "-y", wav_path],
                               capture_output=True, check=True, timeout=10)
                if os.path.exists(wav_path) and os.path.getsize(wav_path) > 0:
                    logger.info(f"[AudioInjector] MP3→WAV 转换成功: {os.path.basename(mp3_path)}")
                    return True
            except Exception:
                continue
        logger.warning(f"[AudioInjector] MP3→WAV 转换失败 (ffmpeg未找到): {mp3_path}")
        return False

    def get_music_keywords(self) -> list[str]:
        return list(self._music.keys())

    def get_capability_hint(self) -> str:
        hints = []
        if self._expressions:
            hints.append("你可以通过语气表达情绪")
        if self._daily_words:
            dw_keys = list(self._daily_words.keys())[:8]
            hints.append(f"你的常用语气词包括: {', '.join(dw_keys)}等")
        if self._music:
            hints.append(f"你可以唱的歌有: {', '.join(list(self._music.keys()))}")
        if hints:
            return "\n".join(hints)
        return ""
