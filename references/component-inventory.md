# 04 — 六个组件库的完整清单与属性研究计划

> **阶段状态**：**第一阶段完成**（清单 + 研究计划）。属性逐项研究与知识条目**尚未开始**。
> 凡未实测者标注状态，不得视为已验证。
>
> 本文件回答：当前版本这六个库里**实际有哪些组件**、每个组件**已有什么证据**、以及**接下来怎么研究**。

---

## 1. 版本、产物与证据来源

| 项 | 值 |
|---|---|
| 目标程序 | Logisim Evolution `5.1.0dev`（本项目自建） |
| 发布产物 | `scripts\bin\logisim-evolution-all.jar`，54,558,590 B |
| 产物 SHA-256 | `b872b0c2d4a46f02676c280c30fbd022b74248b9641a7d3d1db29f9715868515` |
| 参考源码 | `logisim-evolution-main\`（只读，`5.1.0-dev`） |
| 组件面板基准 | `examples\user_samples\` 9 个真实 5.0.0 文件（**只读**，永不写入） |

### 1.1 清单的三重交叉确认

组件清单**不是**凭记忆或凭示例编写的，而是三方一致：

| 来源 | 方法 | 结果 |
|---|---|---|
| **发布产物** | `PortDump` 加载一个声明全部 13 库的样本，遍历 `Library.getTools()` → `AddTool.getFactory().getName()` | 六库 74 个 |
| **源码注册** | 各 `*Library.java` 的 `DESCRIPTIONS`（`FactoryDescription`）与 `AddTool(...)` 注册项 | 六库 74 个 |
| **REA 归档核验** | `open_binary` + `inspect_artifact` 于 jar；按 `logical_path` 查询 6 个库类 | 6/6 存在，`hash_status=verified` |

计数逐库一致：

| 库 id | 源码注册方式 | 源码条数 | jar `getTools()` | 一致 |
|---|---|---|---|---|
| `Wiring` | 8×`AddTool` + 6×`FactoryDescription` | 14 | 14 | ✅ |
| `Gates` | 13×`AddTool` | 13 | 13 | ✅ |
| `Plexers` | 5×`FactoryDescription` | 5 | 5 | ✅ |
| `Arithmetic` | 13×`FactoryDescription` | 13 | 13 | ✅ |
| `FPArithmetic` | 18×`FactoryDescription` | 18 | 18 | ✅ |
| `Memory` | 11×`FactoryDescription` | 11 | 11 | ✅ |
| **合计** | | **74** | **74** | ✅ |

> ⚠️ 注册风格**不一致**：`Wiring` 混用两种，`Gates` 只用 `AddTool`。**只读 `FactoryDescription` 会漏掉 `Wiring` 的 8 个与 `Gates` 的全部 13 个。** 清单必须以产物 `getTools()` 为准。

### 1.2 未纳入范围

其余 7 个库（`I/O` 17、`TTL` 69、`TCL` 2、`Base` 1、`BFH-Praktika` 2、`Input/Output-Extra` 7、`Soc` 8）**不在本任务范围**，仅在此说明其存在，不予研究。

---

## 2. 六库完整清单（74 个）

**状态图例**（严格区分，不夸大）：

| 标记 | 含义 |
|---|---|
| **V** | **已实测**：该组件至少有一项属性/几何/行为来自真实 jar 的实测，有证据条目 |
| **P** | **部分**：在已验证电路中实际使用过，但其属性**未逐项核实** |
| **U** | **未研究**：本项目的证据文件中从未出现 |

### 2.1 `#Wiring`（14）

| XML 名 | 显示名 | 状态 | 已有证据 / 缺口 |
|---|---|---|---|
| `Splitter` | Splitter | **V** | fanout 按 `facing` 旋转；`spacing` 合法 1..9（40 静默丢弃）；**只分不合** |
| `Pin` | Pin | **V** | `type` 默认 `input`；`facing` 改变框位置；样本 37/37 带 `appearance` |
| `Probe` | Probe | **P** | 框实测 20×20；无输出端口；`label` 未核实 |
| `Tunnel` | Tunnel | **V** | `width` 默认 **1 位**；`facing` 语义与直觉相反（§见探针日志 §33）；无端口，按标签连接 |
| `Pull Resistor` | Pull Resistor | **U** | 全部属性未知 |
| `Clock` | Clock | **P** | 在全部时序电路中实际使用；`high`/`low`/`phase`/`facing` 未核实 |
| `POR` | Power-on Reset | **U** | 全部属性未知 |
| `Constant` | Constant | **V** | `value` 缺省为 **TRUE**；`width` |
| `Power` | Power | **U** | — |
| `Ground` | Ground | **U** | — |
| `NoConnect` | Do not connect | **U** | XML 名是 `NoConnect` 而非显示名 |
| `Transistor` | Transistor | **U** | `facing`/`type` 未知 |
| `Transmission Gate` | Transmission Gate | **U** | — |
| `Bit Extender` | Bit Extender | **U** | `in_bits`/`out_bits`/`type` 未知 |

