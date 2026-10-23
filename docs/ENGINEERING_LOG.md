# V2V 工程日志

> 记录时间范围：2026-10-11 ~ 2026-10-14（2026-10-14 更新）
> 目标：让 `v2v` 视频字幕翻译流水线端到端跑通（下载 → 提取音频 → Whisper 转录 → 翻译 → 烧录双语字幕），过程有进度、失败可恢复、主动修复而非等提醒。

---

## 1. 环境与约束

| 项 | 值 |
|---|---|
| OS | macOS 13.7.8 (arm64)，非 Docker |
| Python | 3.12，venv 在 `.venv/`（**系统默认 `python` 不是它，必须 `.venv/bin/python` 或 `./v2v`**） |
| 网络（被墙） | `x.com` / `api.x.com` / `video.twimg.com` / `translate.google.com`（DNS 污染到 103.252.115.49，TCP 443 不通） |
| 网络（通） | `huggingface.co`（模型下载正常） |
| 源视频 | `input/1.mp4` 434MB / 26.8min；`input/2.mp4` 音频 50257KB / 26:48（均为 YouTube TED 演讲类，英语单声道） |
| 成品 | `output/1_subtitled_zh.mp4` 327MB，双语 385 条，2026-10-11 18:25 产出 |

---

## 2. 时间线

| 时间 | 事件 |
|---|---|
| 10-11 | graphify 知识图谱跑通（`graphify-out/`，296 节点/611 边/12 社区） |
| 10-11 | 发现 Whisper 启动即崩 → 根因 av 19.0 API 破坏，`requirements.txt` pin `av>=11,<19` |
| 10-11 | 发现只出 1 条字幕 → 根因音轨双声道反相，下混成静音 → `_downmix_to_mono()` 修复 |
| 10-11 | 发现 ASR 7 分钟黑屏 → 加逐段进度日志 `ASR x% [m:ss/m:ss] text` |
| 10-11 | 发现 Google 翻译全失败（网络墙）→ 确认 `.env` 切 `TRANSLATION_ENGINE=marian`，marian 离线正常 |
| 10-11 18:11-18:25 | 一次性续跑脚本 `/tmp/v2v_resume.py`：复用 373 段转录 → marian 翻译 385 段（约 2min）→ 烧录 5.5min → `output/1_subtitled_zh.mp4` 产出，双语抽查通过 |
| 10-11 | 修引擎降级链 + Google 3 连败熔断（17~23 秒熔断，不再 40 分钟） |
| 10-11 | 修 CLI 四阶段缓存 + `--force` + 0 字幕硬退出 → 全缓存命中 **1.3 秒**（原 12min+） |
| 10-11 19:14 | `input/2.mp4` 跑通（反相警告生效、ASR 进度正常）；用户提出**分句按字符定长导致翻译单元破碎**（见 §5.1，未修） |
| 10-14 01:27 | 补 `SUBTITLE_FONT` 字体家族配置（见 §3.6） |

**踩坑记录**：曾同时跑两个 `./v2v` 进程共用 `temp/1/`、`output/` 互相覆盖（已杀）；macOS 无 `cat -A`；zsh glob 不匹配点开头文件名。

---

## 3. 已完成修复（含验证证据）

### 3.1 依赖：`av` 19.0 破坏 faster-whisper
- **现象**：`av.open(metadata_errors=...)` TypeError，转录无法启动。
- **修复**：`requirements.txt` → `av>=11,<19`（当前装 18.1.0），注释说明原因。
- **验证**：`decode_audio` + `transcribe` 正常；`pip check` 无冲突。

### 3.2 反相音轨静音下混（只出 1 条字幕的根因）
- **现象**：`(L+R)/2` 得 -66.8dB（静音），单声道 -28.4dB；L≈−R，ffmpeg 默认下混互相抵消。
- **修复**：`app/services/audio_extractor.py` — ffmpeg 改 `-ac 2`，新增 `_downmix_to_mono()`：检测到反相（`rms((L+R)/2) < 0.5*max(rmsL,rmsR)`）取较响声道并 WARNING；全带均值 < -40dBFS 再 WARNING。
- **验证**：1.mp4 / 2.mp4 均打出警告并产出 -28.4dB 正常音轨。

