# Logisim Circuit Skill — 总体架构

> **Paths in this document refer to the development tree, not to this package.** The commands
> and file paths were recorded while the measurements were taken and are kept verbatim as
> evidence. This package ships no circuits, no test suite and no `examples/` directory — write
> your own spec and circuit files as shown in `README.md` §5.

- 项目根目录：`<your checkout directory>`
- 版本：Rev 1（2026-10-14）
- 依据：`docs\environment-audit.md`（已实测事实）、`docs\project-plan.md`（阶段划分）

---

## 1. 设计原则

架构由三条原则推导而来，每条都针对一个具体已发现的风险：

| 原则 | 针对的风险 |
|---|---|
| **知识必须可溯源** | AI 凭记忆编造元件名/属性名/坐标 → 生成无法加载的 `.circ` |
| **正确性只能由真实仿真证明** | 结构检查通过被误当成功能正确 |
| **验证框架本身必须可被怀疑** | 「0 失败」可能来自「根本没跑」或「结果解析失败」 |

因此架构分成三层：**证据层**（知识从哪来）、**工具层**（确定性执行）、**Skill 层**（AI 的工作流）。

---

## 2. 三层架构

```
┌──────────────────────────────────────────────────────────────┐
│ Skill 层   —— AI 的工作流与约束                                │
│  SKILL.md + workflows/{analyze,design,generate,verify,repair} │
│  职责：理解自然语言需求 → 拆解 → 调用工具 → 依据证据决策        │
│  禁止：自行发明格式、自行推导期望输出                          │
└───────────────┬──────────────────────────────────────────────┘
                │ 只调用工具，不直接写 XML
┌───────────────▼──────────────────────────────────────────────┐
│ 工具层   —— 确定性、可单测                                     │
│  IR ↔ .circ 序列化 / 布局 / 结构校验 / 向量生成 / 仿真调用      │
│  职责：把「AI 的意图」变成「可验证的产物」                      │
└───────────────┬──────────────────────────────────────────────┘
                │ 一切事实来自
┌───────────────▼──────────────────────────────────────────────┐
│ 证据层   —— 防幻觉的基石                                      │
│  references\01..06  +  docs\CAPABILITIES.md                   │
│  每条断言标注：VERIFIED（带行号/实测） / UNVERIFIED / REFUTED  │
└──────────────────────────────────────────────────────────────┘
```

**关键设计决定：为什么不让 AI 直接写 `.circ` XML。**
`.circ` 的 `<comp lib="0" name="Pin">` 依赖**文件内的库序数**与**精确的元件 XML 名**。让模型直接输出 XML，等于把「记住所有元件属性名」这一易错任务交给概率模型。因此 XML 只由 `circgen` 依据 IR 生成 —— **模型负责结构意图，工具负责语法正确**。

### 2.1 IR 必须分成三个子层（本阶段为设计，未实现）

上述「三层」是**系统分层**。IR 自身还必须再分三层，否则图形缺陷无法被发现。
详见 `docs\ir-schema.md`；布局与布线算法见 `docs\layout-routing-design.md`。

```
拓扑层   谁该连谁          net -> [(part, port)] + 显式位宽
几何层   占哪块矩形、端口在哪  实测 bounds + 由 facing 算出的端口坐标
布局层   放哪、怎么走、有无冲突  placement / routes / occupancy / conflicts
```

**为什么必须分开——实测证据**：现有生成器只有拓扑层，几何层是硬编码的端口坐标表，布局层不存在。
坐标是「从端口反推」的，**元件本体从未参与计算**。后果是同一批文件
**宽度审计 0 问题、仿真 512/512 全通过，同时存在 6 处导线穿过元件本体**：

```
wire (620, 700) -> (620, 330)
Register@(600, 300)  bounds = (600, 300, 60, 90)   →  x ∈ [600, 660]   ← 620 在框内
```

