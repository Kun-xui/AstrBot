import os
import json
import yaml
import shutil
import zipfile
import tempfile
from astrbot.api import logger
from .config_loader import load_role_config, validate_role_config


class RoleManager:
    def __init__(self, roles_dir: str):
        self.roles_dir = roles_dir
        os.makedirs(roles_dir, exist_ok=True)

    def list_roles(self) -> list[dict]:
        result = []
        if not os.path.isdir(self.roles_dir):
            return result
        for name in os.listdir(self.roles_dir):
            role_dir = os.path.join(self.roles_dir, name)
            if not os.path.isdir(role_dir):
                continue
            config = load_role_config(role_dir)
            if config is None:
                result.append({
                    "name": name,
                    "loaded": False,
                    "error": "config.yaml 无效或缺失",
                    "dir": role_dir,
                })
            else:
                result.append({
                    "name": config.get("name", name),
                    "version": config.get("version", "1.0.0"),
                    "author": config.get("author", "unknown"),
                    "loaded": True,
                    "dir": role_dir,
                    "image_count": len(config.get("images", [])),
                    "emotions": list(config.get("emotions", {}).keys()),
                    "has_voice": bool(config.get("voice", {}).get("ref_audio")),
                })
        return result

    def install_from_zip(self, zip_path: str) -> tuple[bool, str]:
        if not os.path.exists(zip_path):
            return False, f"ZIP 文件不存在: {zip_path}"
        tmp_dir = tempfile.mkdtemp(prefix="role_install_")
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                namelist = zf.namelist()
                if not namelist:
                    return False, "ZIP 文件为空"
                root_dir = namelist[0].split("/")[0]
                zf.extractall(tmp_dir)
            extracted_root = os.path.join(tmp_dir, root_dir)
            if not os.path.isdir(extracted_root):
                for item in os.listdir(tmp_dir):
                    item_path = os.path.join(tmp_dir, item)
                    if os.path.isdir(item_path):
                        extracted_root = item_path
                        break
            if not os.path.isdir(extracted_root):
                return False, "ZIP 解压后未找到有效目录"
            config_path = os.path.join(extracted_root, "config.yaml")
            if not os.path.exists(config_path):
                return False, "ZIP 中缺少 config.yaml"
            with open(config_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
            if not raw:
                return False, "config.yaml 内容为空"
            name = raw.get("name", root_dir)
            ok, msg = validate_role_config(raw)
            if not ok:
                return False, f"config.yaml 校验失败: {msg}"
            dest_dir = os.path.join(self.roles_dir, name)
            if os.path.exists(dest_dir):
                shutil.rmtree(dest_dir, ignore_errors=True)
            shutil.copytree(extracted_root, dest_dir)
            logger.info(f"角色 [{name}] 安装成功: {dest_dir}")
            return True, name
        except zipfile.BadZipFile:
            return False, "文件不是有效的 ZIP 格式"
        except Exception as e:
            logger.error(f"安装角色ZIP失败: {e}")
            return False, str(e)
        finally:
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass

    def get_role_config(self, role_name: str) -> dict | None:
        role_dir = os.path.join(self.roles_dir, role_name)
        if not os.path.isdir(role_dir):
            return None
        return load_role_config(role_dir)

    def get_role_dir(self, role_name: str) -> str:
        return os.path.join(self.roles_dir, role_name)

    def delete_role(self, role_name: str) -> bool:
        role_dir = os.path.join(self.roles_dir, role_name)
        if not os.path.isdir(role_dir):
            return False
        try:
            shutil.rmtree(role_dir, ignore_errors=True)
            logger.info(f"角色 [{role_name}] 已删除")
            return True
        except Exception as e:
            logger.error(f"删除角色失败: {e}")
            return False

    def export_role_zip(self, role_name: str) -> str | None:
        role_dir = os.path.join(self.roles_dir, role_name)
        if not os.path.isdir(role_dir):
            return None
        zip_path = os.path.join(tempfile.gettempdir(), f"{role_name}.zip")
        shutil.make_archive(
            os.path.splitext(zip_path)[0],
            "zip",
            os.path.dirname(role_dir),
            role_name
        )
        return zip_path
