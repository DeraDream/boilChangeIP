#!/usr/bin/env python3
import re
import sys
from pathlib import Path


ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
HEADER_HASH_RE = re.compile(r"^#{20,}$")
TAIL_EQ_RE = re.compile(r"^={20,}$")
SECTION_FIVE_RE = re.compile(r"^(五、|5\.)")
REPORT_LINK_RE = re.compile(r"^(报告链接：|Report Link:)")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def filter_report_lines(lines: list[str]) -> list[str]:
    output: list[str] = []
    skipping_detail = False
    keep_tail_lines = 0

    for line in lines:
        plain = strip_ansi(line).replace("\r", "").rstrip("\n")

        if REPORT_LINK_RE.match(plain.strip()):
            continue

        if SECTION_FIVE_RE.match(plain.strip()):
            skipping_detail = True
            keep_tail_lines = 0
            continue

        if skipping_detail:
            if TAIL_EQ_RE.match(plain.strip()):
                output.append(line)
                skipping_detail = False
                keep_tail_lines = 1
            continue

        output.append(line)

        if keep_tail_lines > 0:
            keep_tail_lines -= 1

    filtered = []
    previous_blank = False
    for line in output:
        plain = strip_ansi(line).replace("\r", "").strip()
        is_blank = plain == ""
        if is_blank and previous_blank:
            continue
        filtered.append(line)
        previous_blank = is_blank
    return filtered


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: extract_quality_sections.py input.ansi output.ansi", file=sys.stderr)
        return 2

    source = Path(sys.argv[1])
    target = Path(sys.argv[2])
    lines = source.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    filtered = filter_report_lines(lines)
    target.write_text("".join(filtered), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