`x=620` 是从 `Q`/`D` 端口所在的 `y=330` 反推的，没有任何一步检查过它是否落在 `600..660` 内。

**核心不变量：改布局不得改变拓扑。** 调整后必须重新解析 `.circ`、重建网络、确认 net 集合与位宽
完全一致，并重新仿真（结构检查永远不能替代仿真）。

### 2.2 项目级结构：这就是「工具栏消失」的根因 ✅ 已定位

用户反馈：新生成的 `.circ` 打开后，**原本正常的工具栏或界面元素不见了**。
根因已用**源码 + 9/9 对 4/4 的结构对比**定位，不是推测。

**Logisim 的工具栏来自 `.circ` 的 `<toolbar>` 元素，而它没有默认值。**

`ToolbarData()`（`ToolbarData.java:29-33`）初始化为**空列表**：

```java
public ToolbarData() {
  listeners = new EventSourceWeakSupport<>();
  toolListeners = new EventSourceWeakSupport<>();
  contents = new ArrayList<>();          // ← 空，无任何默认工具
}
```

`XmlReader` **只**在遇到 `<toolbar>` 元素时才填充它（`XmlReader.java:536-537`）：

```java
case "toolbar":
  initToolbarData(sub_elt, isHolyCrossFile, isEvolutionFile);
```

**因此缺少 `<toolbar>` 的文件打开后工具栏是完全空的**——正是用户描述的现象。

结构对比（`scripts\analysis\audit_orientation.py`）：

| 顶层元素 | 用户样本 9 个 | 本项目生成 16 个 |
|---|---|---|
| `<toolbar>` | **9/9** | **0/16** |
| `<options>` | **9/9** | **0/16** |
| `<mappings>` | **9/9** | **0/16** |
| `<lib>` 数量 | **13**（全部库） | 平均 **3.2** |
| `<circuit>` 静态属性 | 9/9 各 4 个 | **0** |

缺失的 `<circuit>` 静态属性（样本 9/9 全有）：
`appearance="logisim_evolution"`、`circuit="main"`、`circuitnamedboxfixedsize="true"`、
`simulationFrequency="16.0"`。

**待修（本阶段未改生成器）**：发射器必须补齐 `<toolbar>`、`<options>`、`<mappings>`、
全部 13 个 `<lib>`、以及 `<circuit>` 静态属性。`<lib>` 数量不足还意味着**元件库树不完整**
（只声明 2–4 个库，用户只能从这几个库里取元件）。

⚠️ **不作为的部分**：本次**未**验证「补上这些元素后工具栏确实恢复」——那需要实际打开 GUI，
本环境是 headless。**该结论有源码依据与结构对比依据，但没有 GUI 打开验证。**

### 2.3 朝向（facing）：生成器几乎不使用，而样本会使用

审计（`scripts\analysis\audit_orientation.py`，生成 16 个文件 vs 样本 9 个）：

| 元件 | 生成文件出现的 `facing` | 样本出现的 `facing` |
|---|---|---|
| `AND Gate` | **（从不设置）** | 未设置, **north, south, west** |
| `Tunnel` | 未设置, **west** | 未设置, **east** |
| `Splitter` | **east, west** | 未设置 |
| `Pin` | 未设置, west | 未设置, west |
| 门 / `Adder` / `Multiplexer` / `Register` / `Clock` / `Comparator` | **（从不设置）** | 未设置 |

两点已核实：

1. **样本中确实存在多方向的 `AND Gate`（north/south/west）**，所以朝向**不是**纯视觉属性，
   它真实影响端口坐标。生成器一律用默认 `east`，即**从不做朝向选择**。
2. **`Tunnel` 的朝向习惯相反**：样本用 `east`，本项目生成 `west`。
   `Tunnel` 的朝向不影响电气语义（按标签连接），但影响图形外观。

### 2.4 `Pin` 缺少 `appearance` 属性

样本的 `Pin` 属性集合比生成文件多一个 **`appearance`**（样本值 `classic`）：

