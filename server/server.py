"""
角色分享服务器 — 社区共创平台

功能:
  - 角色ZIP上传/下载/发现
  - 音频/图片增量更新（热更新）
  - 设备指纹自动认证（免手动Token）
  - 管理面板

启动: python server.py
"""

import os
import json
import hashlib
import shutil
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path

import yaml
from flask import Flask, request, jsonify, send_file, render_template_string

app = Flask(__name__)

SCRIPT_DIR = Path(__file__).parent.absolute()
CONFIG_PATH = SCRIPT_DIR / "config.yaml"
DEVICES_PATH = SCRIPT_DIR / "devices.json"
CHANGELOG_PATH = SCRIPT_DIR / "changelog.json"

DEFAULT_CONFIG = {
    "host": "0.0.0.0",
    "port": 8765,
    "auth_mode": "auto_register",
    "admin_password": "changeme123",
    "roles_dir": "./roles",
    "max_role_size_mb": 500,
    "max_audio_size_mb": 5,
    "max_image_size_mb": 10,
}

config: dict = {}
registered_devices: dict = {}
changelog: list = []


def load_config():
    global config
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    else:
        config = {}
    for k, v in DEFAULT_CONFIG.items():
        config.setdefault(k, v)


def save_config():
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False)


def load_devices():
    global registered_devices
    if DEVICES_PATH.exists():
        with open(DEVICES_PATH, "r", encoding="utf-8") as f:
            registered_devices = json.load(f)
    else:
        registered_devices = {}


def save_devices():
    with open(DEVICES_PATH, "w", encoding="utf-8") as f:
        json.dump(registered_devices, f, ensure_ascii=False, indent=2)


def load_changelog():
    global changelog
    if CHANGELOG_PATH.exists():
        with open(CHANGELOG_PATH, "r", encoding="utf-8") as f:
            changelog = json.load(f)
    else:
        changelog = []


def save_changelog():
    with open(CHANGELOG_PATH, "w", encoding="utf-8") as f:
        json.dump(changelog, f, ensure_ascii=False, indent=2)


def roles_dir() -> Path:
    p = config.get("roles_dir", "./roles")
    if not os.path.isabs(p):
        p = os.path.join(SCRIPT_DIR, p)
    return Path(p)


def role_dir(name: str) -> Path:
    return roles_dir() / name


def role_zip_path(name: str) -> Path:
    return roles_dir() / f"{name}.zip"


def role_meta_path(name: str) -> Path:
    return roles_dir() / f"{name}.json"


