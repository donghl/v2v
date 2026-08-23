# V2V 处理逻辑文档

## 整体架构

```
浏览器 / curl / v2v CLI
    │
    ▼
┌────────────────────────────────────────────────┐
│  FastAPI routes (app/api/routes.py)            │
│  /upload  /process  /process-all  /download-url │
│  /status  /download  /history  /subscriptions   │
│  /   /health                                   │
└──────────────┬─────────────────────────────────┘
               │ background thread (daemon)
               ▼
┌────────────────────────────────────────────────┐
│  pipeline.run_pipeline(task_id)                │
│  ① 提取音频  ② [人声分离]  ③ 转录             │
│  ④ 生成SRT   ⑤ 翻译       ⑥ 烧录字幕          │
└──────────────┬─────────────────────────────────┘
               │ 双写状态
               ▼
┌──────────────────────────────┐  ┌──────────────────────────────┐
│ 内存 _tasks (pipeline.py)    │  │ SQLite v2v.db (database.py)  │
│  /status /download 使用      │  │ /history 使用                │
│ 重启丢失                     │  │ 持久保留                     │
└──────────────────────────────┘  └──────────────────────────────┘
```

另外两条独立路径：

- **`v2v` CLI**（项目根目录）：复用 `audio_extractor / speech_to_text / translator / subtitle_generator / video_burner`，但**自己编排流程**（不调用 `run_pipeline`），且额外调用了 `split_long_segments`（长句切分）。
- **订阅调度器**（`subscription_checker.py`）：后台线程，按 `SUBSCRIPTION_CHECK_INTERVAL` 轮询频道 → 下载新视频 → 提交任务到 pipeline。

## API 接口

| 方法 | 路径 | 功能 | 输入 |
|------|------|------|------|
| POST | `/upload` | 上传文件并处理 | 文件 + target_language |
| POST | `/process` | 处理 input/ 里已有的文件（不复制） | filename + target_language |
| POST | `/process-all` | 顺序批量处理 input/ 全部待处理文件（跳过已输出/`_subtitled_` 文件） | target_language |
| POST | `/download-url` | 下载 URL 视频并处理（支持播放列表，只处理第一个） | url + target_language |
| GET | `/status/{id}` | 查询任务实时状态（内存） | task_id |
| GET | `/download/{id}` | 下载处理结果 | task_id |
| GET | `/history` | 任务历史（SQLite，分页） | limit / offset |
| GET | `/history/{id}` | 单任务记录（SQLite） | task_id |
| POST | `/subscriptions` | 添加频道订阅 | channel_url + platform + target_language |
| GET | `/subscriptions` | 订阅列表 | — |
| DELETE | `/subscriptions/{id}` | 删除订阅 | sub_id |
| POST | `/subscriptions/{id}/check` | 手动触发一次频道检查 | sub_id |
| GET | `/` | Web 操作界面 | — |
| GET | `/health` | 健康检查 | — |

### `/upload` vs `/process` 的区别

- `/upload`：接收上传的文件流 → 写入 `input/` → 处理。**同名文件会复制出 `_1` `_2` 副本**
- `/process`：直接使用 `input/` 里已存在的文件 → 处理。**不复制文件**

批量处理应该用 `/process` 或 `/process-all`，避免在 input/ 里制造副本。

## 处理流程

```
输入视频 (input/xxx.mp4)
    │
    ▼
┌──────────────────────────────────────────────────────┐
│ Step 1: 提取音频                                      │
│   ffmpeg -i input.mp4 -vn -ac 1 -ar 16000 output.wav │
│   → temp/{task_id}/xxx_audio.wav                     │
│   进度: 5% → 15%                                     │
└──────────────┬───────────────────────────────────────┘
               │
               ▼ (if USE_VOCAL_SEPARATION=true)
┌──────────────────────────────────────────────────────┐
│ Step 1.5: 人声分离 (Demucs)                           │
│   Demucs 输出: 44.1kHz stereo vocals.wav              │
│   ffmpeg 转换: → 16kHz mono WAV                      │
│   进度: 15% → 25%                                     │
└──────────────┬───────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────┐
│ Step 2: 语音识别 (faster-whisper)                     │
│   输出: [{start, end, text}, ...] + detected_lang     │
│   写入: temp/{task_id}/xxx_segments.json              │
│   进度: 25% → 45%                                     │
│   (if USE_CROSS_VALIDATION=true)                      │
│     额外运行 OpenAI Whisper → 时间对齐 + 共识合并      │
│     置信度 < 0.1 时回退到单引擎                        │
└──────────────┬───────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────┐
│ Step 3: 生成原始字幕                                  │
│   segments → SRT                                     │
│   写入: temp/{task_id}/original.srt                   │
│   进度: 45% → 55%                                     │
└──────────────┬───────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────┐
│ Step 4: 翻译字幕                                      │
│   引擎: google / marian / nllb (TRANSLATION_ENGINE)   │
│   进度: 55% → 75%                                     │
│   (if USE_CROSS_VALIDATION=true)                      │
│     额外运行 1 个替代引擎 → 共识评分选最佳              │
│   同语言 (source==target) 跳过翻译                     │
└──────────────┬───────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────┐
│ Step 5: 生成翻译字幕                                  │
│   写入: temp/{task_id}/translated.srt                 │
│   进度: 75% → 80%                                     │
└──────────────┬───────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────┐
│ Step 6: 烧录字幕 (video_burner.py)                    │
│   每条字幕渲染成透明 PNG (Pillow)                     │
│   ffmpeg overlay 链式叠加 enable='between(t,s,e)'     │
│   输出: output/{stem}_subtitled_{lang}.mp4            │
│   进度: 80% → 98% → 100%                              │
└──────────────┬───────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────┐
│ 清理: 删除 temp/{task_id}/，源视频保留                 │
│ 写库: status=completed, output_path, completed_at      │
└──────────────────────────────────────────────────────┘
```

