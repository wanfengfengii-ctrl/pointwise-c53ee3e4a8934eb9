from _pointwise_deps import enable
enable()
"""Run the original pandas API, without an alternative covariance implementation."""
import json
import math
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from source_manifest import verify
from samples import get

FEATURES = ["x", "y"]

def validate(case):
    assert isinstance(case["id"], str) and case["id"]
    rows = case["rows"]
    assert isinstance(rows, list) and 1 <= len(rows) <= 8
    assert len({r["obs"] for r in rows}) == len(rows)
    assert 1 <= case["window"] <= 4 and type(case["window"]) is int
    assert type(case["min_periods"]) is int and 1 <= case["min_periods"] <= case["window"]
    assert all(isinstance(r["g"], str) and r["g"] for r in rows)
    assert len({r["g"] for r in rows}) <= 3
    all_rows = rows + (case.get("other") or [])
    for row in all_rows:
        assert type(row["obs"]) is int
        for col in FEATURES:
            v = row[col]
            assert v is None or (type(v) in (int, float) and math.isfinite(v) and abs(v) <= 1000)
    other = case.get("other")
    if other is not None:
        assert isinstance(other, list) and len(other) == len(rows)
        assert {r["obs"] for r in other} == {r["obs"] for r in rows}

def frames(case):
    validate(case)
    df = pd.DataFrame(case["rows"]).set_index("obs")
    df[FEATURES] = df[FEATURES].astype("float64")
    other = case.get("other")
    if other is not None:
        other = pd.DataFrame(other).set_index("obs")[FEATURES].astype("float64")
    return df, other

def calculate(case):
    df, other = frames(case)
    return df.groupby("g", sort=True)[FEATURES].rolling(
        case["window"], min_periods=case["min_periods"],
        center=False, closed="right"
    ).cov(other=other, pairwise=True, ddof=1)

def scalar(value):
    if pd.isna(value):
        return None
    if isinstance(value, np.generic):
        return value.item()
    return value

def pack(frame):
    return {
        "index": [[scalar(v) for v in idx] for idx in frame.index],
        "index_names": list(frame.index.names),
        "columns": list(frame.columns),
        "values": [[scalar(v) for v in row] for row in frame.to_numpy()]
    }

def evaluate(case):
    verify()
    return pack(calculate(case))

def main():
    args = sys.argv[1:]
    if args == ["demo"]:
        for name in ("normal", "single_external", "incident"):
            case = get(name)
            print(json.dumps({"case": case, "actual": evaluate(case)}, allow_nan=False))
    elif len(args) == 2 and args[0] == "sample":
        print(json.dumps(get(args[1]), allow_nan=False))
    elif len(args) == 2 and args[0] == "file":
        case = json.loads(Path(args[1]).read_text(encoding="utf-8"))
        print(json.dumps({"case": case, "actual": evaluate(case)}, allow_nan=False))
    else:
        raise SystemExit("usage: python3 run.py demo|sample NAME|file CASE.json")

if __name__ == "__main__":
    main()
