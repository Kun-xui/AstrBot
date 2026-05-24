#!/usr/bin/env python3
"""
角色分享服务器 — 单文件重构版
端口: 8766 | 数据库: data.db | 配置: config.yaml
"""
import os, sys, json, time, uuid, hashlib, mimetypes, zipfile, io
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from functools import wraps

import pypinyin

import yaml
import bcrypt
from flask import (Flask, request, jsonify, session,
                   send_from_directory, render_template, abort, url_for)

# ============================================================
# 配置加载
# ============================================================
BASE_DIR = Path(__file__).parent.resolve()
CONFIG_FILE = BASE_DIR / 'config.yaml'
CONFIG_EXAMPLE = BASE_DIR / 'config.example.yaml'

if not CONFIG_FILE.exists():
    if CONFIG_EXAMPLE.exists():
        print(f"[config] 复制 {CONFIG_EXAMPLE.name} -> {CONFIG_FILE.name}")
        import shutil
        shutil.copy2(str(CONFIG_EXAMPLE), str(CONFIG_FILE))
    else:
        print(f"[config] 错误: 找不到 {CONFIG_FILE} 或 {CONFIG_EXAMPLE}")
        sys.exit(1)

with open(CONFIG_FILE) as f:
    cfg = yaml.safe_load(f)

HOST = cfg.get('host', '0.0.0.0')
PORT = cfg.get('port', 8766)
AUTH_MODE = cfg.get('auth_mode', 'auto_register')
CONTENT_MODE = cfg.get('content_mode', 'moderated')
ADMIN_PASSWORD = cfg.get('admin_password', 'changeme123')
MAX_AUDIO_SIZE = cfg.get('max_audio_size_mb', 5) * 1024 * 1024
MAX_IMAGE_SIZE = cfg.get('max_image_size_mb', 10) * 1024 * 1024

# 文件路径
AUDIO_DIR = BASE_DIR / 'audio'
IMAGES_DIR = BASE_DIR / 'images'
UPLOADS_DIR = BASE_DIR / 'uploads'
PENDING_DIR = BASE_DIR / 'pending'
DB_PATH = BASE_DIR / 'data.db'
TEMPLATE_DIR = BASE_DIR / 'templates'

