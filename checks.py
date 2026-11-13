from _pointwise_deps import enable
enable()
"""Frozen normal regressions and a public, deliberately failing release gate."""
import json
import math
import sys
import pandas as pd
from pandas.testing import assert_frame_equal
from source_manifest import verify
from samples import get
from run import calculate, evaluate

def normal():
    verify()
    from pandas.tests.window.test_groupby import TestRolling
    # Invoke the original tests directly, with their original assertions.
    TestRolling().test_rolling_corr_cov_unordered("cov", [[1.0, 1.0], [1.0, 4.0]])
    frame = pd.DataFrame({"A": [1] * 20 + [2] * 12 + [3] * 8, "B": range(40)})
    TestRolling().test_rolling_corr_cov_other_diff_size_as_groups("cov", frame)
    TestRolling().test_rolling_corr_cov_pairwise("cov", frame)
    nan = float("nan")
    idx = pd.MultiIndex.from_tuples(
        [(g, i, f) for g, ids in [("a", [0, 1]), ("b", [2, 3, 4])]
         for i in ids for f in ("x", "y")], names=["g", "obs", None])
    expected = pd.DataFrame(
        [[nan, nan], [nan, nan], [2.0, 4.0], [4.0, 8.0],
         [nan, nan], [nan, nan], [50.0, 50.0], [50.0, 50.0],
         [50.0, 50.0], [50.0, 50.0]], index=idx, columns=["x", "y"])
    assert_frame_equal(calculate(get("normal")), expected)
    idx = pd.MultiIndex.from_tuples(
        [("a", i, f) for i in range(3) for f in ("x", "y")],
        names=["g", "obs", None])
    expected = pd.DataFrame(
        [[nan, nan], [nan, nan], [2.0, 4.0], [4.0, 8.0],
         [2.0, 4.0], [4.0, 8.0]], index=idx, columns=["x", "y"])
    assert_frame_equal(calculate(get("single_external")), expected)
    print("NORMAL_REGRESSION_OK")

def defect():
    actual = calculate(get("incident"))
    unexpected = actual.loc[("a", 1, "x"), "x"]
    lost = actual.loc[("b", 4, "x"), "x"]
    print(json.dumps({"a/1/x/x": None if math.isnan(unexpected) else unexpected,
                      "b/4/x/x": None if math.isnan(lost) else lost}, allow_nan=False))
    if not math.isnan(unexpected) or math.isnan(lost) or abs(lost - 50.0) > 1e-10:
        print("GROUPED_COV_ALIGNMENT_MISMATCH")
        return 1
    raise AssertionError("Frozen source no longer reproduces the release gate")

if __name__ == "__main__":
    if sys.argv[1:] == ["normal"]:
        normal()
    elif sys.argv[1:] == ["defect"]:
        verify()
        raise SystemExit(defect())
    else:
        raise SystemExit("usage: python3 checks.py normal|defect")
