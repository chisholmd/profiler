#!/usr/bin/env python3
"""Direct entry point script for profiling a folder of HL7 CDA / C-CDA XML files."""

from pathlib import Path
import sys

# Ensure 'src' is on sys.path when invoked directly without pip install
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cda_profiler.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