| | 属性 |
|---|---|
| 共有 | `facing`, `label`, `type`, `width` |
| **样本独有** | **`appearance`**（`val="classic"`） |
| 生成独有 | 无 |

Logisim 5 的 `Pin` 有 `classic` / `evolution` 两种外观。缺该属性会走默认外观，
**与用户样本的视觉不一致**。`[UNVERIFIED]`：默认外观究竟是哪一种，未实测。

---

## 3. 目标目录结构

```
<your checkout directory>\
├─ docs\
│  ├─ environment-audit.md      环境与隔离审计（已完成）
│  ├─ project-plan.md           分阶段路线图（已完成）
│  ├─ architecture.md           本文件
│  ├─ acceptance-tests.md       验收标准（含 4 位全加器）
│  └─ CAPABILITIES.md           能力台账：CLI/仿真能做什么（P2 产出）
├─ references\                  知识库，每条断言带出处
│  ├─ 01-file-format.md         .circ XML 文法
│  ├─ 02-libraries-components.md 库/元件/属性/端口
│  ├─ 03-geometry-layout.md     坐标、连接 vs 交叉、引脚偏移
│  ├─ 05-test-vector-format.md  测试向量文法
│  └─ 06-failure-modes.md       观察到的错误与成因
├─ scripts\                     确定性工具
│  ├─ bin\logisim-evolution-all.jar   官方 CLI（5.1.0dev，已构建）
│  ├─ py\                       **实际存在**的模块
│  │  ├─ circmodel.py           组合电路 spec → .circ（含 IR 数据模型）
│  │  ├─ rtllib.py              RTL 生成：Mux/Decoder/Adder/Comparator/Splitter + 门
│  │  ├─ seqmodel.py            时序电路生成 + 时钟驱动验证
│  │  ├─ verify_model.py        对 spec 内独立金标准穷举验证（组合/RTL）
│  │  ├─ tabletool.py           headless 仿真调用 + 结果解析（验收案例入口）
│  │  ├─ audit_width.py         结构宽度/连接审计（W1-W5，无需仿真）
│  │  └─ repair.py              有界修复：候选编辑 + 真实仿真证明 + 阴性对照
│  ├─ analysis\                 样本分析、端口实测、审计脚本
│  └─ dsh\                      Harness 内部规则审计工具（含 validate-skill.js）
```

> ⚠️ **上方的模块树已按实际实现更正。** 早期版本列出的 `ir.py`、`circparse.py`、
> `netlist.py`、`layout.py`、`synthesize.py` 是**设计草案**，从未实现：它们的功能实际由
> `circmodel.py`/`rtllib.py`（spec→circuit）、`audit_width.py`（连接与宽度重建）、
> `repair.py`（编辑搜索）承担。另一组同样只存在于设计草案的名字是 `circgen.py`、
> `circvalidate.py`、`logic.py`、`testvector.py`、`verify.py`、`diagnose.py`。
> 列出它们是为了保留设计脉络，**不是**可执行的工具。

```
├─ scripts\(续)
├─ examples\                    经过真实仿真验证的电路样例
│  ├─ combinational\
│  └─ adder4\                   首个端到端验收产物
├─ tests\
│  ├─ py\                       Python 单测
│  ├─ vectors\                  生成的测试向量
│  └─ cases\
│     ├─ regression\            回归集（含历史故障）
│     └─ smoke\                 最小电路与阴性对照（已建）
├─ logisim-evolution-main\      只读参考源码（5.1.0-dev）
└─ PLAN.md                      早期规划稿（保留，未删除）
```

