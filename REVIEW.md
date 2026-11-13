# 分组滚动协方差观测归属审查报告（pandas 2.2.2）

- 审查对象：`df.groupby("g", sort=True)[["x","y"]].rolling(window, min_periods=min_periods, center=False, closed="right").cov(other=other, pairwise=True, ddof=1)`
- 固定版本：pandas 2.2.2，提交 `d9cdd2ee5a58015ef6f4d15c7226110c9aab8140`，numpy 1.26.4（身份与源码摘要由 `source_manifest.verify()` 校验，镜像在 `upstream/pandas/`，行号与发布包一致）
- 审查范围：float64 双列、唯一单层整数行索引、整数尾随窗口、`pairwise=True`；交错且大小不等分组、显式 `other` 行序变化、缺失值
- 结论：**隐式自比较路径正确；多组且传入显式 `other` 时存在已证实的归属错误（跨组计算 + 错标 + 合法值丢失），建议阻断该用法发布，不得以"显式副本等价隐式自比较"为由放行。**

## 1. 真实调用链

1. `core/groupby/groupby.py:3748` `DataFrameGroupBy.rolling` 在 `groupby.py:3879-3885` 构造 `RollingGroupby`，携带 `_grouper`（组名 → 原始行位置数组，如 `{'a': array([0,2]), 'b': array([1,3,4])}`）。
2. `core/window/rolling.py:1750-1795` `Rolling.cov` 定义闭包 `cov_func`，窗口边界来自 `self._get_window_indexer()`（`rolling.py:1766-1778`），数值由 `pandas._libs.window.aggregations` 的 `roll_mean/roll_sum` 在 `rolling.py:1781-1790` 算出，随后调用 `_apply_pairwise`（`rolling.py:1793-1794`）。
3. `RollingGroupby._get_window_indexer`（`rolling.py:2879-2912`）返回 `GroupbyIndexer`，其 `groupby_indices=self._grouper.indices`（`rolling.py:2905-2911`）。
4. `RollingGroupby._apply_pairwise`（`rolling.py:776-858`）先在 `rolling.py:788` 丢弃分组列，再于 `rolling.py:789` 调用基类 `BaseWindow._apply_pairwise`（`rolling.py:539-560`）；基类在 `rolling.py:550` 调 `_create_data`，而分组版 `_create_data`（`rolling.py:860-872`）按组拼接重排对象（incident 中行序变为 obs `[0,2,1,3,4]`），再由 `common.flex_binary_moment`（`core/window/common.py:17-137`）做 pairwise 展开，其 `prep_binary`（`common.py:164-169`）按标签外对齐。
5. 回到 `RollingGroupby._apply_pairwise`：当任一组长度不等于 `len(other)` 时走 GH 42915 分支（`rolling.py:791-816`），对全局结果执行 `result.take(gb_indices).reindex(result.index)`（`rolling.py:799-804`）并按组 `concat`；否则走 `rolling.py:817-837` 的编码重复分支。最终在 `rolling.py:849-857` 拼装三层 `MultiIndex`（组、obs、右列）。
6. `GroupbyIndexer.get_window_bounds`（`core/indexers/objects.py:387-438`）逐组按**组内位置**生成边界，再用 `window_indices`（`objects.py:424-433`）平移拼接。

## 2. 实质根因（三层叠加，均有证据）

### 根因 A：窗口边界的行布局与实际被滚动数组的行布局不一致

`GroupbyIndexer`（`objects.py:402-433`）假定被滚动数组按"组拼接"布局排列：incident 下它对 `num_values=5` 给出边界 `[[0,1],[0,2],[2,3],[2,4],[3,5]]`（见 `evidence.json` 中 `incident.traces.bounds_by_num_values.observed_5`），即第 3 个位置的窗口是位置 `[2,3)`。

但显式 `other` 时，`cov_func` 实际收到的是 `prep_binary` 外对齐后的数组，其标签顺序是并集升序 `[0,1,2,3,4]`（`incident.traces.cov_calls[*].aligned_labels`）。把"组拼接布局"的边界套到"标签并集布局"的数组上，位置窗口切到的观测就错了：并集位置 1 的窗口 `[0,2)` 实际装的是 obs 0（组 a）与 obs 1（组 b）——**跨组观测被放进同一窗口**。隐式路径不发生外对齐（`rolling.py:551-552` 令 `other = target`，数组保持组拼接布局），布局与边界恰好一致，所以隐式路径正确（证据 `normal`、`interleaved_implicit` 差异均为 0）。

### 根因 B：按位置 `take` 的重分配把全局值错标给别的组

`rolling.py:799-804` 不按标签取数，而是用组在**原始帧**中的位置数组 `gb_indices`（a=[0,2]、b=[1,3,4]）去 `take` 一个按**并集标签**排序的全局结果。incident 的全局结果（`traces.global_result_rows/global_result_values`）在并集行 obs 1 上有值 `[40.5,13.5]/[13.5,4.5]`：

