<p align="center">
  <img src="https://img.shields.io/badge/🎬_Jianying_Video_Gen-AI_Automation-blueviolet?style=for-the-badge" alt="Jianying Video Gen"/>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Engine-Playwright-45ba63?style=flat-square&logo=playwright&logoColor=white" alt="Playwright"/>
  <img src="https://img.shields.io/badge/Model-Seedance_2.0-ff6b6b?style=flat-square&logo=bytedance&logoColor=white" alt="Seedance 2.0"/>
  <img src="https://img.shields.io/badge/Python-3.9+-3776ab?style=flat-square&logo=python&logoColor=white" alt="Python 3.9+"/>
  <img src="https://img.shields.io/badge/License-MIT-green?style=flat-square" alt="MIT License"/>
  <img src="https://img.shields.io/badge/Platform-macOS%20|%20Linux-lightgrey?style=flat-square&logo=apple&logoColor=white" alt="Platform"/>
  <img src="https://img.shields.io/badge/Mode-T2V%20|%20I2V%20|%20V2V-orange?style=flat-square" alt="T2V, I2V & V2V"/>
  <img src="https://img.shields.io/badge/Status-Production-brightgreen?style=flat-square" alt="Production"/>
</p>

---

# 🎬 Jianying Video Gen

> 基于 Playwright 自动化操作 **剪映小云雀 (xyq.jianying.com)**，使用 **Seedance 2.0** 模型批量生成 AI 视频。

## ✨ 功能特性

| 功能 | 描述 |
|------|------|
| 🎥 **文生视频 (T2V)** | 输入文字描述，自动生成视频 |
| 🖼️ **图生视频 (I2V)** | 上传参考图片 + 文字描述，让图片动起来 |
| 🔄 **参考视频生成 (V2V)** | 上传参考视频 + 风格描述，生成风格转换视频 |
| 📥 **自动下载** | 通过 thread_id 详情页提取 CDN 链接，curl 直接下载 |
| 🔐 **Cookie 登录** | 免密登录，支持自定义 cookies 路径 |
| 🧪 **Dry-Run 调试** | 填写表单但不提交，生成截图供检查 |
| � **Dry-Run 调试** | 填写表单但不提交，生成截图供检查 |
| �🤖 **AI Agent Skill** | 标准 Skill 格式，完美兼容 OpenClaw、Claude Code 等 Agent 框架 |

## 🚀 快速开始 (作为 AI Agent Skill)

本项目主要设计为 AI Agent 的扩展技能（Skill），让 LLM 获得自动化生成视频的能力。

### 1. 准备 Cookies

