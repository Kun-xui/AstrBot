import os

from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.core import logger
from astrbot.core.character.package_manager import CharacterPackageManager
from astrbot.core.db import BaseDatabase


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
            yield event.plain_result("No character packages installed.")
            return
        lines = ["Available characters:"]
        for pkg in packages:
            status = "Enabled" if pkg.enabled else "Disabled"
            active = " [Active]" if self._get_active_character(event.unified_msg_origin) == pkg.character_id else ""
            lines.append(f"  - {pkg.name} ({pkg.source_anime or 'Unknown'}) [{status}]{active}")
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
            yield event.plain_result(f"Character '{name}' not found or not enabled.")
            return
        self._active_characters[event.unified_msg_origin] = target.character_id
        yield event.plain_result(f"Switched to character: {target.name}")

    @character_cmd.group_command("off", alias={"关闭"})
    async def off_character(self, event: AstrMessageEvent):
        umo = event.unified_msg_origin
        if umo in self._active_characters:
            del self._active_characters[umo]
            yield event.plain_result("Character roleplay disabled.")
        else:
            yield event.plain_result("No character is currently active.")

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

    @filter.on_llm_response()
    async def process_character_response(
        self, event: AstrMessageEvent, resp: LLMResponse
    ) -> None:
        character_id = self._get_active_character(event.unified_msg_origin)
        if not character_id:
            return
        pkg = await self.pkg_mgr.get_package(character_id)
        if not pkg or not pkg.enabled:
            return
        if resp.role != "assistant" or not resp.result:
            return
        resp.result = resp.result

    @filter.after_message_sent()
    async def after_message_sent(self, event: AstrMessageEvent) -> None:
        pass

    async def enrich_message_with_images(
        self, event: AstrMessageEvent, text: str, character_id: str
    ) -> list:
        pkg = await self.pkg_mgr.get_package(character_id)
        if not pkg:
            return [Plain(text=text)]

        cleaned_text, image_filenames = self.pkg_mgr.parse_image_markers(text)
        components = []
        if cleaned_text:
            components.append(Plain(text=cleaned_text))

        for filename in image_filenames:
            img_path = self.pkg_mgr.get_image_path(pkg, filename)
            if img_path and os.path.isfile(img_path):
                try:
                    components.append(Image.from_file(img_path))
                except Exception as e:
                    logger.warning(f"Failed to attach image {filename}: {e}")
            else:
                similar = self._find_similar_image(pkg, filename)
                if similar:
                    try:
                        components.append(Image.from_file(similar))
                    except Exception as e:
                        logger.warning(f"Failed to attach fallback image: {e}")

        if not components:
            components.append(Plain(text=text))

        return components

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
