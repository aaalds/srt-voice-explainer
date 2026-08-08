---
name: srt-voice-explainer
description: >
  Turn a Chinese transcript (transcription.srt) into a finished 9:16 vertical explainer video
  narrated in the user's own locally-cloned voice. Use when the user asks to 出讲解视频 / 做解说视频 /
  用我的音色配音 from an SRT, subtitle file, or transcript, or asks to re-run, repair, remaster, or
  re-QA such a video, or to fold their own screen recording / footage into one. Covers the whole
  pipeline: preflight, beat splitting, local Qwen3-TTS voice cloning, BS.1770-4 loudness mastering
  with per-segment consistency gates, user-footage framing and step-zoom annotation, HyperFrames
  HTML composition, alignment-driven animation timing, 30-gate automated QA plus blocking
  pronunciation and static-stability gates, and versioned delivery.
  Voice stays local — never uploads reference audio, embeddings, or generated speech.
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
5. **文案逐字保留**（`TIMING_MODE = QUALITY_FIRST`，写在 `video.config.json` 的 `timing_mode`）。
   成片比原 SRT 长是允许的；禁止靠加速追时间轴。要压缩文案必须先把 `timing_mode` 改成
   `DURATION_LOCKED`，并在 `SCRIPT_CHANGES.md` 里逐条记原句/改句/删了什么/为什么——
   `check_verbatim.py` 会拦住"压了稿但没有账"的情况。
6. **失败项要修，不是要承认。** 门禁 FAIL → 定位 → 只修相关素材 → 重渲 → 复验，最多 3 轮。
7. **门禁本身必须能失效。** 新加的检查要能把修复前的成片判成 FAIL，否则那是个恒真检查。
   放宽阈值之后必须重跑坏样例，不然分不清"修好了"和"门禁瞎了"。
8. **中英文发音必须正确且全片一致，尤其是英文。** 生成前逐项检查中文多音字、人名、
   地名，以及英文单词、缩写、品牌名、产品名和技术术语；英文不得凭拼写猜读，也不得默认
   逐字母念。优先采用目标受众最熟悉的大众通行读法，品牌、人名等专名以官方读法为准。
   先建立全片唯一的 `video_work/PRONUNCIATION_LEDGER.json`：同一个词或术语无论出现在哪个
   章节、句子或旁白单元，都必须绑定同一个规范读法、同一份 `tts` 替换和同一套发音参数；
   禁止同词前后改读法，禁止靠 TTS 随机性碰运气。纠音只写入 `tts` 或发音台账，绝不改动
   原文 `text`。生成后把重复词的全部出现位置串联试听；任何错音、含混、未经确认的读法，
   或同词前后不一致都判为 FAIL。更新全局台账并只重生成受影响单元，直至全片复验通过。
9. **静止文字和静止背景必须像素级稳定。** 设计为静止的标题、正文、标签、公式、图标和
   背景，在入场动画结束后不得出现位置、缩放、旋转、字距、行高、模糊、锐度或透明度的
   周期性变化；不得因父容器动画、子像素取整、字体回退、随机噪声、镜头缩放或后期防抖
   产生"摇晃""呼吸""漂移"。背景只有在分镜明确标记为动态画面时才允许运动。禁止为了
   通过 `freezedetect` 给静止文字或静止背景强加漂移；节奏变化应由承载语义的前景对象承担。
   任一静止区域在浏览器采样或最终逐帧检查中出现可见抖动都判为 FAIL。
10. **用户口述的话和用户给的素材是输入，不是草稿。** 用户中途口述的开场白、金句、
    指定说法一字不改地进片；只有明显的错别字或同音误写可以纠正，且必须在
    `SCRIPT_CHANGES.md` 里单列一节，写明原文、改后、理由和**怎么改回去**。
    为发音方便改写只允许发生在自己写的句子上（见 `reference/pitfalls.md` R12）。
    用户给的媒体文件只读，派生文件另存（`reference/user-footage.md`）。

## 和 HyperFrames 技能的关系

