import os

from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.core import logger
from astrbot.core.agent.message import TextPart
from astrbot.core.character.package_manager import CharacterPackageManager
from astrbot.core.db import BaseDatabase
from astrbot.core.message.components import Image, Plain, Record


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        self.context = context
        self.db: BaseDatabase = context.db_helper
        self.pkg_mgr = CharacterPackageManager(self.db)
        self._active_characters: dict[str, str] = {}

    def _get_active_character(self, umo: str) -> str | None:
        return self._active_characters.get(umo)

    @filter.command_group("character", alias={"角色"})
    async def character_cmd(self, event: AstrMessageEvent):
        pass

    @character_cmd.group_command("list", alias={"列表"})
    async def list_characters(self, event: AstrMessageEvent):
        packages = await self.pkg_mgr.list_packages()
        if not packages:
            yield event.plain_result("当前没有安装任何角色包。")
            return
        lines = ["可用角色列表:"]
        for pkg in packages:
            status = "已启用" if pkg.enabled else "已禁用"
            active = " [当前使用]" if self._get_active_character(event.unified_msg_origin) == pkg.character_id else ""
            lines.append(f"  - {pkg.name} ({pkg.source_anime or '未知来源'}) [{status}]{active}")
        yield event.plain_result("\n".join(lines))

    @character_cmd.group_command("use", alias={"使用"})
    async def use_character(self, event: AstrMessageEvent, name: str):
        packages = await self.pkg_mgr.list_packages()
        target = None
        for pkg in packages:
            if pkg.name == name and pkg.enabled:
                target = pkg
                break
        if not target:
            yield event.plain_result(f"角色 '{name}' 未找到或未启用。")
            return
        self._active_characters[event.unified_msg_origin] = target.character_id
        yield event.plain_result(f"已切换到角色: {target.name}")

    @character_cmd.group_command("off", alias={"关闭"})
    async def off_character(self, event: AstrMessageEvent):
        umo = event.unified_msg_origin
        if umo in self._active_characters:
            del self._active_characters[umo]
            yield event.plain_result("角色扮演已关闭。")
        else:
            yield event.plain_result("当前没有激活的角色。")

    @filter.on_llm_request()
    async def inject_character_prompt(
        self, event: AstrMessageEvent, req: ProviderRequest
    ) -> None:
        character_id = self._get_active_character(event.unified_msg_origin)
        if not character_id:
            return
        pkg = await self.pkg_mgr.get_package(character_id)
        if not pkg or not pkg.enabled:
            return
        character_prompt = self.pkg_mgr.build_base_prompt(pkg)
        if req.system_prompt:
            req.system_prompt = character_prompt + "\n\n" + req.system_prompt
        else:
            req.system_prompt = character_prompt
        image_section = self.pkg_mgr.build_image_prompt_section(pkg)
        if image_section:
            req.extra_user_content_parts.append(
                TextPart(text=image_section).mark_as_temp()
            )

    @filter.on_llm_response()
    async def on_llm_response(
        self, event: AstrMessageEvent, resp: LLMResponse
    ) -> None:
        character_id = self._get_active_character(event.unified_msg_origin)
        if not character_id:
            return
        pkg = await self.pkg_mgr.get_package(character_id)
        if not pkg or not pkg.enabled:
            return

    @filter.on_decorating_result()
    async def decorate_result(self, event: AstrMessageEvent) -> None:
        character_id = self._get_active_character(event.unified_msg_origin)
        if not character_id:
            return
        pkg = await self.pkg_mgr.get_package(character_id)
        if not pkg or not pkg.enabled:
            return

        result = event.get_result()
        if not result or not result.chain:
            return

        new_chain = []
        for comp in result.chain:
            if isinstance(comp, Plain) and "[img:" in comp.text:
                cleaned_text, image_filenames = self.pkg_mgr.parse_image_markers(comp.text)
                if cleaned_text:
                    new_chain.append(Plain(text=cleaned_text))
                for filename in image_filenames:
                    img_path = self.pkg_mgr.get_image_path(pkg, filename)
                    if img_path and os.path.isfile(img_path):
                        new_chain.append(Image.fromFileSystem(img_path))
                    else:
                        similar = self._find_similar_image(pkg, filename)
                        if similar:
                            new_chain.append(Image.fromFileSystem(similar))
                        else:
                            logger.warning(f"Character image not found: {filename}")
            else:
                new_chain.append(comp)

        if pkg.tts_enabled and new_chain:
            text_parts = [c.text for c in new_chain if isinstance(c, Plain)]
            full_text = " ".join(text_parts).strip()
            if full_text:
                cached_audio = self.pkg_mgr.get_tts_cache_path(pkg, full_text)
                if cached_audio:
                    new_chain.append(Record.fromFileSystem(cached_audio, text=full_text))
                else:
                    try:
                        tts_provider = self.context.get_using_tts_provider(
                            umo=event.unified_msg_origin
                        )
                        if tts_provider:
                            audio_path = await tts_provider.get_audio(full_text)
                            if audio_path and os.path.isfile(audio_path):
                                ext = os.path.splitext(audio_path)[1] or ".wav"
                                with open(audio_path, "rb") as af:
                                    audio_bytes = af.read()
                                saved_path = self.pkg_mgr.save_tts_cache(
                                    pkg, full_text, audio_bytes, ext=ext
                                )
                                new_chain.append(
                                    Record.fromFileSystem(saved_path, text=full_text)
                                )
                    except Exception as e:
                        logger.warning(f"Character TTS failed: {e}")

        result.chain = new_chain

    def _find_similar_image(self, pkg, keyword: str) -> str | None:
        images = self.pkg_mgr.get_image_list(pkg)
        keyword_lower = keyword.lower()
        for img in images:
            if keyword_lower in img["description"].lower():
                return img["path"]
        for img in images:
            desc_words = img["description"].lower().replace("_", " ").split()
            if any(w in keyword_lower for w in desc_words):
                return img["path"]
        return None
