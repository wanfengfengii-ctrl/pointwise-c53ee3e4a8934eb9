# 固定审查边界与证据接口

## 原入口及材料

run.calculate(case) 返回原 pandas DataFrame；run.evaluate(case) 返回 JSON 可表示的实际矩阵。调用是：
df.groupby("g", sort=True)[["x", "y"]].rolling(window, min_periods=min_periods, center=False, closed="right").cov(other=other, pairwise=True, ddof=1)

样例由 samples.get(name) 返回独立副本；normal 是分组连续的隐式自比较，single_external 是单组且外部行序改变，incident 是交错且大小不等的分组与显式副本。已有源码和回归的摘要在 source_manifest.py。审查定位使用 upstream/pandas/ 下的真实路径、函数和原始行号。

## 输入

输入 JSON 对象有 id、window、min_periods、rows、other：
{"id":"small","window":2,"min_periods":2,"rows":[{"obs":0,"g":"a","x":1.0,"y":2.0},{"obs":1,"g":"a","x":3.0,"y":6.0}],"other":null}

rows 长度为 1～8，最多三个非空字符串分组；obs 是互异整数，数组顺序是原观测顺序，不要求 obs 排序。x、y 是有限数值或 null，非空值绝对值不超过 1000，进入 API 时强制为 float64。窗口大小为 1～4，min_periods 为 1～window。other 为 null 或同样含 obs、x、y 的数组；显式输入的 obs 集合与 rows 相同且各出现一次，行序、数值和缺失值可以不同。other 不带分组列。非法输入不属于本轮审查范围。使用内存调用时 None 对应 JSON null。

## 核账口径

每组保留其原观测相对顺序。省略 other 时，只在本组观测上做隐式自比较；输出只包含本组 obs。

显式 other 按标签与每组观测作外对齐，再定整数窗口。两边标签序列完全相同时保持原顺序，否则按 obs 升序取并集；因此外部记录可以使展开序列包含不属于该组的位置。这些位置的左侧指标为空，仍占窗口位置，不能偷偷删掉再滚动。显式输出中每组保留并集的全部 obs，包括结果全为 NaN 的位置。other 的行序变化不能把按标签对应误解释成按位置对应。

对每个组、输出 obs、左侧列 p 和右侧列 q，窗口是展开序列中截至该位置的最后 window 个位置。只有左右值均非空的标签构成有效配对；先取窗口再剔除空配对。配对数 n 不足 min_periods 或 n<=1 时为 NaN；否则协方差为 sum((x-mean_x)*(y-mean_y))/(n-1)。右侧列 q 位于矩阵行索引最后一层，左侧列 p 位于矩阵列。实际比较容差为 1e-9 + 1e-9*abs(expected)。无穷值不在范围内。

例如 small 的第二个 obs 协方差矩阵为 [[2,4],[4,8]]，第一个 obs 全为 NaN。incident 的显式对齐期望在 a/1/x/x 为 NaN、b/4/x/x 为 50；这两项只是公开发布门，不代替完整审查。隐式与显式输出不要求形状相同，也不要求数值总相同。

## 实际矩阵格式

run.pack 的输出为 {"index":[[组,obs,右列],...],"index_names":["g","obs",null],"columns":["x","y"],"values":[[值,值],...]}。每项 values 对应同位置 index；NaN 编码为 null。正常 small 的 index 为 [["a",0,"x"],["a",0,"y"],["a",1,"x"],["a",1,"y"]]，values 为 [[null,null],[null,null],[2.0,4.0],[4.0,8.0]]。index_names 只是名称，不参与数值计算。缺少坐标与该坐标存在但为 null 不同。

## 维护产物

REVIEW.md 说明真实调用链、实质根因、源码行号、必要触发边界、影响及发布意见，关联可重跑证据；给出保持上述口径的最小兼容修复建议及适用边界。修订 docs/window-alignment.md，准确区分合法对齐行为和已证实的缺陷，说明受影响用法；不交付生产修复。

独立期望以普通 Python 逐组、逐标签、逐窗及协方差公式计算，不调用 pandas rolling/cov、生产窗口或对齐辅助函数，不把 groupby.apply 的结果当作期望。实际值必须调用 run.evaluate 或原 API，不能自己模拟。证据同时记录独立窗口成员、有效配对的两侧观测身份，以及实际计算到输出坐标的归属。实际成员可以用只读观测及源码取位映射追溯，不能通过改写计算逻辑制造；目标是在输出单元格上核定“计算了谁、标给了谁”。

review_evidence.py 提供 run、verify、demo 三个命令。run 从样例及新增有限小例重算并写 evidence.json；verify 重新运行正常回归、连续两次公开异常，以及自己的证据生成与独立核对，核对已保存 evidence.json 和报告引用；demo 展示真实差异和合法正例。review_evidence.py 的模块本身不在基线中预置答案。

evidence.json 顶层为 {"source":来源对象,"cases":[...],"replays":[...]}。source 等于 source_manifest.verify()。每个 cases 项包含 case（完整输入）、actual 和 expected（上述矩阵格式）、windows（独立窗口明细）、traces（实际成员与输出归属追溯，结构自定）、differences（按单元格比较结果）。windows 每项含 coordinate=[组,obs,右列,左列]、members=[窗口内obs,...]、pairs=[[左侧obs,右侧obs],...]；有效数值可从 case 回查。differences 每项含 coordinate、actual、expected；后两项形如 {"state":"number","value":50.0}、{"state":"nan"} 或 {"state":"absent"}，相同项不记录。cases 的 id 唯一，报告用 id 和坐标引用证据。replays 保存两次公开异常的退出码及原始 stdout，不能把预期发布门失败误报为证据验证失败。

新增证据要核对完整矩阵，并用单因素及组合对照区分分组重排、外部行序和缺失值的影响，保留合法对齐差异及正确结果的正例。无需枚举所有输入排列，也不要求审查相关系数、时间窗口、多层或重复行索引、扩展类型和性能。

保留 source_manifest.py、run.py、samples.py、checks.py 和上游实现及原断言。最终运行清单的 verify 使用 ["python3","review_evidence.py","verify"]，demo 使用 ["python3","review_evidence.py","demo"]，setup 仍保留固定依赖和源码镜像步骤。verify 必须实际重放并核对证据，不能仅校验文件存在或打印成功。
