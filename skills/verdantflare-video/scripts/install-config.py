#!/usr/bin/env python3
"""Install the VerdantFlare Video configuration on a supported desktop OS."""

from __future__ import annotations

import sys

from config import install


if __name__ == "__main__":
    if sys.version_info < (3, 10):
        print("python3 >= 3.10 is required.", file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(install())