- 组 a 取全局位置 0、2（`traces.attribution_map[0]`：taken 行为 `[0,x]、[1,x]`），于是全局 obs 1 的 `x` 行被贴到输出坐标 `(a,1,x)`；obs 1 根本不属于组 a，且其值是 obs 0 与 obs 1 的跨组协方差。
- 组 b 取位置 1、3、4（`attribution_map[1]`：taken 行为 `[0,y]、[1,y]、[2,x]`），全局 obs 1 的 `y` 行被贴到 `(b,1,y)`，而组 b 自己在 obs 4 的合法结果（全局位置 4）从未被任何组取到，经 `reindex` 变成 NaN——**合法计算丢失并形成缺失坐标值**。

### 根因 C：空出的并集位置无法承担"占位但不删除"的合同语义

合同要求显式对齐后，不属于本组的并集标签左侧为空、仍占窗口座位。现实现中这些位置既被根因 A 的错误边界跨过，又在根因 B 的位置重排中与真实组位置错位，导致 NaN 的含义无法区分：`(a,1)` 的 NaN/非 NaN 与"有效配对不足"无关，纯粹是布局错配的产物。

### 为什么现有回归没有拦住

- `tests/window/test_groupby.py:175-190` `test_rolling_corr_cov_other_diff_size_as_groups` 的 `roll_frame` 分组是连续块（[1]×20+[2]×12+[3]×8），组位置序列在数值上与并集位置一致，掩盖了根因 A/B；其期望还取自 `groupby.apply`（`test_groupby.py:186`），本审查按合同不采信该路径作为独立期望。
- `test_groupby.py:150-172` 是每组长度恰好等于 `other` 的特化形状；`test_groupby.py:193-205`、`211-240` 均为隐式自比较。交错分组 + 显式 `other` 的组合无上游覆盖。

## 3. 逐窗核对结论（实际 vs 期望，观测身份层面）

以 `incident`（window=2、min_periods=2，显式副本，组 a={0,2}、组 b={1,3,4}）为例，完整明细在 `evidence.json` 的 `windows`（独立引擎给出的成员与有效配对身份）、`traces`（生产侧实际取数与归属）与 `differences`：

| 输出坐标 | 合同窗口成员/有效配对 | 独立期望值 | pandas 实际 | 判定 |
|---|---|---|---|---|
| `(a,1,x,x)` | 成员 [0,1]；左侧仅 obs 0 属于 a，配对数 n=1 | NaN | 40.5（obs0 与 obs1 的跨组 cov） | 跨组计算+错标 |
| `(a,1,x,y)` | 同上，n=1 | NaN | 13.5 | 跨组计算+错标 |
| `(b,1,y,x)`、`(b,1,y,y)` | 成员 [0,1]；obs 0 不属于 b，右侧有值但左侧空，n=0 | NaN | 13.5 / 4.5 | 跨组计算+错标 |
| `(b,4,x,x)` 等 4 格 | 成员 [3,4]，配对 (3,3)(4,4)，n=2 | 50.0 | NaN（全局位置 4 未被 take） | 合法值丢失 |
| `(a,0,*)`、`(b,3,*)` 等 | 配对不足 min_periods | NaN | NaN | 数值巧合一致，归属仍按上表追溯 |

公开异常门 `checks.py defect` 的两项（`a/1/x/x` 应为 NaN 实为 40.5；`b/4/x/x` 应为 50 实为 NaN）与本审查独立重算一致；它们只是门限，完整 8 个差异单元格以 `evidence.json` 为准。

## 4. 触发边界与单因素/组合对照

11 个证据用例（`evidence.json.cases`，id 唯一）的差异计数：

| 用例 id | 因素 | 差异单元格 |
|---|---|---|
| `normal` | 连续分组，隐式 | 0（正例） |
| `interleaved_implicit` | 交错不等组，隐式 | 0（正例：组隔离成立） |
| `single_external` | 单组，显式 other 行序打乱 | 0（正例：单组时布局与边界一致，标签对齐不受行序影响） |
| `single_group_missing` | 单组显式 + 缺失值，w=3/mp=2 | 0（正例：合法 NaN 与配对计数） |
| `incident` | 交错不等组 + 显式副本 | 8 |
| `contiguous_explicit_copy` | 连续不等组 + 显式副本（仅"显式"单因素） | 16 |
| `interleaved_explicit_reordered` | 交错 + other 行序置换 | 8 |
| `interleaved_equal_size_explicit` | 交错等组 + 显式（两分支形状均受影响） | 4 |
| `interleaved_explicit_missing` | 交错 + 显式 + 缺失值组合 | 1（其余被合法 NaN 掩盖） |
| `three_groups_w3_mp1` | 三组交错、w=3、mp=1 | 4 |
| `explicit_other_value_shift` | 交错 + other 同标签改值 | 8 |

