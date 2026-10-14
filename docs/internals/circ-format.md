# `.circ` 文件格式 — 基于真实样本与源码的规则汇总

> **Paths in this document refer to the development tree, not to this package.** The commands
> and file paths were recorded while the measurements were taken and are kept verbatim as
> evidence. This package ships no circuits, no test suite and no `examples/` directory — write
> your own spec and circuit files as shown in `README.md` §5.

- 依据样本：`examples\user_samples\` 中 9 个 **Logisim Evolution 5.0.0** GUI 保存的真实文件（只读）
- 依据源码：`logisim-evolution-main\`（5.1.0-dev，只读）
- 事实提取工具：`scripts\analysis\analyze_sample.py`（只读），原始输出见 `docs\samples\_facts.json`
- 相关文档：`docs\evidence-matrix.md`（每条结论的置信度）、`references\00-probe-log.md`（实测陷阱）

> **本文只写「实际观察到/源码证实」的内容。** 推断一律标注「推断」，未确认标注 `[未确认]`。

---

## 1. 文件骨架

9 个样本的文件头完全一致：

```xml
<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<project source="5.0.0" version="1.0">
  ... <!-- 一条说明注释 -->
  <lib desc="#Wiring" name="0"/>
  ...
  <main name="main"/>
  <circuit name="main"> ... </circuit>
</project>
```

| 事实 | 值 | 来源 |
|---|---|---|
| XML 声明 | 恒为 `encoding="UTF-8" standalone="no"` | 9/9 样本 |
| `project@source` | `5.0.0`（GUI 版本） | 9/9 样本 |
| `project@version` | `1.0`（**文件格式版本，与软件版本无关**） | 9/9 样本 |
| 注释 | 每个样本都有一行 "This file is intended to be loaded by Logisim-evolution v5.0.0(...)" | 9/9 样本 |
| `<toolbar>` 元素 | 存在（GUI 保存的标志之一） | 9/9 样本 |

### 1.1 ⚠️ `source` 与我们的构建版本不同 —— 兼容性事实

样本由 **5.0.0** 保存，本项目的 CLI 是 **5.1.0dev**（由源码自行构建）。样本能被 5.1.0dev
正常加载并仿真（见 `docs\samples\*.md` 的原始输出），说明**该版本区间向前兼容**。

**但不要推广为「任意版本都兼容」。** Logisim 的 `source` 只用于提示，不代表校验；实际兼容性
必须以「加载 + 仿真成功」为准，而不是以版本号推断。

---

## 2. ⚠️ 最重要的一条：`<lib>` 声明 ≠ 实际使用的库

**9/9 样本都声明了全部 13 个库**，且 ordinal 恒为 `0..12` 固定顺序：

| name | desc | name | desc |
|---|---|---|---|
| 0 | `#Wiring` | 7 | `#TTL` |
| 1 | `#Gates` | 8 | `#TCL` |
| 2 | `#Plexers` | 9 | `#Base` |
| 3 | `#Arithmetic` | 10 | `#BFH-Praktika` |
| 4 | `#FPArithmetic` | 11 | `#Input/Output-Extra` |
| 5 | `#Memory` | 12 | `#Soc` |
| 6 | `#I/O` | | |

而实际用到的库差别极大：

| 样本 | 实际用到的库 | 未用到却仍声明 |
|---|---|---|
| `NOT.circ` | `#Wiring`(0), `#Gates`(1) | 其余 11 个 |
| `Tunnel_sample.circ` | `#Wiring`(0) | 其余 12 个 |
| `Adder_sample.circ` | `#Wiring`(0), `#Arithmetic`(3) | 其余 11 个 |

**结论（通用规则）**：
1. **不能用 `<lib>` 列表判断电路用了哪些库** —— 它反映的是编辑器保存时的库清单，不是使用情况。
2. ordinal 是**文件内局部**的，由声明顺序决定；`<comp lib="3">` 指向的是**第 3 个被声明的 `<lib>`**，
   不是"第 3 号库"。`XmlWriter.java` 按 `libs.size()` 顺序分配（`references\01-file-format.md`）。
3. **只要每个 `<comp lib=>` 都能对上一个已声明的 `<lib name=>`，声明顺序本身可以任意** ——
   样本恰好都是 `0..12` 连续，但这**不代表**生成时必须如此。`[未确认]` 非连续 ordinal 是否
   被接受（`XmlReader.java` 用字面 `name` 字符串做键，推测可以，但未实测）。

> **对我们的生成器意味着**：只需声明实际用到的库，并保证 ordinal 自洽即可。宣称"必须声明全部
> 13 个库"是**错误的** —— 那是 GUI 的行为，不是格式的要求。

---

## 3. `<circuit>` 与元件

### 3.1 circuit 级属性（9/9 样本都有）

```xml
<circuit name="main">
  <a name="appearance" val="logisim_evolution"/>
  <a name="circuit" val="main"/>
  <a name="circuitnamedboxfixedsize" val="true"/>
  <a name="simulationFrequency" val="16.0"/>
  ...
```