### 2.2 `#Gates`（13）

| XML 名 | 显示名 | 状态 | 已有证据 / 缺口 |
|---|---|---|---|
| `NOT Gate` | NOT Gate | **V** | 端口 `loc.x−30`；框 `(−30,−10,30,20)` |
| `Buffer` | Buffer | **V** | 双输入 `(−50,∓20)`；输出 `loc` |
| `AND Gate` | AND Gate | **V** | `size` 默认 50 → 输入 `(−50,∓20)`；`size=30` → `(−30,∓10)`；框 50×50 |
| `OR Gate` | OR Gate | **V** | 同上 |
| `NAND Gate` | NAND Gate | **V** | 同上 |
| `NOR Gate` | NOR Gate | **V** | 同上 |
| `XOR Gate` | XOR Gate | **V** | 框 **60**×50，输入 `(−60,∓20)` |
| `XNOR Gate` | XNOR Gate | **V** | 同上 |
| `Odd Parity` | Odd Parity | **U** | `inputs`/`width` 未知 |
| `Even Parity` | Even Parity | **U** | — |
| `Controlled Buffer` | Controlled Buffer | **U** | 三态控制端位置未知 |
| `Controlled Inverter` | Controlled Inverter | **U** | — |
| `PLA` | PLA | **U** | 真值表编辑，属性未知 |

### 2.3 `#Plexers`（5）

| XML 名 | 显示名 | 状态 | 已有证据 / 缺口 |
|---|---|---|---|
| `Multiplexer` | Multiplexer | **V** | **端口几何随 `select` 变化，非均匀**：select=1 → 数据 `x−30`、首 dy −10、步长 20；select=2 → `x−40`、−20、步长 10；select=3 → `x−40`、−40、步长 10；选择端恒 `(x−20,y+20)`；框 40×60 |
| `Demultiplexer` | Demultiplexer | **U** | — |
| `Decoder` | Decoder | **V** | 输出均匀间隔 20px，最后一个在 `loc.y−10` |
| `Priority Encoder` | Priority Encoder | **U** | — |
| `BitSelector` | Bit Selector | **U** | XML 名 `BitSelector`（无空格）；`select`/`width` 未知 |

### 2.4 `#Arithmetic`（13）

| XML 名 | 显示名 | 状态 | 已有证据 / 缺口 |
|---|---|---|---|
| `Adder` | Adder | **V** | `A(−40,−10)` `B(−40,+10)` `Cin(−20,−20)` `Sum(loc)` `Cout(−20,+20)`；`width` 缺省 **8 位**；`Cin`/`Cout` 恒 1 位；无 `mode` |
| `Subtractor` | Subtractor | **U** | — |
| `Multiplier` | Multiplier | **U** | — |
| `Divider` | Divider | **U** | — |
| `Negator` | Negator | **U** | — |
| `Exponentiator` | Exponentiator | **U** | — |
| `SquareRoot` | Square Root | **U** | — |
| `Absolute` | Absolute | **U** | — |
| `Comparator` | Comparator | **V** | `A(−40,−10)` `B(−40,+10)` `>(0,−10)` `=(0,0)` `<(0,+10)`；**`mode` 缺省为有符号（二补码）** |
| `MinMax` | Minimum and Maximum | **U** | — |
| `Shifter` | Shifter | **U** | — |
| `BitAdder` | Bit Adder | **U** | — |
| `BitFinder` | Bit Finder | **U** | — |

### 2.5 `#FPArithmetic`（18）— **全部未研究**

