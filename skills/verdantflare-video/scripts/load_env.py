#!/usr/bin/env python3
"""Load skill configuration with process > skill > project precedence."""
from __future__ import annotations
import os
from pathlib import Path


def _read(path: Path) -> dict[str, str]:
    values = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key and key not in values:
            values[key] = value.strip('"\'')
    return values


def load_env(skill_dir: str | Path | None = None) -> dict[str, str]:
    skill = Path(skill_dir or Path(__file__).resolve().parents[1]).resolve()
    project = Path.cwd().resolve()
    project_env = {}
    for directory in (project, *project.parents):
        candidate = directory / ".env"
        if candidate.is_file():
            project_env = _read(candidate)
            break
    merged = {**project_env, **_read(skill / ".env"), **os.environ}
    for key, value in merged.items():
        os.environ.setdefault(key, value)
    return merged


if __name__ == "__main__":
    load_env()
