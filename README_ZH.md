<h1 align="center">🐜 LittleAnt V14</h1>

<p align="center"><strong>AI 意图执行中介系统</strong></p>

<p align="center">
  把自然语言意图，通过循环执行模型，编译为可执行、可核查、可恢复的服务器动作。
</p>

<p align="center">
  <a href="https://github.com/shellylittleant/littleant/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT License"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.10+-green.svg" alt="Python 3.10+"></a>
  <a href="https://github.com/shellylittleant/littleant/releases"><img src="https://img.shields.io/github/v/release/shellylittleant/littleant" alt="Release"></a>
  <a href="https://github.com/shellylittleant/littleant/stargazers"><img src="https://img.shields.io/github/stars/shellylittleant/littleant?style=social" alt="Stars"></a>
</p>

<p align="center">
  <a href="./README.md">English</a> | 简体中文
</p>

---

LittleAnt 是一个住在你服务器上的 AI 管家。你通过 Telegram 跟它对话，它自动帮你在服务器上执行任务——装软件、改配置、写脚本、监控系统。纯 Python 标准库实现，不需要安装任何第三方依赖。

## 截图

<table>
  <tr>
    <td align="center"><strong>对话与任务确认</strong></td>
    <td align="center"><strong>执行计划</strong></td>
    <td align="center"><strong>结果汇报</strong></td>
  </tr>
  <tr>
    <td><img src="docs/1.jpg" width="280"></td>
    <td><img src="docs/2.jpg" width="280"></td>
    <td><img src="docs/3.jpg" width="280"></td>
  </tr>
</table>

## 为什么用 LittleAnt？

大多数 AI Agent 框架止步于生成文字。LittleAnt 把意图变成真正的、可验证的服务器操作。

| | 传统 AI Agent | LittleAnt |
|---|---|---|
| **输出** | 文字建议 | 可执行的 shell 命令 |
| **验证** | 无，或用 AI 验证 | 机械验证（零 AI Token） |
| **失败处理** | 崩溃或问用户 | AI 自主恢复（重试→修改→跳过） |
| **任务拆解** | 一次性生成计划 | 循环执行：查询→判断→行动→重复 |
| **透明度** | 黑箱 | 完整执行树，每步有日志 |
| **依赖** | pip install 一堆包 | 无。纯 Python 标准库 |

## 架构

```
┌─────────────┐       自然语言        ┌──────────────────┐
│   用户       │◄────────────────────►│  前台 AI          │
│  (Telegram)  │                      │  (聊天，只读权限)  │
└─────────────┘                      └────────┬─────────┘
                                              │ 查数据库
                                              │ 跑只读命令
                                              │ 发起任务
                                     ┌────────▼─────────┐
                                     │   LittleAnt Core  │
                                     │   (调度引擎)       │
                                     └────────┬─────────┘
                                              │ JSON 协议
                                     ┌────────▼─────────┐
                                     │  后台 AI          │
                                     │  (执行，读写权限)  │
                                     └──────────────────┘
```

- **前台 AI** — 跟用户自然语言对话，有记忆（保持最近20轮上下文），能跑只读命令，汇报结果。不能改任何东西。
- **后台 AI** — 只输出 JSON，无记忆。编写查询命令、判断系统状态、规划行动方案，通过三级恢复处理失败。拥有完整读写权限。
- **Core 引擎** — 执行命令，机械复核结果，持久化状态。所有复核都是代码级的，零 AI Token 消耗。

## 支持的 AI 服务商

| 服务商 | 模型 | 状态 |
|--------|------|------|
| OpenAI | GPT-4o | ✅ 已测试 |
| DeepSeek | DeepSeek-chat | ✅ 支持 |
| Anthropic | Claude Sonnet | ✅ 支持 |
| Google | Gemini 2.0 Flash | ✅ 支持 |
| xAI | Grok 3 | ✅ 支持 |

任何 OpenAI 兼容的 API 都可以用。安装时选一个即可。

## 快速开始