### 3.3 ASR 进度（消除 7 分钟黑屏）
- **修复**：`app/services/speech_to_text.py` 新增 `_format_time()`，逐段打 `ASR 0.2% [0:00/0:30] We'll be right back.`。
- **验证**：2.mp4 日志每 6 秒滚动一条进度（见用户 10-11 19:14 贴的日志）。

### 3.4 翻译引擎自动降级 + Google 熔断（问题"为什么不自动切离线"）
- **现象**：网络墙导致 Google 不通；旧代码失败后**静默返回原文**（`translator.py`），40 分钟后交付未翻译字幕。
- **修复**：`translator.py`
  - 降级链：配置引擎 → `marian`（离线）→ `google`；
  - Google 连续 3 段失败抛 `TranslationError`（熔断），改用指数退避；
  - 段数校验：引擎返回数 ≠ 输入段数视为失败（顺带兜住 NLLB 错位老 bug）。
- **验证**：强制 `TRANSLATION_ENGINE=google`，4 段测试 → 23 秒内 `Engine 'google' failed ... fell back to 'marian'`，译文正常出中文。

### 3.5 断点续跑 / 阶段缓存（问题"音频字幕反复处理"）
- **修复**：`v2v` CLI `process_video()` 四阶段按 mtime 缓存，新增 `--force` 全量重跑：
  1. 提取音频：`*_audio.wav` ≥ 视频 mtime → 跳过
  2. 转录：`*_segments.json` + `language.txt` ≥ 音频 mtime → 跳过
  3. 翻译：`translated.srt` + `translated.engine.txt`（= 当前引擎）≥ segments mtime → 跳过；换引擎自动重译
  4. 烧录：成品 ≥ `translated.srt` mtime → 跳过
  5. 0 段字幕直接 `SystemExit` 报错，不再假 "✅ 完成"
- **验证**：`./v2v ./input/1.mp4 --to zh` 四项全部"缓存命中"，**1.315 秒**完成。

### 3.6 字体配置（问题"怎么改字号/字体，为什么没配置文件"）
- **答**：有配置文件 = **`.env` + `app/config/settings.py`**。字号/颜色原本就有（`.env: SUBTITLE_FONT_SIZE=48`，彩色字幕描边等 4 项），**唯独字体家族是硬编码的**，已补：
- **修复**：
  - `settings.py` 新增 `SUBTITLE_FONT`（字体文件路径 .ttf/.ttc/.otf，留空 = 自动探测）
  - `video_burner.py::_load_font()` 让 `SUBTITLE_FONT` 排在候选首位（PingFang → STHeiti → Helvetica）
  - `.env` / `.env.example` / `AGENTS.md` 同步补文档
- **验证**：`SUBTITLE_FONT=/System/Library/Fonts/Helvetica.ttc` → `Loaded font: ...Helvetica.ttc`；不设则仍自动 PingFang。
- **用法速查**（改完 `.env` 重启服务或直接跑 CLI）：
  ```bash
  SUBTITLE_FONT=/System/Library/Fonts/STHeiti Medium.ttc   # 换字体
  SUBTITLE_FONT_SIZE=48                                    # 字号（1080p 基准，会按分辨率缩放）
  SUBTITLE_FONT_COLOR=white / SUBTITLE_OUTLINE=2           # 颜色/描边
  ```

---

## 4. 性能与质量数据

| 阶段 | 实测（1080p，26.8min，medium/int8，CPU） |
|---|---|
| 提取音频 | 秒级 |
| Whisper 转录 | 约 7min17s |
| marian 翻译 385 段 | 约 2min |
| 烧录双语 | 约 5.5min |
| 全缓存命中重跑 | **1.3s** |
| Google 熔断→降级 | 17~23s（旧逻辑 40min 且产出错译） |

