---
name: srt-voice-explainer
description: "Turn a Chinese transcript (transcription.srt) into a finished 9:16 vertical explainer video narrated in the user's own locally-cloned voice. Use when the user asks to 出讲解视频 / 做解说视频 / 用我的音色配音 from an SRT, subtitle file, or transcript, including technical explainers with formulas or code, or asks to re-run, repair, remaster, or re-QA such a video. Covers preflight, beat splitting, local Qwen3-TTS voice cloning, loudness mastering, evidence-led editorial-documentary visuals, alignment-driven HyperFrames composition, anti-template visual review, automated QA, and versioned delivery. Voice stays local and is never uploaded."
---

# SRT → 本地音色讲解视频

把 `transcription.srt` 做成一支 1080×1920 竖屏讲解视频，旁白用用户本地已授权的音色克隆。
端到端交付：预检 → 拆分 → 配音 → 母带 → 分镜 → 合成 → 渲染 → 质检 → 修复 → 交付。

**这是执行任务，不是出方案。** 除非关键输入缺失，或某步需要上传私密音频/付费/公开发布，
否则不要中途停下来提问——一路做到成片和质检产物都生成为止。

## 铁律

1. **音色只在本地。** 参考音频、embedding、参考文本、生成的语音，一律不上云、不发外部 API。
2. **不覆盖用户资产。** `transcription.srt`、`my_voice/`、既有成片、用户改动一概不动。
   派生文件进 `video_work/`，产物进 `deliverables/`，同名自动递增版本号，**旧产物不删**。
3. **不自动发布。** 不 publish、不上传、不生成公开链接。
4. **原生竖屏。** 从第一行 HTML 就是 1080×1920；禁止做 16:9 再裁切/缩放/补背景。
5. **文案逐字保留**（`TIMING_MODE = QUALITY_FIRST`）。成片比原 SRT 长是允许的；
   禁止靠加速追时间轴。要压缩文案时必须先切到 `DURATION_LOCKED` 并记录改动。
   用户明确要求新增、纠错或扩写时，创建新的版本化 canonical SRT，更新配置指向它，并在
   `SCRIPT_CHANGES.md` 记录差异；不得绕过 SRT 直接往 `beats.jsonl` 追加正文。
6. **失败项要修，不是要承认。** 门禁 FAIL → 定位 → 只修相关素材 → 重渲 → 复验，最多 3 轮。
7. **门禁本身必须能失效。** 新加的检查要能把修复前的成片判成 FAIL，否则那是个恒真检查。
8. **中英文发音必须正确且全片一致，尤其是英文。** 生成前逐项检查中文多音字、人名、
   地名，以及英文单词、缩写、品牌名、产品名和技术术语；英文不得凭拼写猜读，也不得默认
   逐字母念。优先采用目标受众最熟悉的大众通行读法，品牌、人名等专名以官方读法为准。
   先建立全片唯一的 `video_work/PRONUNCIATION_LEDGER.json`：同一个词或术语无论出现在哪个
   章节、句子或旁白单元，都必须绑定同一个规范读法、同一份 `tts` 替换和同一套发音参数；
   禁止同词前后改读法，禁止靠 TTS 随机性碰运气。纠音只写入 `tts` 或发音台账，绝不改动
   原文 `text`。生成后把重复词的全部出现位置串联试听；任何错音、含混、未经确认的读法，
   或同词前后不一致都判为 FAIL。更新全局台账并只重生成受影响单元，直至全片复验通过。
   **组合词必须继承组成词的读法。** “连读”只允许缩短组成词之间的静音，不允许改变任一组成词
   的音素、重音或元音颜色。组合词须在台账中列出 `components`；先用“单独词 + 组合词”的最小
   发音样片确认，再开始全量 TTS。不得为了让两个词粘在一起，把已确认的专名改写成另一种近似音。
