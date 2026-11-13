# 分组滚动协方差使用说明

本文是本团队针对 pandas 2.2.2（提交 `d9cdd2ee5a58015ef6f4d15c7226110c9aab8140`）的现行核账说明。结论以 `REVIEW.md` 与 `evidence.json` 的复核证据为准；本文不是 pandas 上游文档，也不是正确性证明。

## 使用场景

观测按 `g` 分组，`x`、`y` 为 float64 指标，行索引为唯一单层整数标签。当前工作流使用右端整数尾随窗口（`center=False`、`closed="right"`）、`pairwise=True`、`ddof=1`。每次计算返回三层行索引（组、obs、右列）与左列矩阵列，应按 `(g, obs, 右列, 左列)` 回溯观测身份，不能只看行号位置。

## 已证实正确的用法（可继续使用）

1. **省略 `other` 的隐式自比较**：窗口在本组观测序列内滚动，输出只含本组 obs；交错分组同样保持组隔离（证据用例 `normal`、`interleaved_implicit` 与独立期望零差异）。
2. **单组数据上的显式 `other`**：只有一个组时，显式 `other` 与隐式自比较的窗口位置和输出坐标一致（证据用例 `single_external`、`single_group_missing` 零差异）。
3. **显式 `other` 的行序变化是合法行为**：两侧按 obs 标签外对齐，`other` 数组自身的行序不影响结果；`single_external` 中 other 按 `[2,0,1]` 提供与按 `[0,1,2]` 提供结果相同。按标签对应不能按位置对应解读。
4. **缺失值语义**：先定窗口、再剔除两侧任一为空的配对；有效配对数 `n <= 1` 或 `n < min_periods` 时结果为 NaN。空位置仍占窗口座位，不得先删空行再滚动。`single_group_missing` 给出了 w=3、min_periods=2 下配对计数的正例。
5. **`other` 同标签取不同数值是合法数值差异**：标签决定与谁配对，数值决定协方差大小；隐式与显式输出不要求形状相同，也不要求数值相同。

## 已证实的缺陷用法（发布阻断）

**多组（分组数 > 1）且传入非 None 的显式 `other` 时，pandas 2.2.2 的分组包装器会静默产生归属错误**，与分组是否交错、组大小是否相等无关（证据用例 `incident`、`contiguous_explicit_copy`、`interleaved_equal_size_explicit`、`three_groups_w3_mp1` 等）。三类表现：

- **跨组计算**：为组拼接布局构造的窗口边界被套到标签外对齐后的数组上，窗口可同时装入不同组的观测，例如 `incident` 的 `(a,1,x,x)` 实际是 obs 0（组 a）与 obs 1（组 b）的协方差 40.5，按口径应为 NaN。
- **错标**：全局结果按原始行位置 `take` 重分配给各组，别的组算出的值被贴到本组标签下（如 `(b,1,y,y)` 实为 4.5，口径为 NaN）。
- **合法结果丢失**：组内真实协方差因位置重排取不到而变成 NaN，如 `incident` 的 `(b,4,x,x)` 口径为 50.0，实际为 NaN。

缺失值会让一部分错误格以 NaN 对 NaN 的形式被掩盖，但不会消除缺陷（见 `interleaved_explicit_missing`）。相关系数 `corr` 共用同一条 pairwise 包装路径，按同根因对待，在修复前一并挂起。

## 临时适用边界

- 仅使用省略 `other` 的隐式自比较；多组场景禁止显式 `other`。
- 如业务必须传显式 `other`，先断言分组数为 1，并保留该断言在调用处。
- 发布门 `python3 checks.py defect` 退出 1 并打印 `GROUPED_COV_ALIGNMENT_MISMATCH` 是当前冻结版本的预期行为；`python3 checks.py normal` 通过只覆盖隐式与特化形状，不能作为显式多组用法放行的依据。
- 复核与重放：`python3 review_evidence.py run|verify|demo`，证据文件为 `evidence.json`，根因与源码行号见 `REVIEW.md`。