画面用 HyperFrames 合成。**写 composition HTML 前先读 `/hyperframes-core`**（`data-*` 计时契约、
sub-composition、确定性渲染），动画细节读 `/hyperframes-animation`，CLI 用法读 `/hyperframes-cli`。
本技能不复述这些契约，只补充它们没写、但在本管线里踩过的坑 → `reference/hyperframes-traps.md`。

## 快速开始

```bash
SKILL=~/.claude/skills/srt-voice-explainer

# 0) 首次：生成 video.config.json + 目录骨架，并报告缺什么
python "$SKILL/scripts/init_project.py" --here --name my-film-9x16
```

`video.config.json` 是所有脚本的唯一配置源（路径、分辨率、TTS 参数、停顿、响度阈值、安全区）。
默认值在 `scripts/vconfig.py` 的 `DEFAULTS` 里；只需要写想覆盖的键。
项目根靠 `$SRT_VIDEO_ROOT` 或向上查找 `video.config.json` 解析——从子目录跑脚本也不会错位。

## 管线

每步的产物是下一步的输入。**不要跳步**，尤其不要在 `alignment.json` 生成前写画面时间。

### 1. 预检 → `video_work/PREFLIGHT.md`

- UTF-8 解析 SRT：序号、时间码、空文本、乱码、重复、倒序、重叠。统计汉字数与要求字速。
- 核对 `my_voice/`：`profile.json`、`consent.txt`、`reference.wav`（存在、非静音、不削波）、
  `reference.txt` 与音频内容一致。`allow_cloud_upload` 不为真 → 严格本地生成。
- 检查 Node / ffmpeg / 浏览器渲染环境 / 本地 TTS 环境。已有健康工程优先复用，别无故重建。
  ASR 校验模型也在这一步落地：`huggingface_hub` 在受限网络下会直接 `LocalEntryNotFoundError`，
  而 `curl` 往往是通的——那就逐文件 `curl` 到 `video_work/asr/<model>/`，之后全程 `HF_HUB_OFFLINE=1`。
- 用户提供了自己的录屏 / 实拍 → 先 `ffprobe` 验源（分辨率、帧率、时长、有无音轨、内容区位置），
  按 `reference/user-footage.md` 规划取景与裁切。**原始文件只读**，派生媒体进项目 `assets/`，
  原始文件哈希记进 `manifest.json`。
- 缓存键含文本+模型+参数+参考音频哈希+参考文本哈希；任一变化就重生成，不复用陈旧音频。
- 稿件里有年份、论文归属、复杂度、参数量、性能倍数等可验证陈述 → 用一手资料写
  `video_work/FACT_CHECK.md`。不得编造来源；无法联网就明确标「未核验」。
  不静默改写用户观点；确需纠错在 `SCRIPT_CHANGES.md` 里逐条记原句/改句/理由。
- 扫描全文中的中文多音字、专名、英文单词、缩写、品牌名和技术术语，按规范化表面词分组，
  写 `video_work/PRONUNCIATION_LEDGER.json`。每项至少记录 `surface`、`normalized`、
  `canonical_tts`、`source`、`occurrences` 和 `verified`；重复出现的词不得留到逐段生成时
  临时决定读法。台账未覆盖全部重复英文词和专名 → 预检 FAIL。
  台账里的读法不要凭想象定：拿不准就用本地 TTS 生成几个候选读法，
  用本地 ASR 回读来裁决，把**采纳的读法和被否掉的读法都写进 `source`**。

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

写两个文件，然后展开：

```bash
# video_work/chapters.json   [{"key":"ch1","name":"…","srt_id":1,"world":"…","layers":"…"}]
# video_work/beats.jsonl     每行一个节拍，见脚本 docstring
python "$SKILL/scripts/build_micro_script.py"   # → video_work/micro_script.json
python "$SKILL/scripts/check_verbatim.py"       # 逐字符核对 SRT，必须 PASS
python "$SKILL/scripts/audit_pronunciation.py" --stage script --selftest   # 必须 PASS
```