9. **静止文字和静止背景必须像素级稳定。** 设计为静止的标题、正文、标签、公式、图标和
   背景，在入场动画结束后不得出现位置、缩放、旋转、字距、行高、模糊、锐度或透明度的
   周期性变化；不得因父容器动画、子像素取整、字体回退、随机噪声、镜头缩放或后期防抖
   产生“摇晃”“呼吸”“漂移”。背景只有在分镜明确标记为动态画面时才允许运动。禁止为了
   通过 `freezedetect` 给静止文字或静止背景强加漂移；节奏变化应由承载语义的前景对象承担。
   任一静止区域在浏览器采样或最终逐帧检查中出现可见抖动都判为 FAIL。
10. **默认是编辑纪录片，不是“科技界面”。** 不把黑底、荧光色、英文全大写、固定顶栏、
   细线网格、圆角卡片和持续运动当成技术感。优先使用原始证据、真实图表、克制排版和有目的的
   局部标注。若换掉主题文字后版式仍完全成立，说明它是模板，不是从内容中长出来的视觉设计。
11. **局部返工进入时间槽锁定模式。** 已有成片只需修发音、响度、BGM 或少量画面时，先冻结
    canonical SRT、`alignment.json`、画面流和上一版交付；补丁必须绑定 beat id 与精确时间槽。
    若新音频不能在不伤害关键词的前提下装入原槽，才重排其后的时间轴并重渲；不得用全句变速、
    动态归一化混音或无记录的手工挪帧掩盖时长差。详见 `reference/revision-repair.md`。

## 和 HyperFrames 技能的关系

画面用 HyperFrames 合成。**写 composition HTML 前先读 `$hyperframes-core`**（`data-*` 计时契约、
sub-composition、确定性渲染），动画细节读 `$hyperframes-animation`，CLI 用法读 `$hyperframes-cli`。
本技能不复述这些契约，只补充它们没写、但在本管线里踩过的坑 → `reference/hyperframes-traps.md`。

这三个技能已随本技能一起装在 `$CODEX_HOME/skills/` 下；若某个缺失，直接读该目录的 `SKILL.md`
与 `references/`，不要凭记忆写 HyperFrames 契约。

## 快速开始

```powershell
$SKILL = "$env:USERPROFILE\.codex\skills\srt-voice-explainer"

# Windows：优先读取已有配置里的 venv；没有配置时查找项目 venv / QWEN_TTS_PYTHON
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  "$SKILL\scripts\bootstrap.ps1" -Root . -Name my-film-9x16
```

Linux / macOS 或确认全局 Python 可用的 Git Bash：

```bash
SKILL="${CODEX_HOME:-$HOME/.codex}/skills/srt-voice-explainer"
python "$SKILL/scripts/init_project.py" --here --name my-film-9x16
```

Windows 新项目若没有项目 venv，先把 `QWEN_TTS_PYTHON` 指向 Qwen3-TTS 环境的
`python.exe`。不要依赖 Windows Store 的 `python.exe` 别名或假定 `py -3` 一定存在。
初始化脚本即使发现已有配置也必须继续执行运行时自检，不得因为 `[keep]` 就跳过环境验证。

`video.config.json` 是所有脚本的唯一配置源（路径、分辨率、TTS 参数、停顿、响度阈值、安全区）。
默认值在 `scripts/vconfig.py` 的 `DEFAULTS` 里；只需要写想覆盖的键。
项目根靠 `$SRT_VIDEO_ROOT` 或向上查找 `video.config.json` 解析——从子目录跑脚本也不会错位。

配置建立后，所有 Python 管线脚本统一经由项目解释器启动：

```bash
python "$SKILL/scripts/run_project.py" <script.py> [参数…]
```

`run_project.py` 读取 `video.config.json -> python`，强制 Hugging Face 离线模式，并把
`NUMBA_CACHE_DIR` 放进可写的 `video_work/.cache/numba`。不要绕过它调用错误的系统 Python，
也不要用 `Start-Process`、双击或脱离终端的后台方式启动 TTS。

