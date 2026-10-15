# Logisim Circuit Skill

一个用于指导 AI **分析、设计、生成、验证、修复** Logisim Evolution 数字电路的 Skill。

**目标**：用户用自然语言描述需求，AI 能自主设计复杂数字系统（例如 CPU）—— 拆分为模块、
生成**可真实打开**的 `.circ` 工程、并通过**实际电路仿真**验证功能，而不是只给出示意图或伪代码。

**当前状态**：✅ **核心能力可用，验收案例已通过**；若干缺口如实标注（见下）。

| 能力 | 状态 | 证据 |
|---|---|---|
| **验收案例**：4 位全加器，512 组穷举 | ✅ **512/512** | `examples\adder4\adder4.circ`；阴性对照 128 失配 |
| **4 位全减器**，512 组穷举 | ✅ **512/512** | `output\sub4.circ`；**三个**阴性对照分别失败 512 / 256 / 512 行 |
| 组合电路生成与验证 | ✅ | `adder2` `adder4` `fulladder` `majority3` `mux2` |
| RTL 生成（Mux/Decoder/Adder/Comparator/Splitter） | ✅ | 4 位 ALU **1024/1024** |
| **自然语言 → 电路** | ✅ | 英文段落需求 → 4 选 1 多路器 **64/64**，阴性对照 16 失配 |
| 时序电路（FF/Register/Clock/Adder/Not/RAM） | ✅ | TFF、2 位行波计数器、寄存器载入保持、4 位累加器 |
| 信号发生器（分频触发器） | ✅ | `DFF` 取 `D = NOT(Q)`，731,442 行持续翻转 |
| RAM 端口 | ✅ | `SamplePorts` 实测；`WE` 门控存储已验证 |
| 结构宽度审计（检出静默截断） | ✅ | `scripts\py\audit_width.py`，W1–W5 |
| **有界且可证明的修复** | ✅ | 精确定位 ALU 操作码 mux 的 d2/d3 互换并证得 1024/1024 |
| **`Comparator` 幅值比较（含 mode 属性）** | ✅ **16/16** | 曾误记为开放缺陷：真因是 `mode` 缺省为**有符号**而我漏写该属性（§30）。`rtllib` 现在**总是**写出 `mode`（缺省 `unsigned`，可逐部件指定）；`cmp2` 16/16，阴性对照失败 12 |
| `ROM`、精确读写时序、自动信号调度 | ⚪ 未实现 | 如实记为缺口 |

**不夸大能力**：`docs/internals/CAPABILITIES.md` 列出本工具链能识别的**假通过**模式；
 记录每条实测事实与**已纠正的错误结论**。

---

## 范围

**做**：Skill 本体（`SKILL.md` + 工作流），以及它必需的辅助文件与脚本
（格式研究文档、电路中间表示、生成器、校验器、headless 仿真验证框架）。

**不做**（本阶段明确排除）：
- ❌ 不开发 MCP 服务
- ❌ 不建设独立平台 / Web UI
- ❌ 不修改、移动、删除或依赖旧 MCP 项目（`D:\Logisim MCP`）
- ❌ 不在项目目录外创建文件（构建缓存除外，见下）

**唯一的外部写入**：Gradle wrapper 与 Maven 依赖会落到用户级缓存 `%USERPROFILE%\.gradle`。
这是包管理器的标准行为，无法避免，且不属于任何项目目录。

---

## 隔离约束

本项目的所有文件都在 `this package` 内。旧 MCP 项目位于 `D:\Logisim MCP`，
是**独立**的顶层目录：本项目不读取、不修改、不依赖它。审计已用时间戳证明二者无写入重叠
（见  §1.3）。

`examples\user_samples\` 中的 9 个真实 5.0.0 文件是**只读参考数据**，从未被修改。

---

## 目录结构

```
SKILL.md                  Skill 指令本体（实质性内容，供 AI 阅读）
.dsh\skills\logisim-circuit\SKILL.md   Harness 可加载入口（frontmatter 必需，见下）
docs\                     审计、路线图、架构、验收标准、能力台账、逐样本报告
references\               .circ 格式、元件属性、几何、实测探针日志（每条断言带出处）
workflows\                build / verify / generate / sequential / analyze / repair
scripts\
  py\                     确定性工具（见下表）
  analysis\               样本分析、端口实测、审计脚本
  bin\                    官方 Logisim CLI jar（5.1.0dev，本地构建）
  dsh\                    Harness 内部规则审计工具（含 validate-skill.js）