质量抽检（marian en→zh，opus-mt）：
- ✅ `There are great ruins all across the world.` → `世界各地都有巨大的废墟。`
- ❌ `The Acropolis is different.` → `共产主义是不同的。`（本地小模型质量上限，见 §5.5）

---

## 5. 遗留问题（按优先级，均未修）

### 5.1 分句按固定字符阈值切分（用户 10-11 19:14 提出，**待修**）
`subtitle_generator.py::split_long_segments()` 当前逻辑：`min_split≈80` 字符以下不切、合并到 `max_chars≈60` 字符就强行断行，**先看字符数再看句子**，会把句子拦腰截断，导致翻译单元破碎。
**计划方向**：以句末标点（。！？.!?\n 及逗号兜底）为唯一合法切点，先按句子聚类成段；字符数只做"单句仍超上限"的最后兜底硬切，且优先在句内停顿词处断。改完需对比 1/2 两段视频的双语效果。

### 5.2 其余遗留清单
| # | 问题 | 位置/依据 |
|---|---|---|
| a | 下载策略 3 用 `worst[ext=mp4]`，可能拿到坏/烂音轨 | `video_downloader.py:97` |
| b | 并发无锁：同文件两进程互相覆盖 `temp/`、`output/`（已实际发生） | `v2v` / `pipeline.py` |
| c | 服务端 `pipeline.py` 已接 split/dual（commit `ac4dade`），但**仍无阶段缓存与 ASR 进度**（仅 CLI 有）；AGENTS.md "Known Inconsistencies" 该条已过时待更正 | `pipeline.py:169-221` |
| d | `download_from_url` 向 SQLite 写占位路径后不更新；早期阶段进度不落库；内存失败路径不持久化状态 | AGENTS.md |
| e | marian en→zh（opus-mt）质量不稳定（§4 误译例）；如需高质量需加本地 LLM 引擎（Ollama/兼容端点，尚未确认用户本地运行时） | 待用户确认 |
| f | NLLB 空文本跳过导致返回数 < 段数（根因未修，现由 §3.4 段数校验兜底为降级而非错位） | `translator.py::_translate_nllb` |
| g | X API 被墙，`./v2v <url>` 下载失败；绕过：`yt-dlp --downloader curl_cffi` 偶发 / 直接用 `input/` 本地文件 | 本次全程 |

---

## 6. 本次会话改动文件清单

| 文件 | 改动 |
|---|---|
| `requirements.txt` | `av>=11,<19` pin |
| `app/services/audio_extractor.py` | `-ac 2` + `_downmix_to_mono()` 反相/静音检测 |
| `app/services/speech_to_text.py` | `_format_time()` + 逐段 ASR 进度日志 |
| `app/services/translator.py` | 引擎降级链、Google 熔断、段数校验 |
| `v2v`（CLI） | 四阶段 mtime 缓存、`--force`、0 字幕硬退出 |
| `app/config/settings.py` | 新增 `SUBTITLE_FONT` |
| `app/services/video_burner.py` | `_load_font()` 支持自定义字体路径 |
| `.env` / `.env.example` / `AGENTS.md` | `SUBTITLE_FONT` 文档同步 |
| `docs/ENGINEERING_LOG.md` | 本文件（新建） |

**未提交 git**（按约定仅在明确要求时 commit）。

---

## 7. 下一步（建议顺序）

1. **修 §5.1 分句逻辑**（用户已明确要求）：句子边界优先，字符数只兜底；用 1.mp4/2.mp4 前 3 分钟对比翻译质量。
2. 并发锁（同任务文件加 lock 标记，防双进程覆盖）。
3. `worst[ext=mp4]` 回退加音轨健康检查（下混后 dBFS 阈值已有的 WARNING 升级为重试/换策略）。
4. 服务端 `pipeline.py` 接入缓存与进度，消除 CLI/服务端行为分叉。
5. （待确认）本地 LLM 翻译引擎：`TRANSLATION_ENGINE=llm`，需用户告知本地模型运行时（Ollama / LM Studio / OpenAI 兼容端点）。
