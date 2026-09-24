# 节拍表：格式与规则

## 你写两个文件，脚本生成第三个

```
video_work/chapters.json   章节表（含每章的视觉世界）
video_work/beats.jsonl     节拍表，每行一个 JSON 对象，按播放顺序
        ↓  build_micro_script.py
video_work/micro_script.json   自动编号 + 自动估时长
        ↓  check_verbatim.py    ← 必须 PASS 才能往下走
```

手写 `micro_script.json` 也可以，但插入一个节拍就要手工重编 86 个 id，
而 id 同时出现在 composition HTML、`alignment.json` 和 QA 报告里。别这么干。

## chapters.json

```json
[
  {"key": "ch1", "name": "开场 · 七个概念", "srt_id": 1,
   "world":  "纵向路线图：一条脊线串起 01–07，右侧是四条评价轴",
   "layers": "前景：标题与节点名称／中景：脊线与节点刻度／背景：暖调辉光 + 60px 网格 + 纸张颗粒",
   "enter":  "片头，从墨色画布升起",
   "exit":   "内容上推 0.36s，下一章硬切"}
]
```

`key` 必须和 composition 文件名对应（`compositions/ch1.html`）。
`world` / `layers` / `enter` / `exit` 是可选的，只喂给 `build_docs.py` 生成 STORYBOARD.md；
不填就留占位符。**填了对后面写 composition 有实质帮助**——它逼你在写代码前
先确定这一章的空间组织和上一章有什么不同。

## beats.jsonl

每行一个对象，注释行以 `//` 开头（会被跳过）：

```jsonl
// ---------------- ch2 MHA ----------------
{"chapter":"ch2","text":"MHA，多头注意力，2017年Transformer的原配。","tts":"M H A，多头注意力，二零一七年 Transformer 的原配。","role":"setup","emphasis":["多头注意力","2017"],"metaphor":"章节卡：年份刻度落在 2017，Transformer 字样浮出","screen":["01 MHA","多头注意力 · 2017"]}
{"chapter":"ch2","text":"原理一句话：把Q、K、V切成h份，","role":"mechanism","emphasis":["Q","K","V","h份"],"metaphor":"一条 token 流分裂成 Q/K/V 三束，再纵向切成 h 条通道","screen":["Q / K / V → h 份"]}
```

| 字段 | 必填 | 说明 |
| ---- | ---- | ---- |
| `chapter` | ✓ | `chapters.json` 里的 `key` |
| `text` | ✓ | **原文，逐字来自 SRT。** 这是屏幕文字与字幕的来源 |
| `tts` | | 只在读法需要纠正时给出；不给就等于 `text` |
| `role` | ✓ | `hook` / `setup` / `mechanism` / `contrast` / `example` / `result` / `summary` |
| `emphasis` | ✓ | 关键词，必须是 `text` 的子串（`check_verbatim.py` 会校验） |
| `metaphor` | ✓ | 本节拍的视觉动作，用**具体运动动词** |
| `screen` | ✓ | 屏幕文字，不是旁白复刻 |

`build_micro_script.py` 补上 `id`（`b001`…）、`chapter`（章节名）、
`source_srt_ids`、`estimated_duration_s`（粗估，仅供分镜草案；真实时间以 TTS 对齐为准）。

## 切分规则

每单元 **12–35 汉字 / 1–2 个语义分句 / 预计 2–7 秒**，优先在句号、问号、分号、冒号
和自然呼吸点切分。

**不拆开**：专名、英文缩写、数字与单位、公式、`Q/K/V`、`O(n²)` 这类完整概念。

**要拆开**：同一句里出现「旧方案 vs 新方案」「原因 → 结果」「旧状态 → 新状态」时，
拆成独立节拍——它们是两个认知动作。

**可以合并**：相邻短句共享同一个视觉模型时。

每个节拍只传达一个主要认知动作：提出问题、建立模型、展示变化、形成对比、给出结论。

### 长度的两个软警告

`check_verbatim.py` 会提示 >40 字和 <6 字的单元。都不是硬失败，但都要看一眼：
- 超过 40 字：画面跟不上，观众要么看不完屏幕文字要么听漏旁白；
- 少于 6 字：容易把语气切碎，除非它本来就是一个独立的短促强调。

