# class-helper 代课 bot

大学课堂的听课值守者：你坐在教室里水课自习，**手机收音、宿舍电脑处理**。
它实时转写老师讲课内容自动归档成复习笔记；一旦老师点名提问（或点到你），
几秒内在手机上弹出**问题原文 + 可直接口头作答的速答要点**，看一眼就能从容回答。

```
[Android App 前台服务收音]        [宿舍 PC 服务端 (Python/uv)]
 选课程/开始下课                    Faster-Whisper 滚动转写(GPU自动探测)
 转写流/告警卡/速答卡   ──WS──▶     silero-vad 分段
 「我被点了」大按钮     ◀──事件──    关键词预筛 + LLM 分级(preparing/called)
 课程归档浏览(md)                  LLM 速答(≤5要点) + 增量笔记 + 课后总结
```

## 组成

| 部分 | 技术 | 说明 |
|---|---|---|
| `src/class_helper/` | Python + uv | 服务端：WS 服务、VAD、ASR、检测/速答、归档 |
| `app/` | Flutter | Android 客户端：前台服务录音、UI、通知 |
| `archives/<课程>/<日期>/<时刻>/` | — | transcript.jsonl、notes.md、session_summary.md、qa.md、audio.wav |

## 服务端部署（宿舍 PC）

要求：Windows/Linux，Python 3.11+，[uv](https://docs.astral.sh/uv/)。
GPU 非必需（无 GPU 自动回退 CPU int8，small 模型单核实时转写无压力）。

```bash
# 1. 安装依赖
uv sync

# 2. 配置
cp config.example.toml config.toml
#   填入 llm.base_url / api_key（任意 OpenAI 兼容服务：智谱/DeepSeek/Kimi/Qwen…）
#   修改 server.token（App 连接口令）
#   detection.aliases 改成你的名字/称呼

# 3. Windows 防火墙放行（局域网直连必需）
netsh advfirewall firewall add rule name="class-helper" dir=in action=allow protocol=TCP localport=8765

# 4. 启动
uv run class-helper serve
#   终端会打印 ws://<局域网IP>:8765/ws?token=... 连接信息
```

首次开始上课时会自动下载 Whisper 模型（GPU 默认 medium ≈1.5GB / CPU 默认 small ≈460MB），
建议课前完成一次试运行。GPU 需要 CUDA 12 + cuDNN 9 环境（可
`uv pip install nvidia-cublas-cu12 nvidia-cudnn-cu12` 提供运行库）；折腾不动就保持 CPU。

### 课后补归档（手机录音机文件）

```bash
uv run class-helper import 手机录音.m4a --course 计算机网络
```

走同一套 ASR+笔记管道生成 notes.md 与 session_summary.md（该途径无实时速答）。

## 手机端安装

1. **云构建（推荐，本机零环境）**：把仓库推到 GitHub，Actions 页触发
   `build-apk` workflow，从 Artifacts 下载 `class-helper-apk` 安装。
2. **本机构建**：装 Flutter SDK ≥3.24 + Android SDK，`cd app && flutter build apk --release`。

App 使用：

1. 首页填 `host:port` 与 token（PC 启动时终端打印）→ 连接并进入课堂。
2. 输入课程名 → 「开始上课」→ 前台服务开始收音，之后**可随便切后台/锁屏玩手机**。
3. 老师宣布要点名时弹出黄色预警条；点到具体人时红色卡片显示问题与速答要点并响铃。
4. 检测漏了就按红色大按钮「我被点了！」，立刻对最近两分钟内容出速答。
5. 「下课」后自动生成复习总结；归档页可直接看笔记。

> 首次使用请**关闭本应用的电池优化**（国产 ROM 杀后台），并允许通知权限。

## 组网

- **校园网直连**：手机与 PC 能互 ping 即可用（注意部分校园网教学区与宿舍网段隔离）。
- **Tailscale（备选，推荐常备）**：PC 与手机都装 Tailscale 登录同一账号，
  App 里地址改成 PC 的 Tailscale IP（100.x.x.x:8765）即可，代码零改动。

## 配置速查（config.toml）

| 节 | 关键项 | 说明 |
|---|---|---|
| server | port / token | App 连接口令，务必改掉默认值 |
| llm | base_url / api_key | OpenAI 兼容；detection_model 建议免费小模型，answer_model 用强模型 |
| asr | model / device | auto 自适应；显式指定 tiny/small/medium/large-v3 |
| detection | aliases / trigger_words | 你的别名单与点名触发词，命中别名免 LLM 直接判 called |
| archive | keep_audio / summary_interval_* | 原始音频保留与否、增量小结阈值 |

## 工作原理与边界

- 检测链路：新转写先本地关键词/别名预筛（零成本），命中才让 LLM 在
  none / preparing（准备点名）/ called（已点到）三级里判定，30 秒冷却防刷屏。
- 音频断线补偿：App 按 4 字节序号推流，断线期间音频缓存在手机，
  重连后按服务端 ACK 补传，缺口自动对齐。
- 老师用手机麦克风收音，教室后排/嘈杂环境转写质量会下降；
  判 called 需要点到具体称呼，只说"谁来回答一下"算 preparing（黄色静默预警，不响铃）。
- 课堂录音仅供个人学习使用，请遵守任课教师的课堂规定，勿用于处分他人或传播。

## 开发

```bash
uv run pytest          # 单元测试（假 ASR/LLM 全链路）
uv run ruff check .    # 静态检查
```
