# 分组滚动协方差发布审查仓库

上游是 https://github.com/pandas-dev/pandas ，固定 v2.2.2，提交 d9cdd2ee5a58015ef6f4d15c7226110c9aab8140。本仓库是围绕真实发布包的维护工作区，不含替代版统计引擎。生产调用只经过已安装的 pandas。运行清单先将当前解释器对应的固定二进制 wheel 保存到 vendor/wheels，再从本地 wheel 安装依赖；source_manifest.py 校验上游身份及关键源码摘要，并把发布包内全部 Python 源码（含原测试）复制到 upstream/pandas，原行号保持不变。无需重新构建 pandas，也不需要访问在线业务数据。upstream/ 是只读审查镜像，不作为另一个导入路径；安装包中的源码也是保留对象。

运行清单的 setup 按顺序执行后：
- python3 checks.py normal：运行选定上游原回归及完整矩阵业务断言。
- python3 checks.py defect：公开异常发布门；基线退出 1 并打印 GROUPED_COV_ALIGNMENT_MISMATCH。
- python3 run.py demo：打印正常、外部行序变化和公开异常的真实输入及实际输出。
- python3 run.py sample incident：输出可保存为输入文件的完整 JSON。
- python3 run.py file CASE.json：读取一个样例同格式的 JSON 并调用原 API。

代码审查对象在 upstream/pandas/core/window/、upstream/pandas/core/indexers/ 和分组入口；原测试位于 upstream/pandas/tests/window/test_groupby.py。docs/window-alignment.md 是团队现行使用说明，需要维护，不能当作正确性依据。CONTRACT.md 固定了业务语义与证据接口。

本轮交付是审查、使用说明和复核证据。不要修 pandas、替换 calculate、修改样例或削弱 checks.py；可以新增证据用例和验证代码。源镜像、原安装包和现有断言均保留。选定上游原测试由 checks.py 直接调用其原方法，避免加载无关的全库测试设施。