## 关键配置

| 配置项 | 默认值 | 作用 |
|--------|--------|------|
| `WHISPER_MODEL` | `medium`（`.env.example`: large-v3） | 模型大小 |
| `TRANSLATION_ENGINE` | `marian`（`.env.example`: google） | 翻译引擎 |
| `USE_VOCAL_SEPARATION` | `false`（`.env.example`: true） | 是否分离背景音乐 |
| `USE_CROSS_VALIDATION` | `false` | 是否双引擎交叉验证 |
| `DUAL_SUBTITLES` | `false` | 双语字幕（**仅 CLI 生效**） |
| `SUBSCRIPTION_CHECK_INTERVAL` | 3600s | 订阅轮询间隔 |

## 目录结构约定

| 目录 | 用途 | 清理策略 |
|------|------|---------|
| `input/` | 源视频文件 | 不自动清理 |
| `output/` | 处理结果 `*_subtitled_{lang}.mp4` | 不自动清理 |
| `temp/{task_id}/` | 单个任务的中间文件 | 处理完成后自动删除 |
| `models/` | Whisper/Demucs 模型 | 不清理 |
| `v2v.db` | SQLite 任务/订阅库 | 不清理（git 忽略） |

## 任务状态机

```
waiting → extracting_audio → transcribing → translating → burning_subtitles → completed
                                                                             ↘ failed
```

内存与数据库双写：早期阶段（extracting_audio 5–15%）只写内存；transcribing 起才写库。

## 已知逻辑问题

### 已修复
- [x] Demucs 输出 44.1kHz stereo → 转 16kHz mono
- [x] `/upload` 重复复制 → 新增 `/process`
- [x] 跨验证低置信度混入垃圾 → 置信度 < 0.1 自动回退
- [x] 翻译语言代码映射 (zh→zh-CN for Google)
- [x] `_calculate_transcription_confidence` 函数重复定义
- [x] 失败任务不清理 temp/
- [x] 输出文件名冲突 → 自动加 `_1`, `_2` 后缀
- [x] 同语言跳过翻译
- [x] 启动崩溃（`SUPPORTED_DOMAINS` 导入）→ 已移除导入
- [x] `download_video` 改返回 list 后调用方解包崩溃 → 已适配

### 待处理

| # | 问题 | 影响 | 优先级 |
|---|------|------|--------|
| 1 | 订阅检查用 Python `yt_dlp` 库（与 CLI 下载策略不一致），flat 模式下 `entry["url"]` 是裸视频 ID，直接交给 `download_video` 会失败 | 订阅下载大概率失败 | **高** |
| 2 | `_translate_nllb` 过滤空文本后返回数量可能少于输入 → `zip` 静默丢段 | 翻译错位 | **高** |
| 3 | `download_from_url` 落库的是占位路径，下载完成后未更新 DB | /history 显示假路径 | 中 |
| 4 | `DUAL_SUBTITLES` / `split_long_segments` 只在 CLI 生效，服务器 pipeline 未接线 | 功能不一致 | 中 |
| 5 | 双数据源（内存 + SQLite）靠散落的 update_task 手动同步，失败路径（routes.py 的下载/检查）只改内存不写库 | 状态漂移 | 中 |
| 6 | 无并发限制，同时跑多个任务可能 OOM | 服务器崩溃 | 高 |
| 7 | 进度长时间不动（Demucs/转录/翻译期间无更新） | 用户以为卡住 | 中 |
| 8 | 长段切分的时间戳按字符数等比推算（非真实停顿） | 字幕与语音可能错位 | 低 |
| 9 | 订阅调度器线程无停止机制（daemon，仅随进程退出） | 测试/重启时行为不可控 | 低 |