| 属性 | 观察值 | 说明 |
|---|---|---|
| `appearance` | 恒为 `logisim_evolution` | 子电路外观样式 |
| `circuit` | 恒等于 circuit 的 `name` | 冗余但恒定出现 |
| `circuitnamedboxfixedsize` | 恒为 `true` | 名称框固定尺寸 |
| `simulationFrequency` | 恒为 `16.0` | 仿真时钟频率（Hz） |

`[未确认]` 这些属性**是否必需** —— 我们手写的最小电路（`tests\circ\smoke\minimal.circ`）没有它们
也能加载并仿真，所以推测都是**可选**的。**这是重要区别**：GUI 会写，但格式不要求。

### 3.2 `<comp>` 的属性

```xml
<comp lib="0" loc="(290,210)" name="Pin">
  <a name="label" val="A"/>
  <a name="type" val="input"/>
  <a name="appearance" val="classic"/>
  <a name="width" val="8"/>
</comp>
```

**通用规则**：`<comp>` 自身只有三个属性 —— `lib`、`loc`、`name`；其余一切参数都是
`<a name= val=>` 子元素。这与 `references\02-libraries-components.md` 一致。

**观察到的属性是可选的**：
- `type` 在部分样本中出现 `input`，而**输入引脚常常完全没有 `type`**（默认即输入）；
  输出引脚则总带 `type="output"`。
- 引脚 `appearance` 出现两种值：`classic` 与 `NewPins`。
- 门 `appearance` 为 `logisim_evolution`。

`[未确认]` `appearance` 各取值的视觉差异，以及它是否影响仿真 —— 推测**不影响功能**（只影响绘制），
但未实测验证。

### 3.3 坐标

- `loc="(x,y)"` 为整数像素坐标；样本坐标多为 10 的倍数（网格对齐），但也有 `(230,260)` 这类。
- **`[未确认]` 是否存在强制网格**：样本都用 10 的倍数，我们也一直用 10 的倍数。
  但格式本身是否要求，未实测（`references\03-geometry-layout.md`）。
- `wire` 用 `from`/`to` 两个坐标对，如 `<wire from="(260,180)" to="(400,200)"/>`。

---

## 4. 引脚（`Pin`）

| 事实 | 观察 |
|---|---|
| XML 名 | `Pin`，库 `#Wiring`（ordinal 0） |
| 方向 | 输入**可不写** `type`；输出必须 `type="output"` |
| 位宽 | `width` 属性；缺省为 1（`NOT.circ` 的引脚无 `width`） |
| 名称 | `label` 属性；**可以缺失**（`NOT.circ`、`RAM_sample.circ` 的引脚都无 `label`） |
| 朝东/朝西 | 输出引脚常带 `facing="west"` |

**样本证据**：
- `Half_Adder.circ`：`A,B`（输入，1 位）→ `S,C`（输出，1 位）
- `Adder_sample.circ`：`A,B`（输入，8 位）→ `Output`（输出，8 位）
- `NOT.circ`：两个引脚**都没有 label**，靠位置区分
- `RAM_sample.circ`：5 个引脚**都没有 label**（1 位×2、8 位×2、8 位输出×1）

> ⚠️ **推论（对验证的影响）**：`--tty table` 的列表头用的是引脚 `label`。**无 label 的引脚无法被
> 按名称引用**，且表头排序列依赖坐标（y 然后 x）。所以生成器应**始终给引脚加 label**，
> 而分析无 label 的样本时必须按**坐标顺序**解读列，不能按名称。
> 这一点直接解释了为什么 `NOT.circ` 与 `RAM_sample.circ` 的验证比别人费劲。

---

## 5. 与生成相关的要点（来自样本的正面确认）

1. **手写最小文件足以加载** —— 我们的 `minimal.circ` 省略了 `<toolbar>`、circuit 属性和全部
   未用库，仍能正常加载与仿真。**GUI 保存的文件更冗长，但不是格式要求。**
2. **生成时只声明用到的库**，ordinal 自洽即可。
3. **给每个引脚加 `label`** —— 无 label 会让后续验证无法按名称定位。
4. **写 UTF-8 无 BOM**，`\n` 换行（见 `references\00-probe-log.md`；本机 Python 默认 cp936）。

---

## 6. 未确认清单

| # | 问题 | 为何未确认 |
|---|---|---|
| F1 | 非连续 / 乱序 `<lib name=>` 是否被接受 | 样本恰好都是 0..12 连续 |
| F2 | circuit 级属性是否必需 | 我们的最小文件省略后可加载，但未做对照实验 |
| F3 | `appearance` 是否影响仿真 | 未实测，推测只影响绘制 |
| F4 | 是否存在强制坐标网格 | 样本与我们的文件都恰好网格对齐 |
| F5 | `source="5.0.0"` 与 `5.1.0dev` 的完整兼容边界 | 仅测了这 9 个样本 |
| F6 | 未知属性/未知顶层元素的确切处理 | 源码显示未知属性被忽略、未知顶层元素报错（`references\01`），未逐个实测 |
