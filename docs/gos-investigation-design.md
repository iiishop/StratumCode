# Investigation 收敛机制设计文档（GoS 参考）v0.1

> 状态：设计稿（待评审，未实现）
> 日期：2026-08-22（v0.1）
> 关联：`stratumcode/status/investigation_transitions.py`、`task_updates.py`、`task_contract.py`（现状）
> 灵感：ICML 2026 *Graph of States: Solving Abductive Tasks with Large Language Models*（arXiv:2603.21250，南开大学 / 阿里云 / 联想 / 清华）

## 1. 背景与动机

Investigation 是 StratumCode 的证据收集阶段：用户指认 ≠ 强证据，必须通过工具观测（read/code_nav/lsp/grep/glob）拿到证据链才能进入设计。现状已经有一套结构化状态：任务契约（unknowns/hypotheses/clues）、beliefs（带 evidence 链）、observations/knowledge 分层、`InvestigationTransitionPolicy` 做状态转换。

对照 GoS 论文的诊断，现有实现还有四个结构性缺口：

1. **没有置信度模型**：belief 只有 `status`（默认 supported），没有 confidence 分数，没有"top 假设 vs 第二假设"的区分度概念——收工决策只能靠"还有没有 blocking unknown"，无法判断"证据是否已收敛"
2. **硬上限替代收敛**：`MAX_INVESTIGATION_PASSES = 3` 是武断硬上限。GoS 的结论是：该停的时候是"top 假设置信度差 + 支持证据数达标"，不是"查了 N 次"
3. **没有假设级回溯**：blocking unknown 机制只处理"某个问题没查清"，但"祖先假设被证伪 → 派生的细假设全部作废"这种结构性剪枝没有显式规则，浪费 token 且容易带偏结论
4. **没有粒度链**：hypothesis/unknown 是扁平 list，没有"粗假设 → 细假设"的 refine 关系，调查轨迹不可按粒度审计

GoS 的核心洞察与 StratumCode 的定位是同一件事：**把"什么时候该停、什么时候该回头"从模型自治变成显式规则**——这正是"掌控感"的量化形式。

## 2. 设计原则

- **收敛驱动，不设武断硬上限**：停止条件 = 置信度收敛（δ + η 双阈值），`MAX_INVESTIGATION_PASSES` 降级为预算兜底
- **证据驱动置信度**：confidence 只能由 observations（工具产出的证据）加权得出，模型不得自报——守住"用户指认 ≠ 强证据"红线
- **最小侵入**：不引入 GoS 的完整因果图引擎，不引入角色化多 agent（那是领域特化，与通用定位冲突）；只补缺口
- **粒度链复用现有树结构**：`task_updates` 已有 `parent_id` 树，refine 关系在其上加语义，不新建平行结构
- **IS 最小可审计互不耦合**：置信度计算、转换判定、粒度链各自独立模块，转换决策只读状态不持有状态

## 3. 数据模型（对现状的增量扩展）

### 3.1 belief 扩展

现状（`task_updates.py` `_beliefs_as_knowledge`）：`{ statement, status, evidence }`，status 默认 `supported`。

扩展为：

```json
{
  "id": "B3",
  "statement": "根因是 Redis 连接配置问题",
  "status": "supported",
  "confidence": 0.62,
  "evidence": ["obs:12", "obs:17"],
  "refutes": ["obs:9"],
  "refines": "H1",
  "pending_checks": []
}
```

- `confidence`：0–1，由支持/反证证据加权得出（见 §4.3），**不允许模型直接输出**
- `status`：`supported | refuted | disputed | unverified`——从 `refutes` 非空推导 `refuted`，证据互相矛盾推导 `disputed`
- `refines`：指向更粗粒度的父假设（粒度链，见 §3.2）
- `pending_checks`：需要进一步验证的具体问题（drill-down 的候选动作）

### 3.2 粒度链（refine 关系）

```
H1  "可能是 Redis 连接问题"            ← level 1（粗）
 └─ H2  "是 Redis timeout 配置问题"    ← level 2（细，refines=H1）
     └─ H3  "是 connect_timeout 参数未生效"  ← level 3（最细）
```

- 复用 `task_updates` 的 `parent_id` 树，`refines` 字段 = 语义标注：父节点是"更粗的假设"
- 层级 `level` 从根起算（粗 = 1），下钻 +1，回溯回退
- 粒度链只在 investigation 内部维护，不污染任务契约

### 3.3 转换决策输出

`InvestigationTransitionDecision` 扩展：

```json
{
  "action": "drill_down | backtrack | continue | conclude",
  "target_hypothesis": "H2",
  "reason": "confidence gap 0.21 > δ(0.15); support evidence 3 ≥ η(2)",
  "pruned": ["H2", "H3"]
}
```

- `conclude` = 收敛条件满足且当前粒度足以回答请求（替代 `next_step == "done"` 的语义，但以收敛判定为充分条件）
- `pruned` = 本次回溯剪掉的假设 id 列表（审计用）

## 4. 状态转换规则（GoS Algorithm 2 映射）

