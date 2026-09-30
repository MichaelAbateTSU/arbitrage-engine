"""Deployment uses exactly the same authenticated smoke assertions as local startup."""

import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).with_name("smoke_test.py")), run_name="__main__")
