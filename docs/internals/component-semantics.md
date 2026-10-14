# 元件语义 — XML 名、属性、端口与实测行为

> **Paths in this document refer to the development tree, not to this package.** The commands
> and file paths were recorded while the measurements were taken and are kept verbatim as
> evidence. This package ships no circuits, no test suite and no `examples/` directory — write
> your own spec and circuit files as shown in `README.md` §5.

- 依据：`examples\user_samples\` 9 个真实样本（Logisim 5.0.0）+ 源码 + 实测
- 置信度与适用范围见 `docs\evidence-matrix.md`；连接与陷阱见 `docs\wire-connectivity.md`
- 完整元件目录见 `references\02-libraries-components.md`

> **纪律**：元件名**不是**功能证明。下表「功能」列凡标注 **[推断]** 者，均依据名称/常规语义，
> 尚未用仿真验证；标注 **A** 者为实测确认。未实测的**不得**用于生成器。

---

## 1. 引脚 `Pin`

| 项 | 值 | 级别 |
|---|---|---|
| 库 / XML 名 | `#Wiring`(ordinal 0) / `Pin` | A |
| 端口 | **恰好一个**，位于 `loc` 本身 | A |
| 方向 | 输入可不写 `type`；**输出必须显式 `type="output"`** | A |
| ⚠️ `type` 缺省 | **`input`** —— 缺失时**静默**改变语义 | A |
| 位宽 | `width` 属性，缺省 **1** | A |
| 名称 | `label` 属性，**可缺失** | A |
| 常见属性 | `facing="west"`（输出常用）、`appearance`（`classic` / `NewPins`） | A |

### ⚠️ `type` 缺省是 `input`（重要，静默失效）

`Pin.ATTR_TYPE` 由 `Attributes.forOption("type", …)` 定义（`Pin.java:717-719`），
而 `forOption` **取第一个选项为默认**，`INPUT` 定义在前（`Pin.java:713-716`）。
因此**不写 `type` 的引脚是输入，不是输出**。

用户样本 `Adder_sample.circ` 正好踩中这一点：那个标着 `label="Output"`、带 `facing="west"` 的
引脚，XML 里**没有 `type`** —— 实测其 `attr type = input`，所以**该电路其实一个输出引脚都没有**，
这也直接导致它无法被验证。

`sample-verifier` 的对照实验（把宽度改为 1 的副本）：

| 变体 | 表头 | 行数 | 含义 |
|---|---|---|---|
| 原样（无 `type`） | `A Output B` | **8** | 3 个引脚**全算输入**，表里没有输出列 |
| 加 `type="output"` | `A B Output` | **4** | 2 输入 + 1 输出 |

**生成器规则：输出引脚必须显式写 `type="output"`。靠 `facing="west"` 或标签名叫 "Output" 暗示输出是错的。**

```xml
<!-- 输入（不写 type） -->
<comp lib="0" loc="(260,180)" name="Pin"><a name="label" val="A"/><a name="appearance" val="NewPins"/></comp>
<!-- 输出 -->
<comp lib="0" loc="(420,200)" name="Pin">
  <a name="label" val="S"/><a name="type" val="output"/><a name="facing" val="west"/>
</comp>
```

**⚠️ 对抗验证的影响**：`--tty table` 的表头取自 `label`。**无 label 的引脚无法按名称引用**，
只能按坐标的几何顺序解读列（先 y 后 x，输入在前输出在后）。

**建议**：生成器**始终**给引脚加 `label`。

---

## 2. 门（`#Gates`，ordinal 1）

### 2.1 XML 名（A 级）

XML 名**含空格且带 `Gate` 后缀**：