## 管线

每步的产物是下一步的输入。**不要跳步**，尤其不要在 `alignment.json` 生成前写画面时间。

### 1. 预检 → `video_work/PREFLIGHT.md`

- UTF-8 解析 SRT：序号、时间码、空文本、乱码、重复、倒序、重叠。统计汉字数与要求字速。
- 核对 `my_voice/`：`profile.json`、`consent.txt`、`reference.wav`（存在、非静音、不削波）、
  `reference.txt` 与音频内容一致。`allow_cloud_upload` 不为真 → 严格本地生成。
- 检查 Node / ffmpeg / 浏览器渲染环境 / 本地 TTS 环境。已有健康工程优先复用，别无故重建。
- 必须让 `init_project.py` 的 qwen_tts/CUDA 运行时探针 PASS。仅检查 venv 或模型目录“存在”
  不算通过。探针超时或导入失败就先修环境，不得启动全量 TTS。
- 缓存键含文本+模型+参数+参考音频哈希+参考文本哈希；任一变化就重生成，不复用陈旧音频。
- 稿件里有年份、论文归属、复杂度、参数量、性能倍数等可验证陈述 → 用一手资料写
  `video_work/FACT_CHECK.md`。不得编造来源；无法联网就明确标「未核验」。
  不静默改写用户观点；确需纠错在 `SCRIPT_CHANGES.md` 里逐条记原句/改句/理由。
- 扫描全文中的中文多音字、专名、英文单词、缩写、品牌名和技术术语，按规范化表面词分组，
  写 `video_work/PRONUNCIATION_LEDGER.json`。每项至少记录 `surface`、`normalized`、
  `canonical_tts`、`source`、`occurrences` 和 `verified`；重复出现的词不得留到逐段生成时
  临时决定读法。台账未覆盖全部重复英文词和专名 → 预检 FAIL。
- 对连字符词、品牌后缀、型号前后缀和中英混合组合词，记录 `components` 与是否要求连续衔接。
  每个组成词必须指向已确认的独立读法；组合词的 TTS 表示不能覆盖或改写组成词的读法。
  把用户点名纠过的词、标题首句和开场 30 秒内的专名标记为 `high_risk`，全量生成前先做最小样片。
- 算法、公式或代码讲解先在 `video.config.json` 设置 `"technical_explainer": true`，并另写
  `video_work/TECHNICAL_EXPLAINER_AUDIT.md`：先检查用户稿件有没有
  错误前提和逻辑跳跃，再冻结符号方向、变量语义、一般公式、成立条件、特例和代码对应关系。
  同一变量在旁白、公式、动画和代码里含义不一致 → 预检 FAIL。具体方法按需读取
  `reference/technical-explainer.md`。

### 2. 拆分：章节 / 旁白单元 / 视觉节拍

原 SRT 只作**章节与原文索引**，它的时间码不进最终时间轴。三层结构：

| 层 | 粒度 | 说明 |
| -- | ---- | ---- |
| 主题章节 | 原 SRT 大段 | 叙事分章 |
| 旁白单元 | 12–35 汉字 / 2–7 秒 | TTS 的最小生成单位，也是一个视觉节拍 |
| 视觉节拍 | 2–6 秒 | 语义/因果/对比/数字/视觉对象变化时才切，不按标点机械切 |

不拆开专名、英文缩写、数字+单位、公式、`Q/K/V`、`O(n²)`。
一句里出现「旧方案 vs 新方案」「原因 → 结果」「旧状态 → 新状态」时拆成独立节拍。
屏幕文字不是旁白复刻：每屏只留标题/关键词/公式/关键数字，中文正文 ≤2 行、每行 14–18 字。

