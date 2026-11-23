# StackChan Mac Gateway

Mac-side AI Gateway for M5StackChan (ESP32-S3 / K151). The Mac is the brain; the
robot is the body. **Provider API keys never reach the ESP32 firmware.**

The Gateway now exposes:

- three switchable LLM providers (OpenAI, DeepSeek, MiniMax);
- a Mac-side registry of **37 tools**, with a safe subset available to the
  factory AI Agent via MCP and to the LLM through `POST /v1/chat/tools`;
- short-term conversation memory keyed by `session_id`;
- a focus timer with an automatic rest period and Mac notifications and speech;
- an on-demand time and weather briefing, with its own tool authorization;
- a tiny web dashboard at `GET /` for live status and quick testing;
- a TOML-free `.env`-driven setup that runs in mock mode without any keys.

Autonomous LLM/MCP access defaults to `safe`: playful tools and a small set of
non-sensitive status tools are exposed, while clipboard, calendar, reminders,
network identity and Mac control tools remain available only through the
authenticated direct-tool HTTP endpoint. Trusted installations can explicitly
set `STACKCHAN_AUTONOMOUS_TOOL_MODE=all` to expose all 37 tools autonomously,
or use `custom` with an exact comma-separated allowlist. The dashboard provides
an authenticated switch for every tool and writes this custom allowlist for you.

## Robot command contract

The XiaoZhi bridge sends timer operations to the local Gateway, so robot and
dashboard share the same timer list. Restarting the Gateway clears timers.
Notifications and speech come from the Mac; this bridge does not provide
proactive robot playback.

Every reply from `/v1/chat`, `/v1/chat/tools` and the underlying LLM path uses
the same shape:

```json
{
  "speech": "你好，我已经准备好了。",
  "emotion": "happy",
  "motion": "nod",
  "extras": {}
}
```

`emotion` and `motion` are open enums. v0.3 expands both:

- **Emotion**: `neutral`, `happy`, `sad`, `surprised`, `thinking`, `excited`,
  `sleepy`, `confused`, `proud`, `love`, `wink`, `dizzy`, `embarrassed`,
  `angry`.
- **Motion**: `none`, `nod`, `shake`, `look_left`, `look_right`, `bounce`,
  `nod_slow`, `nod_fast`, `shake_slow`, `tilt_left`, `tilt_right`, `bow`,
  `wave`, `look_up`, `look_down`, `dance`, `hop`, `head_circle`, `peek`.

Unknown values are ignored by current firmware. `extras` is an optional hint
bag for forward compatibility.

## Install

```bash
cd gateway
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` locally. Do not paste provider keys into firmware or chat messages.

## Select a provider

DeepSeek:

```env
STACKCHAN_LLM_PROVIDER=deepseek
STACKCHAN_LLM_MODEL=deepseek-chat
DEEPSEEK_API_KEY=your-key
```

MiniMax:

```env
STACKCHAN_LLM_PROVIDER=minimax
STACKCHAN_LLM_MODEL=MiniMax-M3
MINIMAX_API_KEY=your-key
```

OpenAI:

```env
STACKCHAN_LLM_PROVIDER=openai
STACKCHAN_LLM_MODEL=gpt-5.4-mini
OPENAI_API_KEY=your-key
```

Only the selected provider's key is required. Restart the Gateway after changing
the provider or model.

## Run

Mock mode (no API calls):

```bash
source .venv/bin/activate
STACKCHAN_MOCK_MODE=true uvicorn app:app --host 0.0.0.0 --port 8765
```

Live mode:

```bash
source .venv/bin/activate
STACKCHAN_MOCK_MODE=false uvicorn app:app --host 0.0.0.0 --port 8765
```

Open `http://localhost:8765/` for the dashboard and enter the Gateway Token in
the password field. It is kept in the current tab's session storage and removed
when the tab is closed.

## macOS background services

This installation uses two user LaunchAgents so the HTTP Gateway and XiaoZhi
MCP bridge start automatically at login and restart after an unexpected exit:

- `~/Library/LaunchAgents/com.stackchan.gateway.plist`
- `~/Library/LaunchAgents/com.stackchan.xiaozhi-bridge.plist`

