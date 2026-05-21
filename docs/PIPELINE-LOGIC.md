# V2V 处理逻辑文档

## 整体架构

```
浏览器 / curl
    │
    ▼
┌─────────────────────────────────────────────┐
│  FastAPI routes (app/api/routes.py)         │
│  /upload   /process   /download-url         │
│  /status   /download  /       /health       │
└──────────────┬──────────────────────────────┘
               │ background thread
               ▼
┌─────────────────────────────────────────────┐
│  pipeline.run_pipeline(task_id)             │
│  ① 提取音频  ② [人声分离]  ③ 转录          │
│  ④ 生成SRT   ⑤ 翻译       ⑥ 烧录字幕       │
└─────────────────────────────────────────────┘
```

## API 接口

| 方法 | 路径 | 功能 | 输入 |
|------|------|------|------|
| POST | `/upload` | 上传文件并处理 | 文件 + target_language |
| POST | `/process` | 处理 input/ 里已有的文件 | filename + target_language |
| POST | `/download-url` | 下载 URL 视频并处理 | url + target_language |
| GET | `/status/{id}` | 查询任务状态 | task_id |
| GET | `/download/{id}` | 下载处理结果 | task_id |
| GET | `/` | Web 操作界面 | — |
| GET | `/health` | 健康检查 | — |

### `/upload` vs `/process` 的区别

- `/upload`：接收上传的文件流 → 写入 `input/` → 处理。**同名文件会复制出 `_1` `_2` 副本**
- `/process`：直接使用 `input/` 里已存在的文件 → 处理。**不复制文件**

批量处理应该用 `/process`，避免在 input/ 里制造副本。

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
│   输入: 16kHz mono WAV                                │
│   Demucs 输出: 44.1kHz stereo vocals.wav              │
│   ffmpeg 转换: → 16kHz mono WAV                      │
│   → temp/{task_id}/vocals_16k.wav                    │
│   进度: 15% → 25%                                     │
└──────────────┬───────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────┐
│ Step 2: 语音识别 (faster-whisper)                     │
│   输入: 16kHz mono WAV                                │
│   输出: [{start, end, text}, ...] + detected_lang     │
│   写入: temp/{task_id}/xxx_segments.json              │
│   进度: 25% → 45%                                     │
│                                                       │
│   (if USE_CROSS_VALIDATION=true)                      │
│     额外运行 OpenAI Whisper                            │
│     时间对齐 + 共识合并两引擎结果                         │
│     置信度 < 0.1 时回退到单引擎                         │
│     写入: temp/{task_id}/xxx_validation.json           │
│   进度: 25% → 45%                                     │
└──────────────┬───────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────┐
│ Step 3: 生成原始字幕                                  │
│   segments → SRT 格式                                 │
│   写入: temp/{task_id}/original.srt                   │
│   进度: 45% → 55%                                     │
└──────────────┬───────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────┐
│ Step 4: 翻译字幕                                      │
│   翻译引擎: google / marian / nllb (TRANSLATION_ENGINE)│
│   输入: [{start, end, text}] + source_lang + target    │
│   输出: [{start, end, translated_text}]                │
│   进度: 55% → 75%                                     │
│                                                       │
│   (if USE_CROSS_VALIDATION=true)                      │
│     额外运行 1 个替代翻译引擎                            │
│     共识评分选最佳翻译                                   │
│     写入: temp/{task_id}/translation_validation.json  │
│   进度: 55% → 75%                                     │
└──────────────┬───────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────┐
│ Step 5: 生成翻译字幕                                  │
│   translated segments → SRT                           │
│   写入: temp/{task_id}/translated.srt                 │
│   进度: 75% → 80%                                     │
└──────────────┬───────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────┐
│ Step 6: 烧录字幕                                      │
│   ffmpeg -i input.mp4 -vf subtitles=translated.srt    │
│   输出: output/{stem}_subtitled_{lang}.mp4            │
│   进度: 80% → 98% → 100%                              │
└──────────────┬───────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────┐
│ 清理                                                  │
│   删除 temp/{task_id}/                                │
│   源视频保留在 input/                                  │
│   结果在 output/                                       │
└──────────────────────────────────────────────────────┘
```

## 关键配置

| 配置项 | 默认值 | 作用 |
|--------|--------|------|
| `WHISPER_MODEL` | `large-v3` | 模型大小（越大越准越慢） |
| `TRANSLATION_ENGINE` | `google` | 翻译引擎 |
| `USE_VOCAL_SEPARATION` | `true` | 是否分离背景音乐 |
| `USE_CROSS_VALIDATION` | `false` | 是否双引擎交叉验证 |
| `DEMUCS_MODEL` | `htdemucs` | 人声分离模型 |

## 目录结构约定

| 目录 | 用途 | 清理策略 |
|------|------|---------|
| `input/` | 源视频文件 | 不自动清理 |
| `output/` | 处理结果 `*_subtitled_{lang}.mp4` | 不自动清理 |
| `temp/{task_id}/` | 单个任务的中间文件 | 处理完成后自动删除 |
| `models/` | Whisper/Demucs 模型 | 不清理 |

## 任务状态机

```
waiting → extracting_audio → transcribing → translating → burning_subtitles → completed
                                                                             ↘ failed
```

## 已发现的逻辑问题

### 已修复
- [x] Demucs 输出 44.1kHz stereo → 新增 ffmpeg 转 16kHz mono
- [x] `/upload` 重复复制 → 新增 `/process` 接口
- [x] 跨验证低置信度混入垃圾 → 置信度 < 0.1 自动回退
- [x] 翻译语言代码映射 (zh→zh-CN for Google)
- [x] GPT 调用私有函数时重复定义 `_calculate_transcription_confidence`

### 待处理

| # | 问题 | 影响 | 优先级 |
|---|------|------|--------|
| 1 | 进度长时间不动（Demucs/转录/翻译期间无更新） | 用户以为卡住 | 中 |
| 2 | 翻译跨验证无低置信度保护 | 低质量翻译可能被选用 | 低 |
| 3 | `_tasks` 无锁，多线程不安全 | 高并发可能丢任务 | 低（MVP 单用户） |
| 4 | 无并发限制 | 25 个任务同时跑 25 个 large-v3 → OOM | 高 |
| 5 | 服务器重启丢失任务状态 | 正在跑的任务变孤儿 | 中 |
| 6 | `download_video` 两次 extract_info 调用 | 重复网络请求 | 低 |
| 7 | task.video_path 在线程中直接修改 | 非线程安全 | 低 |

### 已修复
- [x] Demucs 输出 44.1kHz → 转 16kHz mono
- [x] `/upload` 在 input/ 内重复复制 → 新增 `/process`
- [x] 跨验证低置信度混入垃圾 → <0.1 回退
- [x] `zh`→`zh-CN` 翻译代码映射
- [x] `_calculate_transcription_confidence` 函数重复定义
- [x] 失败任务不清理 temp/
- [x] 输出文件名冲突 → 自动加 `_1`, `_2` 后缀
- [x] 同语言跳过翻译