> **待用户确认（U1）**：Harness 只加载 `<projectRoot>\.dsh\skills\<name>\SKILL.md` 或
> `<projectRoot>\.agents\skills\<name>\SKILL.md`，**不扫描项目根的裸 `SKILL.md`**。
> 因此真正的 Skill 入口建议放在：
> `.dsh\skills\logisim-circuit\SKILL.md`，
> 其余 `docs\`/`scripts\`/`tests\`/`examples\` 作为开发工作区。
> 另注意：**Skill 目录在卸载时会被删除**，故持久产物不得放在 Skill 目录内。

---

## 4. 模块职责

### 4.1 证据层

| 模块 | 职责 | 约束 |
|---|---|---|
| `references\01-file-format.md` | `.circ` 元素/属性文法、库序数绑定、版本兼容 | 每条断言引用 `XmlReader.java`/`XmlWriter.java` 行号 |
| `references\02-libraries-components.md` | 库清单、元件 XML 名、属性名/类型/默认值、端口布局 | 严格区分「XML 名」与「界面显示名」 |
| `references\03-geometry-layout.md` | 坐标单位、网格吸附、电气连接判定、引脚偏移 | 以仿真行为为准，不以惯例为准 |
| `references\05-test-vector-format.md` | 向量文法、值格式、`<DC>`/`<float>`、`<set>`/`<seq>` | 精确到可据此写生成器 |
| `references\06-failure-modes.md` | 实测错误与成因 | 只收录实际观察到的 |
| `docs\CAPABILITIES.md` | 能力台账 + **假通过模式清单** | 每条附原始命令与输出 |

**证据等级定义**

| 等级 | 含义 | 可否用于生成 |
|---|---|---|
| `VERIFIED` | 实测通过，或有源码行号引用 | ✅ 可以 |
| `UNVERIFIED` | 看似合理但未验证 | ❌ **禁止** |
| `REFUTED` | 曾以为成立，已被否定 | ❌ 禁止，但**保留记录**以防重犯 |

### 4.2 工具层

| 模块 | 输入 → 输出 | 关键约束 |
|---|---|---|
| `ir.py` | — → IR | 纯数据，不含坐标美观逻辑；可序列化以便 diff |
| `circmodel.py` / `rtllib.py` / `seqmodel.py` | spec → `.circ` 文本 | 写入必须 `encoding="utf-8"`；库序数由代码分配，不靠模型 |
| `circparse.py` | `.circ` → IR | 与 `circgen` 构成往返对，单测保证等价 |
| `netlist.py` | IR + 几何 → 网络表 | 必须区分「电气相连」与「仅交叉」 |
| `layout.py` | 网表 → 带坐标 IR | 保证无悬空引脚、无重叠冲突 |
| `synthesize.py` | 真值表/表达式 → 网表 | 通用算法，**不得**内置「加法器模板」之类特例 |
| `audit_width.py` | 电路 → 诊断列表 | 只报可证明的问题；未建模元件记为 **opaque** 而非猜测 |
| `verify_model.py` | 电路 + spec → 结构化结论 | 金标准来自 spec 的 `expression`/`model`，不共享电路代码 |
| `verify_model.py` / `seqmodel.py` | 电路 + spec → 逐行对照 | 期望值来自 spec 的 `expression`/`model`，绝不复用被测电路代码 |
| `tabletool.py` / `seqmodel.py` | 电路 + spec → 结构化结论 | **不依赖退出码**；解析输出；解析失败 = 框架错误 |
| `analysis\audit_layout.py` | `.circ` → **布局冲突列表** | 几何来自 `SamplePorts` 实测，非推断；只读，不改文件 |
| `analysis\classify_layout.py` | 布局发现 → 真实缺陷 / 设计模式 / 假阳性 | 分类必须可审计；**不得把设计模式算成缺陷** |
| `analysis\audit_orientation.py` | `.circ` 集合 → 朝向与项目级结构对比 | 生成 vs 样本逐项对照，暴露差异 |
| `analysis\audit_attr_defaults.py` | — → 依赖的静默缺省清单 | 凡「省略即变义」的属性必须显式写出 |
| `analysis\LoadDiag.java` | `.circ` → 真实加载异常 | `Loader.showError` 的 `HeadlessException` 会掩盖真实错误 |
| `SamplePorts.java` | `.circ` → 实测 `bounds` 与端口 | 几何层的唯一权威来源 |

### 4.3 Skill 层

| 工作流 | 职责 |
|---|---|
| `analyze.md` | 读 `.circ` → 结构/功能说明（所有格式断言需能对应 `references\`） |
| `design.md` | 自然语言需求 → 规格（真值表/表达式/模块划分） |
| `generate.md` | 规格 → IR → `.circ`（经 `circgen`，不手写 XML） |
| `verify.md` | 生成 → 调 `verify_model.py` → 解读结论（区分「电路错」与「框架错」） |
| `repair.md` | 失败证据 → 定位 → 修改 IR → 重验（禁止在未重验时报成功） |

---

## 5. 数据流：一次完整的「自然语言 → 已验证电路」

```
用户自然语言需求
   │  design.md：拆解为可验证规格（真值表 / 表达式 / 模块划分）
   ▼