由于剪映网页版需要登录，你需要先在本地浏览器获取登录状态：
1. 在浏览器中登录 [xyq.jianying.com](https://xyq.jianying.com)
2. 使用 [EditThisCookie](https://chrome.google.com/webstore/detail/editthiscookie) 等扩展导出 cookies
3. 将文件保存为 `cookies.json` 放置在 Agent 运行目录下。

### 2. 在 OpenClaw / Claude Code 中使用

直接让 Agent 挂载 `jianying-video-gen` 目录即可。当你在对话中输入以下 prompt 时，Agent 就会自动调用该技能：

> **User**: "帮我生成一段赛博朋克风格的视频，10秒横屏。"
> **OpenClaw/Claude**: (自动分析意图 -> 识别出需要视频生成 -> 隐式调用 `jianying_worker.py` -> 自动轮询 -> 最终返回已下载好的 mp4 文件路径给你)

*注意：Agent 运行环境需已安装 Python 3.9+、`playwright` 并执行过 `playwright install chromium`。*

---

## 💻 命令行手动调用 (开发者测试)

如果你想自己写代码或通过终端手动测试生成流程，可以按以下方式执行：

### 安装依赖

```bash
pip install playwright
playwright install chromium
```

### ▶️ 文生视频 (T2V)

```bash
python3 jianying-video-gen/scripts/jianying_worker.py \
  --prompt "巨龙从火山口腾空而起，熔岩四溅，电影级航拍" \
  --duration 10s \
  --model "Seedance 2.0"
```

### 🖼️ 图生视频 (I2V)

```bash
python3 jianying-video-gen/scripts/jianying_worker.py \
  --ref-image ./test_image.png \
  --prompt "将这张图片变成动画，镜头从左向右缓慢平移" \
  --duration 10s \
  --model "Seedance 2.0 Fast"
```

### 🔄 参考视频生成 (V2V)

```bash
python3 jianying-video-gen/scripts/jianying_worker.py \
  --ref-video ./reference.mp4 \
  --prompt "画风改成宫崎骏风格，其他不变" \
  --duration 10s \
  --model "Seedance 2.0"
```

## 📋 参数说明

| 参数 | 默认值 | 可选值 | 说明 |
|:-----|:-------|:-------|:-----|
| `--prompt` | `"一个美女在跳舞"` | 任意文本 | 视频描述 (必填) |
| `--duration` | `10s` | `5s` `10s` `15s` | 视频时长 |
| `--ratio` | `横屏` | `横屏` `竖屏` `方屏` | 画面比例 |
| `--model` | `Seedance 2.0` | `Seedance 2.0` `Seedance 2.0 Fast` | 模型选择 |
| `--ref-image` | — | 本地图片文件路径 | 参考图片 (I2V 模式) |
| `--ref-video` | — | 本地视频文件路径 | 参考视频 (V2V 模式) |
| `--cookies` | `cookies.json` | 文件路径 | 登录凭证路径 |
| `--output-dir` | `.` | 目录路径 | 视频输出目录 |
| `--dry-run` | `false` | — | 仅填表不提交 |

## 💰 模型与积分

| 模型 | 积分/秒 | 5s | 10s | 15s | 推荐场景 |
|:-----|:--------|:----|:-----|:-----|:---------|
| ![Fast](https://img.shields.io/badge/Seedance_2.0_Fast-3积分/s-blue?style=flat-square) | 3 | 15 | 30 | 45 | 测试、快速预览 |
| ![Pro](https://img.shields.io/badge/Seedance_2.0-5积分/s-purple?style=flat-square) | 5 | 25 | 50 | 75 | 正式出片 |

## ⚙️ 自动化流程

```mermaid
graph LR
    A[🔐 Cookie登录] --> B[📝 新建项目]
    B --> C[🎬 沉浸式短片]
    C --> D[🤖 选模型]
    D --> E{V2V?}
    E -->|是| F[📎 上传参考视频]
    E -->|否| G[⏱️ 选时长]
    F --> G
    G --> H[💬 输入Prompt]
    H --> I[🚀 提交生成]
    I --> J[🎯 拦截 thread_id]
    J --> K[📄 导航详情页]
    K --> L[⏳ 轮询视频]
    L --> M[📥 curl 下载 MP4]
```

## 🎨 提示词示例

<details>
<summary><b>🎬 运动 & 动作</b></summary>

```
一位武术大师在雨中练太极，慢动作，水滴飞溅，电影级光影
三个街舞少年在霓虹灯下的篮球场battle，地面反光，烟雾弥漫
```
</details>

<details>
<summary><b>🌊 自然 & 特效</b></summary>

```
一朵玫瑰从花苞到盛开的延时摄影，露珠滚落花瓣，背景虚化，4K微距
巨龙从火山口腾空而起，熔岩四溅，暴风雨中闪电照亮整个天空，电影级航拍
```
</details>

<details>
<summary><b>🔥 风格化 & 超现实</b></summary>

```
赛博朋克风格的长安城，飞行汽车穿梭在霓虹灯笼之间，古代宫殿与全息投影交融
一座中国古城从海底缓缓升起，鲸鱼在宫殿之间游过，阳光穿透海水照亮金色琉璃瓦
```
</details>

<details>
<summary><b>🔄 V2V 风格转换</b></summary>

```
参考视频，画风改成宫崎骏风格的画风，其他不变
赛博朋克风格，霓虹灯光照射下的机械舞者，金属质感皮肤
三只穿着华丽晚礼服的猫咪在舞会上优雅起舞，迪士尼动画风格
```
</details>

## 📁 项目结构

```text
lanshu-waytovideo/
├── cookies.json                    # 登录凭证 (需自行导出，勿提交)
├── README.md                       # 使用说明
└── jianying-video-gen/             # AI Agent Skill 主目录
    ├── SKILL.md                    # Agent 识别用的技能说明文档
    ├── requirements.txt            # Python 依赖
    ├── scripts/
    │   └── jianying_worker.py      # 自动化执行的 Python 入口
    └── references/
        └── prompt-guide.md         # 提供给 Agent 学习的提示词指南
```

## ⚠️ 注意事项

- Cookies 有时效性，过期后需重新导出
- 生成视频消耗积分，建议先用 `Fast` + `5s` 测试
- 参考视频上传可能需要 60-90 秒，脚本会自动等待
- 视频生成通常需要 3-5 分钟，脚本最多等待 10 分钟

---

<p align="center">
  <sub>Made with ❤️ by Lanshu | Powered by <b>Seedance 2.0</b></sub>
</p>