技术讲解的开场先明确承诺本片会讲清什么，例如直觉、训练、推理、代码和常见误区；不要用与内容
无关的悬念句。叙事优先按「问题/直觉 → 符号与方向 → 一般公式 → 训练 → 推理 → 代码 → 边界/误区
→ 一句话总结」组织，按内容删减，不机械套模板。目标时长是约束，不是质量指标：增加必要的代码
或成立条件后可以合理变长，不得为守住整数分钟而删掉关键前提或加速到难以理解。

写两个文件，然后展开：

```bash
# video_work/chapters.json   [{"key":"ch1","name":"…","srt_id":1,"world":"…","layers":"…"}]
# video_work/beats.jsonl     每行一个节拍，见脚本 docstring
python "$SKILL/scripts/run_project.py" build_micro_script.py   # → video_work/micro_script.json
python "$SKILL/scripts/run_project.py" check_verbatim.py       # 逐字符核对 SRT，必须 PASS
```

`check_verbatim.py` 是 QUALITY_FIRST 的守门人：把所有单元 `text` 顺序拼接，必须与 SRT 正文
逐字符相同。**它 FAIL 就不要往下走**——后面每一步都建立在"文案没变"这个前提上。
读法要纠正时只改 `tts`（送给 TTS 的），`text`（屏幕与字幕）永远是原文。
发音字典规则与 schema → `reference/beat-table.md`。

展开 `micro_script.json` 后再做一次全局发音一致性审计：每个 `tts` 中的英文词、缩写和专名
必须能回查到 `PRONUNCIATION_LEDGER.json`；同一 `normalized` 项的所有出现位置必须使用完全
相同的 `canonical_tts`。发现局部覆盖、大小写分叉或同词多种替换 → FAIL，不得开始生成。

### 3. 生成旁白 + 母带 → `video_work/narration/` + `alignment.json`

```bash
python "$SKILL/scripts/run_project.py" generate_tts.py                  # 全量生成
python "$SKILL/scripts/run_project.py" generate_tts.py --only b012 b013 # 只重生成问题段
python "$SKILL/scripts/run_project.py" generate_tts.py --remaster       # 只重跑母带（不加载模型）
```

单元级固定 seed 保证可复现；只有质检失败的段落换受控 seed 重试。
生成失败就标记失败并重试，**禁止用等长静音假装成功**。
停顿：分句内 80–180 ms，句间 220–420 ms，章节间 450–700 ms。

运行时状态写入 `video_work/narration/tts_state.json`，正常运行至少每 20 秒输出一次
`[heartbeat]`。模型导入、模型加载、音色提示构建和每个旁白单元都有硬超时；超时退出码为
`124`。`.generate_tts.lock` 阻止并发运行，进程异常退出后操作系统自动释放锁。若日志和状态
文件超过 60 秒都不更新，立即检查进程与状态，不得无期限等待或重复启动第二份任务。

同一术语即使分布在不同单元，也必须先应用同一个 `canonical_tts` 再送入模型。完成全量生成后，
按发音台账把每个重复术语的全部出现位置拼成 A/B 试听序列，逐项确认音素和重音一致；不能只听
第一次，也不能只依赖 ASR 文本相同。某次出现不一致时，锁定规范读法和参数，只重生成对应单元。

高风险词与组合词先运行局部探针：

```bash
python "$SKILL/scripts/run_project.py" pronunciation_probe.py --terms Zeva Zeva-Ego
```

探针输出逐词时间戳与本地 ASR 候选，但**不自动宣告读音正确**。必须对照官方/用户指定读法试听，
并把每个出现位置、证据、组成词一致性和连读间隔写入 `video_work/PRONUNCIATION_QA.json`。
全句 ASR 相似度高不能替代专名逐词检查；同一个词被 ASR 写成相近拼法也不能自动视为同音。

公式中的标识符也纳入发音台账。展示形式、原文 `text` 与送入 TTS 的 `tts` 必须分开；例如
`x_t` 究竟读“xt”还是“x 下标 t”取决于用户偏好和受众习惯，不能把 LaTeX 或下划线原样交给
TTS 猜读。数学符号的 ASR 结果只能辅助定位，最终以逐项人工试听和全片一致性为准。