| 显示名（推断） | XML 名 | 样本证据 |
|---|---|---|
| NOT | `NOT Gate` | `NOT.circ` |
| 缓冲 | `Buffer` | ⚠️ **无** `Gate` 后缀 |
| AND | `AND Gate` | `AND.circ`、`Half_Adder.circ` |
| OR | `OR Gate` | 实测 |
| NAND | `NAND Gate` | 实测 |
| NOR | `NOR Gate` | 实测 |
| XOR | `XOR Gate` | `Half_Adder.circ` |
| XNOR | `XNOR Gate` | 实测 |
| 奇/偶校验 | `Odd Parity` / `Even Parity` | 源码枚举 |
| 受控缓冲/反相 | `Controlled Buffer` / `Controlled Inverter` | 源码枚举 |
| PLA | `PLA` | 源码枚举 |

**写错名字的后果**：元件被**静默丢弃**，文件仍正常加载、真值表仍正常打印（C-02）。
**唯一廉价的检出手段**是 `--tty stats --test-circuit`。

### 2.2 端口几何（A 级，`facing=east`、`inputs=2`、`size` 默认 50）

| 门 | 输出端口 | 输入端口 |
|---|---|---|
| `XOR Gate` / `XNOR Gate` | `loc` | `(loc.x−60, loc.y−20)`、`(loc.x−60, loc.y+20)` |
| `AND`/`OR`/`NAND`/`NOR Gate` | `loc` | `(loc.x−50, loc.y−20)`、`(loc.x−50, loc.y+20)` |

`XOR`/`XNOR` 多 10 px，因为源码调用 `setAdditionalWidth(10)`（输出反相圈占用宽度）。

**`size` 属性的影响**（A 级）：

| `size` | 外框 | 输入偏移 |
|---|---|---|
| `30` | 30×30 | `(−30, ∓10)` |
| **缺省（=50）** | 50×50 | **`(−50, ∓20)`** |
| `50` | 50×50 | `(−50, ∓20)` |
| `70` | 70×70 | `(−70, ∓20)` |

### 2.3 属性（A 级：样本中实际出现）

| 属性 | 样本中出现的值 | 说明 |
|---|---|---|
| `inputs` | `3` | 输入个数；**默认 2，缺省时不写出** |
| `size` | `30` | 门尺寸；缺省为 50，不写出 |
| `width` | `16` | 数据位宽；默认 1 |
| `facing` | `north` / `south` / `west` | 朝向；默认 east |

### 2.4 实测功能（A 级，2 输入、`facing=east`、1 位）

```
AND Gate   → 0,0,0,1      OR Gate  → 0,1,1,1      XOR Gate  → 0,1,1,0
NAND Gate  → 1,1,1,0      NOR Gate → 1,0,0,0      XNOR Gate → 1,0,0,1
```
（对 4 行输入组合 `a,b = 00,01,10,11`）

### 2.5 未确认（生成器**不得**依赖）

| 项 | 状态 | 备注 |
|---|---|---|
| ~~`inputs=3` 的端口间距~~ | ✅ **已实测** | 见 §2.9 |
| ~~`facing`=north/south/west 的端口旋转~~ | ✅ **已实测** | 见 §2.6 |
| ~~`width=16` 门接 `width=1` 引脚的行为~~ | ⚠️ **样本中不存在该情形** | 见 §2.7；真正位宽不匹配的行为仍**未确认** |
| `inputs>=4` 的端口间距 | **待实测** | 3 输入已测；4 输入是否插入空档未验证 |
| 真实位宽不匹配（宽引脚接窄引脚） | **未确认** | 本批样本全电路宽度一致 |
| `Splitter` 非 east 朝向的端口公式 | **未确认** | 仅实测 east |

### 2.6 ✅ `facing` 旋转（实测四种朝向，三种经仿真证实）

设 `dx = size`（默认 50），`size=50` 时测得：

| `facing` | 输入端口相对 `loc` | 样本内实例 |
|---|---|---|
| east（默认） | `(−dx, −20)`、`(−dx, +20)` | `AND.circ` @(320,150) → (270,130)/(270,170) |
| west | `(+dx, −20)`、`(+dx, +20)` | `AND.circ` @(250,620) → (300,600)/(300,640) |
| north | `(−20, +dx)`、`(+20, +dx)` | `AND.circ` @(410,620) → (390,670)/(430,670) |
| south | `(−20, −dx)`、`(+20, −dx)` | `AND.circ` @(570,620) → (550,570)/(590,570) |

