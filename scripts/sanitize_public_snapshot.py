#!/usr/bin/env python3
"""Remove explicitly marked private sections from a public snapshot."""

from __future__ import annotations

import sys
from pathlib import Path


START = "PUBLIC-SNAPSHOT-PRIVATE-START"
END = "PUBLIC-SNAPSHOT-PRIVATE-END"


def sanitize(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    output = []
    inside_private_section = False

    for line in lines:
        if START in line:
            if inside_private_section:
                raise ValueError(f"Nested private section in {path}")
            inside_private_section = True
            continue
        if END in line:
            if not inside_private_section:
                raise ValueError(f"Unmatched private section end in {path}")
            inside_private_section = False
            continue
        if not inside_private_section:
            output.append(line)

    if inside_private_section:
        raise ValueError(f"Unclosed private section in {path}")

    path.write_text("".join(output), encoding="utf-8")


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: sanitize_public_snapshot.py <file> [<file> ...]")
    for raw_path in sys.argv[1:]:
        sanitize(Path(raw_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
