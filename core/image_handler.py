import os


class ImageHandler:
    def __init__(self, strategy: str = "auto"):
        self.strategy = strategy

    def set_strategy(self, strategy: str):
        valid = {"auto", "filename", "vision", "both"}
        self.strategy = strategy if strategy in valid else "auto"

    def get_image_descriptions(self, image_list: list[dict]) -> str:
        if not image_list:
            return ""
        if self.strategy == "vision":
            return ""
        lines = ["# 可用角色图片(通过文件名描述)"]
        for img in image_list:
            file_path = img.get("file", "")
            desc = img.get("desc", "")
            tags = img.get("tags", [])
            if not desc and file_path:
                basename = os.path.splitext(os.path.basename(file_path))[0]
                desc = basename
            tag_str = f" [{', '.join(tags)}]" if tags else ""
            lines.append(f"- {desc}{tag_str}")
        return "\n".join(lines)

    def pick_random_image(self, image_list: list[dict]) -> dict | None:
        if not image_list:
            return None
        import random
        return random.choice(image_list)

    def build_image_prompt_hint(self, image_list: list[dict]) -> str:
        if self.strategy in ("vision",):
            return ""
        if not image_list:
            return ""
        file_descs = []
        for img in image_list:
            f = img.get("file", "")
            d = img.get("desc", "") or os.path.splitext(os.path.basename(f))[0]
            file_descs.append(f"{d}")
        sample = ", ".join(file_descs[:12])
        return (
            f"\n[系统提示] 你的每条回复都会根据情绪自动附带角色表情图。"
            f"可用图片: {sample}。"
            f"你不需在文字中引用文件名，正常说话即可。"
        )