`check_verbatim.py` 是 QUALITY_FIRST 的守门人：把所有单元 `text` 顺序拼接，必须与 SRT 正文
逐字符相同。**它 FAIL 就不要往下走**——后面每一步都建立在"文案没变"这个前提上。
（`timing_mode = DURATION_LOCKED` 时它转成另一条要求：压稿可以，但 `SCRIPT_CHANGES.md`
必须存在且非空。）
读法要纠正时只改 `tts`（送给 TTS 的），`text`（屏幕与字幕）永远是原文。
发音字典规则与 schema → `reference/beat-table.md`。

`audit_pronunciation.py --stage script` 是铁律 8 的机器实现：校验台账没有 `normalized` 冲突、
`occurrences` 与 beats 完全吻合、每个含规范词的单元其 `tts_text` 都用了规范读法、
且没有任何未登记的拉丁词会被送进 TTS。`--selftest` 会把某个单元的读法回退成原始拼写，
证明这道门禁真的能判 FAIL（铁律 7）。

展开 `micro_script.json` 后再做一次全局发音一致性审计：每个 `tts` 中的英文词、缩写和专名
必须能回查到 `PRONUNCIATION_LEDGER.json`；同一 `normalized` 项的所有出现位置必须使用完全
相同的 `canonical_tts`。发现局部覆盖、大小写分叉或同词多种替换 → FAIL，不得开始生成。

### 3. 生成旁白 + 母带 → `video_work/narration/` + `alignment.json`

```bash
python "$SKILL/scripts/generate_tts.py"              # 全量生成
python "$SKILL/scripts/generate_tts.py" --only b012,b013   # 只重生成问题段
python "$SKILL/scripts/generate_tts.py" --remaster   # 只重跑母带（不加载模型）
```

单元级固定 seed 保证可复现；只有质检失败的段落换受控 seed 重试。
生成失败就标记失败并重试，**禁止用等长静音假装成功**。
停顿：分句内 80–180 ms，句间 220–420 ms，章节间 450–700 ms。

同一术语即使分布在不同单元，也必须先应用同一个 `canonical_tts` 再送入模型。完成全量生成后：

```bash
python "$SKILL/scripts/audit_pronunciation.py" --stage audio --require-verified
```

它把每个重复术语的全部出现位置串成对照序列，逐项确认音素和重音一致；不能只听第一次。
台账里的 `asr_variants` 是这道门禁的核心：**人工判读过一次的可接受回读要写回台账**
（`Sol` 回读成 `Soul` / `SAL` / `扫` 都落在同一音位家族，是 ASR 正字法噪声，不是读法分歧），
未登记的回读一律 FAIL——要么这段读错了，要么这个变体可接受、那就连同理由写进台账。

三条边界：

- **ASR 是定位工具，不是判据。** 整段转写会把相邻词形合并（"L 子" 并进 `.toml`），
  制造出假的漏字；宣布缺陷前先把可疑位置单独截出来重转一次（pitfalls R13）。
  反过来，整段对上了也不代表读对了——同音字照样过。
- **换种子先试，但 2–3 个受控 offset 之后就别试了。** 仍不对说明这是该字符串在该上下文
  里的稳定行为，改写成语义等价、发音无歧义的说法（pitfalls R12），并记进 `SCRIPT_CHANGES.md`。