examples\
  adder4\                 验收产物（512/512）
  generated\              由 spec 生成并验证过的电路
  user_samples\           9 个真实 5.0.0 文件（只读）
tests\
  specs\                  电路规格（comb / rtl / seq）
  py\                     203 个单元与集成测试
  samples_work\           实验中间产物
logisim-evolution-main\   只读参考源码（5.1.0-dev）
```

### 工具速查

| 工具 | 用途 |
|---|---|
| `scripts\py\generator.py` | 组合电路 spec → `.circ`；`describe` 读回结构 |
| `scripts\py\verify_model.py` | 对独立金标准穷举验证组合/RTL 电路 |
| `scripts\py\generator.py` | RTL 生成：Mux / Decoder / Adder / Comparator / Splitter + 门 |
| `scripts\py\verify_clocked.py` | 时序电路生成与**时钟驱动**验证 |
| `scripts\py\audit_width.py` | 结构宽度审计（无需仿真即检出静默截断） |
| `scripts\py\repair.py` | **有界**修复：候选编辑 + 真实仿真证明 + 阴性对照 |
| `audit_skill.py` | 自审：本 Skill 的每条路径/工作流声称是否属实 |
| `scripts\dsh\validate-skill.js` | 按 Harness 真实加载规则校验 `SKILL.md` frontmatter |

⚠️ **`rtllib` 与 `seqmodel` 的 spec 格式不同，且都不明确报错**——新 spec 请从最接近的
**可用**样例复制其约定，而不是读 `--help`。详见  §26。

---

## 快速开始

```powershell
# 1) 生成并穷举验证一个组合电路
py scripts\py\generator.py build tests\specs\comb\adder4.json examples\generated\adder4.circ
py scripts\py\verify_model.py examples\generated\adder4.circ --spec tests\specs\comb\adder4.json

# 2) 验收案例：4 位全加器 512 组
py scripts\py\tabletool.py verify examples\adder4\adder4.circ --spec adder --expect-rows 512

# 3) 结构宽度审计
py scripts\py\audit_width.py examples\generated\alu4.circ

# 4) 时序电路（需 --seconds 硬超时；时钟循环不会自行终止）
py scripts\py\verify_clocked.py build  tests\specs\seq\seq_accum4.json examples\generated\seq_accum4.circ
py scripts\py\verify_clocked.py verify tests\specs\seq\seq_accum4.json examples\generated\seq_accum4.circ --seconds 4 --cycles 64

# 5) 有界修复（--control 必须【不】通过，否则 oracle 无辨别力）
py scripts\py\repair.py --kind rtl --broken examples\generated\alu4_broken.circ `
    --spec tests\specs\rtl\alu4_broken.json `
    --control examples\generated\mux2_broken.circ --control-spec tests\specs\comb\mux2.json