## 全局发音台账

生成任何旁白前先写 `video_work/PRONUNCIATION_LEDGER.json`，它是全片唯一的读法真相。
只改送给 TTS 的读法，**不改展示文本**。映射自动收进 `SCRIPT_CHANGES.md`。

每个条目至少包含：

```json
{
  "surface": "Action Expert",
  "normalized": "action expert",
  "canonical_tts": "Action Expert",
  "source": "official-or-common-usage",
  "occurrences": ["b003", "b027", "b061"],
  "verified": true
}
```

连字符词、品牌后缀、型号前后缀或其他组合词还要声明组成关系。例：

```json
{
  "surface": "Zeva-Ego",
  "normalized": "zeva-ego",
  "canonical_tts": "ZeevaEgo",
  "source": "component pronunciations verified separately",
  "occurrences": ["b008", "b026", "b038"],
  "verified": true,
  "high_risk": true,
  "components": [
    {"normalized": "zeva", "compound_tts": "Zeeva"},
    {"normalized": "ego", "compound_tts": "Ego"}
  ],
  "continuity": {"required": true, "max_gap_ms": 80}
}
```

`components` 的顺序就是口播顺序。`compound_tts` 可以采用不同正字法，但必须与该组成词已确认的
音素和重音一致；不能为了“连读”把组成词换成另一种近似音。`continuity` 只约束词间静音，
不授权改变读法。用户点名纠过的词、标题或开场高曝光专名标记 `high_risk:true`。

- `normalized` 相同的所有出现位置必须使用完全相同的 `canonical_tts`，不得按章节或上下文临时改写。
- 大小写、单复数或连字符变化若读音相同，应归并到同一个规范项；确实不同才拆项并说明理由。
- 英文单词、缩写、品牌名、产品名、模型名、技术术语和旁白会读到的数学标识符必须全量入表；
  重复项必须列出全部 beat id。
- 生成后按条目串联试听全部出现位置；ASR 文本相同不等于读音一致，仍要核对音素和重音。
- 组合词至少试听一次“独立组成词 → 组合词”的 A/B 序列；逐词时间戳只证明边界和停顿，
  不证明音素完全正确。人工确认结果写入 `PRONUNCIATION_QA.json`。
- 任一重复词前后读法不同 → FAIL；先更新全局台账，再只重生成受影响的单元。

常用读法规则：

| 类型 | 规则 | 例 |
| ---- | ---- | -- |
| 英文缩写 | 按字母读，字母间加空格 | `MHA` → `M H A`；`KV Cache` → `K V Cache` |
| 年份 | 逐位读 | `2017年` → `二零一七年` |
| 数学符号 | 读成中文 | `O(n²)` → `O n 平方`；`n×n` → `n 乘 n` |
| 数学标识符 | 按用户偏好/领域习惯固定朗读形式，不把排版符号直接送给 TTS | `x_t` → `xt` 或 `x 下标 t`，全片只能选一种 |
| 大数 | 读成中文 | `128K到1M` → `十二万八千到一百万` |
| 百分数/倍数 | 读成中文 | `25%` → `百分之二十五` |
| 希腊字母 | 中文 TTS 没有稳定读法 → 换成语义词，符号只出现在画面上 | `φ` → `函数` |
| 换气 | 可以在 `tts` 里加逗号求自然换气，`text` 不加 | |

## 常见错误

- **在 `text` 里"顺手"改了个错别字或加了个逗号。** `check_verbatim.py` 会当场报出分叉位置。
  确实需要纠正原稿时，走 `SCRIPT_CHANGES.md` 记录原句/改句/理由，不要静默改。
- **`emphasis` 写了 `text` 里没有的词**（比如写了 TTS 读法而不是原文）。同样会被拦下。
- **`screen` 抄了整句旁白。** 屏幕文字是提炼，中文正文 ≤2 行、每行 14–18 字。
- **`metaphor` 写成"淡入"。** 那不是视觉模型。写"分裂""聚拢""折叠""闸门开合""缓存增长"。