输出 `alignment.json`——**它是唯一的时间真相**，画面时间全部从它派生。

### 4. 响度：整体达标 ≠ 听感一致

这是这条管线上最贵的一课。只调「整片综合 LUFS」会漏掉段与段之间的离散度，
观众听到的"忽大忽小"正是那个离散度。全部阈值**在成片音轨上**测，不是中间文件：

| 指标 | 阈值 |
| ---- | ---- |
| 逐单元门控响度（BS.1770-4）标准差 | ≤ 1.0 LU |
| 逐单元响度极差（max − min） | ≤ 3.0 LU |
| 偏离单元响度中位数 > 2 LU 的单元数 | 0 |
| 短时响度（3 s 窗 / 100 ms 步进，仅有声区）标准差 | ≤ 1.5 LU |
| 短时响度 P5–P95 | ≤ 4.0 LU |
| 每章平均单元响度与节目响度之差 | ≤ 1.0 LU |
| 4× 过采样真峰值 | ≤ −1 dBTP |
| 节目综合响度 | −18 ~ −16 LUFS |

**禁止用 ffmpeg 的 `loudnorm` 做单元级归一化。** 它需要 ≥3 s 输入，短于 3 s 时测量值无效、
会造成最高 45 dB 的错误衰减、而且不报错。短单元在这条管线里是常态。
单元级归一化必须用任意长度都成立的 BS.1770-4 门控实现 → `scripts/loudness.py`。

其余实现约束（只允许线性增益+前瞻限峰、float32 母带、母带不得改变采样数、
渲染器附加增益必须用 `-c:v copy` 重挂音轨复测）→ `reference/loudness.md`。

```bash
python "$SKILL/scripts/run_project.py" loudness_probe.py <成片或音频路径>
```

### 5. 视觉方向 → `video_work/design.md`

默认风格是 `editorial-documentary`：像技术期刊、公共电视纪录片或研究机构的编辑短片，
而不是 AI 生成的科技发布会模板。先从题材与一手素材中决定画布、色彩、字体、网格、留白、
图像处理和运动语法；中文字体必须内嵌进项目，保证渲染环境一致。

论文和技术讲解优先采用“证据原图 → 局部裁切 → 标注关系 → 回到结论”的镜头语法。
重构图只为解决可读性，并明确标注“解释性重构”；不把论文图缩成角落里的装饰缩略图。

明确禁止当默认设计的东西（默认黑底荧光色、蓝紫霓虹渐变、玻璃拟态、科技 HUD、
固定顶栏与章节进度、英文全大写装饰标签、每屏三张圆角卡片、全元素上浮淡入等）
→ `reference/visual-language.md`。

正向要求：每场景必须有清晰主次；满版证据可以单层呈现，只有承载出处、标注、比较或空间关系时
才增加第二层，禁止为了满足“层次感”添加纹理、卡片或装饰背景。竖屏优先上下分区/中心轴/
纵向流程/纵深推进；每屏一个主焦点；标题 72–130 px、正文 34–50 px、小标签 ≥24 px；
安全区左右 ≥72–90 px、顶部 ≥120 px、底部 ≥180 px，核心信息进一步避开顶 140 / 底 260 / 右 140 px。
每个动态对象要有具体运动动词；静态证据可以安静停留 4–9 秒。通常一个语义节拍只安排
一个主动作，动画直接表现数量、方向、层级、因果、比较或状态变化，不为“显得高级”而运动。

**换了字幕内容就要重新提炼视觉隐喻，禁止机械复用上一支片子的图形。**

`design.md` 还必须记录：`visual_register`、主要一手素材、三张关键帧的构图意图、
`template_reuse_audit`，以及哪些常见科技模板元素被主动删除。没有完成反模板审计，不开始写 HTML。

