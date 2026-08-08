# HyperFrames 契约陷阱

composition 的正式契约在 `/hyperframes-core`（`data-*` 计时、`class="clip"`、tracks、
sub-composition、变量、框架托管的媒体播放、确定性渲染），CLI 在 `/hyperframes-cli`，
动画在 `/hyperframes-animation`。**先读那些，再写代码，不要凭记忆猜 API。**

本文只记这条管线上踩过、而上面几份文档里没有直说的坑。

## 数据不能放在 `<script type="application/json">` 里

运行时会剥离子 composition 里的 `<script type="application/json">` 块。
表现是时间线全部未构建、画面停在静态第一帧，`check` 报
`Cannot read properties of null`。

**改法**：把节拍表挂到 composition 内一个零尺寸子元素的 `data-*` 属性上。

```html
<div id="ch3-data" style="width:0;height:0;overflow:hidden"
     data-beats='[{"id":"b014","start":62.4,"dur":3.1}, …]'></div>
```

## 时间线必须同步创建、暂停并注册

每个 composition 只能有**一个**时间线，且必须在同步执行路径上创建 → `pause()` → 注册。
放进 `await` 之后、`requestAnimationFrame` 里、或任何异步回调里，渲染器 seek 时它还不存在。
症状是 `render.log` 里的 `timelines not registered`。

## GSAP target 必须真的在 DOM 里

写了 CSS + 时间线但忘了挂 DOM 元素，`check` 报 `GSAP target #x not found`，
渲染却"成功"——只是那条动画静默不生效。**把这个 warning 当 error 处理**：
要么补上元素，要么删掉死代码。

## 全屏背景挂在绝对定位的全屏子元素上

不要依赖 composition 根元素的背景色/背景图。根元素的尺寸与合成时机不由你控制，
背景会在某些帧上漏底。放一个 `position:absolute; inset:0` 的子元素承担背景。

## ID 全局唯一

composition ID、DOM ID、时间线键在**组装页面**里必须唯一。
九个章节各自复制粘贴一套 `#title` `#grid` 的写法会在拼装后互相覆盖，
而单章预览完全正常——这类 bug 只在 `index.html` 上才暴露。用 `#chN-` 前缀。

## 确定性渲染

禁止 `Date.now()`、未设 seed 的随机数、运行时网络请求、无限循环、依赖实时状态的动画。
环境运动（颗粒漂移之类）必须是时间的纯函数或固定 seed，
否则每次渲染的帧都不一样，`freezedetect`/`compare` 之类的检查全部失去意义。

不要对布局属性做不稳定 tween，不要在 tween 中读取动态 DOM 几何。

## 音频只放一份

媒体播放由框架管理。旁白只挂一份，不要在每个场景里重复叠加——
叠加不会报错，只会让成片的旁白重影。

## 离线运行 CLI

用 `scripts/hf.sh` 而不是 `npx hyperframes`。`npx` 可能在草稿与最终渲染之间
拉到不同版本，那会让两次渲染不可比。`hf.sh` 解析本地已安装的版本并直接 `node` 执行，
可用 `HYPERFRAMES_BIN` 覆盖。

## 可以接受的持久 warning

不是所有 warning 都要清零，但**每一条都要逐项审查并写进 QA 报告**。本管线接受过两类：

- `composition_file_too_large`——信息可视化本身复杂，继续拆分会引入跨文件挂载风险
  （见 R1），而不会改善成片。
- `container_overflow`（背景径向辉光）——`design.md` 明确允许装饰性元素进入出血区。

接受的理由要写下来。"warning 一直在所以忽略"不是理由。
