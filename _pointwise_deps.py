"""Load the locally installed, source-pinned project dependencies."""
import sys
from pathlib import Path

def enable():
    directory = Path(__file__).resolve().parent / "vendor/site"
    if not directory.is_dir():
        raise RuntimeError("Run the offline setup commands from .pointwise-runtime.json first")
    sys.path.insert(0, str(directory))
