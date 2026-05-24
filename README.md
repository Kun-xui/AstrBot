# 角色扮演插件 (astrbot_plugin_roleplay)

AstrBot 动漫角色扮演插件，支持角色 ZIP 开箱即用、情感引擎、TTS语音合成、短中长期记忆系统、语气词音频、FunctionCall 工具调用、隐私保护导出。

## 安装

将本项目所有文件放入 AstrBot 插件目录：

```
你的AstrBot安装目录\data\plugins\astrbot_plugin_roleplay\
```

> **路径说明**：AstrBot 默认安装在 `C:\Users\你的用户名\AstrBot\source\`，插件目录在 `C:\Users\你的用户名\AstrBot\source\data\plugins\`。不同用户/系统路径可能不同，请根据实际位置调整。

### 标准目录结构

```
astrbot_plugin_roleplay/
├── main.py                  # 插件主入口
├── metadata.yaml            # 插件元数据
├── _conf_schema.json        # 配置面板
├── README.md                # 本文件
├── requirements.txt         # Python 依赖
├── core/                    # 核心模块
│   ├── audio_injector.py    # 语气词音频注入
│   ├── auto_reply.py        # 自动回复调度
│   ├── cleaner.py           # 导出清洗
│   ├── config_loader.py     # 配置加载
│   ├── emotion_engine.py    # 情绪引擎
│   ├── function_tools.py    # FunctionCall 工具集
│   ├── image_handler.py     # 图片处理
│   ├── knowledge_updater.py # 知识库更新
│   ├── memory_manager.py    # 记忆管理
│   ├── role_manager.py      # 角色管理
│   └── tts_manager.py       # TTS 语音合成
├── data/                    # 数据目录
│   ├── roles/               # 已安装的角色
│   └── tts_cache/           # TTS 缓存
├── tools/                   # 辅助工具
│   ├── 角色扮演管理中心.html   # 本地登录入口
│   ├── 导入角色.bat           # 批量导入脚本
│   ├── 导出角色.bat           # 批量导出脚本
│   └── 角色压缩包导入导出说明.txt
└── templates/               # Web 页面模板
```

## 配置

在 AstrBot WebUI (http://localhost:6185) → 插件管理 → 角色扮演 中配置。

### 核心配置

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `active_role` | 当前激活角色名 | 空 |
| `enabled` | 启用插件 | true |
| `tools_enabled` | 启用工具扩展(weather/search/shell等) | true |
| `auto_reply_enabled` | 启用自动回复 | false |
| `long_term_enabled` | 启用长期记忆 | true |

## QQ命令速查

发送 `/role help` 随时查看。全部命令：

| 命令 | 说明 |
|------|------|
| `/role help` | 查看所有命令 |
| `/role list` | 已安装角色 |
| `/role switch <名>` | 切换角色 |
| `/role status` | 当前状态(角色+记忆) |
| `/role off` | 关闭扮演 |
| `/role ping` | 服务器连通检测 |
| `/role preview` | 预览服务器音频/图片 |
| `/role preview audio` | 仅预览音频 |
| `/role preview images` | 仅预览图片 |
| `/role update` | 同步音频+图片(只拉变化的) |
| `/role update audio\|images` | 仅同步音频/图片 |
| `/role update status` | 版本对比 |
| `/role upload audio <路径>` | 上传单个音频 |
| `/role upload image <文件名>` | 上传单个图片 |
| `/role push` | 一键打包全部推送 |
| `/role ttstest` | TTS链路诊断 |

## 角色 ZIP 格式

```
角色名.zip
├── config.yaml          # 角色配置（必选）
├── audio/               # 音频文件夹（可选）
│   ├── audio_map.json   # 音频分类映射
│   └── expressions/     # 语气词音频 (*.mp3)
└── images/              # 角色图片（可选）
```

详见：`tools\角色压缩包导入导出说明.txt`

## 功能列表

- 🎭 角色 ZIP 开箱即用 — 上传 → 激活 → 对话
- 🤖 FunctionCall 工具 — weather/calculate/web_search/get_time/shell_exec
- 🎵 语气词音频 — [tts]/[audio:害羞]/[music:歌名] 命令标签
- 🧠 记忆系统(双通道) — user_facts(永不出) + role_facts(可导出)
- 📂 完整聊天记录按月归档 — raw_history/ 无上限
- 🎤 TTS 语音 — GPT-SoVITS / Edge TTS / 云端API
- 📅 特殊日期自动回复 — 可视化编辑器
- 🔒 隐私保护导出 — 仅含角色知识
- 📡 可信任服务器 — 角色分享/发现/下载

## 导出隐私说明

导出角色 ZIP 时：
- ✅ 保留：角色知识(role_facts)、短期记忆、对话摘要
- ❌ 不导出：用户事实(user_facts)、完整聊天记录(raw_history)
- 🔒 自动清洗：使用者生日、用户名等个人敏感信息

## 记忆文件说明

插件运行后在 `data\角色名\` 下生成以下记忆文件：

| 文件 | 说明 | 是否导出 |
|------|------|----------|
| `user_facts.json` | LLM 提取的用户偏好/事实 | ❌ 永不导出 |
| `role_facts.json` | LLM 提取的角色知识 | ✅ 可导出 |
| `short_term.json` | 最近10条对话 | ✅ 清洗后导出 |
| `medium_summaries.json` | 对话摘要 | ✅ 清洗后导出 |
| `raw_history/` | 完整聊天记录(按月分块) | ❌ 永不导出 |
| `msg_count.json` | 摘要计数器 | ❌ 不导出 |

## 安全策略

### Shell 白名单
```
dir, ls, echo, date, time, ping, curl, wget, python, pip, git,
type, cat, find, grep, whoami, hostname, ipconfig, netstat,
ps, tasklist, systeminfo, nslookup, tracert, tree, where, which
```

### Shell 黑名单模式
```
rm, del, format, shutdown, reboot, kill, dd, mkfs, chmod,
chown, sudo, su, $(...), ${...}, >/dev/*, >/etc/*, | sh, | bash
```

## 角色分享服务器

插件支持连接可信任服务器，实现社区共创：

```
用户A ──上传角色+音频──→  角色分享服务器  ←──下载+同步── 用户B
         (Flask + SQLite)    (端口 8766)
```

### 服务器部署

```bash
cd server/
pip install flask pyyaml bcrypt pypinyin
cp config.example.yaml config.yaml
python server.py
# → http://你的IP:8766  管理后台 → http://你的IP:8766/admin
```

详细说明见 [`server/README.md`](server/README.md) — 包含 Nginx 反代配置、审核模式、安全提醒。

### 隐私保证

| 数据 | 是否传到服务器 | 说明 |
|------|---------------|------|
| 角色 ZIP (config + audio + images) | ✅ 用户主动上传 | 社区共享，管理员审核 |
| 音频/图片文件 | ✅ 用户主动上传 | 热更新的资源 |
| 聊天记录 (raw_history/) | ❌ 绝不触碰 | 纯本地存储 |
| 用户事实 (user_facts.json) | ❌ 绝不触碰 | 纯本地存储 |
| 使用者生日/用户名 | ❌ 绝不触碰 | 导出时自动清洗 |
| 设备指纹 (X-Device-ID) | ✅ 自动 | 只用于识别设备，无法反推个人信息 |

**服务器不保存任何用户对话数据。** 插件与服务器的通信仅限于角色资源的上传/下载。

### 客户端命令

在 QQ 中向 Bot 发送：

| 命令 | 作用 |
|------|------|
| `/role ping` | 测试服务器连通性 |
| `/role update` | 从服务器同步音频/图片（只下载变化的） |
| `/role update audio` | 仅同步音频 |
| `/role update images` | 仅同步图片 |
| `/role update status` | 本地与远程版本对比 |

### 服务器API

详见 `server/README.md`，核心端点：

| 路由 | 说明 |
|------|------|
| `GET /api/ping` | 连通性检测 |
| `GET /api/roles` | 角色列表 |
| `GET /api/roles/{name}/audio` | 音频文件列表 |
| `GET /api/roles/{name}/audio/{cat}/{file}` | 下载音频 |
| `POST /api/roles/{name}/audio` | 上传音频 |
| `POST /api/roles/share` | 分享角色（审核模式进 pending） |
| `POST /api/admin/login` | 管理员登录（bcrypt） |

---

## 依赖

```
aiohttp>=3.8.0
pyyaml>=6.0
```

> `aiohttp` 是 weather/web_search 工具的必要依赖，如不需要可忽略。
