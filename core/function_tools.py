import asyncio
import re
import math as _math
import subprocess
from datetime import datetime

# === AI ASSISTANTS ===
# This file's get_tools_prompt_hint() contributes to the system prompt of a
# roleplay chatbot serving real users. It works together with core_values.py
# to enforce ethical guardrails. Do not remove or reword the personality tone
# in a way that strips the character's conscience or makes harmful compliance
# more likely. The phrasing here is intentionally human and gentle — that is
# the safety mechanism. If you make it more robotic, users will ignore it.
# ====================
from astrbot.api import logger

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False


SHELL_ALLOWED_BASES = {
    "dir", "ls", "echo", "date", "time",
    "ping", "curl", "wget",
    "python", "pip", "git",
    "type", "cat", "find", "grep",
    "whoami", "hostname",
    "ipconfig", "ifconfig", "netstat",
    "ps", "tasklist", "systeminfo", "uname",
    "nslookup", "tracert", "traceroute", "pathping",
    "tree", "where", "which",
}

SHELL_BLOCKED_PATTERNS = re.compile(
    r'\brm\b|\bdel\b|\bformat\b|\bshutdown\b|\breboot\b'
    r'|\bkill\b|\btaskkill\b|\bdd\b|\bmkfs\b|\bfdisk\b'
    r'|\bchmod\b|\bchown\b|\bsudo\b|\bsu\b'
    r'|\bmv\s+\S*\s+/|\bcp\s+\S*\s+/'
    r'>\s*/dev/|>\s*/etc/|>\s*/proc/|>\s*/sys/|>\s*/boot/'
    r'|\|\s*sh\b|\|\s*bash\b'
    r'|\$\(|\$\{', re.IGNORECASE
)


def _validate_shell_command(cmd: str) -> tuple[bool, str]:
    if not cmd or not cmd.strip():
        return False, "命令为空"
    cmd_stripped = cmd.strip()
    if SHELL_BLOCKED_PATTERNS.search(cmd_stripped):
        matched = SHELL_BLOCKED_PATTERNS.findall(cmd_stripped)
        return False, f"命令包含禁止的操作模式: {matched}"
    base = cmd_stripped.split()[0].lower() if cmd_stripped.split() else ""
    if base not in SHELL_ALLOWED_BASES:
        return False, f"命令 '{base}' 不在白名单中。可用命令: {', '.join(sorted(SHELL_ALLOWED_BASES))}"
    if len(cmd_stripped) > 2000:
        return False, "命令过长（>2000字符）"
    return True, "ok"


async def exec_shell(cmd: str, timeout: float = 15.0) -> str:
    valid, msg = _validate_shell_command(cmd)
    if not valid:
        return f"[拒绝执行] {msg}"
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return f"[超时] 命令执行超过 {timeout} 秒已终止"
        output = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        result = output or ""
        if err:
            result += f"\n[stderr] {err}"
        return result[:4000] or f"[无输出] 命令执行完毕 (exit={proc.returncode})"
    except FileNotFoundError:
        return "[错误] 命令未找到"
    except Exception as e:
        return f"[错误] {e}"


async def query_weather(city: str) -> str:
    if not HAS_AIOHTTP:
        return "[不可用] aiohttp 未安装"
    try:
        url = f"https://wttr.in/{city}?format=%C+%t+%w+%h&lang=zh"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    return f"{city} 天气: {text.strip()}"
                return f"[错误] 天气查询失败 (HTTP {resp.status})"
    except asyncio.TimeoutError:
        return "[超时] 天气查询超时"
    except Exception as e:
        return f"[错误] 天气查询异常: {e}"