Runtime logs are written under `gateway/logs/` and are excluded from Git.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/health` | Provider, model, mode, configured, full and autonomous tool inventories |
| `POST` | `/v1/chat` | One-shot reply (`{speech,emotion,motion,extras}`) |
| `POST` | `/v1/chat/tools` | LLM may call Mac tools, then returns the same reply + tool log |
| `POST` | `/v1/tools/{name}` | Call a single tool directly |
| `GET` | `/v1/tool-policy` | Read the per-tool autonomous allowlist |
| `PUT` | `/v1/tool-policy` | Save switches and reload the XiaoZhi MCP bridge |
| `GET`  | `/v1/timers` | List active timers |
| `DELETE` | `/v1/timers/{timer_id}` | Cancel a timer |
| `GET`  | `/v1/memory` | List session memory |
| `DELETE` | `/v1/memory/{session_id}` | Forget a session |
| `GET`  | `/` | Web dashboard |

All non-`/health` and non-`/` routes require
`Authorization: Bearer ${STACKCHAN_GATEWAY_TOKEN}` when the token is set.

## Tool inventory

37 tools registered through a single source of truth
(`stackchan_gateway.tools`). Three categories:

### Info (read-only)

`computer_butler_time`, `computer_butler_status`, `computer_butler_battery`,
`computer_butler_volume_get`, `computer_butler_clipboard_read`,
`computer_butler_network`, `computer_butler_frontmost`,
`computer_butler_calendar_today`, `computer_butler_reminders`,
`computer_butler_spotify_now`, `computer_butler_weather`,
`computer_butler_timer_list`.

### Control (side effects)

`computer_butler_volume_set`, `computer_butler_volume_mute`,
`computer_butler_clipboard_write`, `computer_butler_open_app`,
`computer_butler_spotify_control`, `computer_butler_timer_set`,
`computer_butler_timer_cancel`, `computer_butler_speak`,
`computer_butler_sound`, `computer_butler_pomodoro`.

### Playful (no Mac state)

`computer_butler_roll_dice`, `computer_butler_coin_flip`,
`computer_butler_lottery`, `computer_butler_pick_one`,
`computer_butler_joke`, `computer_butler_fortune`,
`computer_butler_color`, `computer_butler_riddle`,
`computer_butler_truth_or_dare`, `computer_butler_story_starter`,
`computer_butler_math`, `computer_butler_unit_convert`,
`computer_butler_emoji_mood`, `computer_butler_word_of_day`,
`computer_butler_countdown`.

Every enabled autonomous tool is exposed via MCP and to the LLM tool-calling
loop with the same parameter schema and description. In the default `safe`
mode this is a 20-tool subset. Set `STACKCHAN_AUTONOMOUS_TOOL_MODE=all` only
when voice users and the upstream MCP/LLM services are trusted.

The dashboard's switches use `custom` mode. For example, enabling only App
launching in addition to the safe defaults adds `computer_butler_open_app`
without exposing clipboard, calendar or reminder data.

## Voice command examples (Chinese)

- 实用:「现在几点了」「Mac 还剩多少电」「今天天气怎么样」「把音量调到 40」
  「三分钟后提醒我」「帮我把这串文字复制到剪贴板」「列出今天日历」
  「打开 Spotify」「让 Mac 自己说『准备开会了』」。
- 可玩性:「掷两个八面骰」「从火锅日料烤肉里帮我选一个」
  「(3+5)*2 是多少」「100 摄氏度换算华氏度」「给我讲个笑话」
  「抽个签」「出一个谜语」「真心话」「随机挑个颜色给我看看」
  「今日一词」「距离 2027 年还有多久」「开始一个 25 分钟番茄钟」。

## System prompt enrichment

When `/v1/chat/tools` is used, the LLM system prompt is prepended with a live
Mac snapshot (provider/model/mode, battery percent, frontmost app). The same
prompt is augmented with the last few turns of the same `session_id` so the
robot can do short multi-turn chats without being re-prompted from scratch.

## Connect the factory AI Agent through XiaoZhi MCP

The factory firmware, voice pipeline, mobile app, and OTA updates all keep
working. The Mac advertises the same 37 tools to the factory AI Agent:

1. Open StackChan World and go to `Settings > MCP` for this StackChan.
2. Copy the full `wss://api.xiaozhi.me/mcp/?token=...` access-point address.
3. Put it in the local `.env`:

```env
XIAOZHI_MCP_ENDPOINT=wss://api.xiaozhi.me/mcp/?token=your-secret-token
```

4. Start the bridge from the `gateway` directory:

```bash
source .venv/bin/activate
python -m stackchan_gateway.xiaozhi_bridge
```

## 用语音操作 Mac 上的应用

在「Jammy 功能开关」中分别启用所需工具。例如：

- `computer_butler_open_website`：“用 Safari 打开 apple.com。”
- `computer_butler_open_folder`：“在 Finder 打开下载文件夹。”也支持桌面、文稿、应用程序和个人文件夹。
- `computer_butler_create_note`：“新建一条备忘录，标题是待办，正文是周五交报告。”新笔记使用备忘录的默认账户，macOS 可能会要求授予自动化权限。

## 多模型问答

在本机控制面板输入 Gateway Token，打开「多模型问答」。选择服务商，填入该账号可用的模型 ID 和 API Key，保存后点击「试问已保存模型」。通义千问预设使用中国内地 DashScope。Ollama 使用本机 11434 端口，模型 ID 填本机已安装的标签，无需密钥。

在「Jammy 功能开关」启用 `ask_model` 后，可说「请用 Gemini 回答，为什么天空是蓝色的」。小智负责识别语音和调用工具，指定模型收到本次问题的文本并返回答案。控制面板的试问不依赖这个开关。

问答配置存放在 Mac 的 `data/model-qa.json`，保存后立即生效。API Key 留空会沿用已保存的值或现有的服务商环境变量，面板不会回显密钥。问答使用独立的模型配置。

## Tests

```bash
source .venv/bin/activate
pytest -q
```

69 tests cover config, providers (with and without tools), the FastAPI app,
the MCP inventory, the tool registry, the conversation memory, and a mock
LLM round trip through the tool-calling loop.