def get_role_meta(name: str) -> dict:
    mp = role_meta_path(name)
    if mp.exists():
        with open(mp, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_role_meta(name: str, meta: dict):
    mp = role_meta_path(name)
    meta["updated_at"] = time.time()
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def scan_roles() -> list[dict]:
    rd = roles_dir()
    if not rd.exists():
        return []
    result = []
    seen = set()
    for f in sorted(rd.iterdir()):
        if f.suffix.lower() == ".zip" and f.stem not in seen:
            seen.add(f.stem)
            meta = get_role_meta(f.stem)
            result.append({
                "name": f.stem,
                "size": f.stat().st_size,
                "version": meta.get("version", "1.0.0"),
                "author": meta.get("author", "未知"),
                "desc": meta.get("desc", ""),
                "updated_at": meta.get("updated_at", 0),
                "audio_count": meta.get("audio_count", 0),
                "image_count": meta.get("image_count", 0),
            })
    return result


def check_device_auth() -> tuple[bool, str]:
    device_id = request.headers.get("X-Device-ID", "")
    auth_mode = config.get("auth_mode", "auto_register")
    if auth_mode == "open":
        return True, device_id or "anonymous"
    if not device_id:
        return False, "缺少 X-Device-ID 请求头"
    if device_id not in registered_devices:
        if auth_mode == "auto_register":
            registered_devices[device_id] = {
                "registered_at": time.time(),
                "name": f"设备_{device_id[:8]}",
                "approved": True,
            }
            save_devices()
            return True, device_id
        elif auth_mode == "admin_approve":
            dev = registered_devices.get(device_id, {})
            if dev.get("approved"):
                return True, device_id
            return False, "设备未注册或未批准"
    dev = registered_devices.get(device_id, {})
    if not dev.get("approved", False) and auth_mode == "admin_approve":
        return False, "设备未批准"
    return True, device_id


def add_changelog(action: str, role_name: str, by_device: str, detail: str = ""):
    entry = {
        "time": time.time(),
        "action": action,
        "role": role_name,
        "device": by_device[:12] + "..." if len(by_device) > 12 else by_device,
        "detail": detail,
    }
    changelog.insert(0, entry)
    if len(changelog) > 500:
        changelog = changelog[:500]
    save_changelog()


# ============================================================
#  角色 API
# ============================================================

@app.route("/api/roles", methods=["GET"])
def api_roles():
    ok, dev_id = check_device_auth()
    if not ok:
        return jsonify({"ok": False, "msg": dev_id}), 403
    roles = scan_roles()
    return jsonify({
        "ok": True,
        "roles": roles,
        "count": len(roles),
        "auth_mode": config.get("auth_mode", "auto_register"),
    })


@app.route("/api/roles/<name>", methods=["GET"])
def api_role_detail(name):
    ok, dev_id = check_device_auth()
    if not ok:
        return jsonify({"ok": False, "msg": dev_id}), 403
    meta = get_role_meta(name)
    zp = role_zip_path(name)
    audio_list = []
    rd = role_dir(name)
    audio_dir = rd / "audio" / "expressions"
    if audio_dir.exists():
        audio_list = sorted([f.name for f in audio_dir.iterdir() if f.suffix.lower() in (".mp3", ".wav")])
    images_list = []
    img_dir = rd / "images"
    if img_dir.exists():
        images_list = sorted([f.name for f in img_dir.iterdir()])
    return jsonify({
        "ok": True,
        "name": name,
        "meta": meta,
        "zip_exists": zp.exists(),
        "zip_size": zp.stat().st_size if zp.exists() else 0,
        "audio_files": audio_list,
        "image_files": images_list,
    })


@app.route("/api/roles/<name>/download", methods=["GET"])
def api_role_download(name):
    ok, dev_id = check_device_auth()
    if not ok:
        return jsonify({"ok": False, "msg": dev_id}), 403
    zp = role_zip_path(name)
    if not zp.exists():
        return jsonify({"ok": False, "msg": f"角色 [{name}] 不存在"}), 404
    add_changelog("download", name, dev_id)
    return send_file(str(zp), as_attachment=True, download_name=f"{name}.zip")


@app.route("/api/roles/share", methods=["POST"])
def api_role_share():
    ok, dev_id = check_device_auth()
    if not ok:
        return jsonify({"ok": False, "msg": dev_id}), 403
    if "file" not in request.files:
        return jsonify({"ok": False, "msg": "未找到文件"}), 400
    file = request.files["file"]
    name = request.form.get("name", "")
    author = request.form.get("author", "")
    version = request.form.get("version", "1.0.0")
    desc = request.form.get("desc", "")
    max_size = config.get("max_role_size_mb", 500) * 1024 * 1024
    tmp = os.path.join(tempfile.gettempdir(), f"role_upload_{uuid.uuid4().hex[:8]}.zip")
    file.save(tmp)
    fsize = os.path.getsize(tmp)
    if fsize > max_size:
        os.remove(tmp)
        return jsonify({"ok": False, "msg": f"文件过大 ({fsize/1024/1024:.1f}MB > {max_size/1024/1024:.0f}MB)"}), 413
    if not name:
        stem = os.path.splitext(file.filename)[0] if file.filename else "unknown"
        name = stem
    dest = role_zip_path(name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(tmp, str(dest))
    meta = {
        "version": version,
        "author": author,
        "desc": desc,
        "size": fsize,
    }
    save_role_meta(name, meta)
    add_changelog("share", name, dev_id, f"v{version} by {author}")
    return jsonify({"ok": True, "msg": f"角色 [{name}] 分享成功", "name": name})


# ============================================================
#  音频热更新 API
# ============================================================

@app.route("/api/roles/<name>/audio", methods=["GET"])
def api_role_audio_list(name):
    ok, _ = check_device_auth()
    if not ok:
        return jsonify({"ok": False, "msg": "未授权"}), 403
    rd = role_dir(name)
    if not rd.exists():
        return jsonify({"ok": False, "msg": f"角色 [{name}] 不存在"}), 404
    expressions = []
    audio_dir = rd / "audio" / "expressions"
    if audio_dir.exists():
        for f in sorted(audio_dir.iterdir()):
            if f.suffix.lower() in (".mp3", ".wav"):
                expressions.append({
                    "name": f.name,
                    "size": f.stat().st_size,
                    "modified": f.stat().st_mtime,
                })
    music = []
    music_dir = rd / "audio" / "music"
    if music_dir.exists():
        for f in sorted(music_dir.iterdir()):
            if f.suffix.lower() in (".mp3", ".wav"):
                music.append({"name": f.name, "size": f.stat().st_size})
    audio_map = {}
    map_path = rd / "audio" / "audio_map.json"
    if map_path.exists():
        with open(map_path, "r", encoding="utf-8") as f:
            audio_map = json.load(f)
    return jsonify({
        "ok": True,
        "name": name,
        "expressions": expressions,
        "music": music,
        "audio_map": audio_map,
    })


@app.route("/api/roles/<name>/audio/<category>/<filename>", methods=["GET"])
def api_role_audio_download(name, category, filename):
    ok, _ = check_device_auth()
    if not ok:
        return jsonify({"ok": False, "msg": "未授权"}), 403
    fp = role_dir(name) / "audio" / category / filename
    if not fp.exists():
        return jsonify({"ok": False, "msg": "文件不存在"}), 404
    return send_file(str(fp))


@app.route("/api/roles/<name>/audio", methods=["POST"])
def api_role_audio_upload(name):
    ok, dev_id = check_device_auth()
    if not ok:
        return jsonify({"ok": False, "msg": dev_id}), 403
    if "file" not in request.files:
        return jsonify({"ok": False, "msg": "未找到文件"}), 400
    file = request.files["file"]
    category = request.form.get("category", "expressions")
    max_size = config.get("max_audio_size_mb", 5) * 1024 * 1024
    fdata = file.read()
    if len(fdata) > max_size:
        return jsonify({"ok": False, "msg": f"音频过大"}), 413
    dest_dir = role_dir(name) / "audio" / category
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / file.filename
    dest.write_bytes(fdata)
    meta = get_role_meta(name)
    meta["audio_count"] = meta.get("audio_count", 0) + 1
    save_role_meta(name, meta)
    add_changelog("audio_upload", name, dev_id, f"{category}/{file.filename}")
    return jsonify({"ok": True, "msg": f"音频 [{file.filename}] 已上传"})


# ============================================================
#  图片热更新 API
# ============================================================

@app.route("/api/roles/<name>/images", methods=["GET"])
def api_role_images_list(name):
    ok, _ = check_device_auth()
    if not ok:
        return jsonify({"ok": False, "msg": "未授权"}), 403
    rd = role_dir(name)
    if not rd.exists():
        return jsonify({"ok": False, "msg": f"角色 [{name}] 不存在"}), 404
    img_dir = rd / "images"
    images = []
    if img_dir.exists():
        for f in sorted(img_dir.iterdir()):
            images.append({
                "name": f.name,
                "size": f.stat().st_size,
                "modified": f.stat().st_mtime,
            })
    return jsonify({
        "ok": True,
        "name": name,
        "images": images,
    })


@app.route("/api/roles/<name>/images/<filename>", methods=["GET"])
def api_role_image_download(name, filename):
    fp = role_dir(name) / "images" / filename
    if not fp.exists():
        return jsonify({"ok": False, "msg": "文件不存在"}), 404
    return send_file(str(fp))


@app.route("/api/roles/<name>/images", methods=["POST"])
def api_role_image_upload(name):
    ok, dev_id = check_device_auth()
    if not ok:
        return jsonify({"ok": False, "msg": dev_id}), 403
    if "file" not in request.files:
        return jsonify({"ok": False, "msg": "未找到文件"}), 400
    file = request.files["file"]
    max_size = config.get("max_image_size_mb", 10) * 1024 * 1024
    fdata = file.read()
    if len(fdata) > max_size:
        return jsonify({"ok": False, "msg": f"图片过大"}), 413
    dest_dir = role_dir(name) / "images"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / file.filename
    dest.write_bytes(fdata)
    meta = get_role_meta(name)
    meta["image_count"] = meta.get("image_count", 0) + 1
    save_role_meta(name, meta)
    add_changelog("image_upload", name, dev_id, file.filename)
    return jsonify({"ok": True, "msg": f"图片 [{file.filename}] 已上传"})


# ============================================================
#  增量更新 API (客户端检查哪些文件需要更新)
# ============================================================

@app.route("/api/roles/<name>/updates", methods=["GET"])
def api_role_updates(name):
    ok, _ = check_device_auth()
    if not ok:
        return jsonify({"ok": False, "msg": "未授权"}), 403
    rd = role_dir(name)
    if not rd.exists():
        return jsonify({"ok": False, "msg": f"角色 [{name}] 不存在"}), 404
    all_files = {}
    for root, dirs, files in os.walk(str(rd)):
        for f in files:
            if f.endswith(".json") and ("user_facts" in f or "raw_history" in f or "msg_count" in f):
                continue
            full = Path(root) / f
            rel = str(full.relative_to(rd)).replace("\\", "/")
            all_files[rel] = {
                "size": full.stat().st_size,
                "mtime": full.stat().st_mtime,
            }
    return jsonify({
        "ok": True,
        "name": name,
        "version": get_role_meta(name).get("version", "1.0.0"),
        "file_count": len(all_files),
        "files": all_files,
    })


# ============================================================
#  设备管理 API
# ============================================================

@app.route("/api/device/register", methods=["POST"])
def api_device_register():
    data = request.get_json() or {}
    device_id = data.get("device_id", "")
    device_name = data.get("name", "")
    if not device_id:
        return jsonify({"ok": False, "msg": "缺少 device_id"}), 400
    if device_id in registered_devices:
        registered_devices[device_id]["name"] = device_name or registered_devices[device_id].get("name", "")
        registered_devices[device_id]["last_seen"] = time.time()
        save_devices()
        return jsonify({"ok": True, "msg": "设备已更新", "approved": registered_devices[device_id].get("approved", True)})
    auth_mode = config.get("auth_mode", "auto_register")
    approved = auth_mode != "admin_approve"
    registered_devices[device_id] = {
        "registered_at": time.time(),
        "name": device_name or f"设备_{device_id[:8]}",
        "approved": approved,
        "last_seen": time.time(),
    }
    save_devices()
    return jsonify({"ok": True, "msg": "设备已注册", "approved": approved})


@app.route("/api/devices", methods=["GET"])
def api_devices():
    password = request.args.get("password", "")
    if password != config.get("admin_password", ""):
        return jsonify({"ok": False, "msg": "密码错误"}), 403
    return jsonify({
        "ok": True,
        "devices": registered_devices,
        "count": len(registered_devices),
    })


@app.route("/api/devices/<device_id>/approve", methods=["POST"])
def api_device_approve(device_id):
    password = (request.get_json() or {}).get("password", "")
    if password != config.get("admin_password", ""):
        return jsonify({"ok": False, "msg": "密码错误"}), 403
    if device_id not in registered_devices:
        return jsonify({"ok": False, "msg": "设备不存在"}), 404
    registered_devices[device_id]["approved"] = True
    save_devices()
    return jsonify({"ok": True, "msg": "设备已批准"})


@app.route("/api/changelog", methods=["GET"])
def api_changelog():
    limit = int(request.args.get("limit", "50"))
    return jsonify({"ok": True, "entries": changelog[:limit]})


# ============================================================
#  管理面板 (简易 WebUI)
# ============================================================

_MANAGE_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>角色分享服务器 - 管理</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,sans-serif;background:#0f0f1a;color:#e0e0e0;min-height:100vh}
.topbar{background:#1a1a2e;padding:16px 24px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #2a2a4a}
.topbar h1{font-size:18px;color:#e94560}
.main{max-width:900px;margin:24px auto;padding:0 24px}
.card{background:#1a1a2e;border-radius:12px;border:1px solid #2a2a4a;padding:20px;margin-bottom:20px}
.card h3{font-size:15px;color:#e94560;margin-bottom:12px}
table{width:100%;border-collapse:collapse}
th{text-align:left;padding:8px 10px;border-bottom:2px solid #2a2a4a;font-size:12px;color:#888}
td{padding:8px 10px;border-bottom:1px solid #2a2a4a;font-size:13px}
.badge{padding:2px 8px;border-radius:10px;font-size:10px;font-weight:600}
.ok{background:rgba(0,200,83,.15);color:#00c853}
.warn{background:rgba(255,171,64,.15);color:#ffab40}
.stat{display:inline-block;padding:4px 12px;background:#0f0f1a;border-radius:6px;font-size:12px;margin-right:8px}
.btn{padding:8px 16px;border-radius:6px;border:none;cursor:pointer;font-size:12px;margin:4px}
.btn-red{background:#e94560;color:#fff}
.btn-green{background:#00c853;color:#fff}
.btn-gray{background:#2a2a4a;color:#aaa}
input{padding:8px 12px;border-radius:6px;border:1px solid #444;background:#0f0f1a;color:#e0e0e0;font-size:13px;margin:4px}
textarea{width:100%;padding:10px;border-radius:6px;border:1px solid #444;background:#0f0f1a;color:#e0e0e0;font-family:monospace;font-size:12px;height:200px}
.msg{display:none;padding:10px 14px;border-radius:6px;margin-top:8px;font-size:13px}
.msg.show{display:block}
.msg.ok{background:rgba(0,200,83,.1);color:#00c853}
.msg.err{background:rgba(255,82,82,.1);color:#ff5252}
</style></head><body>
<div class="topbar"><h1>🎭 角色分享服务器</h1><div><span style="color:#888;font-size:12px">v1.0.0 | </span><input id="pwd" type="password" placeholder="管理密码" style="width:120px"><button class="btn btn-gray" onclick="login()">管理</button></div></div>
<div class="main">
<div class="card"><h3>📊 概览</h3>
<div id="overview">加载中...</div></div>
<div class="card"><h3>🎭 角色列表</h3>
<div id="roleList">加载中...</div></div>
<div class="card" id="deviceCard" style="display:none"><h3>📱 已注册设备</h3>
<div id="deviceList"></div></div>
<div class="card" id="changelogCard" style="display:none"><h3>📝 最近操作日志</h3>
<div id="changelogList"></div></div>
<div id="msg" class="msg"></div>
</div>
<script>
var pwd='';
function login(){pwd=document.getElementById('pwd').value;loadAll()}
function showMsg(t,c){var m=document.getElementById('msg');m.textContent=t;m.className='msg show '+c;setTimeout(function(){m.className='msg'},4000)}
async function loadAll(){
try{
var rr=await(await fetch('/api/roles')).json();
var roles=rr.roles||[];
document.getElementById('overview').innerHTML='<span class="stat">角色: '+roles.length+'</span><span class="stat">模式: '+rr.auth_mode+'</span>';
var rh='';roles.forEach(function(r){rh+='<tr><td>'+r.name+'</td><td>v'+r.version+'</td><td>'+r.author+'</td><td>'+(r.size/1024).toFixed(0)+'KB</td><td>'+new Date(r.updated_at*1000).toLocaleDateString()+'</td></tr>'});
document.getElementById('roleList').innerHTML=rh?'<table><tr><th>名称</th><th>版本</th><th>作者</th><th>大小</th><th>更新</th></tr>'+rh+'</table>':'暂无角色'
if(pwd){
document.getElementById('deviceCard').style.display='block';document.getElementById('changelogCard').style.display='block';
var dr=await(await fetch('/api/devices?password='+pwd)).json();
var dh='';Object.entries(dr.devices||{}).forEach(function(e){var d=e[1];dh+='<tr><td>'+d.name+'</td><td style="font-size:11px;color:#888">'+e[0]+'</td><td><span class="badge '+(d.approved?'ok':'warn')+'">'+(d.approved?'已批准':'待批准')+'</span></td><td>'+new Date(d.registered_at*1000).toLocaleDateString()+'</td></tr>'});
document.getElementById('deviceList').innerHTML=dh?'<table><tr><th>名称</th><th>设备ID</th><th>状态</th><th>注册时间</th></tr>'+dh+'</table>':'暂无设备'
var cr=await(await fetch('/api/changelog')).json();
var ch='';cr.entries.forEach(function(e){ch+='<tr><td>'+e.action+'</td><td>'+e.role+'</td><td style="font-size:11px;color:#888">'+e.device+'</td><td style="font-size:11px;color:#888">'+e.detail+'</td><td style="font-size:11px">'+new Date(e.time*1000).toLocaleString()+'</td></tr>'});
document.getElementById('changelogList').innerHTML=ch?'<table><tr><th>操作</th><th>角色</th><th>设备</th><th>详情</th><th>时间</th></tr>'+ch+'</table>':'暂无日志'
}
}catch(e){showMsg('加载失败: '+e.message,'err')}
}
loadAll();
</script></body></html>"""


@app.route("/", methods=["GET"])
def serve_manage():
    return render_template_string(_MANAGE_HTML)


if __name__ == "__main__":
    load_config()
    load_devices()
    load_changelog()
    roles_dir().mkdir(parents=True, exist_ok=True)
    print(f"\n🎭 角色分享服务器已启动")
    print(f"   地址: http://{config['host']}:{config['port']}")
    print(f"   认证: {config['auth_mode']}")
    print(f"   角色: {len(scan_roles())} 个")
    print(f"   设备: {len(registered_devices)} 台\n")
    app.run(host=config["host"], port=config["port"], debug=False)