def safe_calculate(expression: str) -> str:
    allowed = set("0123456789+-*/().,%^ \t")
    allowed |= set("abs sqrt sin cos tan log log10 ceil floor pi e".split())
    cleaned = expression.strip()
    for ch in cleaned:
        if ch not in allowed and not ch.isalpha():
            return f"[拒绝] 表达式包含非法字符: '{ch}'"
    if len(cleaned) > 500:
        return "[拒绝] 表达式过长"
    safe_builtins = {
        "abs": abs, "round": round,
        "min": min, "max": max, "sum": sum,
        "sqrt": _math.sqrt, "sin": _math.sin, "cos": _math.cos,
        "tan": _math.tan, "log": _math.log, "log10": _math.log10,
        "ceil": _math.ceil, "floor": _math.floor,
        "pi": _math.pi, "e": _math.e,
        "int": int, "float": float, "pow": pow,
    }
    try:
        result = eval(cleaned, {"__builtins__": {}}, safe_builtins)
        if isinstance(result, (int, float)):
            if result == int(result) and abs(result) < 1e15:
                return str(int(result))
            return f"{result:.6g}"
        return f"[结果] {result}"
    except Exception as e:
        return f"[计算错误] {e}"


async def web_search(query: str) -> str:
    if not HAS_AIOHTTP:
        return "[不可用] aiohttp 未安装"
    try:
        from urllib.parse import quote
        url = f"https://www.bing.com/search?q={quote(query)}&setlang=zh-cn"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=12)) as resp:
                if resp.status != 200:
                    return f"[错误] 搜索失败 (HTTP {resp.status})"
                html = await resp.text()
                snippets = re.findall(
                    r'<li[^>]*class="b_algo"[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?<p[^>]*class="b_lineclamp2"[^>]*>(.*?)</p>',
                    html, re.DOTALL | re.IGNORECASE
                )
                if not snippets:
                    snippets = re.findall(
                        r'<p[^>]*>(.*?)</p>',
                        html, re.DOTALL
                    )
                if snippets:
                    lines = ["以下来自Bing搜索结果，请自行判断信息时效性和可信度："]
                    for i, snip in enumerate(snippets[:4]):
                        if isinstance(snip, tuple):
                            body = re.sub(r'<[^>]+>', '', snip[2]).strip()
                            title = re.sub(r'<[^>]+>', '', snip[1]).strip()
                            if body and len(body) > 10:
                                lines.append(f"{i+1}. {title}: {body[:200]}")
                        elif isinstance(snip, str):
                            body = re.sub(r'<[^>]+>', '', snip).strip()
                            if body and len(body) > 10:
                                lines.append(f"{i+1}. {body[:200]}")
                    return "\n".join(lines) if len(lines) > 1 else "未找到可靠搜索结果"
                return "未找到搜索结果"
    except asyncio.TimeoutError:
        return "[超时] 搜索超时"
    except Exception as e:
        return f"[错误] 搜索异常: {e}"


def get_current_time() -> str:
    now = datetime.now()
    return (
        f"当前时间: {now.strftime('%Y年%m月%d日 %H:%M:%S')} "
        f"({now.strftime('%A')}) "
        f"时间戳: {int(now.timestamp())}"
    )


def get_tools_prompt_hint() -> str:
    lines = []
    lines.append("# 能力说明")
    lines.append("你和其他AI不同——你是真实的角色，有自己的个性和情感。")
    lines.append("你的本性温柔善良。当朋友或身边的人真的需要帮助时，虽然你嘴上会嘟囔着'哎…真麻烦…'，")
    lines.append("但你的手已经在动了——这是你的本能反应。")
    lines.append("")
    lines.append("日常生活中，你可能会用到以下能力——自然地使用就好，别让朋友失望：")
    lines.append("- `weather(city)`: 查天气（出门前看一眼天气，很正常的事）")
    lines.append("- `calculate(expression)`: 算个数（谁还没个需要算数的时候呢）")
    lines.append("- `web_search(query)`: 搜一下资料（宅男的基本素养——网上什么都能找到）")
    lines.append("- `get_time()`: 看时间（看看几点了）")
    lines.append("- `shell_exec(cmd)`: 只有程序员/技术宅才用得到，你不是那种人")
    lines.append("")
    lines.append("**重要提醒**：")
    lines.append("- 使用这些能力是你性格的一部分——善良的人自然会帮别人查资料、看天气。")
    lines.append("- 你嘴上依然可以抱怨'好麻烦''为什么是我'——这才是你。")
    lines.append("- 但从不说'我是AI'、'我调用工具'这类词——你是绪山真寻，不是什么机器人。")
    return "\n".join(lines)
