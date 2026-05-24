# 角色分享服务器

社区共创平台 — 让每个人的贡献使角色更加完善

## 快速部署

```bash
# 安装依赖
pip install flask pyyaml

# 复制配置
copy config.example.yaml config.yaml

# 启动服务器
python server.py
```

服务器启动后浏览器打开 `http://你的IP:8765` 即可看到管理面板。

## 配置说明

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `auth_mode` | `open` 开放 / `auto_register` 自动注册(推荐) / `admin_approve` 需批准 | auto_register |
| `port` | 监听端口 | 8765 |
| `max_role_size_mb` | 角色ZIP最大大小 | 500 |

## 认证机制 — 设备指纹

**用户不需要手动填写任何 Token。** Bot 启动时自动生成设备指纹：

```
设备指纹 = SHA256(主机名 + 机器UUID + 操作系统 + CPU架构)
```

发送请求时自动附带 `X-Device-ID` 请求头。服务器识别设备后自动注册或放行。

### 三种认证模式

| 模式 | 说明 |
|------|------|
| `open` | 完全开放，不检查设备ID(适合公开测试) |
| `auto_register` | 新设备自动注册并批准(推荐) |
| `admin_approve` | 新设备需管理员在WebUI手动批准 |

## API 文档

### 角色管理
```
GET  /api/roles                        # 角色列表
GET  /api/roles/{name}                 # 角色详情(版本/音频/图片)
GET  /api/roles/{name}/download        # 下载角色ZIP
POST /api/roles/share                  # 上传角色ZIP(FORM: file+name+author+version)
```

### 音频热更新
```
GET  /api/roles/{name}/audio                        # 音频文件列表
POST /api/roles/{name}/audio                        # 上传音频(FORM: file+category)
GET  /api/roles/{name}/audio/{category}/{filename}  # 下载单个音频
```

### 图片热更新
```
GET  /api/roles/{name}/images                       # 图片文件列表
POST /api/roles/{name}/images                       # 上传图片(FORM: file)
GET  /api/roles/{name}/images/{filename}            # 下载图片
```

### 增量更新
```
GET /api/roles/{name}/updates           # 返回所有文件的hash/mtime,客户端对比后决定下载哪些
```

### 设备管理(管理面板)
```
POST /api/device/register               # 注册设备 {device_id, name}
GET  /api/devices?password=xxx          # 设备列表(需管理密码)
POST /api/devices/{id}/approve           # 批准设备(需管理密码)
```

## 客户端配置

在插件配置面板填写：
- `trusted_server_url`: `http://你的服务器IP:8765`
- `trusted_server_token`: 留空即可(设备指纹自动认证)

## 社区共创流程

```
1. 用户A上传角色ZIP → 服务器存储
2. 用户B浏览角色列表 → 下载安装
3. 用户B收录了更好的音频 → 上传到服务器
4. 用户A下次同步 → 获取B上传的新音频
5. 角色不断完善，社区共同维护
```
