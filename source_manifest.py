from _pointwise_deps import enable
enable()
"""Identify and mirror the unmodified Python sources shipped by pandas v2.2.2."""
import hashlib
from pathlib import Path
import shutil
import sys
import pandas as pd
import numpy as np

COMMIT = "d9cdd2ee5a58015ef6f4d15c7226110c9aab8140"
HASHES = {
    "core/window/rolling.py": "8e3e4d982576f0d82c59731a055a95fa3f3e24fc19382bb785e2e2f4001b4cc5",
    "core/window/common.py": "2d905d763132ec2fe76fef609ac936c10afe16c17558132c18a77ca6d98c76e8",
    "core/indexers/objects.py": "3d1d3adc3565bbcffeb62ec6b0b45bd1eee8e28033db1a4c8a5d2731e7b5f33d",
    "core/groupby/groupby.py": "ca336c8099aeb6b5b1663fbaa2f782b640c580983a4b8adc5c7b09f2bb235535",
    "tests/window/test_groupby.py": "297c40e60112c534891239a09c8cf6f4acf58c9473d4f610b03eb8192b280f3d",
}

def verify():
    assert pd.__version__ == "2.2.2", pd.__version__
    assert pd.__git_version__ == COMMIT, pd.__git_version__
    assert np.__version__ == "1.26.4", np.__version__
    root = Path(pd.__file__).parent
    for name, digest in HASHES.items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest, name
    return {"repo": "pandas-dev/pandas", "tag": "v2.2.2", "commit": COMMIT,
            "pandas": pd.__version__, "numpy": np.__version__, "sha256": dict(HASHES)}

def mirror():
    verify()
    root = Path(pd.__file__).parent
    dest = Path(__file__).resolve().parent / "upstream" / "pandas"
    for src in root.rglob("*.py"):
        if "__pycache__" in src.parts:
            continue
        target = dest / src.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
    for name, digest in HASHES.items():
        assert hashlib.sha256((dest / name).read_bytes()).hexdigest() == digest, name
    print("SOURCE_MIRROR_READY", dest)

if __name__ == "__main__":
    if sys.argv[1:] == ["mirror"]:
        mirror()
    elif sys.argv[1:] == ["check"]:
        print(verify())
    else:
        raise SystemExit("usage: python3 source_manifest.py check|mirror")