在 `design.md` 里给每类元素声明稳定性：

- `static-text`：入场结束后位置与字形完全锁定；不挂在持续 transform 的父容器上。
- `static-background`：不平移、不缩放、不旋转、不做随机噪声逐帧刷新。
- `dynamic`：只有语义或素材本身要求运动时使用，并写清运动动词、起止时间和幅度。

字体必须在首帧渲染前加载完成，禁止渲染中途从 fallback 切换到目标字体。文字坐标、字号、行高
和容器尺寸优先使用整数像素；需要运动时动画外层包装器，静止文字本体保持固定。详细标准见
`reference/visual-language.md`。

公式和代码按“证据画面”处理，不受普通正文每行 14–18 字的机械限制，但必须保持可读字号并逐步
揭示。公式先显示一般形式，再在成立条件旁显示化简后的特例；代码只高亮当前讲解行，其他行降低
视觉权重。字幕使用独立轨道，不得覆盖当前公式、代码行或核心动画；画面构图要同时预留平台说明区、
底部进度条和右侧交互栏，而不是只给字幕留一条缝。

### 6. 合成与同步

读 `$hyperframes-core` 后再写 composition。写完把时间机械地灌进去：

```bash
python "$SKILL/scripts/run_project.py" sync_timing.py
```

同步规则：关键词在对应发音前 0–150 ms 出现，核心动作在关键词发音区间内发生，
误差 ≤200 ms。通常每 2–6 秒有一次有意义的状态变化；变化应由承载语义的动态对象承担。
有设计意图的静态停留必须在分镜中标注，不能靠晃动静止文字或背景来消除静止窗口。
转场不能截断词尾或盖住下一句的第一个关键词。

论文图上的框、线和箭头必须从同一套源图坐标变换得到，不能在裁切/缩放后凭肉眼各自猜坐标。
连接线使用一条连续 path，箭头只落在 path 终点；标签放在图外安全带，框线不得遮住模块文字。
每个用户点名框选或连接问题都要抓取精确帧与前后邻帧复核。细则见
`reference/revision-repair.md` 与 `reference/visual-language.md`。

所有静止元素必须标记 `data-stability="static"`，动态元素标记 `data-stability="dynamic"`。
静止文字不要与镜头、背景或持续动画共用 transform 父节点；确需镜头运动时，把文字放在独立的
屏幕空间图层。静止背景不得为了制造“高级感”添加呼吸缩放、手持抖动或随机漂移。

本管线踩过的 HyperFrames 坑（`<script type="application/json">` 被剥离、时间线未注册、
GSAP target 缺失、全屏背景挂错元素…）→ `reference/hyperframes-traps.md`。

### 7. 渲染 → 质检 → 修复

先草稿渲染再检查。渲染用 `scripts/hf.sh`（离线解析本地 hyperframes，避免渲染中途拉网络）。

```bash
bash "$SKILL/scripts/hf.sh" check --samples 22
bash "$SKILL/scripts/hf.sh" render …                   # 草稿 → 检查 → 最终
python "$SKILL/scripts/run_project.py" qa_video.py <成片.mp4> --label draft
python "$SKILL/scripts/run_project.py" verify_asr.py
python "$SKILL/scripts/run_project.py" safe_area_check.py <快照1.png> <快照2.png> …
```

`qa_video.py` 覆盖工程运行时、视频技术指标、音频、响度一致性（7 项）、同步、时长。
另外必须执行五项阻断式专项复核：

1. **发音一致性门禁**：按 `PRONUNCIATION_LEDGER.json` 检查所有重复词的全部出现位置；
   对照试听确认音素、重音和字母读法一致。组合词逐项核对组成词，并用逐词时间戳测相邻词间隔；
   结果写入 `PRONUNCIATION_QA.json`。只要同一个词有一次不同读法，整片 FAIL。
