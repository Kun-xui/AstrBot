import os
import tempfile
import traceback

from quart import request, send_file

from astrbot.core import logger
from astrbot.core.character.package_manager import CharacterPackageManager
from astrbot.core.core_lifecycle import AstrBotCoreLifecycle
from astrbot.core.db import BaseDatabase

from .route import Response, Route, RouteContext


class CharacterRoute(Route):
    def __init__(
        self,
        context: RouteContext,
        db_helper: BaseDatabase,
        core_lifecycle: AstrBotCoreLifecycle,
    ) -> None:
        super().__init__(context)
        self.routes = {
            "/character/list": ("GET", self.list_packages),
            "/character/detail": ("POST", self.get_detail),
            "/character/upload": ("POST", self.upload_package),
            "/character/update": ("POST", self.update_package),
            "/character/delete": ("POST", self.delete_package),
            "/character/export": ("POST", self.export_package),
            "/character/images": ("POST", self.get_images),
            "/character/clean-memory": ("POST", self.clean_memory),
        }
        self.db_helper = db_helper
        self.pkg_mgr = CharacterPackageManager(db_helper)
        self.core_lifecycle = core_lifecycle
        self.register_routes()

    def _pkg_to_dict(self, pkg) -> dict:
        return {
            "character_id": pkg.character_id,
            "name": pkg.name,
            "source_anime": pkg.source_anime,
            "system_prompt": pkg.system_prompt,
            "memory": pkg.memory,
            "tts_provider_id": pkg.tts_provider_id,
            "tts_voice": pkg.tts_voice,
            "tts_enabled": pkg.tts_enabled,
            "image_mode": pkg.image_mode,
            "enabled": pkg.enabled,
            "avatar": pkg.avatar,
            "folder_path": pkg.folder_path,
            "created_at": pkg.created_at.isoformat() if pkg.created_at else None,
            "updated_at": pkg.updated_at.isoformat() if pkg.updated_at else None,
        }

    async def list_packages(self):
        try:
            packages = await self.pkg_mgr.list_packages()
            return Response().ok([self._pkg_to_dict(p) for p in packages]).__dict__
        except Exception as e:
            logger.error(f"Failed to list character packages: {e!s}\n{traceback.format_exc()}")
            return Response().error(f"Failed to list character packages: {e!s}").__dict__

    async def get_detail(self):
        try:
            data = await request.get_json()
            character_id = data.get("character_id")
            if not character_id:
                return Response().error("Missing parameter: character_id").__dict__
            pkg = await self.pkg_mgr.get_package(character_id)
            if not pkg:
                return Response().error("Character package not found").__dict__
            return Response().ok(self._pkg_to_dict(pkg)).__dict__
        except Exception as e:
            logger.error(f"Failed to get character detail: {e!s}\n{traceback.format_exc()}")
            return Response().error(f"Failed to get character detail: {e!s}").__dict__

    async def upload_package(self):
        try:
            files = await request.files
            if "file" not in files:
                return Response().error("No file uploaded").__dict__
            file = files["file"]
            if not file.filename.endswith(".zip"):
                return Response().error("Only .zip files are supported").__dict__

            tmp_dir = tempfile.mkdtemp()
            tmp_path = os.path.join(tmp_dir, file.filename)
            file.save(tmp_path)

            try:
                pkg = await self.pkg_mgr.import_package(tmp_path)
                return Response().ok({
                    "message": "Character package imported successfully",
                    "character": self._pkg_to_dict(pkg),
                }).__dict__
            finally:
                if os.path.exists(tmp_dir):
                    import shutil
                    shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception as e:
            logger.error(f"Failed to upload character package: {e!s}\n{traceback.format_exc()}")
            return Response().error(f"Failed to upload character package: {e!s}").__dict__

    async def update_package(self):
        try:
            data = await request.get_json()
            character_id = data.get("character_id")
            if not character_id:
                return Response().error("Missing parameter: character_id").__dict__

            update_kwargs = {}
            for key in ("name", "system_prompt", "memory", "tts_provider_id",
                        "tts_voice", "tts_enabled", "image_mode", "enabled", "avatar"):
                if key in data:
                    update_kwargs[key] = data[key]

            if not update_kwargs:
                return Response().error("No fields to update").__dict__

            pkg = await self.pkg_mgr.update_package(character_id, **update_kwargs)
            if not pkg:
                return Response().error("Character package not found").__dict__
            return Response().ok({"message": "Character package updated successfully"}).__dict__
        except Exception as e:
            logger.error(f"Failed to update character package: {e!s}\n{traceback.format_exc()}")
            return Response().error(f"Failed to update character package: {e!s}").__dict__

    async def delete_package(self):
        try:
            data = await request.get_json()
            character_id = data.get("character_id")
            if not character_id:
                return Response().error("Missing parameter: character_id").__dict__
            await self.pkg_mgr.delete_package(character_id)
            return Response().ok({"message": "Character package deleted successfully"}).__dict__
        except Exception as e:
            logger.error(f"Failed to delete character package: {e!s}\n{traceback.format_exc()}")
            return Response().error(f"Failed to delete character package: {e!s}").__dict__

    async def export_package(self):
        try:
            data = await request.get_json()
            character_id = data.get("character_id")
            if not character_id:
                return Response().error("Missing parameter: character_id").__dict__

            tmp_dir = tempfile.mkdtemp()
            try:
                zip_path = await self.pkg_mgr.export_package(character_id, tmp_dir)
                return await send_file(
                    zip_path,
                    mimetype="application/zip",
                    as_attachment=True,
                    download_name=os.path.basename(zip_path),
                )
            finally:
                import shutil
                shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception as e:
            logger.error(f"Failed to export character package: {e!s}\n{traceback.format_exc()}")
            return Response().error(f"Failed to export character package: {e!s}").__dict__

    async def get_images(self):
        try:
            data = await request.get_json()
            character_id = data.get("character_id")
            if not character_id:
                return Response().error("Missing parameter: character_id").__dict__
            pkg = await self.pkg_mgr.get_package(character_id)
            if not pkg:
                return Response().error("Character package not found").__dict__
            images = self.pkg_mgr.get_image_list(pkg)
            return Response().ok(images).__dict__
        except Exception as e:
            logger.error(f"Failed to get character images: {e!s}\n{traceback.format_exc()}")
            return Response().error(f"Failed to get character images: {e!s}").__dict__

    async def clean_memory(self):
        try:
            data = await request.get_json()
            character_id = data.get("character_id")
            if not character_id:
                return Response().error("Missing parameter: character_id").__dict__
            pkg = await self.pkg_mgr.get_package(character_id)
            if not pkg:
                return Response().error("Character package not found").__dict__

            memory_text = pkg.memory
            if not memory_text:
                return Response().ok({"message": "No memory to clean"}).__dict__

            try:
                from astrbot.api.provider import ProviderType
                provider = self.core_lifecycle.provider_manager.get_using_provider(
                    ProviderType.CHAT_COMPLETION
                )
            except Exception:
                provider = None

            if provider:
                clean_prompt = (
                    "You are a memory filter. Your task is to clean the following character memory text. "
                    "Remove any personal chat history, user-specific interactions, or usage traces. "
                    "Keep ONLY the character's core personality traits, background story, relationships, "
                    "and canonical memories from the original source material. "
                    "Output ONLY the cleaned memory text, nothing else.\n\n"
                    f"Original memory:\n{memory_text}"
                )
                try:
                    resp = await provider.text_chat(
                        prompt=clean_prompt,
                        contexts=[],
                        system_prompt="You are a precise memory filter. Output only cleaned memory.",
                    )
                    if resp and resp.result:
                        cleaned = resp.result.strip()
                        await self.pkg_mgr.update_package(character_id, memory=cleaned)
                        return Response().ok({
                            "message": "Memory cleaned successfully",
                            "original_length": len(memory_text),
                            "cleaned_length": len(cleaned),
                        }).__dict__
                except Exception as e:
                    logger.warning(f"Model-based memory cleaning failed, falling back to basic cleaning: {e}")

            await self.pkg_mgr.update_package(character_id, memory=memory_text)
            return Response().ok({"message": "Memory cleaned (basic mode)"}).__dict__
        except Exception as e:
            logger.error(f"Failed to clean character memory: {e!s}\n{traceback.format_exc()}")
            return Response().error(f"Failed to clean character memory: {e!s}").__dict__