# 6) 全部测试
py -m unittest discover -s tests\py
```

---

## 两条铁律

1. **真实仿真才算证据。** 结构检查只是廉价过滤器，永远不能替代仿真。
2. **不得把未验证内容当既定事实。** 每条关于文件格式、元件属性、引脚坐标、仿真能力的陈述，
   必须能指向**实测输出**或**源码行号**；否则标注 `[UNVERIFIED]` 且**禁止**用于生成电路。

由此派生、且已被反复验证的三条：

3. **退出码不携带正确性信息。** 错误的电路同样以 `0` 退出。必须解析输出内容。
4. **阴性对照不是形式。** 没有「必须失败」的对照，就无法区分「修好了电路」与「弄坏了测试」。
5. **期望值必须来自独立模型并经过实测。** 与自己共享代码的模型不算独立；
   与代码同源的测试证明不了任何事。

---

## 环境相关注意事项（已实测，务必遵守）

本机是一个编码陷阱，三条硬性要求：

| 要求 | 原因（实测） |
|---|---|
| 所有 CLI 调用加 `--locale en` | JVM locale 为 `zh_CN`，否则结果行为中文且以 **MS936** 编码，无法按 UTF-8 解析 |
| 所有 CLI 调用加 `-Djava.awt.headless=true` | `GraphicsEnvironment.isHeadless()` 默认为 **false**，不强制会尝试开 GUI |
| 所有 Python 文件写入显式 `encoding="utf-8"` | Python 默认编码为 **cp936**，而 `.circ` 声明为 UTF-8，不指定会破坏非 ASCII 内容 |

另有三条会**伪造结果**的陷阱：

* 测量退出码时**不得**对 java 输出使用 `Select-Object -First N` 之类截断管道 ——
  实测会提前关闭管道杀死 JVM，产生**假的失败退出码**。
* `--tty table` 重定向到文件时写出 **UTF-16LE + BOM**，且**读完前不得提前读取**
  （JVM 缓冲会截断证据）。
* **启动器陷阱**：`Start-Process -ArgumentList` 会按空格拆路径；带 `;` 的 classpath 同样会被拆，
  并产生**看似代码错误**的 `ClassNotFoundException`。请用批处理文件传递 java 参数。

---

## 如何运行真实仿真

```powershell
# 组合电路：无 halt 引脚时 --tty table 会穷举所有输入引脚
java '-Djava.awt.headless=true' -jar scripts\bin\logisim-evolution-all.jar `
     --locale en --tty table <file.circ>

# 时序电路：存在名为 halt 的输出 Pin 时，走时钟驱动循环
```

⚠️ **`--test-vector` 在 headless 下不可用**（抛 `HeadlessException`），本项目已改用 `--tty table`。

⚠️ **行只在输出【变化】时打印。** 长度为 1 的流是**真实结果**（「它从未变化」），
不是工具故障。详见  §14。

---

## Skill 入口点（关键）

Harness 只加载 `<projectRoot>\.dsh\skills\<名>\SKILL.md` 或 `.agents\skills\<名>\SKILL.md`，
**不扫描项目根的裸 `SKILL.md`**。入口文件**必须**以 `---` 开头并含 `name` 与 `description`，
否则 Harness **静默忽略**该 Skill（UTF-8 BOM 会造成同样后果）。

本项目曾因此在多轮内**完全不可加载**而无人察觉——校验器早已写出却从未对入口点运行过。
现在 `tests\py\test_skill_packaging.py` 与 `audit_skill.py` 共同守卫这一点。

```powershell
node scripts\dsh\validate-skill.js .dsh\skills\logisim-circuit\SKILL.md   # 必须 VALID
```

---

## 文档索引

| 文档 | 内容 |
|---|---|
| `SKILL.md` | Skill 指令本体：工作流、非协商规则、静默陷阱、诚实的能力边界 |
|  | P1–P10 分阶段路线图，含各阶段**实际**状态与遗留问题 |
| `docs/internals/CAPABILITIES.md` | 能力台账与**假通过模式**清单 |
|  | 环境、路径、版本、隔离证据、Harness 加载规则 |
| `SKILL.md` | 一个目标一个文件：`ir` / `emitter` / `connect` / `layout` / `generator` |
|  | 5 级验收标准（含 4 位全加器 512 组穷举），逐级分别证明 |
| `references\logisim-format.md` / the component catalogue | `.circ` 格式与元件语义 |
|  | V-1…V-84 断言—证据矩阵 |
|  | 全部实测探针日志（含**被推翻的结论**与纠正记录） |
|  | 9 个用户样本的逐样本分析报告 |

---

## 待用户确认

1. **`PLAN.md` 的去留**：根目录 `PLAN.md` 已被  取代，是删除还是保留？
2. 是否允许 `git init`（属不可逆操作，未擅自执行）。
3. 目标版本是否统一用我方构建的 **5.1.0dev**（与参考源码一致），而非系统已装的 5.0.0。

详见  §8。