### 4.1 Drill-down（下钻）：双阈值收敛

现状：`_investigation_allows_patch` = 无 blocking unknown + 无 unknown task + `ready_for_patch_planning`。

改为：在现有条件之上，增加收敛判定作为**充分条件**：

1. **置信度差**：`P(H_top) − P(H_2nd) > δ`——当前方向明确优于竞争假设
2. **最小支持证据数**：`|support_evidence(H_top)| ≥ η`——防止仅凭先验概率收窄

两者同时满足才允许 `drill_down`（细化到下一粒度）或 `conclude`（当前粒度足够细）。

- 不满足 → `continue` 收集更多证据，**不触发中断**
- δ/η 是控制旋钮：调高 → 更保守（倾向停在表层结论），调低 → 更激进（倾向冲细粒度根因）——GoS sensitivity 实验已验证这一 trade-off

### 4.2 Backtracking（回溯）：祖先证伪 → 剪枝

现状：blocking unknown 机制只重新触发调查，不剪枝。

新增规则：**监控当前推理焦点的祖先链**。任一祖先假设被证伪（`refuted`）或不再是其兄弟中的最高置信度假设：

1. 找到最浅的被降级祖先层级 `l*`
2. 剪掉所有 `level > l*` 的假设（`pruned` 列表记录）
3. 状态回到 `l*`，调查焦点切到之前休眠的替代分支

原理：建立在错误前提上的推断天然无效。对应红线"快捷路径不得绕过证据链"的结构化形式。

### 4.3 置信度计算（证据加权）

`confidence(H) = f(support, refute)`，初始建议：

- `support` 证据 +1，`refute` 证据 −1，加 sigmoid 归一或简单比例 `support / (support + refute)`
- 证据强度分级：静态观测（grep/read 命中）0.5 权重，动态验证（运行/测试通过）1.0 权重
- 归一化到 0–1，冷启动（无证据）给 0.5（未验证态）

具体函数形式待 MyCAS 实验确定，原则只有一条：**输入只允许 observations/knowledge 里的证据，模型自述不算数**。

### 4.4 与现有 InvestigationTransitionPolicy 的整合

| 现状判定 | 改动 |
|---|---|
| `MAX_INVESTIGATION_PASSES = 3` 硬性判定 | 降级为预算兜底：仅当收敛条件持续不满足且预算耗尽时触发，提示"证据未收敛，建议人工介入" |
| `_investigation_allows_patch`（无 blocking/unknown） | 保留为必要条件，新增收敛条件为充分条件 |
| `next_step == "failed"` + blocking unknowns | 保留，回溯剪枝后同一机制继续工作 |
| `_has_blocking_unknown` | 保留——blocking unknown 是"信息缺口"，收敛判定是"证据强度"，两者互补 |

## 5. 实现映射（改动点清单）

| 文件 | 改动 |
|---|---|
| `status/task_updates.py` | belief 加 `confidence`/`refutes`/`refines`/`pending_checks`；status 枚举扩展 |
| `status/investigation_transitions.py` | 新增收敛判定（§4.1）+ 回溯剪枝（§4.2）；passes 上限改预算兜底 |
| `status/task_analysis.py` | 契约分析阶段对 hypothesis 标注 level/refines 候选（可选，先由模型产出） |
| `status/investigation_events.py` | 事件流加 `converged` / `pruned` 事件（审计可见性） |
| `light_agent/`（prompt 侧） | investigation prompt 增加"输出 confidence 证据依据"约束，禁止自报数值 |

## 6. 已定决策

1. **收敛判定替代硬上限**（δ + η 双阈值，passes 上限降为兜底）——与"拒武断硬上限，调查收敛驱动"一致
2. **不引入完整因果图引擎**——StratumCode 已有任务契约 + beliefs + unknowns 三层，够用；GoS 只补"置信度 + 回溯剪枝 + 粒度链"三个缺口
3. **不引入角色化多 agent**——GoS 的 Primary_Physician 式分工是领域特化，StratumCode 要通用，现有 subagent_catalog 已覆盖分工需求
4. **置信度证据驱动，模型禁自报**——红线结构化
5. **粒度链复用 parent_id 树**——不新建平行结构

## 7. 待定项 / 未做

1. **δ/η 默认值**：GoS 未给出固定推荐（sensitivity 实验只验证了 trade-off 方向），需在 MyCAS 上实验标定
2. **confidence 函数形式**：sigmoid vs 比例归一，待实验
3. **推理焦点（DFS）策略**：GoS 强制每次只查最高置信度假设；StratumCode 现状可并行查多个 unknown——保留并行作为可选策略，不强制 DFS
4. **剪枝后证据保留**：剪掉的细假设其证据是否回收到 observations（防止重复采集），待定
5. **粒度链与现有 task contract 的持久化**：粒度链只在 investigation 内部维护，是否进 session memory 长期保存，待定
6. **成本度量**：GoS 报告收敛搜索比宽搜索省 ~6 倍成本（$0.12 vs $0.73/case）；StratumCode 是否引入成本 KPI 待定
