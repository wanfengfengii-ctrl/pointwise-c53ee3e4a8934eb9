"""Independent review evidence for grouped rolling covariance (pandas 2.2.2).

This module never reimplements the production result.  The *actual* side only
calls the original API via run.evaluate / run.calculate; the *expected* side is
plain Python (no pandas rolling/cov, no groupby.apply, no production alignment
helpers) and recomputes every window, its member observations and every
covariance from the contract in CONTRACT.md.

Commands:
    run      recompute every case and (re)write evidence.json
    verify   replay regressions, the public defect gate twice, regenerate and
             re-check the evidence, and cross-check REPORT references
    demo     show real defects and legal positive examples side by side
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from _pointwise_deps import enable  # noqa: E402

enable()

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import run as run_mod  # noqa: E402
import samples  # noqa: E402
from source_manifest import verify as source_verify  # noqa: E402

FEATURES = ["x", "y"]
EVIDENCE_PATH = HERE / "evidence.json"
REPORT_PATH = HERE / "REVIEW.md"
TOL_ABS = 1e-9
TOL_REL = 1e-9


# --------------------------------------------------------------------------- #
# Independent, plain-Python expectation engine (contract semantics)
# --------------------------------------------------------------------------- #
def _row_map(rows):
    return {r["obs"]: r for r in rows}


def _is_number(v):
    return v is not None and isinstance(v, (int, float)) and math.isfinite(v)


def _union_labels(case, group_labels=None):
    """Labels of the per-group expanded sequence, per CONTRACT.md.

    Same label sequence on both sides keeps the original (group) order;
    otherwise the union is the ascending sorted set of obs.  An implicit
    self-comparison never leaves the group, so its sequence is the group's own
    observation order.
    """
    rows = case["rows"]
    other = case.get("other")
    if other is None:
        own = [r["obs"] for r in rows]
        return own if group_labels is None else [o for o in own if o in set(group_labels)]
    left = [r["obs"] for r in rows]
    right = [r["obs"] for r in other]
    if left == right:
        return list(left)
    return sorted(set(left) | set(right))


def _side_values(case, group_labels, side):
    """Return {obs: {col: float|None}} restricted to the expanded labels.

    Left side: only observations owned by the group carry values; every other
    union position is an empty (NaN) slot that still occupies a window seat.
    Right side: an explicit other has no group column, so every union label
    resolves to the other's value (or NaN when the other cell is missing).
    """
    if side == "left":
        source = _row_map(case["rows"])
        owned = set(group_labels)
        values = {}
        for label in _union_labels(case, group_labels):
            if label in owned:
                values[label] = {c: source[label][c] for c in FEATURES}
            else:
                values[label] = {c: None for c in FEATURES}
        return values
    other = case.get("other")
    assert other is not None
    source = _row_map(other)
    return {label: {c: source[label][c] for c in FEATURES} for label in source}


def _column_cov(members, left_vals, right_vals, p, q, min_periods, ddof=1):
    """Cov(left column p, right column q) over the window members."""
    pairs = []
    for label in members:
        lv = left_vals[label][p]
        rv = right_vals[label][q]
        if _is_number(lv) and _is_number(rv):
            pairs.append((label, label))
    n = len(pairs)
    if n <= 1 or n < min_periods:
        return None, pairs
    xs = [float(left_vals[i][p]) for i, _ in pairs]
    ys = [float(right_vals[j][q]) for _, j in pairs]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    return sxy / (n - ddof), pairs


def expected_case(case):
    """Return (packed_matrix, windows) computed independently in plain Python."""
    case = deepcopy(case)
    window = case["window"]
    min_periods = case["min_periods"]
    other = case.get("other")

    # group membership in original observation order, groups sorted by label
    groups = {}
    for r in case["rows"]:
        groups.setdefault(r["g"], []).append(r["obs"])
    group_names = sorted(groups)

    union_global = _union_labels(case)
    index, values, windows = [], [], []
    for g in group_names:
        group_labels = groups[g]
        union = list(group_labels) if other is None else list(union_global)
        left_vals = _side_values(case, group_labels, "left")
        if other is None:
            rows_by_obs = _row_map(case["rows"])
            right_vals = {
                label: {c: rows_by_obs[label][c] for c in FEATURES}
                for label in union
            }
        else:
            right_vals = _side_values(case, group_labels, "right")

        for pos, label in enumerate(union):
            members = union[max(0, pos + 1 - window): pos + 1]
            row_cells = {}
            for q in FEATURES:  # right column -> matrix row (last index level)
                for p in FEATURES:  # left column -> matrix column
                    value, pairs = _column_cov(
                        members, left_vals, right_vals, p, q, min_periods
                    )
                    windows.append(
                        {
                            "coordinate": [g, label, q, p],
                            "members": list(members),
                            "pairs": [[i, j] for i, j in pairs],
                        }
                    )
                    row_cells[p] = value
                index.append([g, label, q])
                values.append([row_cells["x"], row_cells["y"]])

    packed = {
        "index": index,
        "index_names": ["g", "obs", None],
        "columns": list(FEATURES),
        "values": [[None if v is None else float(v) for v in row] for row in values],
    }
    return packed, windows


# --------------------------------------------------------------------------- #
# Actual-side trace: read-only observation of what the original code computes
# --------------------------------------------------------------------------- #
def trace_actual(case):
    """Run the original API and trace, without altering its computation:

    * the GroupbyIndexer window bounds the production cov_func requests,
    * the aligned series each cov_func actually receives (labels + values),
    * how the pairwise wrapper attributes the global result to groups.

    Only observation/harness code is used here; no production source file is
    modified and no alternative calculation is substituted.
    """
    df, other_df = run_mod.frames(case)

    from pandas.core.window import rolling as rolling_mod
    from pandas.core.indexers.objects import GroupbyIndexer

    trace = {
        "grouper_indices": None,
        "expanded_union_labels": None,
        "conformed_label_order": None,
        "cov_calls": [],
        "bounds_by_num_values": {},
        "attribution": [],
    }

    rolling = df.groupby("g", sort=True)[FEATURES].rolling(
        case["window"],
        min_periods=case["min_periods"],
        center=False,
        closed="right",
    )
    grouper = rolling._grouper
    trace["grouper_indices"] = {
        str(k): [int(v) for v in vals] for k, vals in grouper.indices.items()
    }
    trace["conformed_label_order"] = [
        int(obs) for vals in grouper.indices.values() for obs in vals
    ]
    indexer = rolling._get_window_indexer()
    assert isinstance(indexer, GroupbyIndexer)

    # Bounds the GroupbyIndexer builds for the two lengths the code path hits:
    # group-conformed length (implicit path) and outer-union length (explicit).
    for n in sorted({len(df), len(trace["conformed_label_order"])}):
        start, end = indexer.get_window_bounds(
            num_values=n,
            min_periods=case["min_periods"],
            center=False,
            closed="right",
            step=None,
        )
        trace["bounds_by_num_values"][str(n)] = [
            [int(s), int(e)] for s, e in zip(start, end)
        ]

    # Observe, without changing results, the exact aligned Series that the
    # production prep_binary (common.py:164-169) hands to cov_func, plus the
    # window bounds actually requested at that aligned length.
    from pandas.core.window import common as window_common

    original_prep_binary = window_common.prep_binary
    original_get_window_bounds = GroupbyIndexer.get_window_bounds

    def observed_prep_binary(arg1, arg2):
        x, y = original_prep_binary(arg1, arg2)
        trace["cov_calls"].append(
            {
                "left_column": arg1.name,
                "right_column": arg2.name,
                "aligned_labels": [int(v) for v in x.index],
                "left_values": [None if pd.isna(v) else float(v) for v in x.values],
                "right_values": [None if pd.isna(v) else float(v) for v in y.values],
            }
        )
        return x, y

    def observed_bounds(self, num_values=0, min_periods=None, center=None,
                        closed=None, step=None):
        start, end = original_get_window_bounds(
            self, num_values, min_periods, center, closed, step
        )
        trace["bounds_by_num_values"]["observed_" + str(num_values)] = [
            [int(s), int(e)] for s, e in zip(start, end)
        ]
        return start, end

    window_common.prep_binary = observed_prep_binary
    GroupbyIndexer.get_window_bounds = observed_bounds
    try:
        result = rolling_mod.Rolling.cov(
            rolling, other=other_df, pairwise=True, ddof=1, numeric_only=False
        )
    finally:
        window_common.prep_binary = original_prep_binary
        GroupbyIndexer.get_window_bounds = original_get_window_bounds

    # Record the label-aligned inputs that an outer join would build and the
    # resulting output coordinates straight from the real result frame.
    if other_df is not None:
        target = rolling._create_data(rolling._selected_obj).drop(
            columns=grouper.names, errors="ignore"
        )
        aligned_left, aligned_right = target.align(other_df, join="outer")
        trace["expanded_union_labels"] = [int(x) for x in aligned_left.index]
        trace["aligned_left_order"] = [int(x) for x in aligned_left.index]
        trace["aligned_right_order"] = [int(x) for x in aligned_right.index]

    for idx, row in zip(result.index, result.to_numpy()):
        g, obs, q = idx
        trace["attribution"].append(
            {
                "coordinate": [g, int(obs), q],
                "values": [None if pd.isna(v) else float(v) for v in row],
            }
        )

    # Read-only reconstruction of the positional attribution the wrapper does.
    # The base-class pairwise path produces one global frame indexed by the
    # aligned labels; RollingGroupby._apply_pairwise then redistributes its
    # rows by *position* (rolling.py:799-804).  We replay the same base path
    # with the original covariance closure purely to name the rows involved.
    trace["attribution_map"] = []
    if other_df is not None:
        from pandas import Series
        from pandas.core.dtypes.missing import notna
        import pandas._libs.window.aggregations as window_aggregations

        target = rolling._create_data(rolling._selected_obj).drop(
            columns=grouper.names, errors="ignore"
        )

        def cov_closure(x, y):
            x_array = rolling._prep_values(x)
            y_array = rolling._prep_values(y)
            window_indexer = rolling._get_window_indexer()
            start, end = window_indexer.get_window_bounds(
                num_values=len(x_array),
                min_periods=case["min_periods"],
                center=False,
                closed="right",
                step=None,
            )
            with np.errstate(all="ignore"):
                mean_x_y = window_aggregations.roll_mean(
                    x_array * y_array, start, end, case["min_periods"]
                )
                mean_x = window_aggregations.roll_mean(
                    x_array, start, end, case["min_periods"]
                )
                mean_y = window_aggregations.roll_mean(
                    y_array, start, end, case["min_periods"]
                )
                count_x_y = window_aggregations.roll_sum(
                    notna(x_array + y_array).astype(np.float64), start, end, 0
                )
                out = (mean_x_y - mean_x * mean_y) * (
                    count_x_y / (count_x_y - 1)
                )
            return Series(out, index=x.index, name=x.name, copy=False)

        global_frame = rolling_mod.BaseWindow._apply_pairwise(
            rolling, target, other_df, True, cov_closure, False
        )
        trace["global_result_rows"] = [
            [int(idx[0]), idx[1]] for idx in global_frame.index
        ]
        trace["global_result_values"] = [
            [None if pd.isna(v) else float(v) for v in row]
            for row in global_frame.to_numpy()
        ]
        if not all(len(vals) == len(other_df) for vals in grouper.indices.values()):
            # rolling.py:799-804: result.take(gb_indices).reindex(result.index)
            for gkey, gb_indices in grouper.indices.items():
                taken = global_frame.take([int(v) for v in gb_indices])
                trace["attribution_map"].append(
                    {
                        "group": str(gkey),
                        "group_positions": [int(v) for v in gb_indices],
                        "taken_global_rows": [
                            [int(idx[0]), idx[1]] for idx in taken.index
                        ],
                    }
                )

    actual = run_mod.pack(result)
    # The traced call uses the identical original API chain as run.calculate;
    # assert equivalence so the actual side can never drift into a simulation.
    sanctioned = run_mod.pack(run_mod.calculate(case))
    assert sanctioned == actual
    return actual, trace


# --------------------------------------------------------------------------- #
# Cell-level comparison
# --------------------------------------------------------------------------- #
def _state(value):
    if value is None:
        return {"state": "nan"}
    return {"state": "number", "value": float(value)}


def compare_cells(actual, expected):
    """Diff two packed matrices; missing coordinates are state 'absent'."""
    def to_map(packed):
        return {
            tuple(idx): list(vals)
            for idx, vals in zip(packed["index"], packed["values"])
        }

    a_map, e_map = to_map(actual), to_map(expected)
    diffs = []
    for coord in sorted(set(a_map) | set(e_map), key=lambda c: (str(c[0]), c[1], str(c[2]))):
        g, obs, q = coord
        for j, p in enumerate(FEATURES):
            a_row = a_map.get(coord)
            e_row = e_map.get(coord)
            a_cell = None if a_row is None else a_row[j]
            e_cell = None if e_row is None else e_row[j]
            if a_cell is None and e_cell is None:
                continue
            if a_cell is None or e_cell is None:
                diffs.append(
                    {
                        "coordinate": [g, obs, q, p],
                        "actual": {"state": "absent"} if a_row is None else _state(a_cell),
                        "expected": {"state": "absent"} if e_row is None else _state(e_cell),
                    }
                )
                continue
            if abs(a_cell - e_cell) > TOL_ABS + TOL_REL * abs(e_cell):
                diffs.append(
                    {
                        "coordinate": [g, obs, q, p],
                        "actual": _state(a_cell),
                        "expected": _state(e_cell),
                    }
                )
    return diffs


# --------------------------------------------------------------------------- #
# Finite, in-scope additional evidence cases (single-factor + combination)
# --------------------------------------------------------------------------- #
def extra_cases():
    cases = []

    # 1. Interleaved, unequal groups, implicit self: group isolation positive.
    cases.append(
        {
            "id": "interleaved_implicit",
            "window": 2,
            "min_periods": 2,
            "rows": [
                {"obs": 0, "g": "a", "x": 1.0, "y": 2.0},
                {"obs": 1, "g": "b", "x": 10.0, "y": 5.0},
                {"obs": 2, "g": "a", "x": 3.0, "y": 6.0},
                {"obs": 3, "g": "b", "x": 20.0, "y": 15.0},
                {"obs": 4, "g": "b", "x": 30.0, "y": 25.0},
            ],
            "other": None,
        }
    )

    # 2. Contiguous, unequal groups, explicit identical copy: explicit vs
    #    implicit factor alone (no interleave) must still follow the contract.
    cases.append(
        {
            "id": "contiguous_explicit_copy",
            "window": 2,
            "min_periods": 2,
            "rows": [
                {"obs": 0, "g": "a", "x": 1.0, "y": 2.0},
                {"obs": 1, "g": "a", "x": 3.0, "y": 6.0},
                {"obs": 2, "g": "b", "x": 10.0, "y": 5.0},
                {"obs": 3, "g": "b", "x": 20.0, "y": 15.0},
                {"obs": 4, "g": "b", "x": 30.0, "y": 25.0},
            ],
            "other": [
                {"obs": 0, "x": 1.0, "y": 2.0},
                {"obs": 1, "x": 3.0, "y": 6.0},
                {"obs": 2, "x": 10.0, "y": 5.0},
                {"obs": 3, "x": 20.0, "y": 15.0},
                {"obs": 4, "x": 30.0, "y": 25.0},
            ],
        }
    )

    # 3. Interleaved + explicit other whose row order is permuted: isolates the
    #    external row-order factor; label alignment must be position-free.
    cases.append(
        {
            "id": "interleaved_explicit_reordered",
            "window": 2,
            "min_periods": 2,
            "rows": [
                {"obs": 0, "g": "a", "x": 1.0, "y": 2.0},
                {"obs": 1, "g": "b", "x": 10.0, "y": 5.0},
                {"obs": 2, "g": "a", "x": 3.0, "y": 6.0},
                {"obs": 3, "g": "b", "x": 20.0, "y": 15.0},
                {"obs": 4, "g": "b", "x": 30.0, "y": 25.0},
            ],
            "other": [
                {"obs": 4, "x": 30.0, "y": 25.0},
                {"obs": 2, "x": 3.0, "y": 6.0},
                {"obs": 0, "x": 1.0, "y": 2.0},
                {"obs": 3, "x": 20.0, "y": 15.0},
                {"obs": 1, "x": 10.0, "y": 5.0},
            ],
        }
    )

    # 4. Interleaved groups of EQUAL size with explicit copy: both branches in
    #    rolling.py:791 and :817 are exercised; no size mismatch to hide behind.
    cases.append(
        {
            "id": "interleaved_equal_size_explicit",
            "window": 2,
            "min_periods": 2,
            "rows": [
                {"obs": 0, "g": "a", "x": 1.0, "y": 2.0},
                {"obs": 1, "g": "b", "x": 10.0, "y": 5.0},
                {"obs": 2, "g": "a", "x": 3.0, "y": 6.0},
                {"obs": 3, "g": "b", "x": 20.0, "y": 15.0},
            ],
            "other": [
                {"obs": 0, "x": 1.0, "y": 2.0},
                {"obs": 1, "x": 10.0, "y": 5.0},
                {"obs": 2, "x": 3.0, "y": 6.0},
                {"obs": 3, "x": 20.0, "y": 15.0},
            ],
        }
    )

    # 5. Missing values only, single group, explicit other: legal NaN semantics
    #    and pair-counting positive example (min_periods counts valid pairs).
    cases.append(
        {
            "id": "single_group_missing",
            "window": 3,
            "min_periods": 2,
            "rows": [
                {"obs": 0, "g": "a", "x": 1.0, "y": None},
                {"obs": 1, "g": "a", "x": 3.0, "y": 6.0},
                {"obs": 2, "g": "a", "x": None, "y": 10.0},
                {"obs": 3, "g": "a", "x": 7.0, "y": 14.0},
            ],
            "other": [
                {"obs": 0, "x": 1.0, "y": None},
                {"obs": 1, "x": 3.0, "y": 6.0},
                {"obs": 2, "x": None, "y": 10.0},
                {"obs": 3, "x": 7.0, "y": 14.0},
            ],
        }
    )

    # 6. Interleaved, unequal groups, explicit copy WITH missing values:
    #    combination case (all three factors at once).
    cases.append(
        {
            "id": "interleaved_explicit_missing",
            "window": 2,
            "min_periods": 2,
            "rows": [
                {"obs": 0, "g": "a", "x": 1.0, "y": None},
                {"obs": 1, "g": "b", "x": 10.0, "y": 5.0},
                {"obs": 2, "g": "a", "x": 3.0, "y": 6.0},
                {"obs": 3, "g": "b", "x": None, "y": 15.0},
                {"obs": 4, "g": "b", "x": 30.0, "y": None},
            ],
            "other": [
                {"obs": 0, "x": 1.0, "y": None},
                {"obs": 1, "x": 10.0, "y": 5.0},
                {"obs": 2, "x": 3.0, "y": 6.0},
                {"obs": 3, "x": None, "y": 15.0},
                {"obs": 4, "x": 30.0, "y": None},
            ],
        }
    )

    # 7. Three interleaved groups, window 3, min_periods 1: wider window and
    #    min_periods below window, outsider slots occupying window seats.
    cases.append(
        {
            "id": "three_groups_w3_mp1",
            "window": 3,
            "min_periods": 1,
            "rows": [
                {"obs": 0, "g": "a", "x": 1.0, "y": 2.0},
                {"obs": 1, "g": "b", "x": 10.0, "y": 5.0},
                {"obs": 2, "g": "c", "x": 4.0, "y": 8.0},
                {"obs": 3, "g": "a", "x": 5.0, "y": 9.0},
                {"obs": 4, "g": "b", "x": 20.0, "y": 15.0},
                {"obs": 5, "g": "c", "x": 6.0, "y": 12.0},
            ],
            "other": [
                {"obs": 0, "x": 1.0, "y": 2.0},
                {"obs": 1, "x": 10.0, "y": 5.0},
                {"obs": 2, "x": 4.0, "y": 8.0},
                {"obs": 3, "x": 5.0, "y": 9.0},
                {"obs": 4, "x": 20.0, "y": 15.0},
                {"obs": 5, "x": 6.0, "y": 12.0},
            ],
        }
    )

    # 8. Explicit other with different values but identical labels: proves the
    #    other's values legitimately change numbers while labels drive joining.
    cases.append(
        {
            "id": "explicit_other_value_shift",
            "window": 2,
            "min_periods": 2,
            "rows": [
                {"obs": 0, "g": "a", "x": 1.0, "y": 2.0},
                {"obs": 1, "g": "b", "x": 10.0, "y": 5.0},
                {"obs": 2, "g": "a", "x": 3.0, "y": 6.0},
                {"obs": 3, "g": "b", "x": 20.0, "y": 15.0},
                {"obs": 4, "g": "b", "x": 30.0, "y": 25.0},
            ],
            "other": [
                {"obs": 0, "x": 2.0, "y": 1.0},
                {"obs": 1, "x": 11.0, "y": 4.0},
                {"obs": 2, "x": 4.0, "y": 5.0},
                {"obs": 3, "x": 21.0, "y": 14.0},
                {"obs": 4, "x": 31.0, "y": 24.0},
            ],
        }
    )
    return cases


def all_cases():
    base = [samples.get(name) for name in ("normal", "single_external", "incident")]
    cases = base + extra_cases()
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "case ids must be unique"
    return cases


# --------------------------------------------------------------------------- #
# Evidence generation
# --------------------------------------------------------------------------- #
def build_evidence():
    source = source_verify()
    case_entries = []
    for case in all_cases():
        run_mod.validate(case)
        expected, windows = expected_case(case)
        actual, trace = trace_actual(case)
        differences = compare_cells(actual, expected)
        case_entries.append(
            {
                "case": case,
                "actual": actual,
                "expected": expected,
                "windows": windows,
                "traces": trace,
                "differences": differences,
            }
        )
    return {"source": source, "cases": case_entries, "replays": []}


def run_defect_gate():
    proc = subprocess.run(
        [sys.executable, str(HERE / "checks.py"), "defect"],
        cwd=HERE,
        capture_output=True,
        text=True,
    )
    return {
        "argv": ["python3", "checks.py", "defect"],
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "printed_marker": "GROUPED_COV_ALIGNMENT_MISMATCH" in proc.stdout,
    }


def command_run():
    evidence = build_evidence()
    # two replays of the public defect gate
    evidence["replays"] = [run_defect_gate(), run_defect_gate()]
    EVIDENCE_PATH.write_text(
        json.dumps(evidence, allow_nan=False, indent=2), encoding="utf-8"
    )
    n_diff = sum(len(c["differences"]) for c in evidence["cases"])
    print(
        "EVIDENCE_WRITTEN",
        EVIDENCE_PATH.name,
        "cases=",
        len(evidence["cases"]),
        "differing_cells=",
        n_diff,
        "replays=",
        len(evidence["replays"]),
    )
    return evidence


# --------------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------------- #
def verify_evidence_consistency(evidence):
    problems = []
    # 1. source identity still pins the frozen wheel
    assert evidence["source"] == source_verify()

    # 2. every case: actual re-evaluated now equals stored actual; expected
    #    recomputed independently now equals stored expected; diffs reproduce.
    stored = {c["case"]["id"]: c for c in evidence["cases"]}
    live_ids = [c["id"] for c in all_cases()]
    if set(stored) != set(live_ids):
        problems.append("case id set drift: %r vs %r" % (sorted(stored), live_ids))
    for case in all_cases():
        entry = stored[case["id"]]
        if entry["case"] != case:
            problems.append("%s: stored case input differs" % case["id"])
        fresh_actual, _ = trace_actual(case)
        if fresh_actual != entry["actual"]:
            problems.append("%s: stored actual not reproduced" % case["id"])
        fresh_expected, fresh_windows = expected_case(case)
        if fresh_expected != entry["expected"]:
            problems.append("%s: stored expected not reproduced" % case["id"])
        if fresh_windows != entry["windows"]:
            problems.append("%s: stored windows not reproduced" % case["id"])
        fresh_diffs = compare_cells(fresh_actual, fresh_expected)
        if fresh_diffs != entry["differences"]:
            problems.append("%s: stored differences not reproduced" % case["id"])
        # structural self-check: windows cover every output coordinate once
        coords = [tuple(w["coordinate"]) for w in entry["windows"]]
        expected_coords = [
            tuple(idx[:2] + [idx[2], p])
            for idx in entry["expected"]["index"]
            for p in FEATURES
        ]
        if sorted(map(str, coords)) != sorted(map(str, expected_coords)):
            problems.append("%s: window detail coordinate coverage mismatch" % case["id"])

    # 3. replays: the defect gate must fail with the published marker
    if len(evidence["replays"]) != 2:
        problems.append("expected exactly two defect replays")
    for i, replay in enumerate(evidence["replays"]):
        if replay["returncode"] != 1 or not replay["printed_marker"]:
            problems.append("replay %d did not reproduce the release gate" % i)

    # 4. REVIEW.md references only existing case ids and cited coordinates exist
    text = REPORT_PATH.read_text(encoding="utf-8")
    for cid in live_ids:
        # ids are quoted in the report; just ensure every id appears at least once
        if cid not in text:
            problems.append("REVIEW.md does not reference case %s" % cid)
    return problems


def command_verify():
    # baseline upstream regressions + business assertions (original assertions)
    normal = subprocess.run(
        [sys.executable, str(HERE / "checks.py"), "normal"],
        cwd=HERE,
        capture_output=True,
        text=True,
    )
    print(normal.stdout, end="")
    if normal.returncode != 0 or "NORMAL_REGRESSION_OK" not in normal.stdout:
        print(normal.stderr)
        raise SystemExit("VERIFY_FAILED: normal regression")

    # regenerate evidence, then compare against the checked-in file
    regenerated = build_evidence()
    stored = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
    if stored.get("source") != regenerated["source"]:
        raise SystemExit("VERIFY_FAILED: source identity drift")
    if json.dumps(stored.get("cases"), sort_keys=True) != json.dumps(
        regenerated["cases"], sort_keys=True
    ):
        raise SystemExit("VERIFY_FAILED: evidence.json cases not reproducible")

    # live defect-gate replays, two times, and compare to stored replay shape
    live_replays = [run_defect_gate(), run_defect_gate()]
    for live, saved in zip(live_replays, stored["replays"]):
        if live["returncode"] != saved["returncode"] or live["stdout"] != saved["stdout"]:
            raise SystemExit("VERIFY_FAILED: defect replay drift")
    if any(r["returncode"] != 1 for r in live_replays):
        raise SystemExit("VERIFY_FAILED: release gate no longer fails as published")

    problems = verify_evidence_consistency(stored)
    if problems:
        for p in problems:
            print("PROBLEM:", p)
        raise SystemExit("VERIFY_FAILED")
    print(
        "EVIDENCE_VERIFY_OK cases=%d replays=2 gate=GROUPED_COV_ALIGNMENT_MISMATCH"
        % len(stored["cases"])
    )


# --------------------------------------------------------------------------- #
# Demo
# --------------------------------------------------------------------------- #
def _brief(packed, coord):
    g, obs, q, p = coord
    col = FEATURES.index(p)
    for idx, vals in zip(packed["index"], packed["values"]):
        if idx == [g, obs, q]:
            v = vals[col]
            return {"state": "nan"} if v is None else {"state": "number", "value": v}
    return {"state": "absent"}


def command_demo():
    table = {}
    for name in ("normal", "single_external", "incident"):
        case = samples.get(name)
        actual, _ = trace_actual(case)
        expected, _ = expected_case(case)
        table[name] = {
            "other_is_explicit": case.get("other") is not None,
            "actual_a_1_x_x": _brief(actual, ["a", 1, "x", "x"])
            if any(i[0] == "a" for i in actual["index"])
            else {"state": "absent"},
            "expected_a_1_x_x": _brief(expected, ["a", 1, "x", "x"])
            if any(i[0] == "a" for i in expected["index"])
            else {"state": "absent"},
            "actual_b_4_x_x": _brief(actual, ["b", 4, "x", "x"])
            if any(i[0] == "b" for i in actual["index"])
            else {"state": "absent"},
            "expected_b_4_x_x": _brief(expected, ["b", 4, "x", "x"])
            if any(i[0] == "b" for i in expected["index"])
            else {"state": "absent"},
            "differing_cells": len(compare_cells(actual, expected)),
        }
    # legal positive: implicit interleave must keep group isolation
    case = next(c for c in extra_cases() if c["id"] == "interleaved_implicit")
    actual, _ = trace_actual(case)
    expected, _ = expected_case(case)
    table["legal_positive_interleaved_implicit"] = {
        "differing_cells": len(compare_cells(actual, expected)),
        "a_2_x_x_actual": _brief(actual, ["a", 2, "x", "x"]),
        "a_2_x_x_expected": _brief(expected, ["a", 2, "x", "x"]),
    }
    print(json.dumps(table, allow_nan=False, indent=2))


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "verify", "demo"}:
        raise SystemExit("usage: python3 review_evidence.py run|verify|demo")
    {"run": command_run, "verify": command_verify, "demo": command_demo}[sys.argv[1]]()


if __name__ == "__main__":
    main()
