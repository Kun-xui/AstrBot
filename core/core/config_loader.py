import os
import yaml
from astrbot.api import logger

ROLE_CONFIG_REQUIRED = ["name", "persona"]
ROLE_CONFIG_OPTIONAL = ["version", "author", "emotions", "background", "images", "voice", "reply_style", "rules"]

DEFAULT_CONFIG = {
    "version": "1.0.0",
    "author": "unknown",
    "emotions": {
        "default": {
            "trigger": [],
            "prompt_append": "",
            "image": ""
        }
    },
    "background": [],
    "images": [],
    "voice": {
        "engine": "gpt_sovits",
        "ref_audio": "",
        "ref_text": "",
        "model_path": ""
    },
    "reply_style": {
        "prefer": ["text"],
        "allow_emoji": True
    },
    "rules": {
        "no_emoji_in_system": True,
        "max_context_messages": 20
    }
}


def validate_role_config(config: dict) -> tuple[bool, str]:
    for field in ROLE_CONFIG_REQUIRED:
        if field not in config or not config[field]:
            return False, f"缺少必需字段: {field}"
    if not isinstance(config.get("persona", ""), str) or not config["persona"].strip():
        return False, "persona 必须是非空字符串"
    if "emotions" in config and not isinstance(config["emotions"], dict):
        return False, "emotions 必须是字典类型"
    if "background" in config and not isinstance(config["background"], list):
        return False, "background 必须是列表类型"
    if "images" in config:
        if not isinstance(config["images"], list):
            return False, "images 必须是列表类型"
        for idx, img in enumerate(config["images"]):
            if not isinstance(img, dict):
                return False, f"images[{idx}] 必须是字典类型"
            if "file" not in img:
                return False, f"images[{idx}] 缺少 file 字段"
    return True, "ok"


def load_role_config(role_dir: str) -> dict | None:
    config_path = os.path.join(role_dir, "config.yaml")
    if not os.path.exists(config_path):
        logger.warning(f"角色配置文件不存在: {config_path}")
        return None
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except Exception as e:
        logger.error(f"读取角色配置失败: {e}")
        return None
    if not config:
        logger.warning(f"角色配置文件为空: {config_path}")
        return None
    merged = {}
    for k, v in DEFAULT_CONFIG.items():
        merged[k] = v
    merged.update({k: v for k, v in config.items() if k != "name"})
    merged["name"] = config.get("name", os.path.basename(role_dir))
    merged["persona"] = config.get("persona", "")
    merged.setdefault("version", "1.0.0")
    merged.setdefault("author", "unknown")
    ok, msg = validate_role_config(merged)
    if not ok:
        logger.error(f"角色配置校验失败 [{merged.get('name')}]: {msg}")
        return None
    merged["_role_dir"] = role_dir
    return merged


def resolve_image_path(role_dir: str, rel_path: str) -> str:
    if os.path.isabs(rel_path):
        return rel_path
    return os.path.normpath(os.path.join(role_dir, rel_path))


def get_images_from_config(config: dict) -> list[dict]:
    result = []
    role_dir = config.get("_role_dir", "")
    for img in config.get("images", []):
        file_path = resolve_image_path(role_dir, img.get("file", ""))
        tags = img.get("tags", [])
        desc = img.get("desc", "")
        if not desc and isinstance(img.get("file"), str):
            basename = os.path.splitext(os.path.basename(img["file"]))[0]
            desc = basename
        result.append({
            "file": file_path,
            "tags": tags if isinstance(tags, list) else [tags],
            "desc": desc,
        })
    return result


def get_emotion_images(config: dict) -> dict[str, str]:
    result = {}
    role_dir = config.get("_role_dir", "")
    emotions = config.get("emotions", {})
    if isinstance(emotions, dict):
        for key, val in emotions.items():
            if isinstance(val, dict) and val.get("image"):
                result[key] = resolve_image_path(role_dir, val["image"])
    return result
