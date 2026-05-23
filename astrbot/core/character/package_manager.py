import hashlib
import os
import shutil
import zipfile
from pathlib import Path

import yaml

from astrbot.core import logger
from astrbot.core.db import BaseDatabase
from astrbot.core.db.po import CharacterPackage
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

CHARACTER_DATA_DIR = os.path.join(get_astrbot_data_path(), "character_packages")


class CharacterPackageManager:
    def __init__(self, db: BaseDatabase) -> None:
        self.db = db
        os.makedirs(CHARACTER_DATA_DIR, exist_ok=True)

    async def import_package(self, zip_path: str) -> CharacterPackage:
        extract_dir = os.path.join(
            CHARACTER_DATA_DIR, hashlib.md5(open(zip_path, "rb").read()).hexdigest()[:12]
        )
        os.makedirs(extract_dir, exist_ok=True)

        with zipfile.ZipFile(zip_path, "r") as zf:
            top_dirs = set()
            for name in zf.namelist():
                parts = name.split("/")
                if len(parts) > 1:
                    top_dirs.add(parts[0])
            if len(top_dirs) == 1:
                prefix = top_dirs.pop() + "/"
                for name in zf.namelist():
                    if name.startswith(prefix):
                        target = os.path.join(extract_dir, name[len(prefix):])
                        if name.endswith("/"):
                            os.makedirs(target, exist_ok=True)
                        else:
                            os.makedirs(os.path.dirname(target), exist_ok=True)
                            with zf.open(name) as src, open(target, "wb") as dst:
                                dst.write(src.read())
            else:
                zf.extractall(extract_dir)

        config_path = os.path.join(extract_dir, "config.yaml")
        config = {}
        if os.path.exists(config_path):
            with open(config_path, encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}

        prompt_path = os.path.join(extract_dir, "prompt.txt")
        system_prompt = ""
        if os.path.exists(prompt_path):
            with open(prompt_path, encoding="utf-8") as f:
                system_prompt = f.read().strip()

        memory_path = os.path.join(extract_dir, "memory.txt")
        memory = ""
        if os.path.exists(memory_path):
            with open(memory_path, encoding="utf-8") as f:
                memory = f.read().strip()

        name = config.get("name", Path(zip_path).stem)
        source_anime = config.get("source_anime")
        tts_provider_id = config.get("tts_provider_id")
        tts_voice = config.get("tts_voice")
        tts_enabled = config.get("tts_enabled", False)
        image_mode = config.get("image_mode", "combined")
        avatar = config.get("avatar")

        images_dir = os.path.join(extract_dir, "images")
        if not os.path.exists(images_dir):
            os.makedirs(images_dir, exist_ok=True)

        audio_dir = os.path.join(extract_dir, "audio")
        if not os.path.exists(audio_dir):
            os.makedirs(audio_dir, exist_ok=True)

        pkg = await self.db.insert_character_package(
            name=name,
            system_prompt=system_prompt,
            folder_path=extract_dir,
            source_anime=source_anime,
            memory=memory,
            tts_provider_id=tts_provider_id,
            tts_voice=tts_voice,
            tts_enabled=tts_enabled,
            image_mode=image_mode,
            avatar=avatar,
        )
        logger.info("Imported character package: %s (id=%s)", name, pkg.character_id)
        return pkg

    async def delete_package(self, character_id: str) -> None:
        pkg = await self.db.get_character_package(character_id)
        if pkg and os.path.exists(pkg.folder_path):
            shutil.rmtree(pkg.folder_path, ignore_errors=True)
        await self.db.delete_character_package(character_id)
        logger.info("Deleted character package: %s", character_id)

    async def update_package(self, character_id: str, **kwargs) -> CharacterPackage | None:
        return await self.db.update_character_package(character_id, **kwargs)

    async def get_package(self, character_id: str) -> CharacterPackage | None:
        return await self.db.get_character_package(character_id)

    async def list_packages(self) -> list[CharacterPackage]:
        return await self.db.get_all_character_packages()

    def get_image_list(self, pkg: CharacterPackage) -> list[dict]:
        images_dir = os.path.join(pkg.folder_path, "images")
        if not os.path.isdir(images_dir):
            return []
        result = []
        for fname in sorted(os.listdir(images_dir)):
            fpath = os.path.join(images_dir, fname)
            if not os.path.isfile(fpath):
                continue
            stem = Path(fname).stem
            result.append({
                "filename": fname,
                "path": fpath,
                "description": stem,
            })
        return result

    def get_image_path(self, pkg: CharacterPackage, filename: str) -> str | None:
        fpath = os.path.join(pkg.folder_path, "images", filename)
        if os.path.isfile(fpath):
            return fpath
        return None

    def get_tts_cache_path(self, pkg: CharacterPackage, text: str) -> str | None:
        audio_dir = os.path.join(pkg.folder_path, "audio")
        os.makedirs(audio_dir, exist_ok=True)
        text_hash = hashlib.md5(text.encode("utf-8")).hexdigest()
        for ext in (".wav", ".mp3", ".ogg", ".silk"):
            cached = os.path.join(audio_dir, f"{text_hash}{ext}")
            if os.path.isfile(cached):
                return cached
        return None

    def save_tts_cache(self, pkg: CharacterPackage, text: str, audio_data: bytes, ext: str = ".wav") -> str:
        audio_dir = os.path.join(pkg.folder_path, "audio")
        os.makedirs(audio_dir, exist_ok=True)
        text_hash = hashlib.md5(text.encode("utf-8")).hexdigest()
        fpath = os.path.join(audio_dir, f"{text_hash}{ext}")
        with open(fpath, "wb") as f:
            f.write(audio_data)
        return fpath

    async def export_package(self, character_id: str, output_path: str) -> str:
        pkg = await self.db.get_character_package(character_id)
        if not pkg:
            raise ValueError(f"Character package {character_id} not found")

        export_dir = os.path.join(output_path, pkg.name)
        os.makedirs(export_dir, exist_ok=True)

        config = {
            "name": pkg.name,
            "source_anime": pkg.source_anime,
            "tts_provider_id": pkg.tts_provider_id,
            "tts_voice": pkg.tts_voice,
            "tts_enabled": pkg.tts_enabled,
            "image_mode": pkg.image_mode,
            "avatar": pkg.avatar,
        }
        with open(os.path.join(export_dir, "config.yaml"), "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False)

        with open(os.path.join(export_dir, "prompt.txt"), "w", encoding="utf-8") as f:
            f.write(pkg.system_prompt)

        with open(os.path.join(export_dir, "memory.txt"), "w", encoding="utf-8") as f:
            f.write(pkg.memory)

        src_images = os.path.join(pkg.folder_path, "images")
        dst_images = os.path.join(export_dir, "images")
        if os.path.isdir(src_images):
            if os.path.exists(dst_images):
                shutil.rmtree(dst_images)
            shutil.copytree(src_images, dst_images)

        zip_path = os.path.join(output_path, f"{pkg.name}.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(export_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, output_path)
                    zf.write(file_path, arcname)

        shutil.rmtree(export_dir, ignore_errors=True)
        return zip_path

    def build_image_prompt_section(self, pkg: CharacterPackage) -> str:
        images = self.get_image_list(pkg)
        if not images:
            return ""

        lines = [
            "\n[Available Character Images]",
            "You can use images to express emotions. Use the format [img:filename] to include an image in your response.",
            "Available images (filename describes the emotion/expression):",
        ]
        for img in images:
            lines.append(f"  - {img['filename']}: {img['description']}")
        lines.append("")
        lines.append("IMPORTANT: Choose images that match your current emotional state or the situation. Use [img:filename] to embed the image.")
        lines.append("You should frequently use images alongside text to make the conversation more vivid and expressive.")
        lines.append("Prefer responses in the format: short text + [img:filename] + short text, or [img:filename] + voice + short text.")
        return "\n".join(lines)

    def build_base_prompt(self, pkg: CharacterPackage) -> str:
        parts = [
            pkg.system_prompt,
        ]
        if pkg.memory:
            parts.append(f"\n[Character Memory]\n{pkg.memory}")
        image_section = self.build_image_prompt_section(pkg)
        if image_section:
            parts.append(image_section)
        parts.append(
            "\n[Response Rules]\n"
            "1. NEVER use emoji characters in your responses.\n"
            "2. You may use character expression images via [img:filename] format.\n"
            "3. Prefer mixed media responses: short text + image + short text, or voice + image.\n"
            "4. Stay in character at all times. Never break the fourth wall.\n"
            "5. Express emotions vividly through images and tone rather than emoji."
        )
        return "\n".join(parts)

    def parse_image_markers(self, text: str) -> tuple[str, list[str]]:
        import re
        pattern = r'\[img:([^\]]+)\]'
        matches = re.findall(pattern, text)
        cleaned = re.sub(pattern, '', text).strip()
        return cleaned, matches
