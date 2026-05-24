import os
import sys
import hashlib
import platform
import uuid as _uuid
from astrbot.api import logger


def generate_device_id() -> str:
    sources = []

    sources.append(platform.node() or "unknown_host")

    try:
        if sys.platform == "win32":
            import subprocess
            result = subprocess.run(
                ["wmic", "csproduct", "get", "uuid"],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.split("\n"):
                line = line.strip()
                if line and line.lower() != "uuid":
                    sources.append(line)
                    break
        elif sys.platform == "darwin":
            import subprocess
            result = subprocess.run(
                ["system_profiler", "SPHardwareDataType"],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.split("\n"):
                if "UUID" in line:
                    sources.append(line)
                    break
        else:
            try:
                with open("/etc/machine-id", "r") as f:
                    sources.append(f.read().strip())
            except Exception:
                pass
    except Exception:
        sources.append(str(_uuid.getnode()))

    sources.append(sys.platform)
    sources.append(platform.machine() or "unknown_arch")

    try:
        sources.append(os.path.abspath(__file__))
    except Exception:
        pass

    combined = "|".join(sources)
    device_id = hashlib.sha256(combined.encode()).hexdigest()[:32]
    logger.info(f"[DeviceFingerprint] 设备ID: {device_id}")
    return device_id


def get_device_name() -> str:
    try:
        return platform.node() or f"AstrBot_{platform.system()}"
    except Exception:
        return f"AstrBot_{platform.system()}_{_uuid.getnode()}"