2. **静态画面稳定性门禁**：在入场动画结束后的静止区间，以至少 5 fps 采样所有
   `[data-stability="static"]` 元素。浏览器侧 `getBoundingClientRect()` 的 x/y/width/height
   最大漂移均须 ≤0.25 CSS px，computed transform、字体、字号、字距和行高必须恒定；最终无损
   PNG 帧中静止区域不得出现可见的往复位移、缩放或边缘跳动。静态背景的全局对齐漂移须
   ≤0.5 输出像素。门禁必须拿一个人为加入 1 px 往复位移的坏样例验证能判 FAIL。
3. **技术内容一致性门禁**：逐项核对符号方向、变量语义、一般公式与特例条件；屏幕代码必须和
   实际运行的版本一致，口播中的行数、输入输出和更新方向必须能由代码或测试证明。不能把近似、
   特例或直觉类比写成无条件等式。结论写入 `TECHNICAL_EXPLAINER_AUDIT.md`。
4. **平台遮挡与指定时间点门禁**：除均匀 contact sheet 外，建立 `TIMESTAMP_AUDIT.md`，覆盖每章
   入场、全部转场、公式/代码最密集帧、字幕最高帧，以及用户点名时间的精确帧和前后邻帧。
   对这些帧同时做安全区蒙版和人眼语义检查；像素统计为 0 也不能证明字幕没有盖住核心图示。
5. **补丁隔离门禁**：修订已有成片时，列出允许变化的 beat/时间槽/画面区域；验证槽外音频没有
   增益漂移、画面帧未变化、总时长和字幕时间轴符合预期。仅修音频时优先用
   `patch_audio_slots.py` 做样本级替换，再以 `-c:v copy` 重挂最终音轨；不得用会随输入结束而改变
   归一化系数的 `amix` 充当替换器。

逐格视觉检查要人眼过 contact sheet：黑屏、溢出、裁切、重叠、过小字、低对比、乱码缺字、
空洞构图、模板化卡片阵列、过度 AI 风。

再做一次阻断式**反 AI 风格审计**：每个独立视觉场景至少抓取一张入场完成帧，每章至少 3 帧；
固定界面外壳是否重复占据画面、是否凭空发明英文标签、
是否把所有信息装进同尺寸卡片、是否默认黑底配荧光色、是否存在不承载关系的持续运动、
是否把原始证据缩成装饰。任一项在全片反复出现且没有内容理由 → FAIL，重做视觉系统而非换配色。
`STYLE_AUDIT.md` 每项必须记录 frame/scene、出现次数或覆盖时长、判定与例外理由，不能自报 PASS。

`freezedetect` 只负责发现意外冻结，不得把“静态文字/背景保持稳定”误判成缺陷。需要视觉节奏时，
优先让流程线、数据点、遮罩或其他语义前景变化；若整个镜头本就设计为静态停留，在分镜中明确
标注并人工复核。禁止通过晃动背景或文字来消除 freeze 报告。

FAIL → 定位原因 → 只修相关素材/场景 → 重渲 → 复验。上限 3 轮，3 轮后仍阻断就停下来
写明确的失败报告，**不得把未通过的成片标记为完成**。
历史缺陷清单（含每一条的症状/证据/修法/复验）→ `reference/pitfalls.md`。

### 8. 交付

```bash
python "$SKILL/scripts/run_project.py" deliver.py <最终成片.mp4>
```

`deliverables/` 至少包含：`final_video_vNN.mp4`、`final_narration_vNN.wav`、
`final_aligned_vNN.srt`、`micro_script_vNN.json`、`STORYBOARD_vNN.md`、`design_vNN.md`、
`mastering_report_vNN.json`、`PRONUNCIATION_LEDGER.json`、`PRONUNCIATION_QA.json`、`VISUAL_STABILITY_REPORT.json`、
`STYLE_AUDIT.md`、`QA_REPORT.md`、`qa_metrics.json`、`contact_sheet.png`、`manifest.json`。
技术讲解还必须交付 `TECHNICAL_EXPLAINER_AUDIT.md` 和 `TIMESTAMP_AUDIT.md`。

