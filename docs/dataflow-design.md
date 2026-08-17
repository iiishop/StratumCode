# 数据流图（Dataflow Graph）设计文档 v1.0

> 状态：需求定稿（v0.1 实现已 discard，按本文档重新实现）
> 日期：2026-08-05（v1.0 迭代记录）
> 关联：`stratumcode/code_structure/`（现有调用图），新视图与调用图以 tab 共存

## 1. 产品定位

Code Structure 面板的第二个视图：**函数级数据流图，UE 蓝图风格**。

- **不展开函数体**：看到的是函数间的数据流转——文件 > 类 > 函数 嵌套（UML 类图形态），函数节点带 pin（左侧输入、右侧输出），数据边在 pin 间连接
- **双击展开函数体**：**主页面原地内联展开**（不切子页面），函数体是蓝图式的节点 + 连线——大部分由函数组成，一个的输出流向另一个的输入
- **动态层（愿景，后续）**：给 validation 的 agent builtin 工具，输入测试参数跑插桩代码，回放 trace——"测试数据流入 → 经过什么 → 怎么变化 → 怎么流出"

## 2. 设计原则

- **蓝图 pin 语义**：数据连接是 pin 级的，`sourcePin → targetPin`，不是节点级
- **从左到右**：函数体执行方向从左到右（UE 蓝图习惯），入参在左、return 在右
- **两套线**：白色 exec 线（执行顺序）+ 橙色数据线（数据流动），蓝图式分离
- **两层分离**：静态层（画布，AST 推断）+ 动态层（trace 动画，bdb 采集）——trace 不污染静态数据模型
- **懒加载**：函数体在展开时才请求，避免大项目一次性爆炸
- **函数块唯一实现（复用）**：主图函数节点与函数体内项目内函数调用渲染成**同一个函数块组件**（FuncBlock），只有尺寸差异——函数体内的 add 块必须和外面定义 add 的块一模一样：名字、入参名、出参名、蓝/橙 pin 圆点
- **真实包围盒**：展开函数节点的大小 = 函数体内容的真实布局尺寸（自动计算，不是估算）；类/文件容器宽度高度实时跟随展开态（与 call graph 的 module-group 背景框机制一致）
- **pin 类型粗分**：Python 动态类型，推断后粗分 `number | string | collection | object | callable | unknown`

## 3. 数据模型

### 3.1 节点类型

```
file      文件容器（最外层框）
class     类容器（类变量 + 方法列表）
function  函数节点（蓝图风格，pin 输入输出）
entry     文件级入口块（__main__ / Main / 顶层代码）
```

### 3.2 函数节点

```json
{
  "id": "func:app.py:add",
  "kind": "function",
  "name": "add",
  "parent": "file:app.py",
  "signature": "def add(a, b):",
  "line": 1,
  "inputs": [
    { "pin": "a", "type": "unknown", "kind": "param" },
    { "pin": "b", "type": "unknown", "kind": "param" }
  ],
  "outputs": [
    { "pin": "result", "type": "unknown", "kind": "return", "doc": "docstring 提取的返回值说明" }
  ],
  "implicit": {
    "reads":  [ { "name": "MATH_CONST", "kind": "global" } ],
    "writes": [ { "name": "self.cache",  "kind": "class_attr" } ]
  },
  "doc": "docstring 全文（AST ast.get_docstring 提取，Sphinx :param: / Google Args: 双风格）",
  "body": null
}
```

- `inputs/outputs`：正式 pin（形参 / return），pin 名 = **被调函数定义的参数名**
- `implicit`：隐式依赖（全局/类/闭包变量读写）——节点底部角标，悬停显示详情，不占 pin 位置
- `body: null` = 未展开，前端双击时单独请求

### 3.3 数据边（跨函数）

```json
{
  "id": "edge:calc->Formant_Input:input",
  "kind": "data",
  "source": "func:app.py:calc",
  "sourcePin": "result",
  "target": "func:app.py:Formant_Input",
  "targetPin": "input",
  "confidence": 0.6
}
```

- 语义：**函数 A 的返回值流入函数 B 的入参**（跨函数数据边）
- 统计口径：主图 toolbar 显示 "N cross-func data edges"——注意与函数体内数据边区分
- `confidence`：静态推断可信度（AST 直传 0.9，动态访问 0.4 之类）

### 3.4 类容器 / 3.5 入口块（同 v0.1）

```json
{
  "id": "class:app.py:Calculator",
  "kind": "class",
  "name": "Calculator",
  "parent": "file:app.py",
  "attrs": [ { "name": "precision", "type": "float" } ],
  "methods": ["func:app.py:Calculator.calc", "func:app.py:Calculator.helper"]
}
```