**输出端口在四种朝向下都恒在 `loc`，不随朝向移动。**

**仿真证实**（受控探针，按上表坐标接线，8 行真值表）：

```
facing=west  inputs (350,80)/(350,120) → 0,0,0 / 0,1,0 / 1,0,0 / 1,1,1   ← 正确 AND
facing=north inputs (280,150)/(320,150) → 0,0,0 / 0,1,0 / 1,0,0 / 1,1,1   ← 正确 AND
facing=south inputs (280,50)/(320,50)   → 0,0,0 / 0,1,0 / 1,0,0 / 1,1,1   ← 正确 AND
```

规律与源码 `AbstractGate.getInputOffset` 末尾一致：`east→(−dx,dy)`、`west→(dx,dy)`、
`north→(dy,dx)`、`south→(dy,−dx)`。

### 2.7 ✅ 门几何只由 `size` 决定，与 `width` 无关

`AND.circ` 中 `width=16`、`size=30` 的门，其包围盒仍是 **30×30**，输入偏移仍是 **`(−30, ∓10)`**，
与同 `size` 的 1 位门**完全相同**。

> **注意**：该样本的 16 位门配的是 16 位引脚，**全电路宽度一致**，因此这批样本
> **不能**说明「宽引脚接窄引脚」时的行为 —— 那仍然是 `[未确认]`。

### 2.8 ✅ 其它元件的实测端口

| 元件 | 实测端口（相对 `loc`） | 来源 |
|---|---|---|
| `NOT Gate`（`size=30`） | 输入 `(−30, 0)`、输出 `loc` | `NOT.circ` @(270,170) |
| `Multiplexer`（`size=40`, w=1） | A `(−30,−10)`、B `(−30,+10)`、Sel `(−20,+20)`、输出 `loc` | `Mutiplexer_sample.circ` @(320,260) |
| `Tunnel` | 单端口在 `loc`，类型 `INPUT_OUTPUT`；**默认 facing = west** | `Tunnel_sample.circ` |
| `Register`（w=8） | Q `(+60,+30)` 出、D `(0,+30)` 入、clock `(0,+70)` 入、控制 `(+30,+90)`/`(0,+50)` | `Reg_sample.circ` @(430,250) |
| `RAM` | 数据入 `(0,+10)` w8、数据出 `(+240,+90)` w8、地址 `(0,+90)` w8、三个 1 位控制 `(0,+50/+60/+70)` | `RAM_sample.circ` @(280,200) |

**`Multiplexer` 的实测功能**（`Mutiplexer_sample.circ`，8 行）：**`Sel=0 → Output=A`，`Sel=1 → Output=B`**
（A、B、Sel、Output 均 1 位）。

### 2.9 ✅ 3 输入门端口（Lead 实测）

`AND Gate`，`inputs="3"`，`loc=(400,200)`，`facing=east`（默认），`size` 默认 50：

| 端口 | 实测坐标 | 相对 `loc` |
|---|---|---|
| 输出 | `(400,200)` | `(0, 0)` |
| 输入 1 | `(350,180)` | `(−50, −20)` |
| 输入 2 | `(350,200)` | `(−50, 0)` |
| 输入 3 | `(350,220)` | `(−50, +20)` |

**原始证据**（导线端点 x 扫描，8 行输入）：

```
xin=330 → E,E,E,E,E,E,E,E     （未达端口）
xin=340 → E,E,E,E,E,E,E,E
xin=350 → 0,0,0,0,0,0,0,1     ← 正确的 3 输入 AND
xin=360 → 0,0,0,0,0,0,0,1
xin=370 → 0,0,0,0,0,0,0,1
```