- **只能改写自己写的稿子。** 用户口述的句子不许为发音方便改掉（铁律 10）。

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
python "$SKILL/scripts/loudness_probe.py"   # 独立诊断：节目/单元/短时/逐章/最差段
```

### 5. 视觉方向 → `video_work/design.md`

先定视觉系统再写代码：画布、主色、**唯一**主强调色、字体、网格、边距、圆角、线条、
阴影、纹理、图标、运动语法。中文字体必须内嵌进项目，保证渲染环境一致。

明确禁止当默认设计的东西（蓝紫霓虹渐变、玻璃拟态、无意义粒子、科技 HUD、
每屏居中标题+三张卡片、emoji 当图标、全元素同一种上浮淡入……）→ `reference/visual-language.md`。

正向要求：每场景 ≥2 层（前景/中景/背景）；竖屏优先上下分区/中心轴/纵向流程/纵深推进；
每屏一个主焦点；标题 72–130 px、正文 34–50 px、小标签 ≥24 px；
安全区左右 ≥72–90 px、顶部 ≥120 px、底部 ≥180 px，核心信息进一步避开顶 140 / 底 260 / 右 140 px。
每个视觉对象要有具体运动动词（分裂、聚合、锁定、采样、折叠、写入、遗忘、缓存、旋转），
不是笼统的"淡入"。重点动画直接表现知识关系：数量、方向、层级、因果、比较、状态变化、复杂度。

**换了字幕内容就要重新提炼视觉隐喻，禁止机械复用上一支片子的图形。**

在 `design.md` 里给每类元素声明稳定性：

- `static-text`：入场结束后位置与字形完全锁定；不挂在持续 transform 的父容器上。
- `static-background`：不平移、不缩放、不旋转、不做随机噪声逐帧刷新。
- `dynamic`：只有语义或素材本身要求运动时使用，并写清运动动词、起止时间和幅度。

字体必须在首帧渲染前加载完成，禁止渲染中途从 fallback 切换到目标字体。文字坐标、字号、行高
和容器尺寸优先使用整数像素；需要运动时动画外层包装器，静止文字本体保持固定。详细标准见
`reference/visual-language.md`。

### 6. 用户实拍素材接入（如有）→ 项目 `assets/`

用户给了自己的录屏 / 实拍时，这一步在写 composition 之前做完。完整流程见
`reference/user-footage.md`，三条要点：

- **放大 = 从原片切原生像素，不是把压过的视频 CSS 缩放。** 细节标注用
  `crop=W:H:X:Y` 导出的原生静帧，一处一张；"这是真的"由一段视频片段承担。
- **音轨一律 `-an` 丢掉。** 片子里只存在一条音轨：母带过的旁白。
- **取景框宽度先过安全区再定构图**（`可用宽度 = 1080 − 左边距 − 右侧 140`），
  媒体面板是最容易撞平台覆盖区的元素（pitfalls R11）。

取景写成 `view(scale, tx, ty)` 这种"把源图某点送到窗口正中"的参数化形式，
每次取景变化对齐到某个节拍的发音时间，**移到位就停住**——
持续推移（Ken Burns）会被静态稳定性门禁判为全局漂移。

### 7. 合成与同步

读 `/hyperframes-core` 后再写 composition。写完把时间机械地灌进去：

```bash
python "$SKILL/scripts/sync_timing.py"   # 用 alignment.json 重写所有 data-start/data-duration
```

同步规则：关键词在对应发音前 0–150 ms 出现，核心动作在关键词发音区间内发生，
误差 ≤200 ms。通常每 2–6 秒有一次有意义的状态变化；变化应由承载语义的动态对象承担。
有设计意图的静态停留必须在分镜中标注，不能靠晃动静止文字或背景来消除静止窗口。
转场不能截断词尾或盖住下一句的第一个关键词。

所有静止元素必须标记 `data-stability="static"`，动态元素标记 `data-stability="dynamic"`。
静止文字不要与镜头、背景或持续动画共用 transform 父节点；确需镜头运动时，把文字放在独立的
屏幕空间图层。静止背景不得为了制造“高级感”添加呼吸缩放、手持抖动或随机漂移。

本管线踩过的 HyperFrames 坑（`<script type="application/json">` 被剥离、时间线未注册、
GSAP target 缺失、全屏背景挂错元素…）→ `reference/hyperframes-traps.md`。

### 8. 渲染 → 质检 → 修复

先草稿渲染再检查。渲染用 `scripts/hf.sh`（离线解析本地 hyperframes，避免渲染中途拉网络）。

```bash
"$SKILL/scripts/hf.sh" check --samples 22
"$SKILL/scripts/hf.sh" render …                        # 草稿 → 检查 → 最终
python "$SKILL/scripts/qa_video.py"                    # 30 项门禁
python "$SKILL/scripts/verify_asr.py"                  # 本地 whisper 逐段转写核对
python "$SKILL/scripts/safe_area_check.py"             # 竖屏安全区蒙版
python "$SKILL/scripts/audit_pronunciation.py" --stage audio --require-verified
python "$SKILL/scripts/check_static_stability.py" <成片.mp4> --selftest \
       --json video_work/VISUAL_STABILITY_REPORT.json
