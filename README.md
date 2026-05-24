# 🎭 AstrBot Plugin — 角色扮演 (Roleplay)

一个功能完整的动漫角色扮演插件，支持角色 ZIP 开箱即用、情感引擎、TTS 语音合成、短中长期记忆系统、语气词音频、特殊日期自动回复、知识库联网更新、分享前隐私清洗。

## ✨ 功能

- 🎭 **角色扮演核心** — 自定义角色 personality，情绪识别 + 情感图片 + 语音合成
- 🎵 **语气词音频** — 角色目录下 `audio/expressions/` 存放MP3，根据情绪/关键词自动匹配
- 🔊 **TTS 语音** — 支持 GPT-SoVITS（本地）/ Edge TTS / 云端 API
- 🧠 **短中长期记忆** — 分层记忆系统，支持摘要和事实提取
- 📅 **特殊日期自动回复** — 角色生日/节日自动发消息
- 🔍 **知识库联网更新** — 定时搜索更新角色背景知识
- 📦 **角色 ZIP 生态** — 导入/导出/分享，支持可信任服务器
- 🔒 **导出隐私保护** — 自动过滤使用者信息，安全分享

## 📥 安装

### 方式一：AstrBot 插件商店
> 在 WebUI → 扩展 → 商店中搜索 `astrbot_plugin_roleplay` 安装

### 方式二：手动安装
1. 下载本仓库 ZIP 或 `git clone`
2. 放到 `AstrBot/source/data/plugins/` 目录下
3. 重启 AstrBot

### 方式三：安装角色（别人分享给你的 ZIP）
1. 将 `.zip` 放到 `tools/待导入角色/` 文件夹
2. 双击 `tools/导入角色.bat`
3. 重启 AstrBot

## 📂 目录结构

```
astrbot_plugin_roleplay/
├── main.py                 # 插件主入口
├── metadata.yaml           # 插件元数据
├── _conf_schema.json       # 插件配置 schema
├── core/                   # 核心模块
│   ├── role_manager.py     # 角色安装/导出/管理
│   ├── config_loader.py    # YAML 配置加载
│   ├── memory_manager.py   # 短/中/长期记忆
│   ├── tts_manager.py      # TTS 语音合成
│   ├── emotion_engine.py   # 情绪识别引擎
│   ├── image_handler.py    # 图片策略
│   ├── cleaner.py          # 记忆清洗 + 隐私保护
│   ├── audio_injector.py   # 语气词音频注入
│   ├── auto_reply.py       # 自动回复调度器
│   └── knowledge_updater.py # 知识库联网更新
├── data/
│   └── roles/              # 角色存放目录
│       └── 绪山真寻/        # 示例角色
├── templates/              # WebUI 模板
│   └── memory.html
└── tools/                  # 辅助工具
    ├── 角色扮演管理中心.html  # 浏览器管理面板
    ├── 导入角色.bat          # 一键导入
    ├── 导出角色.bat          # 一键导出
    └── 角色压缩包导入导出说明.txt  # 使用指南
```

## 🔧 配置

AstrBot WebUI → 扩展 → astrbot_plugin_roleplay → 设置：

| 配置项 | 说明 |
|--------|------|
| 启用插件 | 开关 |
| 当前激活角色名 | 输入角色名切换 |
| 可信任服务器地址 | 远程角色商店地址 |
| TTS 语音引擎 | gpt_sovits / edge_tts / cloud_api / disabled |
| 短期记忆条数 | 给 LLM 参考的最近 N 条对话 |
| 启用自动回复 | 角色主动发消息 |
| 特殊日期 | 角色生日 / 节日配置 |
| 启用知识库更新 | 定时联网搜索 |

## 🎤 角色 ZIP 格式

```yaml
# config.yaml (必需)
name: 角色名
version: 1.0.0
author: 作者
birthday: MM-DD                    # 角色生日
birthday_desc: 说明
persona: |
  你是xxx，来自xxx作品。
  你的性格...
emotions:
  default:
    triggers: []
    prompt: 平静状态
  happy:
    triggers: [开心, 高兴]
    prompt: 开心的状态
    image: images/xxx.png          # 改为相对路径
voice:
  engine: gpt_sovits               # 或 edge_tts / disabled
```

```
角色.zip
├── config.yaml        ← 必需
├── audio/
│   ├── audio_map.json ← 音频分类映射
│   └── expressions/   ← 语气词MP3
└── images/            ← 角色图片
```

参见 `tools/角色压缩包导入导出说明.txt` 获取完整模板。

## 💻 QQ频道命令

```
/role list      列出已安装角色
/role switch    切换角色
/role status    查看角色状态
/role off       关闭角色扮演
/role ttstest   测试TTS链路
```

## 🏪 上架插件市场

1. Fork [AstrBot_Plugins_Collection](https://github.com/Soulter/AstrBot_Plugins_Collection)
2. 在本仓库的 Release 页面发布一个新版本
3. 向 Plugins_Collection 提 PR 添加你的插件信息

## 📄 License

MIT
