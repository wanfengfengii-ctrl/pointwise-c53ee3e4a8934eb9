from copy import deepcopy

CASES = {
    "normal": {
        "id": "normal", "window": 2, "min_periods": 2,
        "rows": [
            {"obs": 0, "g": "a", "x": 1.0, "y": 2.0},
            {"obs": 1, "g": "a", "x": 3.0, "y": 6.0},
            {"obs": 2, "g": "b", "x": 10.0, "y": 5.0},
            {"obs": 3, "g": "b", "x": 20.0, "y": 15.0},
            {"obs": 4, "g": "b", "x": 30.0, "y": 25.0}
        ],
        "other": None
    },
    "incident": {
        "id": "incident", "window": 2, "min_periods": 2,
        "rows": [
            {"obs": 0, "g": "a", "x": 1.0, "y": 2.0},
            {"obs": 1, "g": "b", "x": 10.0, "y": 5.0},
            {"obs": 2, "g": "a", "x": 3.0, "y": 6.0},
            {"obs": 3, "g": "b", "x": 20.0, "y": 15.0},
            {"obs": 4, "g": "b", "x": 30.0, "y": 25.0}
        ],
        "other": [
            {"obs": 0, "x": 1.0, "y": 2.0},
            {"obs": 1, "x": 10.0, "y": 5.0},
            {"obs": 2, "x": 3.0, "y": 6.0},
            {"obs": 3, "x": 20.0, "y": 15.0},
            {"obs": 4, "x": 30.0, "y": 25.0}
        ]
    },
    "single_external": {
        "id": "single_external", "window": 2, "min_periods": 2,
        "rows": [
            {"obs": 0, "g": "a", "x": 1.0, "y": 2.0},
            {"obs": 1, "g": "a", "x": 3.0, "y": 6.0},
            {"obs": 2, "g": "a", "x": 5.0, "y": 10.0}
        ],
        "other": [
            {"obs": 2, "x": 5.0, "y": 10.0},
            {"obs": 0, "x": 1.0, "y": 2.0},
            {"obs": 1, "x": 3.0, "y": 6.0}
        ]
    }
}

def get(name):
    return deepcopy(CASES[name])
