# 响度一致性

## 为什么单一的综合 LUFS 不够

`ebur128` 给出的整片综合响度是一个**门控平均值**。它可以在某几段被衰减 40 dB 的情况下
依然落在 −17 LUFS 的目标区间里——那些段太短，对门控平均的贡献被稀释掉了。
观众听到的"忽大忽小"是**段与段之间的离散度**，一个合格的综合 LUFS 完全可以掩盖它。

所以必须逐段测量。下列全部指标在**成片音轨**上测（从交付的 mp4 解码音频），
不是在中间 wav 上测——渲染器会给内嵌音轨附加增益。

| 指标 | 阈值 | 为什么是它 |
| ---- | ---- | ---------- |
| 逐单元门控响度（BS.1770-4）标准差 | ≤ 1.0 LU | 直接量化"段间忽大忽小" |
| 逐单元响度极差（max − min） | ≤ 3.0 LU | 标准差会被大量正常段拉低，极差抓单点异常 |
| 偏离单元响度中位数 > 2 LU 的单元数 | 0 | 允许 0 个例外——1 个就听得见 |
| 短时响度（3 s 窗 / 100 ms 步进，仅有声区）标准差 | ≤ 1.5 LU | 抓单元**内部**的起伏，单元级指标看不到 |
| 短时响度 P5–P95 | ≤ 4.0 LU | 对离群点比标准差稳健 |
| 每章平均单元响度与节目响度之差 | ≤ 1.0 LU | 抓整章系统性偏移 |
| 4× 过采样真峰值 | ≤ −1 dBTP | 采样峰值不等于真峰值；重采样后会更高 |
| 节目综合响度 | −18 ~ −16 LUFS | 语音讲解的常规投放区间 |

统计短时响度时**只统计有声区**（用绝对门 −70 LUFS 排除静音块）。
把段间静音也算进去的话，停顿越多标准差越大，指标会变成"停顿计数器"而不是响度指标。

## 禁止用 ffmpeg 的 loudnorm 做单元级归一化

`loudnorm` 的单遍模式需要 **≥ 3 秒**输入。短于 3 秒时它照样输出一组测量值，
**不报错、不警告**，而那些值是无效的。后果是最高 45 dB 的错误衰减。

实测证据（86 个旁白单元的一支片子）：15 个单元短于 3 s，**这 15 个全部被毁**
（最差的 `b052` 峰值只剩 0.005，约 −63 LUFS，实际近乎听不见）；
71 个 ≥3 s 的单元**全部正常**。相关性 100%。

短单元在这条管线里是常态——旁白单元的设计目标就是 2–7 秒。
单元级测量与归一化必须用在任意长度下都成立的 BS.1770-4 门控实现，见 `scripts/loudness.py`。

## 实现约束

- **单元级只允许线性增益 + 前瞻真峰值限制。** 不允许压缩、EQ、时间伸缩。
  单元内部的自然强弱要保留，被拉齐的只能是单元**之间**的电平。
- **节目级同样只做一次线性增益 + 限峰**，避免把已经对齐的相对关系再次打散。
- **单元母带存 float32 或 24-bit。** 单元增益可能达到 +12 dB，
  16-bit 中间文件会把量化噪声一起放大。（节目母带可用 PCM_24 换取解码器兼容性。）
- **母带处理不得改变任何单元的采样数。** 否则整条时间轴与画面失效。
  重跑母带后必须比对 `alignment.json` 确认时间码逐字段未变——
  `generate_tts.py --remaster` 会在任一单元时长漂移 >2 ms 时直接中止。
- **渲染器可能给内嵌音轨附加增益**（实测 +3.1 dB）。成片必须用母带音频重挂音轨
  （`-c:v copy`，画面零改动、零重渲染），并在成片上复测以上全部指标。

## scripts/loudness.py

纯本地、确定性、无网络的 BS.1770-4 实现。

```python
gated_lufs(x, sr, pre_weighted=False) -> float
    # 门控整体响度。绝对门 −70 LUFS，相对门 −10 LU。任意长度成立。

short_term(x, sr, block_s=3.0, hop_s=0.1) -> np.ndarray
    # 短时响度序列（LUFS），已剔除绝对门以下的块。

true_peak_dbfs(x, sr, oversample=4) -> float
    # 4× 过采样真峰值，分块处理避免大数组峰值内存。

normalise(x, sr, target_lufs, ceiling_dbfs) -> (np.ndarray, dict)
    # 迭代 gain→limit（≤4 次）直到 |delta| < 0.05 LU，累计增益钳在 ±18 dB。
    # 返回的 dict 含 input_lufs / output_lufs / gain_db / gain_reduction_db / true_peak_dbtp。

limit_peaks(x, sr, ceiling, lookahead_ms=5, hold_ms=20, smooth_ms=15) -> (np.ndarray, float)
    # 前瞻峰值限制：max 包络 → hold → Hann 平滑 → 与硬性要求逐点取 min。

kweight(x, sr)            # K 加权（high-shelf + RLB high-pass）
block_powers(y, sr, block_s, hop_s)   # 400 ms 块功率网格
```

### 限峰器里的一个坑

平滑增益曲线时用 `np.convolve(gain, window, mode="same")` 会**零填充边界**，
在每个片段的首尾各挖出半个窗口宽的增益凹陷（实测降到 ~0.5）。
表现是每一个单元都报告"6.0 dB 限峰量"——一个整齐得可疑的数字。
修法是卷积前用边界值填充，再用 `mode="valid"`：

```python
pad = smooth // 2
padded = np.concatenate([np.full(pad, gain[0]), gain, np.full(pad, gain[-1])])
gain = np.convolve(padded, window, mode="valid")[: gain.size]
```

单元测试：峰值恰好落在 ceiling、边界增益 1.0000、响度变化 −0.017 LU。

另外 `normalise` 要用 `reduction = max(reduction, gr)` 汇总各次迭代的限峰量，
只记最后一次会低报。

## 诊断

```bash
python "$SKILL/scripts/loudness_probe.py"          # 默认测成片
python "$SKILL/scripts/loudness_probe.py --wav …"  # 测任意 wav
```

报告节目响度、逐单元分布、短时分布、逐章偏差、最差的若干段。
`qa_video.py` 把上表的 7 项做成门禁并写进 `qa_metrics.json → loudness_consistency`；
逐段母带增益记录在 `mastering_report_vNN.json`。

## 加新门禁时的自检

**门禁本身必须能失效。** 加完这 7 项后，把它们直接跑在修复前的成片上，
应当 6/6（响度一致性部分）判为 FAIL。跑不出 FAIL 说明门禁写错了，
而不是说明片子好——一个恒真的检查比没有检查更危险，因为它会写进 QA 报告里当证据。