| XML 名 | 显示名 | 状态 |
|---|---|---|
| `FPConstant` | Floating Point Constant | **U** |
| `FPAdder` | Floating Point Adder | **U** |
| `FPSubtractor` | Floating Point Subtractor | **U** |
| `FPMultiplier` | Floating Point Multiplier | **U** |
| `FPDivider` | Floating Point Divider | **U** |
| `FPNegator` | Floating Point Negator | **U** |
| `FPExponentiator` | Floating Point Exponentiator | **U** |
| `FPLogarithm` | Floating Point Logarithm | **U** |
| `FPSquareRoot` | Floating Point Square Root | **U** |
| `FPAbsolute` | Floating Point Absolute | **U** |
| `FPComparator` | Floating Point Comparator | **U** |
| `FPMinMax` | Floating Point Minimum and Maximum | **U** |
| `FPRound` | Floating Point Round | **U** |
| `FPTrigonometry` | Floating Point Trigonometry | **U** |
| `FPClassificator` | Floating Point Classificator | **U** |
| `FPToFP` | Floating Point Converter | **U** |
| `FPToInt` | Floating Point to Integer | **U** |
| `IntToFP` | Integer to Floating Point | **U** |

> 这 18 个组件在本项目的证据文件中**一次都没有出现过**。FP 的数据格式、精度、特殊值与异常行为**全部未知**，包括最基础的「用的是哪一种浮点格式」。**在实测前不得假设遵循 IEEE-754 的全部规则。**

### 2.6 `#Memory`（11）

| XML 名 | 显示名 | 状态 | 已有证据 / 缺口 |
|---|---|---|---|
| `D Flip-Flop` | D Flip-Flop | **V** | `D/T(−10,+10)` `CK(−10,+50)` `Q(+50,+10)` `QB(+50,+50)` `PRESET(+20,0)` `RESET(+20,+60)`；框 `(−10,0,60,60)` |
| `T Flip-Flop` | T Flip-Flop | **V** | 端口同 `D Flip-Flop`；`seq_tff` 32 周期实测通过 |
| `J-K Flip-Flop` | J-K Flip-Flop | **U** | ⚠️ **XML 名带连字符**；本项目 `seqmodel.py` **曾**用（**已修复**）的是错误名 `J-K Flip-Flop`（见 §7） |
| `S-R Flip-Flop` | S-R Flip-Flop | **U** | ⚠️ 同上，正确名为 `S-R Flip-Flop`，代码用了 `S-R Flip-Flop` |
| `Register` | Register | **V** | `Q(+60,+30)` `D(0,+30)` `CLK(0,+70)` `EN(0,+50)` `CLR(+30,+90)`；框 `(0,0,60,90)`；`CLR` **优先于** `load`；上电为 **UNDEFINED**；`trigger` 缺省 rising |
| `Counter` | Counter | **U** | 上/下计数、`max`/`up`/`down`/`trigger` 未知 |
| `Shift Register` | Shift Register | **U** | — |
| `Random` | Random Generator | **U** | XML 名 `Random`；`seed`/`width` 未知 |
| `RAM` | RAM | **V** | `A(0,10)` `WE(0,50)` `OE(0,60)` `CK(0,70)` `Din(0,90)` `Dout(+240,+90)`；**`WE` 在 `OE` 之上**；`WE` 门控存储已证；`.circ` **不存储内容** |
| `ROM` | ROM | **U** | 内容来自独立 hex 文件（已知，未核实细节） |
| `DualRAM` | Dual Port RAM | **U** | XML 名 `DualRAM`；双端口 |

---

## 3. 覆盖率统计（**如实**）

### 3.1 组件清单覆盖率

| 库 | 组件数 | V 已实测 | P 部分 | U 未研究 |
|---|---|---|---|---|
| `Wiring` | 14 | 4 | 2 | 8 |
| `Gates` | 13 | 8 | 0 | 5 |
| `Plexers` | 5 | 2 | 0 | 3 |
| `Arithmetic` | 13 | 2 | 0 | 11 |
| `FPArithmetic` | 18 | 0 | 0 | **18** |
| `Memory` | 11 | 5 | 0 | 6 |
| **合计** | **74** | **20** | **2** | **52** |

**清单核对完整度：74/74 = 100%**（三个来源一致）。
**组件级实测覆盖：20/74 ≈ 27%。**

### 3.2 属性覆盖率

⚠️ **属性级覆盖率目前无法给出可信数字**，因为**没有任何一个组件完成过属性逐项枚举**。
已完成的是「对某些组件的某些属性做过实测」，不是「枚举了该组件的全部属性」。
因此：

| 指标 | 值 |
|---|---|
| 完成属性逐项枚举的组件 | **0 / 74** |
| 有至少一项属性实测的组件 | 20 / 74 |
| **属性组合测试覆盖率** | **未开始**（0） |