### 环境要求
- Python 3.10+（不需要 pip install）
- 一个 Telegram Bot Token（从 [@BotFather](https://t.me/BotFather) 获取）
- 上述任一服务商的 API Key

### 安装

```bash
git clone https://github.com/shellylittleant/littleant.git
cd littleant
python3 setup.py
```

安装向导会问你：
1. 🌐 语言（English / 中文）
2. 🤖 Telegram Bot Token
3. 🧠 AI 服务商（OpenAI / Claude / Gemini / Grok）
4. 🔑 API Key

### 启动

```bash
bash start.sh
```

### 后台运行（推荐）

```bash
cp littleant.service /etc/systemd/system/
# 编辑 service 文件里的 WorkingDirectory 改成你的安装路径
systemctl daemon-reload
systemctl enable littleant
systemctl start littleant
```

## 使用方法

| 你说什么 | 会发生什么 |
|---------|-----------|
| "nginx 怎么配置？" | AI 直接回答（聊天模式） |
| "帮我装个 LNMP" | 确认→规划→你审批→静默执行→汇报结果 |
| "crontab 现在配了什么？" | 直接跑 `crontab -l`（只读），告诉你结果 |
| "磁盘还剩多少？" | 跑 `df -h`，翻译成人话告诉你 |
| /status | 查看任务进度 |
| /cancel | 取消当前任务 |

### 一个任务是怎么执行的

```
1. 你说："帮我装个 WordPress"
2. 前台AI确认："你是要我执行这个操作吗？" → 你确认
3. 后台AI设计步骤，然后扫描当前系统状态
4. AI判断："nginx没装，PHP没装" → 写安装命令
5. AI审查命令 → 展示计划给你 → 你批准 → 执行
6. AI重新扫描系统 → "nginx装好了，PHP装好了，需要配置"
7. AI写配置命令 → 你批准 → 执行
8. AI再次扫描 → 全部达标 → 任务完成
```

## 实际应用场景

LittleAnt 已在真实服务器环境中用于：

- **LNMP 环境搭建** — 一次对话完成 Nginx + MySQL + PHP 安装配置
- **服务器监控** — 自动编写并部署监控插件
- **定时任务** — 配置 cron 定时任务并通过 Telegram 提醒
- **SSL 和 CDN** — 开发 Cloudflare 对接插件
- **爬虫工具** — 编写并运行数据采集工具
- **系统诊断** — 全面服务器配置检查，结果翻译成人话

## 项目结构

```
littleant/
├── setup.py                  # 安装向导
├── run.py                    # 主入口
├── start.sh                  # 一键启动
├── littleant.service          # systemd 服务文件
├── docs/
│   └── WHITEPAPER.md         # 技术白皮书
├── littleant/
│   ├── i18n/                 # 语言包（en.json, zh.json）
│   ├── ai/adapter.py         # 多服务商 AI 适配器
│   ├── core/
│   │   ├── decomposer.py     # 步骤分解（复杂任务备用）
│   │   ├── executor.py       # 命令执行器
│   │   ├── verifier.py       # 8 种机械复核器
│   │   ├── recovery.py       # 节点清理与崩溃恢复
│   │   ├── orchestrator.py   # 循环引擎 + 三级恢复（L1/L2/L3）
│   │   ├── protocol.py       # 命令协议（20 条命令 + 7 种反馈）
│   │   └── readonly_executor.py  # 只读执行器（白名单机制）
│   ├── models/project.py     # 数据模型和状态机
│   └── storage/              # JSON + SQLite 混合存储
└── README.md
```

## 设计哲学

> **"AI 是操作员，程序是电脑。"**

- 命令集是有限的——AI 只能从预定义命令中选择，不能发明新命令
- 所有复核都是机械的——零 AI Token 消耗
- 失败的节点从链中**删除**，不是标记——执行自动继续
- 成功的项目自动保存到模板库——下次 AI 直接复用
- 操作员可以换（GPT→Claude→Gemini）——协议不变

## 文档

- [技术白皮书（English）](docs/WHITEPAPER.md) — 完整的架构、协议和设计说明
- [更新日志](CHANGELOG.md) — 版本记录

## 参与贡献

欢迎贡献！详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 开源协议

[MIT](LICENSE)

---

<p align="center">
  <sub>循环执行 + 机械复核 + 三级恢复。零依赖，零信任 AI 输出。</sub>
</p>