**两条结论**：
1. **`inputs` 增加不改变 x 偏移**（仍是 `x−50`），只是增加 y 间距（间距 20）。
2. ⚠️ **源码曾报告 3 输入门为 `x−60`，实测否决了该说法。** 这与 §2.2 的 2 输入结果一致：
   `AND` 系门恒为 `x−50`。**再次印证：端口位置必须实测，不能从公式推断。**

`sample-verifier` 独立复核（样本 `AND.circ` @(320,280)，`inputs=3 size=50`）得到同样结果：
`(270,260)`、`(270,280)`、`(270,300)`，并附受控仿真（8 行只有 `111` 出 1）。
**两次独立测量一致。**

---

## 3. 隧道 `Tunnel`（`#Wiring`）

| 项 | 值 | 级别 |
|---|---|---|
| XML 名 | `Tunnel` | A（`Tunnel_sample.circ`） |
| 属性 | `label`（连接键）、`facing`（`east`/`west`） | A |
| 连接方式 | **按 `label` 连接**，与坐标无关 | A（实测跨距离连通） |
| 端口 | 位于自身 `loc` | A |

```xml
<comp lib="0" loc="(200,210)" name="Tunnel"><a name="label" val="A"/><a name="facing" val="east"/></comp>
<comp lib="0" loc="(510,500)" name="Tunnel"><a name="label" val="A"/><a name="facing" val="west"/></comp>
```

**语义**：同名 `label` 的多个 `Tunnel` 属于**同一网络**。因此它等价于"具名网络"，
可以跨越任意距离连接，不需要走线。

**⚠️ 但**：把 `Pin` 与 `Tunnel` 放同一坐标**不会**让该网络被驱动（W-08），必须显式画导线。

**⚠️ 且**：同一网络**只能有一个驱动源**。用同名 label 既驱动又消费是可以的，但两个门输出
挂同一个 label 会造成多驱动 → 全部 `E`（W-09）。

---

## 4. 分线器 `Splitter`（`#Wiring`）

`Splitter_sample.circ` 是唯一的真实样本，其属性为：

```
fanout=3  incoming=8  spacing=4
bit1=0  bit2=1  bit3=1  bit4=1  bit5=1  bit6=1  bit7=2
```
输出引脚位宽为 **2 / 5 / 1**。

| 项 | 值 | 级别 |
|---|---|---|
| XML 名 | `Splitter`；库 `#Wiring` | A |
| 主要属性 | `fanout`（输出股数）、`incoming`（输入位宽）、`spacing`、`bitN` | A |
| `bitN` 语义 | **推断**：指定第 N 个 fanout 股包含的输入位索引；`none` 表示该位不引出 | C |
| 位序 | 自造探针实测为 **LSB-first**（fanout i 对应 bus bit i） | A（自造） / **样本未交叉验证** |
| `spacing` 取值范围 | **1..9**；超出会**静默丢弃**该元件 | A |
| `spacing=1` 端口坐标 | fanout i 在 `(x+20, y−20+10i)` | A（自造探针） |
| `spacing=4` 端口坐标 | **待实测** | 需确认是否为 `y−20+10*spacing*i` |

⚠️ **`spacing` 超范围是静默失效**：文件仍加载、表仍打印，分线器直接消失。必须用 `--tty stats` 检出。

⚠️ `Splitter_sample.circ` 有 **0 条 wire**，因此其 fanout 端口在**功能上未接线**，
真值表预计全为 `U` —— 该样本能证明**结构**，但无法从功能上验证位序。

---

## 5. 算术：`Adder`（`#Arithmetic`，ordinal 3）

`Adder_sample.circ`：1 个 `Adder` + 3 个 8 位 `Pin`（`A`、`B` 输入，`Output` 输出）。

| 项 | 值 | 级别 |
|---|---|---|
| 库 / XML 名 | `#Arithmetic`(3) / `Adder` | A |
| 采样位宽 | 8（属性 `width`） | A |
| 功能 | **推断**：`Output = A + B`（未取进位输出） | C |

⚠️ **对本项目有特殊意义**：用户的验收要求明确**禁止**用单个内置 `Adder` 代替"4 个 1 位全加器"。
本样本正好演示了被禁止的做法，可作为**反例参照**。