`manifest.json` 记录输入哈希、音色参考哈希、TTS 模型与参数、字体与媒体、依赖版本、
分辨率、fps、时长、成片哈希、QA 结论和复现步骤。

## 汇报格式

只做简洁、可验证的汇报：成片绝对路径/时长/分辨率/fps/大小；使用的音色与 `TIMING_MODE`、
是否压缩文案；单元数/节拍数/章节数；门禁是否全过、修了什么；QA 报告与工程源文件路径。
同时明确报告重复英文词/专名的数量、发音一致性门禁结论，以及静态文字/背景稳定性门禁结论。
技术讲解还要报告符号/公式/代码一致性结论，以及指定时间点和平台遮挡复核结论。
**有任何未通过项就写「未完成」，不要写「已完成」。**

## 脚本清单

| 脚本 | 作用 |
| ---- | ---- |
| `bootstrap.ps1` | Windows 启动器：选择可用的配置/项目 venv，再执行初始化与运行时预检 |
| `init_project.py` | 生成 `video.config.json` + 目录，自动找 TTS snapshot 与 venv，报告缺失项 |
| `run_project.py` | 用配置中的 Python 启动脚本，强制离线并设置项目内可写缓存 |
| `runtime_support.py` | 单实例锁、状态文件、20 秒心跳与阶段硬超时 |
| `vconfig.py` | 配置层：所有脚本的路径与阈值都从这里读 |
| `build_micro_script.py` | `chapters.json` + `beats.jsonl` → `micro_script.json` |
| `check_verbatim.py` | 逐字符核对 SRT + schema 校验 |
| `generate_tts.py` | 本地音色克隆逐单元生成、母带、拼装、`alignment.json`；`--only` / `--remaster` |
| `loudness.py` | BS.1770-4 门控响度 / 短时 / 真峰值 / 归一化 / 前瞻限峰（任意长度成立） |
| `loudness_probe.py` | 独立响度诊断报告 |
| `sync_timing.py` | 用 `alignment.json` 重写 composition 的 `data-*` 时间 |
| `build_docs.py` | 生成 `SCRIPT.md` / `STORYBOARD.md` / `SCRIPT_CHANGES.md` |
| `qa_video.py` | 自动门禁（含按配置启用的技术专项检查）→ `qa_metrics.json` |
| `verify_asr.py` | 本地 whisper 转写与脚本比对 |
| `pronunciation_probe.py` | 对高风险词/组合词生成本地逐词时间戳证据，供人工专项验收 |
| `patch_audio_slots.py` | 按样本数替换既有旁白的精确时间槽，阻止补丁外增益或时长漂移 |
| `safe_area_check.py` | 竖屏平台安全区越界像素统计 |
| `deliver.py` | 版本化交付 + `manifest.json` |
| `hf.sh` | 离线 HyperFrames CLI 包装 |

## 参考文档

| 文件 | 内容 |
| ---- | ---- |
| `reference/loudness.md` | 响度一致性的完整标准、DSP 实现要点、`loudness.py` API |
| `reference/pitfalls.md` | 历史缺陷：症状、证据、根因、修法、复验 |
| `reference/hyperframes-traps.md` | 本管线踩过的 HyperFrames 契约陷阱 |
| `reference/beat-table.md` | `chapters.json` / `beats.jsonl` / `micro_script.json` schema 与发音字典规则 |
| `reference/visual-language.md` | 视觉禁令清单与竖屏构图正向要求 |
| `reference/technical-explainer.md` | 有公式或代码时的内容结构、符号语义、TTS 读法、代码证据与专项 QA |
| `reference/revision-repair.md` | 已有成片的局部修音、时间槽锁定、图示坐标修复、混音与版本化验收 |
