import os
import asyncio
import hashlib
import aiohttp
from astrbot.api import logger


class TTSManager:
    def __init__(self, cache_dir: str):
        self.cache_dir = cache_dir
        self.engine = "edge_tts"
        self.gpt_sovits_url = "http://127.0.0.1:9880"
        self.launch_script = ""
        self._sovits_online = False
        self._models_loaded = False
        os.makedirs(cache_dir, exist_ok=True)

    def configure(self, engine: str, gpt_sovits_url: str = "", launch_script: str = "",
                  cloud_api_url: str = "", cloud_api_key: str = "",
                  edge_voice: str = "zh-CN-XiaoxiaoNeural"):
        self.engine = engine
        if gpt_sovits_url:
            self.gpt_sovits_url = gpt_sovits_url.rstrip("/")
        if launch_script:
            self.launch_script = launch_script
        self.cloud_api_url = cloud_api_url
        self.cloud_api_key = cloud_api_key
        self.edge_voice = edge_voice

    async def check_gpt_sovits_online(self) -> bool:
        if self.engine != "gpt_sovits":
            return False
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.gpt_sovits_url}/docs", timeout=aiohttp.ClientTimeout(total=3)) as resp:
                    self._sovits_online = resp.status < 500
                    return self._sovits_online
        except Exception:
            self._sovits_online = False
            return False

    async def ensure_gpt_sovits_online(self) -> bool:
        if await self.check_gpt_sovits_online():
            return True
        if not self.launch_script or not os.path.exists(self.launch_script):
            logger.warning("GPT-SoVITS 未在线且未配置启动脚本")
            return False
        logger.info(f"正在启动 GPT-SoVITS: {self.launch_script}")
        try:
            subprocess = await asyncio.create_subprocess_exec(
                "cmd.exe", "/c", self.launch_script,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=0x00000008
            )
            for _ in range(30):
                await asyncio.sleep(2)
                if await self.check_gpt_sovits_online():
                    logger.info("GPT-SoVITS 启动成功")
                    return True
            logger.warning("GPT-SoVITS 启动超时(60s)")
            return False
        except Exception as e:
            logger.error(f"启动 GPT-SoVITS 失败: {e}")
            return False

    async def _load_models(self, gpt_model: str, sovits_model: str) -> bool:
        if self._models_loaded:
            return True
        try:
            async with aiohttp.ClientSession() as session:
                r1 = await session.get(
                    f"{self.gpt_sovits_url}/set_gpt_weights?weights_path={gpt_model}",
                    timeout=aiohttp.ClientTimeout(total=30)
                )
                r2 = await session.get(
                    f"{self.gpt_sovits_url}/set_sovits_weights?weights_path={sovits_model}",
                    timeout=aiohttp.ClientTimeout(total=30)
                )
                if r1.status == 200 and r2.status == 200:
                    self._models_loaded = True
                    logger.info("GPT-SoVITS 语音模型加载成功")
                    return True
                logger.error(f"模型加载失败: GPT={r1.status} SoVITS={r2.status}")
                return False
        except Exception as e:
            logger.error(f"加载模型异常: {e}")
            return False

    async def synthesize_edge_tts(self, text: str, voice: str = "zh-CN-XiaoxiaoNeural") -> str | None:
        cache_key = hashlib.md5(f"edge_{voice}_{text}".encode()).hexdigest()
        cache_path = os.path.join(self.cache_dir, f"{cache_key}.mp3")
        if os.path.exists(cache_path):
            return cache_path
        try:
            import edge_tts
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(cache_path)
            logger.info(f"Edge-TTS 合成完成: {cache_path}")
            return cache_path
        except Exception as e:
            logger.error(f"Edge-TTS 合成失败: {e}")
            return None

    async def synthesize_gpt_sovits(self, text: str, ref_audio: str = "",
                                      ref_text: str = "", prompt_lang: str = "ja",
                                      gpt_model: str = "", sovits_model: str = "") -> str | None:
        cache_key = hashlib.md5(f"sovits_{ref_audio}_{text}".encode()).hexdigest()
        cache_path = os.path.join(self.cache_dir, f"{cache_key}.wav")
        if os.path.exists(cache_path):
            return cache_path
        if not self._sovits_online or not self._models_loaded:
            online = await self.ensure_gpt_sovits_online()
            if not online:
                return None
            if gpt_model and sovits_model:
                await self._load_models(gpt_model, sovits_model)
        try:
            payload = {
                "text": text,
                "text_lang": "zh",
                "prompt_lang": prompt_lang,
                "ref_audio_path": ref_audio if ref_audio and os.path.exists(ref_audio) else "",
                "text_split_method": "cut0",
                "media_type": "wav",
            }
            if ref_text:
                payload["prompt_text"] = ref_text
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.gpt_sovits_url}/tts",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=60)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        with open(cache_path, "wb") as f:
                            f.write(data)
                        logger.info(f"GPT-SoVITS 合成完成: {cache_path}")
                        return cache_path
                    else:
                        text_resp = await resp.text()
                        logger.error(f"GPT-SoVITS API 返回 {resp.status}: {text_resp[:200]}")
                        return None
        except Exception as e:
            logger.error(f"GPT-SoVITS 合成失败: {e}")
            self._sovits_online = False
            return None

    async def synthesize(self, text: str, voice_config: dict | None = None,
                          role_dir: str = "") -> str | None:
        if self.engine == "disabled":
            return None
        if self.engine == "edge_tts":
            voice = self.edge_voice or "zh-CN-XiaoxiaoNeural"
            if voice_config:
                voice = voice_config.get("edge_voice", voice)
            return await self.synthesize_edge_tts(text, voice)
        if self.engine == "gpt_sovits":
            ref_audio = ""
            ref_text = ""
            prompt_lang = "ja"
            gpt_model = ""
            sovits_model = ""
            if voice_config:
                ref_audio = voice_config.get("ref_audio", "")
                ref_text = voice_config.get("ref_text", "")
                prompt_lang = voice_config.get("prompt_lang", "ja")
                gpt_model = voice_config.get("gpt_model", "")
                sovits_model = voice_config.get("sovits_model", "")
                if ref_audio and not os.path.isabs(ref_audio) and role_dir:
                    ref_audio = os.path.normpath(os.path.join(role_dir, ref_audio))
            ref_audio = ref_audio.replace("\\", "/")
            return await self.synthesize_gpt_sovits(text, ref_audio, ref_text, prompt_lang=prompt_lang,
                                                     gpt_model=gpt_model, sovits_model=sovits_model)
        logger.warning(f"未知 TTS 引擎: {self.engine}")
        return None