```json
{
  "id": "entry:app.py",
  "kind": "entry",
  "name": "__main__",
  "parent": "file:app.py",
  "calls": ["func:app.py:main"]
}
```

### 3.6 函数体（懒加载，展开时请求）

```json
{
  "functionId": "func:app.py:multiply",
  "blocks": [
    { "id": "blk:1", "kind": "param", "name": "a", "type": "unknown" },
    { "id": "blk:2", "kind": "param", "name": "b", "type": "unknown" },
    { "id": "blk:3", "kind": "var",  "name": "res", "type": "int", "init": "0" },
    { "id": "blk:4", "kind": "container", "name": "for", "condition": "_ in range(b)",
      "inputs": [ { "pin": "b", "src": "blk:2" } ],
      "outputs": [ { "pin": "element", "kind": "element" } ],
      "children": ["blk:6", "blk:5"] },
    { "id": "blk:6", "kind": "call", "name": "add", "target": "func:app.py:add",
      "modifier": null,
      "inputs": [ { "pin": "a", "arg": "res", "src": "blk:3" },
                  { "pin": "b", "arg": "a",   "src": "blk:1" } ],
      "outputs": [ { "pin": "result" } ] },
    { "id": "blk:5", "kind": "var", "name": "res", "type": "int",
      "from": "blk:6", "op": "res = add(res, a)" },
    { "id": "blk:7", "kind": "return", "name": "return", "value": "res", "src": "blk:5" }
  ],
  "data_edges": [
    { "source": "blk:1", "sourcePin": "result", "target": "blk:6", "targetPin": "b", "confidence": 0.9 },
    { "source": "blk:3", "sourcePin": "result", "target": "blk:6", "targetPin": "a", "confidence": 0.9 },
    { "source": "blk:6", "sourcePin": "result", "target": "blk:5", "targetPin": "result", "confidence": 0.9 },
    { "source": "blk:2", "sourcePin": "result", "target": "blk:4", "targetPin": "b", "confidence": 0.85 },
    { "source": "blk:5", "sourcePin": "result", "target": "blk:7", "targetPin": "result", "confidence": 0.9 }
  ],
  "exec_order": ["blk:3", "blk:4", "blk:7"],
  "calls_out": ["func:app.py:add", "builtin:range"]
}
```

块类型说明：

- `param`：函数入参（**最原始输入源**），函数体最左侧一列，纵向排列
- `var`：变量/常量赋值（值来源）。`init` = 字面量（常量），`op` = 运算文本；若赋值源是项目内函数调用（`res = add(...)`），`from` = 对应 call 块 id（渲染时 call 块作为独立节点排在 var 前面）
- `call`：函数调用。`target` 前缀分类：`func:` 项目内 / `builtin:` / `external:` / `external_member_call:` / `array:`（数组操作）。**`inputs[].pin` 用被调函数参数名，`arg` 存实参名**（hover 显示 `a ← res`）；`modifier: "await"` 标记异步调用
- `container`：控制形态（if/for/while/with/try），`children` 嵌套、不画回边。**`inputs` = 条件/迭代表达式里消费的变量**（`range(b)` 只取 b 不取 range）；`outputs` 按形态：
  - if → `true / false`
  - for → `element`（循环变量名，`_` 归一为 element）
  - while → `body / done`
  - with → `ctx`
  - try → `handled`
- `return`：`value` = 返回表达式文本，`src` = 值来源块（数据边指向 return）

数据边覆盖：入参块/变量块/常量/前一个调用输出 → 调用参数、变量来源、return 来源、容器条件消费——**函数体内的数据流全量**。

## 4. 函数体蓝图规范（UE 蓝图参考）

### 4.1 布局：从左到右

- 节点按执行顺序从左到右排列（x 递增），同一级同一行；控制容器是横向虚线框，包住内部子图
- 最左侧 = param 列（入参，纵向排列，最原始输入）
- 最右侧 = return
- 容器（if/for/while）本身占一个位置，内部子节点从容器 x 起向右排（递归布局）
- **节点尺寸**：call 节点高度随 pin 数（输入/输出最大值 × 行高）；容器宽高 = 内部子图包围盒 + padding + header
- **展开函数节点大小 = 函数体真实包围盒**（bodyLayout.width/height），主图类/文件容器宽度跟随展开态自动计算——与 call graph 的 module-group 机制一致（绝对坐标 + 包围盒 + zIndex 分层）

### 4.2 两套线

- **exec 白线**（实线 + 箭头）：执行顺序，从入参后第一个节点起串联；进容器 → 子链 → 容器 completed 出（容器左缘 → 首子 → … → 末子 → 容器右缘）
- **数据线**（橙色虚线 + 箭头）：数据流动，从来源节点输出点 → 消费节点输入点

### 4.3 节点形态（复用 FuncBlock 函数块）