规格 (spec)                          ← 此处即可与用户对齐，成本最低
   │  synthesize.py（通用算法，无特例模板）
   ▼
网表 (netlist)
   │  layout.py（网表 → 合法坐标）
   ▼
IR
   │  circmodel.py / rtllib.py / seqmodel.py（spec → XML，编码固定 UTF-8）
   ▼
adder4.circ ──────────────┐
   │                      │ audit_width.py（结构审计，快速过滤）
   │                      ▼
   │                   诊断（若有缺陷，回到 IR 修改）
   │  seqmodel / circmodel 的 spec：内含【独立黄金模型】与接口宽度
   ▼
 spec JSON
   │  verify_model.py / seqmodel.py verify：
   │      java -Djava.awt.headless=true -jar <jar> --locale en --tty table <circ>
   │  解析实际输出表，逐行对照 spec 中的独立模型
   ▼
 结构化结论 ──► 通过 ──► 记录进 examples\ 与验收记录
           └─► 失败 ──► repair.md（定位→改 spec→重新生成→重验）
           └─► 框架错误（解析不到输出）──► **视为失败**，修框架
 ```

> ⚠️ **本图已按实际实现更正。** 早期版本描述的是 `--test-vector` + 向量文件路线，
> 但 `--test-vector` 在 headless 下抛 `HeadlessException`（`--new-file-format` 同样），
> 因此改用 **`--tty table`**：无 `halt` 引脚时穷举输入引脚，有 `halt` 引脚时走时钟驱动循环。
> 它引用的 `testvector.py`、`circgen.py` 从未存在；实际入口是 `verify_model.py`、
> `seqmodel.py`、`tabletool.py`、`circmodel.py`、`rtllib.py`。

**为什么在仿真之前还要结构审计**：审计是**廉价过滤器**，能在几毫秒内抓出悬空引脚/宽度不匹配
这类必然失败的电路。但审计 **永远不能** 替代仿真 —— 它无法证明功能正确。
它已被证明有价值：对全部生成电路跑一遍，查出**所有功能检查都漏掉的真实缺陷**
（`seq_divclk_reg.circ` 缺少 `QB -> D` 反馈，导致「分频时钟」这个前提根本不成立）。

---

## 6. 仿真与验证边界（诚实声明）

已实测确认（详见 `docs\environment-audit.md`）：

- CLI 具备 `--test-vector`、`--tty`、`--toplevel-circuit`、`--test-circuit`、`--new-file-format`；
- 强制 headless 与固定 locale 的手段已确认（`-Djava.awt.headless=true`、`--locale en`）；
- **已知风险**：源码显示 `--test-vector` 的失败计数被丢弃、退出码可能恒为 0 —— 故框架**不得依赖退出码**（待 P2 实测确认）。

尚未验证（`UNVERIFIED`，见环境审计 §5.2）：`--test-vector` 能否真正跑通并给出计数、
往返一致性、完整文法与元件属性。**这些在 P2 完成前不得用于任何生成逻辑。**

架构必须容忍「仿真能力有限」这一现实：`04/06` 与 `CAPABILITIES.md` 的职责就是把边界写清楚，
使 Skill 在超界时**声明无法验证**，而不是给出未经验证的「通过」。

---

## 7. 关键接口约定

### 7.1 验证器返回契约（最关键）

```
{
  "status": "passed" | "failed" | "error",
  "passed": <int|null>,
  "failed": <int|null>,
  "expected_vectors": <int>,
  "raw_stdout": "<原文>",
  "command": "<完整命令行>",
  "harness_error": "<若 status=error，说明为何无法采信输出>"
}
```

判定规则（**顺序敏感**）：

1. 命令行未成功启动 / 无任何输出 → `error`
2. 解析不到结果行 `Passed: X, Failed: Y` → `error`（**绝不**当作 passed）
3. 解析到 `failed > 0` → `failed`
4. 解析到 `failed == 0` 但 `passed != expected_vectors` → `error`（向量可能被误读/截断）
5. 解析到 `failed == 0` 且 `passed == expected_vectors` → `passed`

第 4 条专门防御「向量文件为空或格式错误导致 0 行被执行，于是 0 失败」。

### 7.2 强制调用参数

```powershell
java '-Djava.awt.headless=true' -jar scripts\bin\logisim-evolution-all.jar `
     --locale en --tty table <file.circ>
```