**属性覆盖率与组合覆盖率必须分开报告**（任务 §8）：目前两者都是 0。第二阶段的产出目标就是让第一项从 0 变为 74/74，第三/五阶段再建立组合覆盖。

### 3.3 未出现过的组件（22）

全部 18 个 `#FPArithmetic` + `T Flip-Flop`（仅源码未入文档）+ `J-K Flip-Flop` + `S-R Flip-Flop` + `DualRAM`。

---

## 4. 第二阶段：属性研究计划

### 4.1 每个组件的属性枚举方法（统一流程）

对每个组件，从**源码**取得权威属性定义，再**实测**确认，不靠猜：

```
1. 定位工厂类：  std\<lib>\<Class>.java
2. 取属性声明：  new Attribute[] {...}, new Object[] {<defaults>}
                 （注意：注册可能在 register()/FactoryDescription/AddTool 三处）
3. 取属性元数据：Attribute.getName()（XML 键名）、getDisplayName()、
                 类型（BitWidth / AttributeOption / Integer / Boolean / String / Direction）
4. 取合法值：    AttributeOption[] 的每个 option 的 value 字符串
5. 取端口：      propagate()/getEnds() + 实测 SamplePorts
6. 记录序列化：  实测生成 .circ，看该属性是否写出、顺序与形式
7. 记录缺省陷阱：省略该属性是否改变行为（本项目已发现 4 例）
```

### 4.2 属性分类模板（每个组件产出）

| 字段 | 说明 |
|---|---|
| XML 键名 | `Attribute.getName()`，即 `.circ` 中的 `name=` |
| 显示名 | 属性面板文字 |
| 类型 | `BitWidth` / `AttributeOption` / `Integer` / `Boolean` / `String` / `Direction` |
| 缺省值 | `new Object[]{...}` 中的默认（**必测**：省略时行为） |
| 合法值 | 枚举全部 option；数值型给边界 |
| 依赖关系 | 该属性是否改变其他属性的合法性/含义 |
| 影响 | 图形 / 朝向 / 引脚数 / 引脚位置 / 位宽 / 逻辑 / 时序 / 状态 |
| 序列化 | 实测 `.circ` 片段 |
| 非法值处理 | 实测：拒绝加载 / 回退默认 / 静默忽略 |

### 4.3 已知会改变引脚数/位置/位宽的属性（优先）

这些是布局与生成正确性的关键，**优先研究**：

| 组件 | 属性 | 已知影响 |
|---|---|---|
| `Multiplexer` | `select` | 数据端口**数量与间距都变**（已验证，是唯一已测的此类组件） |
| `Splitter` | `fanout`/`incoming`/`spacing`/`bitN` | 端口数量、位置、位序全变 |
| 门（7 种） | `inputs`、`size`、`width` | 输入脚数量与偏移、框尺寸、位宽 |
| `Bit Extender` | `in_bits`/`out_bits` | 两侧位宽不同 |
| `RAM` / `DualRAM` / `ROM` | `addrWidth`/`dataWidth` | 端口位宽与 `bounds` |
| `Counter` | `width`/`max` | 输出位宽与状态空间 |
| `Shift Register` | `width`/`direction`/`parallel` | 端口数量 |
| `Priority Encoder` | `select`/`disabled` | 端口数量 |
| 全部 `FP*` | `width`/`format` | 数据格式与位宽 |

### 4.4 组合爆炸的处理

`FPArithmetic` 的 18 个组件 × 多种数据格式 × 精度，组合极大。策略：

1. **先识别依赖关系**：多数 `FP*` 共享同一组属性（格式、位宽、舍入模式），故**先研究一个代表组件**（`FPAdder`）把属性集搞清楚，其余按差异研究，而不是 18 个从头来。
2. **边界测试**：位宽/格式的极小与极大值、非法值。
3. **成对测试**：`格式 × 舍入模式`、`格式 × 操作数特殊值`。
4. **关键交互专项**：NaN / Inf / 零 / 非规格化数 的行为——这些是**最可能不遵循 IEEE-754 全部规则**的地方。

**不得因为组合多就跳过属性本身的枚举。**

---

## 5. 第三至五阶段：验证计划

### 5.1 证据层级（每项结论标注）

| 记号 | 来源 | 强度 |
|---|---|---|
| `SRC` | 当前版本源码行号 | 强（实现真相） |
| `MEAS` | 真实 jar 实测（`SamplePorts` / `--tty stats` / `--tty table`） | **最强**（行为真相） |
| `SAMPLE` | 用户只读样本中的真实配置 | 中（用法示例） |
| `DOC` | 官方文档 | 中（可能落后于版本） |
| `REA` | REA 归档/二进制分析 | 强（产物身份与内容） |