for d in [AUDIO_DIR, IMAGES_DIR, UPLOADS_DIR, PENDING_DIR, TEMPLATE_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# 允许的文件类型
ALLOWED_IMAGES = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
ALLOWED_AUDIO = {'.mp3', '.wav', '.ogg', '.flac'}
ALLOWED_ZIP = {'.zip', '.rar', '.7z'}

app = Flask(__name__, template_folder=str(TEMPLATE_DIR))
app.secret_key = hashlib.sha256(os.urandom(32)).hexdigest()
app.config['SESSION_COOKIE_NAME'] = 'kunxun_admin'
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB

# ============================================================
# 数据库
# ============================================================
def get_db():
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def init_db():
    conn = get_db()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at REAL
        );
        CREATE TABLE IF NOT EXISTS roles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            author TEXT DEFAULT '',
            version TEXT DEFAULT '1.0.0',
            desc TEXT DEFAULT '',
            download_url TEXT DEFAULT '',
            approved INTEGER DEFAULT 0,
            rejected INTEGER DEFAULT 0,
            created_at REAL,
            updated_at REAL
        );
        CREATE TABLE IF NOT EXISTS audio_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role_name TEXT NOT NULL,
            category TEXT NOT NULL,
            filename TEXT NOT NULL,
            size INTEGER DEFAULT 0,
            md5 TEXT DEFAULT '',
            uploaded_by TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            created_at REAL,
            UNIQUE(role_name, category, filename)
        );
        CREATE TABLE IF NOT EXISTS image_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role_name TEXT NOT NULL,
            filename TEXT NOT NULL,
            size INTEGER DEFAULT 0,
            md5 TEXT DEFAULT '',
            uploaded_by TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            created_at REAL,
            UNIQUE(role_name, filename)
        );
        CREATE TABLE IF NOT EXISTS devices (
            device_id TEXT PRIMARY KEY,
            name TEXT DEFAULT '',
            approved INTEGER DEFAULT 1,
            registered_at REAL,
            last_seen REAL
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            role_name TEXT DEFAULT '',
            device_id TEXT DEFAULT '',
            detail TEXT DEFAULT '',
            created_at REAL
        );
    ''')
    # 迁移：加slug列（如果不存在）
    try:
        conn.execute("ALTER TABLE roles ADD COLUMN slug TEXT DEFAULT ''")
        for row in conn.execute("SELECT name FROM roles WHERE slug='' OR slug IS NULL"):
            slug = name_to_slug(row['name'])
            conn.execute("UPDATE roles SET slug=? WHERE name=?", (slug, row['name']))
            print(f"  [migrate] {row['name']} → {slug}")
    except sqlite3.OperationalError:
        pass
    # 迁移：文件状态列
    for tbl in ('audio_files', 'image_files'):
        try:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN status TEXT DEFAULT 'approved'")
            print(f"  [migrate] 添加 status 列到 {tbl}")
        except sqlite3.OperationalError:
            pass
    # 迁移：角色预览图 + tags
    for col in ('preview_image_id', 'tags', 'display_desc', 'preview_filename'):
        try:
            conn.execute(f"ALTER TABLE roles ADD COLUMN {col} TEXT DEFAULT ''")
            print(f"  [migrate] 添加 {col} 列到 roles")
        except sqlite3.OperationalError:
            pass
    # 创建默认管理员
    existing = conn.execute("SELECT id FROM admins WHERE username='admin'").fetchone()
    if not existing:
        pw_hash = bcrypt.hashpw(ADMIN_PASSWORD.encode(), bcrypt.gensalt()).decode()
        conn.execute(
            "INSERT INTO admins (username, password_hash, created_at) VALUES (?, ?, ?)",
            ('admin', pw_hash, time.time())
        )
        print(f"[init] 已创建默认管理员: admin")
    conn.commit()
    conn.close()
    print("[init] 数据库初始化完成")

# ============================================================
# 工具函数
# ============================================================
def now_ts():
    return time.time()

def name_to_slug(name):
    """中文名 → 拼音slug（纯小写字母数字下划线）"""
    s = pypinyin.slug(name, separator='_', style=pypinyin.Style.NORMAL,
                      errors=lambda x: re.sub(r'[^a-zA-Z0-9]', '_', x).lower())
    # 确保纯字母数字下划线
    s = re.sub(r'[^a-z0-9_]', '_', s)
    # 去重下划线并去掉首尾
    s = re.sub(r'_+', '_', s).strip('_')
    return s or 'role_' + uuid.uuid4().hex[:8]

def safe_filename(ext):
    return f"{uuid.uuid4().hex}{ext}"

def allowed_file(filename, allowed_set):
    ext = os.path.splitext(filename)[1].lower()
    if filename.lower().endswith('.tar.gz'):
        ext = '.tar.gz'
    return ext in allowed_set

def file_md5(filepath):
    h = hashlib.md5()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()

def log_audit(action, role_name='', device_id='', detail=''):
    conn = get_db()
    conn.execute(
        "INSERT INTO audit_log (action, role_name, device_id, detail, created_at) VALUES (?, ?, ?, ?, ?)",
        (action, role_name, device_id, detail, now_ts())
    )
    conn.commit()
    conn.close()

def require_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'admin_id' not in session:
            return jsonify({'error': '未登录'}), 401
        return f(*args, **kwargs)
    return decorated

def require_device(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if AUTH_MODE == 'open':
            return f(*args, **kwargs)
        device_id = request.headers.get('X-Device-ID', '')
        # 兼容模式：没有设备ID时自动分配
        if not device_id:
            if request.headers.get('User-Agent', '').startswith('curl/'):
                device_id = f"curl_{request.remote_addr}"
            elif request.headers.get('User-Agent', ''):
                device_id = f"web_{hashlib.md5(request.headers.get('User-Agent','').encode()).hexdigest()[:12]}"
            else:
                device_id = f"anon_{hashlib.md5(request.remote_addr.encode()).hexdigest()[:12]}"
        conn = get_db()
        dev = conn.execute("SELECT * FROM devices WHERE device_id=?", (device_id,)).fetchone()
        if not dev:
            if AUTH_MODE == 'auto_register':
                conn.execute(
                    "INSERT INTO devices (device_id, approved, registered_at, last_seen) VALUES (?, 1, ?, ?)",
                    (device_id, now_ts(), now_ts())
                )
                conn.commit()
                conn.close()
                log_audit('device_register', device_id=device_id)
                return f(*args, **kwargs)
            conn.close()
            return jsonify({'error': '设备未注册'}), 401
        if not dev['approved']:
            conn.close()
            return jsonify({'error': '设备已被禁用'}), 403
        conn.execute("UPDATE devices SET last_seen=? WHERE device_id=?", (now_ts(), device_id))
        conn.commit()
        conn.close()
        return f(*args, **kwargs)
    return decorated

def build_role_dict(row):
    d = dict(row)
    return {
        'id': d['id'],
        'name': d['name'],
        'slug': d.get('slug', ''),
        'author': d['author'],
        'version': d['version'],
        'desc': d['desc'],
        'display_desc': d.get('display_desc', '') or d['desc'],
        'tags': d.get('tags', ''),
        'preview_image_id': d.get('preview_image_id', 0),
        'preview_filename': d.get('preview_filename', ''),
        'download_url': d['download_url'],
        'approved': d['approved'],
        'rejected': d['rejected'],
        'created_at': d['created_at'],
        'updated_at': d['updated_at'],
    }

def resolve_role(conn, name_or_slug):
    """按名称或slug查找角色，返回角色row（含slug）"""
    role = conn.execute(
        "SELECT * FROM roles WHERE name=? OR slug=?", (name_or_slug, name_or_slug)
    ).fetchone()
    return role

def resolve_name(conn, name_or_slug):
    """按名称或slug查找，返回实际角色名"""
    r = resolve_role(conn, name_or_slug)
    return r['name'] if r else None

# ============================================================
# 公开 API
# ============================================================
@app.route('/api/ping')
@require_device
def api_ping():
    conn = get_db()
    role_count = conn.execute("SELECT COUNT(*) FROM roles WHERE approved=1").fetchone()[0]
    device_count = conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
    pending_audio = conn.execute("SELECT COUNT(*) FROM audio_files WHERE status='pending'").fetchone()[0]
    pending_images = conn.execute("SELECT COUNT(*) FROM image_files WHERE status='pending'").fetchone()[0]
    conn.close()
    return jsonify({
        'status': 'ok',
        'version': '2.0.0',
        'roles': role_count,
        'devices': device_count,
        'pending_files': pending_audio + pending_images,
        'time': now_ts(),
    })

@app.route('/api/server/info')
def api_server_info():
    """服务器信息（带宽等，公开）"""
    return jsonify({
        'bandwidth_mbps': 10,
        'bandwidth_mbs': 1.2,
        'max_upload_single_file_mb': 50,
        'max_upload_total_mb': 200,
        'message': '当前服务器带宽约 1.2MB/s，上传大文件建议后台/夜间操作',
    })

@app.route('/api/roles/create', methods=['POST'])
def api_create_role():
    """公开创建角色（轻量）"""
    data = request.get_json() or {}
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'error': '角色名不能为空'}), 400
    if len(name) > 64:
        return jsonify({'error': '角色名过长，最多64个字符'}), 400

    conn = get_db()
    existing = conn.execute("SELECT id FROM roles WHERE name=?", (name,)).fetchone()
    if existing:
        conn.close()
        return jsonify({'error': '角色名已存在'}), 409

    slug = name_to_slug(name)
    desc = data.get('desc', '').strip()[:256]
    author = data.get('author', '').strip()[:64]
    version = '1.0.0'

    conn.execute(
        "INSERT INTO roles (name, slug, author, version, desc, created_at, updated_at, approved) VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
        (name, slug, author, version, desc, now_ts(), now_ts())
    )
    conn.commit()
    conn.close()

    device_id = request.headers.get('X-Device-ID', '') or 'web_public'
    log_audit('role_create_public', role_name=name, device_id=device_id, detail=f'用户创建角色')

    return jsonify({
        'name': name,
        'slug': slug,
        'approved': False,
        'message': '角色创建成功！请等待管理员审核通过后即可上传文件。',
    }), 201

@app.route('/api/roles', methods=['GET'])
@require_device
def api_list_roles():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM roles WHERE approved=1 ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    return jsonify([build_role_dict(r) for r in rows])

@app.route('/api/roles/<name>', methods=['GET'])
@require_device
def api_get_role(name):
    conn = get_db()
    role = resolve_role(conn, name)
    if not role:
        conn.close()
        return jsonify({'error': '角色不存在'}), 404
    real_name = role['name']
    audio = conn.execute(
        "SELECT id, category, filename, size FROM audio_files WHERE role_name=? ORDER BY category, filename",
        (real_name,)
    ).fetchall()
    images = conn.execute(
        "SELECT id, filename, size FROM image_files WHERE role_name=? ORDER BY filename",
        (real_name,)
    ).fetchall()
    conn.close()
    result = build_role_dict(role)
    result['audio'] = [dict(a) for a in audio]
    result['images'] = [dict(i) for i in images]
    return jsonify(result)

@app.route('/api/roles/<name>/status', methods=['GET'])
@require_device
def api_role_status(name):
    """角色状态摘要（轻量，含计数）"""
    conn = get_db()
    role = resolve_role(conn, name)
    if not role:
        conn.close()
        return jsonify({'error': '角色不存在'}), 404
    real_name = role['name']
    audio_count = conn.execute(
        "SELECT COUNT(*) FROM audio_files WHERE role_name=?", (real_name,)
    ).fetchone()[0]
    image_count = conn.execute(
        "SELECT COUNT(*) FROM image_files WHERE role_name=?", (real_name,)
    ).fetchone()[0]
    conn.close()
    result = build_role_dict(role)
    result['audio_count'] = audio_count
    result['image_count'] = image_count
    return jsonify(result)

@app.route('/api/roles/<name>/audio', methods=['GET'])
@require_device
def api_list_audio(name):
    conn = get_db()
    real_name = resolve_name(conn, name)
    if not real_name:
        conn.close()
        return jsonify({'error': '角色不存在'}), 404
    files = conn.execute(
        "SELECT id, category, filename, size, md5 FROM audio_files WHERE role_name=? ORDER BY category, filename",
        (real_name,)
    ).fetchall()
    conn.close()
    return jsonify([dict(f) for f in files])

@app.route('/api/roles/<name>/audio/<cat>/<file>', methods=['GET'])
@require_device
def api_get_audio(name, cat, file):
    safe_name = os.path.basename(file)
    conn = get_db()
    real_name = resolve_name(conn, name)
    if not real_name:
        conn.close()
        abort(404)
    # 检查文件是否已审核
    f = conn.execute(
        "SELECT status FROM audio_files WHERE role_name=? AND category=? AND filename=?",
        (real_name, cat, safe_name)
    ).fetchone()
    conn.close()
    if not f or f['status'] != 'approved':
        abort(404)
    audio_path = AUDIO_DIR / real_name / cat
    if not audio_path.exists():
        abort(404)
    return send_from_directory(str(audio_path), safe_name)

@app.route('/api/roles/<name>/audio', methods=['POST'])
@require_device
def api_upload_audio(name):
    """公开上传音频（进审核队列）"""
    if 'file' not in request.files:
        return jsonify({'error': '请选择文件'}), 400
    file = request.files['file']
    # 先验证角色存在
    conn = get_db()
    real_name = resolve_name(conn, name)
    conn.close()
    if not real_name:
        return jsonify({'error': '角色不存在'}), 404
    category = request.form.get('category', '') or 'expressions'
    if category not in ('expressions', 'music'):
        return jsonify({'error': '分类必须是 expressions 或 music'}), 400
    if not file.filename or not allowed_file(file.filename, ALLOWED_AUDIO):
        return jsonify({'error': '不支持的音频格式'}), 400
    if request.content_length and request.content_length > MAX_AUDIO_SIZE:
        return jsonify({'error': f'音频太大，最大{MAX_AUDIO_SIZE//1024//1024}MB'}), 413

    device_id = request.headers.get('X-Device-ID', '')
    ext = os.path.splitext(file.filename)[1].lower()
    fname = safe_filename(ext)
    
    save_dir = PENDING_DIR / real_name / 'audio' / category
    save_dir.mkdir(parents=True, exist_ok=True)
    save_path = save_dir / fname
    file.save(str(save_path))
    
    size = save_path.stat().st_size
    md5 = file_md5(save_path)
    
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO audio_files (role_name, category, filename, size, md5, uploaded_by, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
            (real_name, category, fname, size, md5, device_id, now_ts())
        )
        conn.commit()
        log_audit('audio_upload', role_name=real_name, device_id=device_id, detail=f'{category}/{fname}')
        conn.close()
    except Exception:
        conn.close()
        save_path.unlink(missing_ok=True)
        return jsonify({'error': '文件名冲突'}), 409
    
    return jsonify({'filename': fname, 'size': size, 'md5': md5, 'pending': True}), 201

@app.route('/api/roles/<name>/images', methods=['GET'])
@require_device
def api_list_images(name):
    conn = get_db()
    real_name = resolve_name(conn, name)
    if not real_name:
        conn.close()
        return jsonify({'error': '角色不存在'}), 404
    files = conn.execute(
        "SELECT id, filename, size, md5 FROM image_files WHERE role_name=? ORDER BY filename",
        (real_name,)
    ).fetchall()
    conn.close()
    return jsonify([dict(f) for f in files])

@app.route('/api/roles/<name>/images/<file>', methods=['GET'])
@require_device
def api_get_image(name, file):
    safe_name = os.path.basename(file)
    conn = get_db()
    real_name = resolve_name(conn, name)
    if not real_name:
        conn.close()
        abort(404)
    
    # 预览图例外：preview_ 开头的文件跳过数据库检查（已审核通过直接显示）
    if safe_name.startswith('preview_'):
        conn.close()
        img_path = IMAGES_DIR / real_name
        if not img_path.exists() or not (img_path / safe_name).exists():
            abort(404)
        return send_from_directory(str(img_path), safe_name)
    
    # 检查文件是否已审核
    f = conn.execute(
        "SELECT status FROM image_files WHERE role_name=? AND filename=?",
        (real_name, safe_name)
    ).fetchone()
    conn.close()
    if not f or f['status'] != 'approved':
        abort(404)
    img_path = IMAGES_DIR / real_name
    if not img_path.exists():
        abort(404)
    return send_from_directory(str(img_path), safe_name)

@app.route('/api/roles/<name>/images', methods=['POST'])
@require_device
def api_upload_image(name):
    """公开上传图片（进审核队列）"""
    if 'file' not in request.files:
        return jsonify({'error': '请选择文件'}), 400
    file = request.files['file']
    # 先验证角色存在
    conn = get_db()
    real_name = resolve_name(conn, name)
    conn.close()
    if not real_name:
        return jsonify({'error': '角色不存在'}), 404
    if not file.filename or not allowed_file(file.filename, ALLOWED_IMAGES):
        return jsonify({'error': '不支持的图片格式'}), 400
    if request.content_length and request.content_length > MAX_IMAGE_SIZE:
        return jsonify({'error': f'图片太大，最大{MAX_IMAGE_SIZE//1024//1024}MB'}), 413

    device_id = request.headers.get('X-Device-ID', '')
    ext = os.path.splitext(file.filename)[1].lower()
    fname = safe_filename(ext)
    
    save_dir = PENDING_DIR / real_name / 'images'
    save_dir.mkdir(parents=True, exist_ok=True)
    save_path = save_dir / fname
    file.save(str(save_path))
    
    size = save_path.stat().st_size
    md5 = file_md5(save_path)
    
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO image_files (role_name, filename, size, md5, uploaded_by, status, created_at) VALUES (?, ?, ?, ?, ?, 'pending', ?)",
            (real_name, fname, size, md5, device_id, now_ts())
        )
        conn.commit()
        log_audit('image_upload', role_name=real_name, device_id=device_id, detail=fname)
        conn.close()
    except Exception:
        conn.close()
        save_path.unlink(missing_ok=True)
        return jsonify({'error': '文件名冲突'}), 409
    
    return jsonify({'filename': fname, 'size': size, 'md5': md5, 'pending': True}), 201

@app.route('/api/roles/<name>/push', methods=['POST'])
@require_device
def api_push_zip(name):
    """批量推送ZIP（音频+图片），进审核队列"""
    if 'file' not in request.files:
        return jsonify({'error': '请选择ZIP文件'}), 400
    file = request.files['file']
    # 先验证角色存在
    conn = get_db()
    real_name = resolve_name(conn, name)
    conn.close()
    if not real_name:
        return jsonify({'error': '角色不存在'}), 404
    if not file.filename or not allowed_file(file.filename, ALLOWED_ZIP):
        return jsonify({'error': '请上传ZIP/RAR/7z文件'}), 400

    device_id = request.headers.get('X-Device-ID', '')
    
    # 保存ZIP到pending
    ext = os.path.splitext(file.filename)[1].lower()
    fname = f"push_{uuid.uuid4().hex}{ext}"
    save_path = PENDING_DIR / fname
    file.save(str(save_path))
    
    # 解析ZIP内容
    extracted = {'audio': [], 'images': []}
    try:
        with zipfile.ZipFile(save_path, 'r') as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                base = os.path.basename(info.filename)
                if not base:
                    continue
                ext2 = os.path.splitext(base)[1].lower()
                if ext2 in ALLOWED_AUDIO:
                    extracted['audio'].append({
                        'filename': base,
                        'size': info.file_size,
                        'category': 'expressions' if 'music' not in info.filename.lower() else 'music',
                    })
                elif ext2 in ALLOWED_IMAGES:
                    extracted['images'].append({
                        'filename': base,
                        'size': info.file_size,
                    })
    except Exception as e:
        save_path.unlink(missing_ok=True)
        return jsonify({'error': f'ZIP解析失败: {str(e)}'}), 400
    
    log_audit('zip_push', role_name=real_name, device_id=device_id, detail=json.dumps(extracted))
    
    return jsonify({
        'filename': fname,
        'size': save_path.stat().st_size,
        'extracted': extracted,
        'pending': True,
    }), 201

@app.route('/api/roles/share', methods=['POST'])
@require_device
def api_share_role():
    """上传角色ZIP（完整角色包）"""
    if 'file' not in request.files:
        return jsonify({'error': '请选择ZIP文件'}), 400
    file = request.files['file']
    role_name = request.form.get('name', '')
    if not role_name:
        return jsonify({'error': '请提供角色名'}), 400
    if not file.filename or not allowed_file(file.filename, ALLOWED_ZIP):
        return jsonify({'error': '请上传ZIP文件'}), 400

    device_id = request.headers.get('X-Device-ID', '')
    ext = os.path.splitext(file.filename)[1].lower()
    fname = f"share_{role_name}_{uuid.uuid4().hex[:8]}{ext}"
    save_path = PENDING_DIR / fname
    file.save(str(save_path))
    
    log_audit('role_share', role_name=role_name, device_id=device_id, detail=fname)
    
    conn = get_db()
    existing = conn.execute("SELECT id FROM roles WHERE name=?", (role_name,)).fetchone()
    if not existing:
        slug = name_to_slug(role_name)
        conn.execute(
            "INSERT INTO roles (name, slug, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (role_name, slug, now_ts(), now_ts())
        )
        conn.commit()
    conn.close()
    
    return jsonify({
        'filename': fname,
        'size': save_path.stat().st_size,
        'pending': True,
    }), 201

# ============================================================
# 设备API
# ============================================================
@app.route('/api/device/register', methods=['POST'])
def api_register_device():
    data = request.get_json() or {}
    device_id = data.get('device_id', '') or request.headers.get('X-Device-ID', '')
    if not device_id:
        return jsonify({'error': '缺少 device_id'}), 400
    name = data.get('name', '')
    
    conn = get_db()
    existing = conn.execute("SELECT * FROM devices WHERE device_id=?", (device_id,)).fetchone()
    if existing:
        conn.execute("UPDATE devices SET name=?, last_seen=? WHERE device_id=?",
                     (name or existing['name'], now_ts(), device_id))
        conn.commit()
        conn.close()
        return jsonify({'device_id': device_id, 'registered': True, 'approved': bool(existing['approved'])})
    
    conn.execute(
        "INSERT INTO devices (device_id, name, approved, registered_at, last_seen) VALUES (?, ?, 1, ?, ?)",
        (device_id, name, now_ts(), now_ts())
    )
    conn.commit()
    conn.close()
    log_audit('device_register', device_id=device_id, detail=name)
    return jsonify({'device_id': device_id, 'registered': True, 'approved': True}), 201

@app.route('/api/plugins.json')
def api_plugins_json():
    """动态生成插件目录 JSON（供主站使用）"""
    conn = get_db()
    roles = conn.execute("SELECT * FROM roles WHERE approved=1 ORDER BY updated_at DESC").fetchall()
    conn.close()
    result = []
    for r in roles:
        d = dict(r)
        slug = d.get('slug', '') or name_to_slug(d['name'])
        preview = ''
        
        # 1. 优先用管理员单独上传的预览图
        preview_fn = d.get('preview_filename', '')
        if preview_fn:
            preview = f"/api/roles/{slug}/images/{preview_fn}"
        
        # 2. 用 preview_image_id 指定某张已审核图片
        if not preview:
            preview_img_id = d.get('preview_image_id', 0)
            if preview_img_id:
                img = conn.execute("SELECT filename FROM image_files WHERE id=? AND role_name=? AND status='approved'",
                                   (preview_img_id, d['name'])).fetchone()
                if img:
                    preview = f"/api/roles/{slug}/images/{img['filename']}"
        
        # 3. 自动取第一张已审核图片
        if not preview:
            conn2 = get_db()
            img = conn2.execute("SELECT filename FROM image_files WHERE role_name=? AND status='approved' LIMIT 1",
                                (d['name'],)).fetchone()
            conn2.close()
            if img:
                preview = f"/api/roles/{slug}/images/{img['filename']}"
        
        tags = d.get('tags', '') or 'AstrBot'
        tag_list = [t.strip() for t in tags.split(',') if t.strip()]
        
        result.append({
            'id': slug or d['name'],
            'name': d['name'],
            'desc': d.get('display_desc', '') or d['desc'],
            'author': d.get('author', ''),
            'preview': preview,
            'tags': tag_list,
            'version': d['version'],
            'updated': datetime.fromtimestamp(d['updated_at']).strftime('%Y-%m-%d') if d.get('updated_at') else '',
        })
    return jsonify(result)

# ============================================================
# 管理 API
# ============================================================
@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    data = request.get_json() or {}
    username = data.get('username', '')
    password = data.get('password', '')
    
    conn = get_db()
    admin = conn.execute("SELECT * FROM admins WHERE username=?", (username,)).fetchone()
    conn.close()
    
    if not admin or not bcrypt.checkpw(password.encode(), admin['password_hash'].encode()):
        return jsonify({'error': '用户名或密码错误'}), 401
    
    session['admin_id'] = admin['id']
    session['admin_username'] = admin['username']
    session.permanent = True
    app.permanent_session_lifetime = 86400  # 24h
    
    log_audit('admin_login', detail=username)
    return jsonify({'username': username, 'message': '登录成功'})

@app.route('/api/admin/logout', methods=['POST'])
@require_admin
def admin_logout():
    log_audit('admin_logout', detail=session.get('admin_username', ''))
    session.clear()
    return jsonify({'message': '已退出'})

@app.route('/api/admin/me', methods=['GET'])
@require_admin
def admin_me():
    return jsonify({
        'id': session.get('admin_id'),
        'username': session.get('admin_username'),
    })

@app.route('/api/admin/roles', methods=['GET'])
@require_admin
def admin_list_roles():
    conn = get_db()
    rows = conn.execute("SELECT * FROM roles ORDER BY updated_at DESC").fetchall()
    conn.close()
    return jsonify([build_role_dict(r) for r in rows])

@app.route('/api/admin/roles', methods=['POST'])
@require_admin
def admin_create_role():
    data = request.get_json() or {}
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'error': '角色名不能为空'}), 400
    
    conn = get_db()
    existing = conn.execute("SELECT id FROM roles WHERE name=?", (name,)).fetchone()
    if existing:
        conn.close()
        return jsonify({'error': '角色名已存在'}), 409
    
    slug = name_to_slug(name)
    conn.execute(
        "INSERT INTO roles (name, slug, author, version, desc, download_url, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (name, slug, data.get('author', ''), data.get('version', '1.0.0'),
         data.get('desc', ''), data.get('download_url', ''), now_ts(), now_ts())
    )
    conn.commit()
    conn.close()
    log_audit('role_create', role_name=name)
    return jsonify({'name': name, 'slug': slug, 'message': '已创建'}), 201

@app.route('/api/admin/roles/<name>', methods=['PUT'])
@require_admin
def admin_update_role(name):
    data = request.get_json() or {}
    conn = get_db()
    role = resolve_role(conn, name)
    if not role:
        conn.close()
        return jsonify({'error': '角色不存在'}), 404
    
    real_name = role['name']
    fields = {}
    for key in ('author', 'version', 'desc', 'download_url', 'tags', 'display_desc', 'preview_image_id', 'preview_filename'):
        if key in data:
            fields[key] = data[key]
    if data.get('name') and data['name'] != real_name:
        fields['name'] = data['name']
        fields['slug'] = name_to_slug(data['name'])
    
    if fields:
        fields['updated_at'] = now_ts()
        set_clause = ', '.join(f"{k}=?" for k in fields)
        vals = list(fields.values())
        vals.append(real_name)
        conn.execute(f"UPDATE roles SET {set_clause} WHERE name=?", vals)
        conn.commit()
        log_audit('role_update', role_name=real_name, detail=json.dumps(fields))
    
    role = conn.execute("SELECT * FROM roles WHERE name=?", (fields.get('name', real_name),)).fetchone()
    conn.close()
    return jsonify(build_role_dict(role))

@app.route('/api/admin/roles/<name>/preview', methods=['POST'])
@require_admin
def admin_upload_preview(name):
    """上传角色预览图"""
    if 'file' not in request.files:
        return jsonify({'error': '请选择图片文件'}), 400
    file = request.files['file']
    
    conn = get_db()
    role = resolve_role(conn, name)
    if not role:
        conn.close()
        return jsonify({'error': '角色不存在'}), 404
    real_name = role['name']
    conn.close()
    
    if not file.filename or not allowed_file(file.filename, ALLOWED_IMAGES):
        return jsonify({'error': '不支持的图片格式（支持 jpg/png/gif/webp）'}), 400
    
    ext = os.path.splitext(file.filename)[1].lower()
    fname = f"preview_{uuid.uuid4().hex}{ext}"
    
    # 保存到图片目录
    save_dir = IMAGES_DIR / real_name
    save_dir.mkdir(parents=True, exist_ok=True)
    
    # 删除旧预览图
    cursor = get_db()
    old = cursor.execute("SELECT preview_filename FROM roles WHERE name=?", (real_name,)).fetchone()
    if old and old['preview_filename']:
        old_path = save_dir / old['preview_filename']
        old_path.unlink(missing_ok=True)
    cursor.close()
    
    save_path = save_dir / fname
    file.save(str(save_path))
    
    # 更新数据库
    conn2 = get_db()
    conn2.execute("UPDATE roles SET preview_filename=?, updated_at=? WHERE name=?",
                  (fname, now_ts(), real_name))
    conn2.commit()
    conn2.close()
    
    log_audit('preview_upload', role_name=real_name, detail=fname)
    return jsonify({'filename': fname, 'url': f'/api/roles/{role["slug"] or real_name}/images/{fname}', 'message': '预览图已上传'})

@app.route('/api/admin/roles/<name>/preview', methods=['DELETE'])
@require_admin
def admin_remove_preview(name):
    """删除角色预览图"""
    conn = get_db()
    role = resolve_role(conn, name)
    if not role:
        conn.close()
        return jsonify({'error': '角色不存在'}), 404
    real_name = role['name']
    preview = role['preview_filename']
    
    if preview:
        (IMAGES_DIR / real_name / preview).unlink(missing_ok=True)
        conn.execute("UPDATE roles SET preview_filename='', updated_at=? WHERE name=?",
                     (now_ts(), real_name))
        conn.commit()
        log_audit('preview_remove', role_name=real_name)
    
    conn.close()
    return jsonify({'message': '预览图已清除'})

@app.route('/api/admin/roles/<name>', methods=['DELETE'])
@require_admin
def admin_delete_role(name):
    conn = get_db()
    role = resolve_role(conn, name)
    if not role:
        conn.close()
        return jsonify({'error': '角色不存在'}), 404
    real_name = role['name']
    # 删除文件
    for f in conn.execute("SELECT filename FROM audio_files WHERE role_name=?", (real_name,)).fetchall():
        for cat in ('expressions', 'music'):
            p = AUDIO_DIR / real_name / cat / f['filename']
            p.unlink(missing_ok=True)
    for f in conn.execute("SELECT filename FROM image_files WHERE role_name=?", (real_name,)).fetchall():
        p = IMAGES_DIR / real_name / f['filename']
        p.unlink(missing_ok=True)
    # 清理pending
    import shutil
    for d in [PENDING_DIR / real_name, AUDIO_DIR / real_name, IMAGES_DIR / real_name]:
        if d.exists():
            shutil.rmtree(str(d), ignore_errors=True)
    
    conn.execute("DELETE FROM audio_files WHERE role_name=?", (real_name,))
    conn.execute("DELETE FROM image_files WHERE role_name=?", (real_name,))
    conn.execute("DELETE FROM roles WHERE name=?", (real_name,))
    conn.commit()
    conn.close()
    log_audit('role_delete', role_name=real_name)
    return jsonify({'message': '已删除'})

@app.route('/api/admin/pending', methods=['GET'])
@require_admin
def admin_pending():
    """待审核文件：status=pending 的音频和图片"""
    conn = get_db()
    audio = conn.execute(
        "SELECT af.*, r.slug FROM audio_files af JOIN roles r ON r.name=af.role_name WHERE af.status='pending' ORDER BY af.created_at DESC"
    ).fetchall()
    images = conn.execute(
        "SELECT imf.*, r.slug FROM image_files imf JOIN roles r ON r.name=imf.role_name WHERE imf.status='pending' ORDER BY imf.created_at DESC"
    ).fetchall()
    conn.close()
    return jsonify({
        'audio': [dict(a) for a in audio],
        'images': [dict(i) for i in images],
    })

@app.route('/api/admin/approve', methods=['POST'])
@require_admin
def admin_approve():
    """批准角色"""
    data = request.get_json() or {}
    name = data.get('name', '')
    if not name:
        return jsonify({'error': '请指定角色名'}), 400
    
    conn = get_db()
    role = conn.execute("SELECT * FROM roles WHERE name=?", (name,)).fetchone()
    if not role:
        conn.close()
        return jsonify({'error': '角色不存在'}), 404
    
    conn.execute("UPDATE roles SET approved=1, rejected=0, updated_at=? WHERE name=?", (now_ts(), name))
    
    # 移动pending文件到正式目录
    import shutil
    pending_role = PENDING_DIR / name
    if pending_role.exists():
        # 移动音频
        for cat in ('expressions', 'music'):
            src = pending_role / 'audio' / cat
            if src.exists():
                dst = AUDIO_DIR / name / cat
                dst.mkdir(parents=True, exist_ok=True)
                for f in src.iterdir():
                    if f.is_file():
                        shutil.move(str(f), str(dst / f.name))
        # 移动图片
        src = pending_role / 'images'
        if src.exists():
            dst = IMAGES_DIR / name
            dst.mkdir(parents=True, exist_ok=True)
            for f in src.iterdir():
                if f.is_file():
                    shutil.move(str(f), str(dst / f.name))
        shutil.rmtree(str(pending_role), ignore_errors=True)
    
    conn.commit()
    conn.close()
    log_audit('role_approve', role_name=name)
    return jsonify({'name': name, 'approved': True})

@app.route('/api/admin/approve-file', methods=['POST'])
@require_admin
def admin_approve_file():
    """批准单个文件"""
    data = request.get_json() or {}
    file_type = data.get('type', '')
    file_id = data.get('id', 0)
    if not file_type or not file_id:
        return jsonify({'error': '请指定文件类型和ID'}), 400
    
    conn = get_db()
    if file_type == 'audio':
        f = conn.execute("SELECT * FROM audio_files WHERE id=?", (file_id,)).fetchone()
    else:
        f = conn.execute("SELECT * FROM image_files WHERE id=?", (file_id,)).fetchone()
    
    if not f:
        conn.close()
        return jsonify({'error': '文件不存在'}), 404
    
    role_name = f['role_name']
    filename = f['filename']
    conn.close()  # 先关查询连接
    
    # 文件操作（不涉及数据库）
    import shutil
    if file_type == 'audio':
        src = PENDING_DIR / role_name / 'audio' / f['category'] / filename
        dst_dir = AUDIO_DIR / role_name / f['category']
        dst_dir.mkdir(parents=True, exist_ok=True)
        if src.exists():
            shutil.move(str(src), str(dst_dir / filename))
        detail = f'音频/{f["category"]}/{filename}'
    else:
        src = PENDING_DIR / role_name / 'images' / filename
        dst_dir = IMAGES_DIR / role_name
        dst_dir.mkdir(parents=True, exist_ok=True)
        if src.exists():
            shutil.move(str(src), str(dst_dir / filename))
        detail = f'图片/{filename}'
    
    # 数据库操作
    conn2 = get_db()
    if file_type == 'audio':
        conn2.execute("UPDATE audio_files SET status='approved' WHERE id=?", (file_id,))
    else:
        conn2.execute("UPDATE image_files SET status='approved' WHERE id=?", (file_id,))
    conn2.commit()
    conn2.close()
    
    log_audit('file_approve', role_name=role_name, detail=detail)
    return jsonify({'id': file_id, 'type': file_type, 'status': 'approved'})

@app.route('/api/admin/reject-file', methods=['POST'])
@require_admin
def admin_reject_file():
    """拒绝单个文件"""
    data = request.get_json() or {}
    file_type = data.get('type', '')
    file_id = data.get('id', 0)
    if not file_type or not file_id:
        return jsonify({'error': '请指定文件类型和ID'}), 400
    
    conn = get_db()
    if file_type == 'audio':
        f = conn.execute("SELECT * FROM audio_files WHERE id=?", (file_id,)).fetchone()
    else:
        f = conn.execute("SELECT * FROM image_files WHERE id=?", (file_id,)).fetchone()
    
    if not f:
        conn.close()
        return jsonify({'error': '文件不存在'}), 404
    
    role_name = f['role_name']
    filename = f['filename']
    conn.close()
    
    # 删除pending文件
    if file_type == 'audio':
        src = PENDING_DIR / role_name / 'audio' / f['category'] / filename
        src.unlink(missing_ok=True)
        detail = f'音频/{f["category"]}/{filename}'
    else:
        src = PENDING_DIR / role_name / 'images' / filename
        src.unlink(missing_ok=True)
        detail = f'图片/{filename}'
    
    # 数据库操作
    conn2 = get_db()
    if file_type == 'audio':
        conn2.execute("UPDATE audio_files SET status='rejected' WHERE id=?", (file_id,))
    else:
        conn2.execute("UPDATE image_files SET status='rejected' WHERE id=?", (file_id,))
    conn2.commit()
    conn2.close()
    
    log_audit('file_reject', role_name=role_name, detail=detail)
    return jsonify({'id': file_id, 'type': file_type, 'status': 'rejected'})

@app.route('/api/admin/preview/<type>/<int:file_id>', methods=['GET'])
@require_admin
def admin_preview_file(type, file_id):
    """管理员预览pending中的文件"""
    conn = get_db()
    if type == 'audio':
        f = conn.execute("SELECT * FROM audio_files WHERE id=?", (file_id,)).fetchone()
    else:
        f = conn.execute("SELECT * FROM image_files WHERE id=?", (file_id,)).fetchone()
    conn.close()
    if not f:
        abort(404)
    
    role_name = f['role_name']
    filename = f['filename']
    
    # 先在正式目录找，再在pending目录找
    if type == 'audio':
        path = AUDIO_DIR / role_name / f['category'] / filename
        if not path.exists():
            path = PENDING_DIR / role_name / 'audio' / f['category'] / filename
    else:
        path = IMAGES_DIR / role_name / filename
        if not path.exists():
            path = PENDING_DIR / role_name / 'images' / filename
    
    if not path.exists():
        abort(404)
    
    mime = mimetypes.guess_type(str(path))[0] or 'application/octet-stream'
    return open(str(path), 'rb').read(), 200, {'Content-Type': mime}

@app.route('/api/admin/reject', methods=['POST'])
@require_admin
def admin_reject():
    """拒绝角色"""
    data = request.get_json() or {}
    name = data.get('name', '')
    if not name:
        return jsonify({'error': '请指定角色名'}), 400
    
    conn = get_db()
    role = conn.execute("SELECT * FROM roles WHERE name=?", (name,)).fetchone()
    if not role:
        conn.close()
        return jsonify({'error': '角色不存在'}), 404
    
    conn.execute("UPDATE roles SET approved=0, rejected=1, updated_at=? WHERE name=?", (now_ts(), name))
    
    # 清理pending文件
    import shutil
    pending_role = PENDING_DIR / name
    if pending_role.exists():
        shutil.rmtree(str(pending_role), ignore_errors=True)
    
    conn.commit()
    conn.close()
    log_audit('role_reject', role_name=name, detail=data.get('reason', ''))
    return jsonify({'name': name, 'rejected': True})

@app.route('/api/admin/audit', methods=['GET'])
@require_admin
def admin_audit():
    limit = request.args.get('limit', 50, type=int)
    conn = get_db()
    logs = conn.execute(
        "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return jsonify([dict(l) for l in logs])

@app.route('/api/admin/devices', methods=['GET'])
@require_admin
def admin_devices():
    conn = get_db()
    devices = conn.execute("SELECT * FROM devices ORDER BY last_seen DESC").fetchall()
    conn.close()
    return jsonify([dict(d) for d in devices])

@app.route('/api/admin/devices/<device_id>/toggle', methods=['POST'])
@require_admin
def admin_toggle_device(device_id):
    conn = get_db()
    dev = conn.execute("SELECT * FROM devices WHERE device_id=?", (device_id,)).fetchone()
    if not dev:
        conn.close()
        return jsonify({'error': '设备不存在'}), 404
    new_status = 0 if dev['approved'] else 1
    conn.execute("UPDATE devices SET approved=? WHERE device_id=?", (new_status, device_id))
    conn.commit()
    conn.close()
    log_audit('device_toggle', device_id=device_id, detail=f'approved={new_status}')
    return jsonify({'device_id': device_id, 'approved': bool(new_status)})

# ============================================================
# 管理后台页面
# ============================================================
@app.route('/admin')
@app.route('/admin/')
def admin_page():
    return render_template('admin.html')

@app.route('/upload')
@app.route('/upload/')
def upload_page():
    return render_template('upload.html')

@app.route('/create')
@app.route('/create/')
def create_role_page():
    return render_template('create.html')

# ============================================================
# 静态文件（公开API文件的访问通过角色路由）
# ============================================================
# 注意：音频/图片文件通过 /api/roles/<name>/audio/<cat>/<file> 访问
# pending中的文件不对外公开

# ============================================================
# 启动
# ============================================================
if __name__ == '__main__':
    init_db()
    print(f"[start] 角色分享服务器已启动 http://{HOST}:{PORT}")
    app.run(host=HOST, port=PORT, debug=False)
