# 角色分享服务器 (server/)

本目录是角色扮演插件的配套服务端 —— 社区共创平台。

**负责：** 角色包管理、音频/图片热更新、审核防投毒、设备认证  
**不负责：** 聊天记录、用户隐私、LLM 推理

与客户端（插件）**零长连接**，全按需 HTTP 请求。

---

## 快速部署

```bash
cd server/
pip install flask pyyaml bcrypt pypinyin
cp config.example.yaml config.yaml
# 编辑 config.yaml 改 admin_password 和端口
python server.py
# → http://你的IP:8766
# → http://你的IP:8766/admin  管理后台
```

---

## 构建私有服务器需要注意

### 1. 修改默认密码

`config.example.yaml` 中 `admin_password` 默认是 `changeme123`，**部署到公网前必须改**。

### 2. Nginx 反向代理

服务器默认监听 `0.0.0.0:8766`，生产环境应该用 Nginx 反代并提供 HTTPS。参考 `nginx/kunxun.conf.example`：

```nginx
location /api/ {
    proxy_pass http://127.0.0.1:8766;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
location /admin {
    proxy_pass http://127.0.0.1:8766;
}
```

### 3. 内容审核模式

```yaml
content_mode: "moderated"   # 推荐：上传进审核队列，管理员逐文件批准
content_mode: "open"        # 开放：上传即上架（适合内部信任网络）
```

### 4. 认证模式

```yaml
auth_mode: "auto_register"   # 推荐：新设备自动注册
auth_mode: "open"             # 开放：不检查设备ID
auth_mode: "admin_approve"    # 严格：新设备需管理员手动批准
```

### 5. 数据备份

SQLite 数据库文件 `data.db` + `config.yaml` + `audio/` `images/` 目录全部在 `server/` 下。定期备份这整个目录即可。

### 6. 大文件存储

角色 ZIP 包不应存在服务器上。API 只存 `download_url`（外部链接，如 GitHub Releases），10Mbps 带宽只服务音频下载。

### 7. 数据库

SQLite 文件 `data.db`，启动时自动创建。不需要单独安装 MySQL/PostgreSQL。

---

## 目录结构

```
server/
├── server.py              # 主程序 (Flask + SQLite)
├── config.example.yaml    # 配置模板
├── config.yaml            # 实际配置 (gitignore排除)
├── data.db                # SQLite运行时生成 (gitignore排除)
├── requirements.txt       # Python依赖
├── .gitignore
├── README.md              # 本文件
├── index.html             # 首页
├── nginx/
│   └── kunxun.conf.example   # Nginx反代配置参考
├── templates/
│   ├── admin.html         # 管理后台
│   ├── create.html        # 创建角色
│   └── upload.html        # 上传页面
├── audio/                 # 音频存储 (运行时)
│   └── <角色名>/
│       ├── expressions/<file>.mp3
│       └── music/<file>.mp3
├── images/                # 图片存储 (运行时)
│   └── <角色名>/<file>.png
├── uploads/               # 已审核的ZIP
└── pending/               # 待审核文件
```

---

## 依赖

```
flask>=2.0.0
pyyaml>=6.0
bcrypt>=4.0.0
pypinyin>=0.50.0
```

---

## 隐私说明

本服务器 **不接触任何用户对话数据**：

| 数据类型 | 是否经过服务器 |
|----------|:---:|
| 角色 ZIP / 音频 / 图片 | ✅ 用户主动上传 |
| 聊天记录 / 用户偏好 | ❌ 纯本地 |
| 设备指纹 | ✅ 仅用于设备识别 |