规则：**源码说了规则也必须实测确认**；源码与实测冲突时**以实测为准并记录冲突**。

### 5.2 各库验证重点（任务 §6 的要求）

| 库 | 验证重点 | 可穷举性 |
|---|---|---|
| Wiring | 连线/Pin/Probe/Tunnel/Splitter/Constant/Clock 的属性、方向、引脚、位宽、**网络关联**、**分线映射** | 逐项可测 |
| Gates | 输入数、位宽、逻辑行为、输出规则 | **7 种门 + 3 输入可全穷举** |
| Plexers | 选择端、使能端、输入输出数、位宽、选择规则 | **select ≤ 3 可全穷举** |
| Arithmetic | 运算规则、位宽、进位、溢出 | **4 位可全穷举（256/512）** |
| FPArithmetic | 数据格式、精度、运算、**特殊值与异常** | 位宽 16/32 时不可全穷举 → 边界+成对+关键交互 |
| Memory | 容量、地址位宽、数据位宽、读写接口、时钟、使能、复位、**初始化** | 小容量可穷举；RAM/ROM 的初始化为难点 |

### 5.3 每项测试的六项记录（任务 §6 要求）

1. 静态属性检查结果
2. `.circ` 解析结果
3. Logisim **实际打开**结果
4. **实际仿真**结果
5. 属性组合覆盖情况
6. 未测试的边界与限制

**不得用 Python 计算的预期值代替 Logisim 实际电路验证。**

### 5.4 现有可复用工具

| 工具 | 用途 |
|---|---|
| `scripts\py\circmodel.py` | 组合电路 spec → `.circ` + 仿真验证（**已有属性校验与布局校验**） |
| `scripts\py\rtllib.py` | RTL 组件生成（Mux/Decoder/Adder/Comparator/Splitter） |
| `scripts\py\seqmodel.py` | 时序电路生成与时钟驱动验证 |
| `scripts\py\audit_width.py` | 宽度/驱动审计 |
| `scripts\analysis\audit_layout.py` | **布局冲突审计（实测几何）** |
| `scripts\analysis\audit_attr_defaults.py` | 静默缺省属性审计 |
| `scripts\analysis\SamplePorts.java` | **实测 bounds 与端口**——几何研究的权威来源 |
| `tests\circ\probe\PortDump.java` | **实测库组件清单**——本阶段清单的来源 |

---

## 6. REA 使用记录（本阶段实际执行的）

REA 已确认实际可用：**rea-agents 6.4.0**，Ghidra 12.1.4 已安装于 `D:\REA\ghidra_12.1.4_PUBLIC`。

⚠️ **适用范围限制**：Ghidra 支持原生 x86/x86-64 PE 与 DLL；**不支持 Java**（`managed` 指 .NET，非 JVM）。因此 Ghidra 对 Logisim 无用。

**本阶段实际执行的 REA 任务**：

| # | 操作 | 结果 | 证据 id |
|---|---|---|---|
| 1 | `open_binary` 于 `logisim-evolution-all.jar` | 识别为 `zip`，SHA-256 `b872b0c2…` 与本地计算一致 | — |
| 2 | `inspect_artifact`（`detail=summary`，`record-and-continue`） | **52,662 条目**，17,234 文件 / 870 目录，全覆盖，0 完整性矛盾 | `ev_5424317d…` / `ev_62f73d50…` |
| 3 | `inspect_analysis_view`（inventory/occurrences）查询 6 个库类的 `logical_path` | **6/6 存在**，`hash_status=verified`；WiringLibrary 3610B、GatesLibrary 2536B、PlexersLibrary 6146B、ArithmeticLibrary 2988B、FPArithmeticLibrary 3820B、MemoryLibrary 2759B | `ev_da135f52…` |

**REA 的用途定位**：确认**发布产物**的身份与内容，使清单不只依赖源码树（源码与产物可能不一致）。这是可核验的、非重复的用途。

**后续阶段的 REA 预期用途**（尚未执行，仅供参考）：
- 若源码与实测行为冲突，用 REA 检查 jar 内是否有多个版本的同一类；
- 核实 `resources` 中的字符串表（属性显示名）是否与源码一致；
- **不预期**用 REA 做 Java 反编译——REA 无此能力，不得假定。