- 主图函数节点与函数体内项目内函数调用 = **同一个组件**（FuncBlock），full/mini 两档尺寸
- 形态：标题栏（类型徽章 + 函数名）+ 左右 pin 名 + **pin 圆点**（in 蓝、out 橙）+ 隐式依赖角标
- 函数体内 call 块：`inputs[].pin` 显示**被调函数参数名**（与主图一致），hover 显示实参来源（`a ← res`）；docstring 从主图函数数据查（hover 全文）；双击跳转展开目标函数
- 外部/builtin/array 调用：同形态但徽章颜色区分（builtin 绿、external 紫、array 紫），不可跳转

### 4.4 类型着色（UE 蓝图规范映射）

| 蓝图 | 我们的表达 |
|---|---|
| 红 Event | 入口块 / param 输入源（红色系） |
| 蓝 Impure Function | 项目内函数调用（蓝色徽章 + 左边框） |
| 绿 Pure Function | return / builtin 纯函数（绿色） |
| 灰 Flow Control | if/for/while 容器框（各色虚线框：if 蓝、for 紫、while 橙、try 红、with 绿） |
| 黄 Async/Latent | await 调用（⚡ 标记，黄色） |
| 紫 Array 节点 | 数组操作（append/len/range/enumerate 等，array 徽章） |
| 变量 Get/Set | var 块（读/写角标区分） |

### 4.5 控制形态（容器 → 蓝图节点语义）

- `for` = ForEachLoop：iterable 输入 pin（消费的变量，如 range(b) 的 b）+ element 输出 pin + 内部子链 exec
- `if` = Branch：条件输入 pin（x、lo…）+ true/false 输出 pin 标注
- `while` = WhileLoop：condition 输入 + body/done 输出
- `try` = 异常容器：handled 输出（子块含 except 分支）
- `await` = 异步调用：调用块带 ⚡（后续可加 delegate 感输出 pin）
- `yield` = 生成器块（绿色系独立块）

### 4.6 交互

- 双击函数节点：主页面原地内联展开（不切子页面），✕ 收起 / 再双击自身收起
- 展开时周围节点不淡化（保持整体上下文）
- 双击函数体内项目内函数块：跳转展开目标函数（布局自动重排）

## 5. 已定决策（迭代记录）

1. **函数体内项目内函数 = 复用外面函数块的完整形态**（FuncBlock 组件，唯一实现）——不是简化卡片
2. **展开交互 = 主页面内联展开**（删聚焦子页面模式），✕ 收起
3. **函数体布局从左到右**（不是纵向），入参列在最左
4. **展开节点/容器大小 = 内容真实包围盒**（自动计算，非估算），类/文件容器跟随展开态
5. **pin 名用被调函数参数名**（`a ← res` 实参作 hover 标注）
6. **两套线**：exec 白线 + 数据橙线；函数级不画 exec 线，函数体内画
7. **容器条件消费**：`range(b)` / `x < lo` 等条件表达式里的变量 → 容器输入 pin + 数据边（排除调用目标名）
8. **主图统计口径**：cross-func data edges（跨函数），与函数体内数据边区分
9. **只做 Python**（后端 AST 分析，前端通用）
10. **`__main__` 等 = 文件级入口块**，统一跨语言概念
11. **动态追踪机制**参考 OnlinePythonTutor / python-preview：`bdb.Bdb` 钩子 + trace_entry schema + pg_encoder 对象图编码；python-preview 是 2018 年代码，`imp` 模块在 Python 3.12 已移除，逻辑要重写（importlib 替代）
12. **静态图是画布，动态 trace 是动画帧**；trace JSON 是动态层数据源
13. **动态层触发** = 用户选中函数 → 输入测试参数 → 运行；同时做成 agent builtin 工具给 validation 用（输入: function+args，输出: trace+summary）
14. **docstring 用 AST 直接提取**（ast.get_docstring），Sphinx/Google 双风格——不走 LSP

## 6. 待定项 / 未做

1. **if true/false 物理分轨布局**（Branch 双输出轨道，两个分支上下分叉）——当前是线性排 + 输出 pin 标注
2. **exec 线执行高亮动画**（运行时白线闪烁，UE 蓝图执行指示）——动态层联动
3. **自由拖拽布局**（当前固定行高/列宽自动布局）
4. **赋值型外部调用数据来源标注**（`result = math.sqrt(total)` 函数体内边）——当前覆盖独立调用 + 项目内赋值调用
5. **数组节点的完整操作集**（MakeArray/Get/Length 等专用形态）——当前只做了分类徽章
6. **隐式依赖伪 pin 展开**后数据边静态先猜（低 confidence），动态层验证
7. `entry` 块发出的边——执行入口的数据边语义（调用边 or 特殊 exec 起点）