```

`qa_video.py` 覆盖工程运行时、视频技术指标、音频、响度一致性（7 项）、同步、时长。
另外两项**阻断式**专项复核有各自的脚本，都自带负向自检：

1. **发音一致性门禁**（铁律 8）→ `audit_pronunciation.py`。script 阶段查台账与脚本，
   audio 阶段逐段回读并要求每个回读都在 `asr_variants` 里有登记。
   同一个词有一次读法不同，整片 FAIL。
2. **静态画面稳定性门禁**（铁律 9）→ `check_static_stability.py`。
   **在成片上做**：5 fps 抽帧逐对比较，把"变化像素 <2% 但变化框覆盖 ≥55% 画幅
   且连续 ≥1.2 s"判为全局漂移。判据是**变化区域的覆盖面积**而不是变化量——
   有意的前景动画集中在局部，1 px 漂移的边缘变化铺满全屏。
   `--selftest` 会给同一支片子加 1 px 逐帧往复位移后重跑同一判据，必须判 FAIL。
   浏览器侧 `getBoundingClientRect()` 采样（漂移 ≤0.25 CSS px、computed style 恒定）
   仍然要做，但它只能证明 DOM 没动——子像素取整、字体回退、编码器都在那之后。

逐格视觉检查要人眼过 contact sheet：黑屏、溢出、裁切、重叠、过小字、低对比、乱码缺字、
空洞构图、模板化卡片阵列、过度 AI 风。**自动门禁全绿不等于画面对**——
`scaleX` 作用在 0 宽元素上的元素会彻底不显示而 `check` 一个 error 都没有（pitfalls R10）。

`freezedetect` 只负责发现意外冻结，不得把“静态文字/背景保持稳定”误判成缺陷。需要视觉节奏时，
优先让流程线、数据点、遮罩或其他语义前景变化；若整个镜头本就设计为静态停留，在分镜中明确
标注并人工复核。禁止通过晃动背景或文字来消除 freeze 报告。

FAIL → 定位原因 → 只修相关素材/场景 → 重渲 → 复验。上限 3 轮，3 轮后仍阻断就停下来
写明确的失败报告，**不得把未通过的成片标记为完成**。
动手修之前先确认症状在**成片**上成立：`check` 的快照不解码视频（媒体位置是黑的，
pitfalls R15）、整段 ASR 会合并相邻词形制造假漏字（R13）——修假问题会引入真问题。
历史缺陷清单（R1–R16，含每一条的症状/证据/修法）→ `reference/pitfalls.md`。

### 9. 交付

```bash
python "$SKILL/scripts/deliver.py"   # 版本化拷贝 + manifest.json
```

`deliverables/` 至少包含：`final_video_vNN.mp4`、`final_narration_vNN.wav`、
`final_aligned_vNN.srt`、`micro_script_vNN.json`、`STORYBOARD_vNN.md`、`design_vNN.md`、
`SCRIPT_CHANGES_vNN.md`、`FACT_CHECK_vNN.md`、`mastering_report_vNN.json`、
`PRONUNCIATION_LEDGER.json`、`pronunciation_asr_vNN.json`、`VISUAL_STABILITY_REPORT.json`、
`QA_REPORT.md`、`qa_metrics.json`、`contact_sheet.png`、`manifest.json`。

`manifest.json` 记录输入哈希、音色参考哈希、TTS 模型与参数、字体与媒体、依赖版本、
分辨率、fps、时长、成片哈希、QA 结论和复现步骤。用了用户素材就记原始文件哈希；
`timing_mode` 不是默认值、或某些单元用了 seed offset，都要写进去——
否则"可复现"是假的。

`QA_REPORT.md` 最后留一节 **「未做 / 需要你拍板的事」**，逐条写清楚：
没发布（永远是第一条）、无法独立核验因而只作转述的论断、为时长舍弃的内容、
对用户口述文案做过的纠正及其回退方法。这一节是给用户做决定用的，
不是给自己开脱用的——每条都要具体到"要改的话我怎么改"。

### 可选：标题与简介

用户要标题/简介时，给 3–4 个不同方向（痛点前置 / 提问 / 第一人称经验 / 结果导向）
并标出推荐哪个，再给短平台版和长平台版各一份简介。
**禁止编造没有实测支撑的数字**（"省 80% token"）——片子里没有的数据，标题里也不能有。

## 汇报格式

只做简洁、可验证的汇报：成片绝对路径/时长/分辨率/fps/大小；使用的音色与 `TIMING_MODE`、
是否压缩文案；单元数/节拍数/章节数；门禁是否全过、修了什么；QA 报告与工程源文件路径。
同时明确报告重复英文词/专名的数量、发音一致性门禁结论，以及静态文字/背景稳定性门禁结论。
用了用户素材就说清楚用在哪一章、怎么处理的。
对用户口述文案做过的任何纠正、以及因时长做的压缩，都要在汇报里主动说，不要等用户发现。
**有任何未通过项就写「未完成」，不要写「已完成」。**

## 脚本清单

| 脚本 | 作用 |
| ---- | ---- |
| `init_project.py` | 生成 `video.config.json` + 目录，自动找 TTS snapshot 与 venv，报告缺失项 |
| `vconfig.py` | 配置层：所有脚本的路径与阈值都从这里读 |
| `build_micro_script.py` | `chapters.json` + `beats.jsonl` → `micro_script.json` |
| `check_verbatim.py` | 逐字符核对 SRT + schema 校验；DURATION_LOCKED 下改为核对压稿有无记录 |
| `audit_pronunciation.py` | 铁律 8 的机器实现：台账 ↔ 脚本 ↔ 逐段回读；`--selftest` 证明能失效 |
| `generate_tts.py` | 本地音色克隆逐单元生成、母带、拼装、`alignment.json`；`--only` / `--remaster` |
| `loudness.py` | BS.1770-4 门控响度 / 短时 / 真峰值 / 归一化 / 前瞻限峰（任意长度成立） |
| `loudness_probe.py` | 独立响度诊断报告 |
| `sync_timing.py` | 用 `alignment.json` 重写 composition 的 `data-*` 时间 |
| `build_docs.py` | 生成 `SCRIPT.md` / `STORYBOARD.md` / `SCRIPT_CHANGES.md`（不覆盖手写版） |
| `qa_video.py` | 30 项自动门禁 → `qa_metrics.json` |
| `verify_asr.py` | 本地 whisper 转写与脚本比对 |
| `check_static_stability.py` | 铁律 9 的机器实现：成片抽帧查全局漂移；`--selftest` 证明能失效 |
| `safe_area_check.py` | 竖屏平台安全区越界像素统计 |
| `deliver.py` | 版本化交付 + `manifest.json` |
| `hf.sh` | 离线 HyperFrames CLI 包装 |

## 参考文档

| 文件 | 内容 |
| ---- | ---- |
| `reference/loudness.md` | 响度一致性的完整标准、DSP 实现要点、`loudness.py` API |
| `reference/pitfalls.md` | R1–R16 历史缺陷：症状、证据、根因、修法、复验 |
| `reference/hyperframes-traps.md` | 本管线踩过的 HyperFrames 契约陷阱 |
| `reference/beat-table.md` | `chapters.json` / `beats.jsonl` / `micro_script.json` schema 与发音字典规则 |
| `reference/visual-language.md` | 视觉禁令清单与竖屏构图正向要求 |
| `reference/user-footage.md` | 用户录屏/实拍的验源、裁切、取景放大、标注与安全区 |