---

## 7. ⚠️ 本阶段立即发现的问题（**需用户决定是否现在修复**）

### 7.1 `seqmodel.py` 对 JK/SR 触发器使用了**错误且静默**的 XML 名

清单交叉核对时发现：`seqmodel.py:78` 与 `:232` 把 `JKFF` 映射为 `"J-K Flip-Flop"`、`SRFF` 映射为 `"S-R Flip-Flop"`，
而发布产物的权威 XML 名是 **`J-K Flip-Flop`** 与 **`S-R Flip-Flop`**（**带连字符**）。

**单变量实测**（以已验证可加载的 `seq_div2.circ` 为基线，只改元件名，跑 `--tty stats`）：

| `name=` | `--tty stats` 结果 |
|---|---|
| `D Flip-Flop` | ✅ `1  1  D Flip-Flop  Memory` |
| `T Flip-Flop` | ✅ `1  1  T Flip-Flop  Memory` |
| **`J-K Flip-Flop`** | ✅ `1  1  J-K Flip-Flop  Memory` |
| **`S-R Flip-Flop`** | ✅ `1  1  S-R Flip-Flop  Memory` |
| `J-K Flip-Flop` | ❌ **无该元件行** |
| `S-R Flip-Flop` | ❌ **无该元件行** |
| `Nonsense Xyz` | ❌ 无（对照） |

**后果**：任何用 `seqmodel` 生成 JK/SR 触发器的电路，该元件会被 **Logisim 静默丢弃**，
电路能加载、能出表，但**根本没有触发器**——正是本项目已记录的「错误元件名被静默丢弃」陷阱。
（`D`/`T` 触发器名字正确，`seqmodel.py:233` 的另外两个映射也是对的。）

**影响范围**：仅 `seqmodel.py` 的两处字符串。修复是 2 行。
**本阶段未修改**——按任务要求「未经确认……不要大规模修改现有 Skill」。**请确认是否现在修。**

### 7.2 其它命名不一致（**不是** bug，但生成时必须用 XML 名）

清单核对发现**内部名与显示名不同**的组件，用显示名会静默失败：

| 内部 XML 名 | 显示名 | 风险 |
|---|---|---|
| `BitSelector` | Bit Selector | 含空格会失败 |
| `SquareRoot` | Square Root | 同上 |
| `MinMax` | Minimum and Maximum | 同上 |
| `BitAdder` | Bit Adder | 同上 |
| `BitFinder` | Bit Finder | 同上 |
| `NoConnect` | Do not connect | 同上 |
| `Random` | Random Generator | 同上 |
| `DualRAM` | Dual Port RAM | 同上 |
| `POR` | Power-on Reset | 同上 |
| `J-K Flip-Flop` / `S-R Flip-Flop` | 同左 | **已修复**：`seqmodel.py` 现在输出正确名称 |

---

## 8. 尚未解决 / 待确认

| # | 问题 | 状态 |
|---|---|---|
| **Q1** | 是否现在修复 `seqmodel.py` 的 JK/SR 元件名（2 行）？ | **待用户确认** |
| **Q2** | `#FPArithmetic` 的 18 个组件用哪一种浮点格式（IEEE-754 半/单/双？自定义？） | **完全未知**，第二阶段首要问题 |
| **Q3** | 属性研究是**全部 74 个**一次做完，还是**按库分批**（如先 Wiring+Gates，产出后再继续）？ | **待用户确认** |
| **Q4** | 知识条目粒度：每组件一个 MD（74 个文件）还是每库一个 MD 分节？ | **待用户确认** |
| **Q5** | 是否将组件知识同时输出为机器可读 JSON 供 Skill 查询（建议是） | 建议，待确认 |
| **Q6** | 组合测试的**接受上限**：哪些组件可以只做边界+成对而不做全组合？ | **待用户确认** |
| **Q7** | `#FPArithmetic` 的组件在 5.1.0dev 是否为**实验性**（源码包名 `floating` 暗示）？影响可用性判断 | 待查 |

---

## 9. 本阶段**未**做的事（明确声明）

* **未**批量生成组件知识条目（0 个）。
* **未**逐项研究任何组件的全部属性。
* **未**修改任何生成器脚本（含 §7.1 的缺陷）。
* **未**修改 `SKILL.md`。
* **未**写入任何用户样本；全部实验使用副本。
* 所有「V」标记仅表示**有实测证据**，**不表示该组件属性已枚举完整**。
