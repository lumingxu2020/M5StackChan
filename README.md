# M5StackChan · Mac Gateway

让 **M5Stack StackChan** 通过对话操作 Mac mini 或其他运行 macOS 的 Mac。你可以让它打开应用或网页、设置番茄钟、玩语音小游戏，或者把一个问题交给指定的大模型。

本仓库是独立的社区集成项目。它在 Mac 上运行 FastAPI Gateway 和可选的小智 MCP 桥接，不包含设备固件。按照下文接入原厂 AI Agent 的 MCP 功能时，无需为本项目刷机。

- [功能与语音示例](#功能与语音示例)
- [快速开始](#快速开始)
- [连接原厂-stackchan](#连接原厂-stackchan)
- [模型配置](#模型配置)
- [授权与数据处理](#授权与数据处理)
- [接口与开发](#接口与开发)
- [常见问题](#常见问题)
- [免责声明与许可](#免责声明与许可)

## 功能与语音示例

唤醒 StackChan 后说出指令。需要控制 Mac 或访问个人数据的工具须先在控制面板逐项授权。语音识别和工具选择由原厂 AI Agent 负责。

| 功能 | 示例 | 条件与行为 |
| --- | --- | --- |
| 打开应用 | “在 Mac 上打开 Google Chrome。” | App 须已安装，并出现在 Gateway 的「应用白名单」中。 |
| Safari / Finder | “用 Safari 打开 apple.com。”“在 Finder 打开下载文件夹。” | 网页限 HTTP/HTTPS。Finder 支持桌面、下载、文稿、应用程序和个人文件夹。 |
| 音量与媒体 | “把电脑音量调到 30。”“Spotify 下一首。” | 系统音量及播放取决于 Mac 当前环境，Spotify 功能要求安装对应应用。 |
| 新建备忘录 | “新建备忘录，标题是待办，正文是周五交报告。” | 写入备忘录默认账户，可能随账户同步。macOS 可能要求自动化授权。 |
| 提醒与番茄钟 | “三分钟后提醒我。”“开始专注 25 分钟，任务是写报告，休息 5 分钟。” | 桥接和网页共用计时器。通知与朗读来自 Mac。 |
| 查询与简报 | “今天上海天气怎么样？”“看看今天的日程。” | 天气需要网络。查询日历、提醒事项或剪贴板需要各自的工具授权，部分功能还需 macOS 权限。 |
| 小游戏与角色 | “来玩猜数字。”“开始冒险故事。”“切换成侦探。” | 支持猜数字、分支冒险、掷骰子、谜语等。角色设置会影响 Gateway 的带工具对话，原厂 AI Agent 的持续表现取决于其上下文。 |
| 指定模型问答 | “请用 Gemini 回答，为什么天空是蓝色的？” | 先配置服务商并启用 `ask_model`。这次问题会交给指定模型，原厂语音链路继续处理唤醒、识别和播报。 |

摄像头互动、触摸事件、主动操控舵机和通过机器人扬声器主动播报没有接入本项目。HTTP 对话返回的表情和动作字段需要调用客户端自行解释和执行。

## 快速开始

需要 macOS、Python 3.11 或更新版本，以及安装 Python 依赖所需的网络连接。只测试 HTTP 面板时无需连接机器人或准备云端模型密钥。语音接入需要能在 StackChan World 中配置小智 MCP 的设备及账户。

```bash
git clone https://github.com/lumingxu2020/M5StackChan.git
cd M5StackChan/gateway
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp -n .env.example .env
chmod 600 .env
```

首次配置时，运行 `openssl rand -hex 32` 生成随机字符串，在本机文本编辑器里用它替换 `.env` 中的 `STACKCHAN_GATEWAY_TOKEN`。保留 `STACKCHAN_MOCK_MODE=true` 和 `STACKCHAN_AUTONOMOUS_TOOL_MODE=safe`，然后运行：

```bash
python -m uvicorn app:app --host 127.0.0.1 --port 8765
```

打开 [本机控制面板](http://127.0.0.1:8765/)，输入 Gateway Token 并点「保存到本次标签页」。在「快速测试」输入一句话，点击「问 jammy」即可看到模拟响应。`curl -fsS http://127.0.0.1:8765/health` 可检查服务状态和工具清单。

模拟模式跳过 Gateway 的模型请求。直接调用工具或通过原厂小智调用工具时，获准的操作仍可能真实执行，例如写剪贴板、开应用或发送网络查询。

## 连接原厂 StackChan

1. 在 StackChan World 中选择设备，在设置中打开 MCP，复制小智 MCP 接入地址。菜单名称及可用性视固件和应用版本而定。
2. 将完整的 WebSocket 地址填入 Mac 本机的 `gateway/.env` 变量 `XIAOZHI_MCP_ENDPOINT`。地址包含凭据。
3. 保持 Gateway 在本机 8765 端口运行，再开一个终端，在 `gateway/` 下运行以下命令：

   ```bash
   source .venv/bin/activate
   python -m stackchan_gateway.xiaozhi_bridge
   ```

4. 桥接日志出现 `Connected; advertising configured StackChan tools` 后，唤醒机器人并说“帮我掷一个骰子”。以设备实际回复和桥接工具调用日志为准，单有心跳只能确认云端桥接在线。
5. 在控制面板按需打开 Mac 操作开关，例如 `computer_butler_open_app`。如果手动运行桥接，修改开关后需停止并重新启动桥接，以更新工具清单。

桥接由 Mac 主动连接小智服务，普通 MCP 接入可保留 Gateway 的本机监听地址。仓库不安装后台服务。需要开机登录后运行时，可另行配置用户级 macOS LaunchAgent。程序工作目录应指向 `gateway/`，可使用 LaunchAgent 标签 `com.stackchan.gateway` 与 `com.stackchan.xiaozhi-bridge`，其中后者是控制面板尝试重新加载的标签。

## 模型配置

### Gateway 的默认对话模型

把 `STACKCHAN_MOCK_MODE` 设为 `false`，选择服务商并填入对应密钥，随后重启 Gateway。模型 ID 可以通过 `STACKCHAN_LLM_MODEL` 指定，留空会使用代码中的默认值。请使用服务商当前向你的账户开放的模型。通过原厂小智进行的普通对话由小智侧配置决定。

| `STACKCHAN_LLM_PROVIDER` | 密钥变量 |
| --- | --- |
| `openai` | `OPENAI_API_KEY` |
| `deepseek` | `DEEPSEEK_API_KEY` |
| `minimax` | `MINIMAX_API_KEY` |

### 单次指定模型问答

在控制面板「多模型问答」中选择服务商，保存模型 ID 和 API Key 后点「试问已保存模型」。支持 OpenAI、DeepSeek、MiniMax、Gemini、Claude、通义千问（中国内地 DashScope）和本机 Ollama。Ollama 需要本机的 11434 端口可用，填写已安装的模型标签，无需密钥。

密钥留空会继续使用已保存的密钥或相应环境变量。面板不会回显密钥。要让机器人发起指定模型请求，在功能开关中启用 `ask_model`。

## 授权与数据处理

### 工具授权

| 模式 | 行为 |
| --- | --- |
| `safe` | 默认。小智与 Gateway 对话模型只获得娱乐工具和少量状态查询。部分娱乐工具会写入本机游戏或角色状态。 |
| `custom` | 使用 `STACKCHAN_AUTONOMOUS_ALLOWED_TOOLS` 指定工具。在面板保存开关时会写入该模式。 |
| `all` | 向小智和 Gateway 对话模型提供所有工具，包括读写剪贴板、查询日历和控制 Mac。 |

工具开关控制模型和小智可自动使用的工具。管理员的 `POST /v1/tools/{name}` 可直接调用完整工具列表，打开应用仍会检查应用白名单。Gateway Token 为空时，大部分接口会跳过认证，多模型配置、应用白名单和调用记录需要事先配置 Token。使用本机监听地址。若确有局域网访问需求，请先配置受限访问和 TLS，再考虑对其他设备开放。

### 应用白名单与调用记录

在「应用白名单」里移除不需要的应用，或添加常用应用和自定义 Bundle ID，再点击「保存白名单」。保存后立即生效，留空会阻止所有通过「打开 App」工具启动应用的请求。开启语音启动应用需要同时启用 `computer_butler_open_app`。Safari 打开网页、Finder 打开文件夹和写备忘录是独立工具，需分别管理开关。

「调用记录」会显示最近 200 条带工具执行结果的记录，包括调用时间、来源、工具、结果和耗时。它不会保存语音、执行参数、剪贴板、笔记、模型回复或凭据。应用规则与调用元数据存储在忽略提交的本机 `gateway/data/tool-access.sqlite3` 中。读取记录和修改白名单需要 Gateway Token。

### 本地与云端数据

| 数据 | 存放或传输方式 |
| --- | --- |
| Gateway Token、模型密钥、小智 MCP 地址 | 使用本机 `gateway/.env`。从控制面板保存的问答配置另存为 `gateway/data/model-qa.json`。均应视为明文凭据。 |
| 对话与记忆 | Gateway 按 `session_id` 保存在进程内存中。带工具对话可以把历史、部分 Mac 状态及工具结果发给所选模型。 |
| 原厂语音与 MCP | 音频和语音服务按设备的小智配置处理。小智调用工具时会传递请求参数并接收结果。 |
| 指定模型问答与查询 | 问答把本次问题文本发给指定服务商。天气会向 wttr.in 发送查询地点，公网 IP 工具会访问 ipify。 |
| 计时器、冒险与角色进度 | 计时器在 Gateway 内存中，重启会清空。游戏与角色状态默认保存在本机 `gateway/data/play.sqlite3`，同一安装共享。 |
| 记录与诊断 | LaunchAgent 等自行配置的运行日志可能包括请求信息。排查问题时，分享日志前应删除凭据、设备标识和私人内容。 |

不应通过语音传入密码、验证码等秘密。启用日历、提醒事项、剪贴板等工具前，请确认谁能对设备发出指令、结果可能被哪些服务处理。禁用一个开关无法撤回该工具此前已发送的数据。

## 接口与开发

FastAPI 的交互文档位于本机 `/docs`。受保护接口使用 `Authorization: Bearer <Gateway Token>`。

| 方法与路径 | 用途 |
| --- | --- |
| `GET /` 与 `GET /health` | 控制面板与健康状态，健康接口可查看当前工具清单。 |
| `POST /v1/chat` | 单次模型问答。 |
| `POST /v1/chat/tools` | 带工具的问答，包含会话上下文和调用记录。 |
| `POST /v1/tools/{name}` | 管理员直接调用指定工具。 |
| `GET /v1/tool-policy` 与 `PUT /v1/tool-policy` | 查看和保存授权。写入时尝试重启已配置的桥接 LaunchAgent。 |
| `GET /v1/app-allowlist` 与 `PUT /v1/app-allowlist` | 查看和保存允许启动的应用。 |
| `GET /v1/tool-activity` | 读取最近的工具调用元数据。 |
| `GET /v1/timers` 与 `DELETE /v1/timers/{timer_id}` | 列出和取消计时。 |
| `GET /v1/memory` 与 `DELETE /v1/memory/{session_id}` | 查看和清除会话记忆。 |
| `GET /v1/model-qa`、`PUT /v1/model-qa/{provider}`、`POST /v1/model-qa/{provider}/test` | 管理服务商配置，使用已保存配置试问。 |

`POST /v1/chat` 的响应示例：

```json
{"speech":"你好，我已经准备好了。","emotion":"happy","motion":"nod","extras":{}}
```

从仓库根目录运行测试：

```bash
cd gateway
source .venv/bin/activate
python -m pytest -q
```

实现位于 `gateway/stackchan_gateway/`。工具定义在 `tools.py`，macOS 操作封装在 `mac.py`，主模型和指定模型问答分别在 `providers.py` 与 `model_qa.py`，MCP 接入在 `mcp_tools.py` 与 `xiaozhi_bridge.py`。报告问题时，请提供 macOS 和设备固件版本、复现步骤，以及脱敏后的日志片段。

## 常见问题

| 现象 | 检查方式 |
| --- | --- |
| 打不开控制面板 | 检查 Gateway 是否启动及 8765 端口是否被占用。桥接默认通过这个本机端口共享计时器。 |
| 面板提示 Token 无效 | 确认输入的是 `STACKCHAN_GATEWAY_TOKEN` 的值，标签页关闭后需重新输入。更换 Token 后重启服务。 |
| 小智能聊天，但调用不了 Mac | 检查 MCP 地址与桥接日志，核对单项开关。手动启动桥接时，保存开关后需重启桥接，再发出具体指令。 |
| 桥接报 SOCKS 代理依赖错误 | 在虚拟环境里重新安装 `requirements.txt`，并检查本机代理设置。 |
| 备忘录、日历或 Spotify 执行失败 | 检查应用是否可用、是否已登录账户，以及「系统设置 → 隐私与安全性 → 自动化」中的实际运行进程权限。 |
| 模型试问失败 | 核对模型 ID、API Key、账户额度及网络。Ollama 还需要确认本地服务已启动且模型已安装。 |
| 番茄钟到点未听见 | 检查 Mac 音量、通知权限、睡眠和 Gateway 运行状态。重启、睡眠或进程退出都可能使提醒丢失或延迟。 |

## 免责声明与许可

本项目按现状提供，供个人实验和技术交流。在适用法律允许的范围内，维护者不就软件的准确性、可用性、安全性、兼容性、适销性、特定用途适用性或不侵权作出明示或默示保证，也不对因使用或无法使用本项目、第三方服务故障、自动化误操作、数据丢失或泄露、服务费用或设备异常造成的损失承担责任。适用法律规定不得排除或限制的责任不受此段影响。

语音识别、模型生成和工具调用都可能出错。使用者应自行核对执行结果、授权范围及第三方费用，并负责备份、网络访问控制和凭据管理。请仅在有权管理的设备、账户和数据上使用本项目。计时、通知和模型输出不得作为紧急、医疗或其他关键场景的唯一依据。

本项目与 M5Stack、小智及文中提到的服务商无隶属、赞助或官方支持关系。相关名称和商标归各自权利人。使用第三方服务或依赖须遵守其各自的服务条款、隐私政策和许可。

本仓库目前未声明单独的开源许可证。公示源码和本免责声明均不授予额外的使用、修改或分发权利。具体权利范围以适用法律、平台条款及权利人授权为准。参考 [GitHub 的仓库许可说明](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository)。