边界结论：

- **必要条件**：传入非 None 的显式 `other`，且分组数 > 1。单组（`single_external`、`single_group_missing`）不受影响，因为只有一个组时 `GroupbyIndexer` 边界覆盖整帧、无跨段拼接，`take` 也不发生组间错位。
- **不需要**分组交错：`contiguous_explicit_copy` 证明连续分组同样出错（根因 B 的位置重排独立于交错）。
- **不需要**组大小不等：`interleaved_equal_size_explicit` 证明等组也出错（此时仍走 `rolling.py:791` 的扩展分支，因为条件是"任一组长度 != len(other)"，等组但每组小于整帧即为真）。
- other 的**行序变化本身合法且不改变标签对齐结果**：`single_external` 中 other 按 [2,0,1] 提供，结果与隐式完全一致；`interleaved_explicit_reordered` 与 `incident` 的实际输出逐格相同，证明 pandas 也是按标签而非按位置消费 other 行序——文档不得把行序变化描述为风险源，风险源是分组包装器。
- other 的**数值变化是合法差异**：`explicit_other_value_shift` 中其他值整体平移，非分组的 `DataFrame.rolling(...).cov(other=...)` 能正确使用两侧不同数值（已手工复核 obs1 的 x-x=40.5、y-y=4.5）；分组路径的错误数值仍取自这套正确对齐后的数组，错在窗口切分与归属，而不是对齐取值本身。
- 缺失值会**掩盖**部分错误格（NaN 与 NaN 不可区分），但不消除根因：`interleaved_explicit_missing` 仍有 1 格实值错标（40.5）。
- 窗口 1～4、min_periods 1～window 内规律一致；`three_groups_w3_mp1` 显示 mp<window 时错误非 NaN 值更容易暴露。

## 5. 影响与发布意见

- 影响面：所有"多组 groupby + rolling + 显式 other + pairwise 协方差/相关类矩"用法。相关系数 `Rolling.corr` 共用 `_apply_pairwise` 与同一索引器（`rolling.py:1797-1848`），同根因，虽不在本轮复核范围，应同步挂起。
- 风险性质：静默数据错误。不抛异常、形状与坐标看似完整，错误表现为（1）把 A 组窗口算成跨 A/B 观测、（2）把值贴到未参与计算的组标签下、（3）真实组内结果变 NaN。下游按 `(g, obs)` 核账会把他人观测记到本组，属于账实不符，无法靠输出自检发现。
- 发布意见：**不通过**。在修复发布前，业务侧只允许使用省略 `other` 的隐式自比较；确需显式 `other` 时限单组数据，并在调用处断言分组数为 1。保留 `checks.py defect` 退出 1 的公开异常门，不得调门限或改样例。

## 6. 最小兼容修复建议（仅建议，不交付生产修复）

保持 CONTRACT.md 口径、改动面最小的方向：

1. 在 `RollingGroupby._apply_pairwise`（`rolling.py:776-858`）中，当 `other is not None` 时，不要把为组拼接布局构造的 `GroupbyIndexer` 复用于外对齐后的数组。应为 pairwise 路径按"每组 × 并集标签序列"构造窗口：先在 `prep_binary` 之后取得并集标签顺序，再以组为单位把边界限制在该组在并集中的位置（组外标签保留为空座位、参与窗口计数），从源头消除根因 A。
2. 用**标签化重排**替换 `rolling.py:799-804` 的位置 `take`：按 `(组, obs)` 标签把全局结果映射回各组并集坐标，禁止跨组取行，消除根因 B，并保证组内合法结果不丢失。
3. 适用边界与回归：修复需新增"交错/连续分组 × 显式 other × 缺失值 × other 行序置换"的最小矩阵断言（即本审查 11 个用例的形状），且不得改变隐式路径与单组显式路径的现有数值（`normal`、`single_external`、`single_group_missing` 必须保持 0 差异）；`pairwise=False`、时间窗口、多层/重复索引不在该补丁的兼容承诺内，需另行评估。

## 7. 证据与重跑方式

- `review_evidence.py run`：独立纯 Python 引擎逐组、逐标签、逐窗重算（不调用 rolling/cov、不使用 groupby.apply、不调用生产对齐辅助函数），实际值仅经 `run.evaluate`/原 API 取得，写出 `evidence.json`。
- `review_evidence.py verify`：重放 `checks.py normal` 原断言、连续两次 `checks.py defect`（退出码 1 且打印 `GROUPED_COV_ALIGNMENT_MISMATCH` 属预期发布门，不计为验证失败）、重新生成证据并与已保存文件逐格比对、校验报告引用。
- `review_evidence.py demo`：并列展示真实错标/丢失与合法正例。
- `evidence.json` 结构：`source`（= `source_manifest.verify()`）、`cases`（含 `case/actual/expected/windows/traces/differences`）、`replays`（两次发布门的退出码与原始 stdout）。