---

## 6. 选择器：`Multiplexer`（`#Plexers`，ordinal 2）

`Mutiplexer_sample.circ`：1 个 `Multiplexer` + 4 个 1 位 `Pin`（`A`、`B` 输入，`Sel` 选择，`Output` 输出）。

| 项 | 值 | 级别 |
|---|---|---|
| 库 / XML 名 | `#Plexers`(2) / `Multiplexer` | A |
| 引脚 | `A`、`B`、`Sel` → `Output`，均 1 位 | A |
| 功能 | **推断**：`Output = Sel ? B : A` | C |

`Sel` 与 `A`/`B` 的**对应顺序**（选 0 出 A 还是出 B）**必须**用真值表实测确认，
不能按常规假设 —— 这正是本文件反复强调「名称不等于语义」的原因。

---

## 7. 存储与时序（`#Memory`，ordinal 5）

### 7.1 `Register`

`Reg_sample.circ`：1 个 `Register` + 1 个 `Clock` + 3 个 `Pin`（8 位输入、1 位、8 位输出）。

| 项 | 值 | 级别 |
|---|---|---|
| 库 / XML 名 | `#Memory`(5) / `Register` | A |
| 伴随元件 | `Clock`（`#Wiring`） | A |
| 功能 | **推断**：时钟上升沿锁存输入 → 输出 | C |

### 7.2 `RAM`

`RAM_sample.circ`：1 个 `RAM` + 1 个 `Clock` + 5 个 `Pin`（两个 8 位输入、两个 1 位、一个 8 位输出）。

| 项 | 值 | 级别 |
|---|---|---|
| 库 / XML 名 | `#Memory`(5) / `RAM` | A |
| 引脚 | 无 `label` —— 无法按名称引用 | A |

### 7.3 ⚠️ 时序电路无法用 `--tty table` 验证功能

**推断（C 级，待实测确认）**：`--tty table` 是**组合**真值表机制 —— 它枚举输入组合并读输出。
对含 `Clock` 的时序电路，输出取决于**历史状态**而非当前输入，因此：

- 单跑一遍真值表**不能**证明时序功能正确；
- 可能因未初始化状态而输出 `U`，或不收敛。

**结论：`RAM_sample.circ` 与 `Reg_sample.circ` 的功能正确性无法用现有工具验证。**
这必须如实报告，**不得**因为"文件能加载且打印了表"就宣称验证通过。

**需要的能力（尚未实现）**：时序验证需要能驱动时钟沿并逐步推进仿真 —— 属于 P8/P10 范围。

---

## 8. 其他观察到但未深究的元件

| 元件 | 库 | 样本 | 状态 |
|---|---|---|---|
| `Clock` | `#Wiring` | `RAM_sample`、`Reg_sample` | 属性见 `appearance=NewPins`；频率/高电平时间等未实测 |
| `Probe`、`Constant`、`Power`、`Ground`、`NoConnect`、`Pull Resistor`、`Transistor`、`Transmission Gate`、`Bit Extender` | `#Wiring` | 源码枚举，**样本中未出现** | 属性与端口 `[未确认]` |
| `Decoder`、`PriorityEncoder` | `#Plexers` | 未出现 | `[未确认]` |
| `Comparator`、`Shifter` | `#Arithmetic` | 未出现 | `[未确认]` |
| `Counter`、`ROM` | `#Memory` | 未出现 | `[未确认]` |
| `Button`、`LED`、`7-Segment` | `#I/O` | 未出现 | `[未确认]` |
| `#FPArithmetic`、`#TTL`、`#TCL`、`#Base`、`#BFH-Praktika`、`#Input/Output-Extra`、`#Soc` | — | 仅出现在 `<lib>` 声明中 | 内容 `[未确认]` |

**⚠️ 不要因为某元件出现在 `<lib>` 声明里，就认为它可用或已被使用** —— 见 `docs\circ-format.md` §2。
