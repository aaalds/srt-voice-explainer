# srt-voice-explainer

把一份中文字幕稿（`transcription.srt`）做成一支 **1080×1920 竖屏讲解成片**，旁白用你本地已授权的音色克隆——不是出方案，是一路做到成片和质检产物都落地。

仓库里是同一条管线的两个发行版：`srt-voice-explainer-claude`（Claude Code）和 `srt-voice-explainer-codex`（Codex）。

**音色只在本地。** 参考音频、embedding、生成的语音一律不上云、不发外部 API，也不自动发布。

## 管线

```
预检 → 拆分 → 配音 → 母带 → 视觉方向 → 合成 → 渲染 → 质检 → 修复 → 交付
```

每步的产物是下一步的输入，不跳步。几个关键约束：

- **文案逐字保留**（`TIMING_MODE = QUALITY_FIRST`）。成片比原 SRT 长是允许的，禁止靠加速追时间轴；要压稿必须切到 `DURATION_LOCKED` 并在 `SCRIPT_CHANGES.md` 里逐条记账，`check_verbatim.py` 会拦住"压了稿但没有账"。
- **`alignment.json` 是唯一的时间真相**，所有画面时间由 `sync_timing.py` 从它机械派生，不手写。
- **响度按听感一致收**：不只看整片 LUFS，还卡逐单元标准差 ≤1.0 LU、极差 ≤3.0 LU、短时 P5–P95 ≤4.0 LU、真峰值 ≤−1 dBTP。单元级归一化禁用 ffmpeg `loudnorm`（短于 3 s 时测量无效且不报错），改用任意长度都成立的 BS.1770-4 门控实现。
- **发音全片一致**：先建 `PRONUNCIATION_LEDGER.json`，同一个词在任何位置都绑定同一个规范读法；纠音只改 `tts`，屏幕上的 `text` 永远是原文。
- **静止文字和背景像素级稳定**：不许为了骗过 `freezedetect` 给静止元素强加漂移。
- **门禁本身必须能失效**：新加的检查要能把修复前的片子判成 FAIL，否则那是个恒真检查。

画面用 [HyperFrames](https://github.com/) 合成，写 composition 前先读 `hyperframes-core` / `hyperframes-animation` / `hyperframes-cli`；本技能只补它们没写、但在这条管线里踩过的坑。

## 两个版本的差异

| | `-claude` | `-codex` |
| --- | --- | --- |
| 安装位置 | `~/.claude/skills/srt-voice-explainer` | `$CODEX_HOME/skills/srt-voice-explainer` |
| 初始化 | `python scripts/init_project.py --here` | `scripts/bootstrap.ps1`（Windows）或 `init_project.py` |
| 脚本调用 | 直接 `python scripts/xxx.py` | 统一经 `run_project.py`（锁定项目解释器、强制 HF 离线、可写缓存） |
| 长任务保护 | — | `runtime_support.py`：单实例锁、20 s 心跳、阶段硬超时（超时退出码 124） |
| 发音门禁 | `audit_pronunciation.py`（script/audio 两阶段 + `--selftest`） | 流程要求，人工按台账执行 |
| 稳定性门禁 | `check_static_stability.py`（成片抽帧查全局漂移 + `--selftest`） | 浏览器侧 `getBoundingClientRect()` 采样 |
| 用户实拍素材 | 支持，`reference/user-footage.md`（原生像素裁切、取景参数化、丢音轨） | — |
| 铁律 / 缺陷清单 | 10 条 / R1–R16 | 9 条 / R1–R10 |
| 其他 | — | `agents/openai.yaml` 提供 Codex 界面元数据 |

简单说：**claude 版把发音和画面稳定做成了带负向自检的可执行门禁，并支持接入你自己的录屏；codex 版重在 Windows 环境的运行时可靠性**（解释器解析、离线模式、长任务心跳与超时）。

## 快速开始

Claude Code：

```bash
SKILL=~/.claude/skills/srt-voice-explainer
python "$SKILL/scripts/init_project.py" --here --name my-film-9x16
```

Codex（Windows）：

```powershell
$SKILL = "$env:USERPROFILE\.codex\skills\srt-voice-explainer"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  "$SKILL\scripts\bootstrap.ps1" -Root . -Name my-film-9x16
```

初始化会生成 `video.config.json`（所有脚本的唯一配置源：路径、分辨率、TTS 参数、停顿、响度阈值、安全区）和目录骨架，并报告缺什么。默认值在 `scripts/vconfig.py` 的 `DEFAULTS` 里，只需写想覆盖的键。

项目里需要准备好：

```
transcription.srt          # 字幕稿
my_voice/
  profile.json
  consent.txt              # 授权声明
  reference.wav            # 参考音频（非静音、不削波）
  reference.txt            # 与音频内容一致
```

然后直接对 agent 说「把 transcription.srt 做成竖屏讲解视频，用我的音色配音」即可。

## 交付物

产物进 `deliverables/`，同名自动递增版本号，**旧产物不删**：

`final_video_vNN.mp4`、`final_narration_vNN.wav`、`final_aligned_vNN.srt`、`micro_script_vNN.json`、`STORYBOARD_vNN.md`、`design_vNN.md`、`SCRIPT_CHANGES_vNN.md`、`FACT_CHECK_vNN.md`、`mastering_report_vNN.json`、`PRONUNCIATION_LEDGER.json`、`VISUAL_STABILITY_REPORT.json`、`QA_REPORT.md`、`qa_metrics.json`、`contact_sheet.png`、`manifest.json`。

`manifest.json` 记录输入哈希、音色参考哈希、TTS 模型与参数、依赖版本、成片哈希、QA 结论和复现步骤。`QA_REPORT.md` 末尾留一节「未做 / 需要你拍板的事」。

## 参考文档

两个版本的 `reference/` 下：

| 文件 | 内容 |
| --- | --- |
| `loudness.md` | 响度一致性标准、DSP 实现要点、`loudness.py` API |
| `pitfalls.md` | 历史缺陷清单：症状、证据、根因、修法、复验 |
| `hyperframes-traps.md` | 这条管线踩过的 HyperFrames 契约陷阱 |
| `beat-table.md` | `chapters.json` / `beats.jsonl` / `micro_script.json` schema 与发音字典规则 |
| `visual-language.md` | 视觉禁令清单与竖屏构图正向要求 |
| `user-footage.md` | 用户录屏/实拍的验源、裁切、取景放大、标注（仅 claude 版） |
