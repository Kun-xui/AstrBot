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
        return (
            "\n[系统提示] 你的每条回复都会根据情绪自动附带一张你的角色表情图，"
            "你不需要在文字中引用任何图片文件名，正常说话即可。"
        )