> ⚠️ 早期本文档此处的示例用 `--test-vector`，**已作废**：它在 headless 下抛 `HeadlessException`
> （`--new-file-format` 同样）。改用 **`--tty table`**：无 `halt` 引脚时穷举输入引脚，
> 有 `halt` 引脚时走时钟驱动循环。

- `-Djava.awt.headless=true`：本机默认**不是** headless（已实测）。
- `--locale en`：本机 JVM locale 为 `zh_CN`，否则结果行为中文且以 **MS936** 编码，无法按 UTF-8 解析。
- **禁止**对 java 进程使用 `Select-Object -First N` 之类截断管道：已实测会提前关闭管道杀死 JVM，
  产生**假的失败退出码**。

---

## 8. 待确认的架构问题

| # | 问题 | 影响 |
|---|---|---|
| A1 | Skill 入口放在 `.dsh\skills\` 还是 `.agents\skills\`（或两者） | 决定 Harness 能否加载 |
| A2 | `scripts\` 究竟放在 Skill 目录内（随 Skill 分发但卸载即删）还是项目工作区（持久） | 决定分发模型与产物持久性 |
| A3 | 是否需要根级 `SKILL.md` 指针（Harness 不会加载，仅供人阅读） | 影响文档一致性 |
| A4 | IR 的序列化格式（JSON vs 其他） | 影响可 diff 性与工具互操作 |
| A5 | 是否允许 `git init`（会把 `projectRoot` 锚定到仓库根） | 影响 Skill 发现路径与版本管理 |
| **A6** | 布局层是**独立模块**（`layout.py`，网表 → 带坐标 IR）还是织入三个生成器 | 决定改动范围；独立模块可单测但需重构现有硬编码坐标 |
| **A7** | **是否重排既有 16 个已验证产物**：重排会改变坐标，必须重跑全部仿真；不重排则 6 处穿体缺陷保留 | 决定「已验证」结论是否需要重新建立 |
| **A8** | 布局验收是否作为**门禁**（不通过则拒绝写文件），还是仅报告 | 决定生成器是否可能产出图形不合格的文件 |
| **A9** | `Pin`+`Tunnel` 共位模式（214 处）是否改为「偏移 + 短线」 | 会改动所有既有产物的坐标；Tunnel 位置不影响电气语义，故技术风险低 |
| **A10** | 子电路层级是否纳入布局（端口位置规则特殊，输出端口尚未跑通） | 决定能否构建 CPU 级层次设计 |
