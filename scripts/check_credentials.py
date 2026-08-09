#!/usr/bin/env python3
"""Reject likely committed credentials while allowing documented fixtures."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
TEXT_SUFFIXES = {
    "",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
SIGNATURES = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
)
ASSIGNMENT = re.compile(
    r"(?i)\b(?:password|passwd|token|secret|session|cookie|api[_-]?key|"
    r"authorization)\w*\b\s*[:=]\s*[\"']([^\"']{4,})[\"']"
)
SYNTHETIC_MARKERS = ("example", "not-real", "session-one", "session-two")


def _files_to_scan() -> list[Path]:
    """Return tracked and non-ignored files to scan."""
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    paths = []
    for raw_path in result.stdout.decode().split("\0"):
        if not raw_path:
            continue
        path = ROOT / raw_path
        if path.resolve() == SELF or not path.is_file():
            continue
        if path.suffix.lower() in TEXT_SUFFIXES:
            paths.append(path)
    return paths


def _allowed_assignment(path: Path, line: str, value: str) -> bool:
    """Return whether an assignment is an intentional public or test value."""
    relative = path.relative_to(ROOT)
    normalized = value.casefold()
    if relative == Path("custom_components/contact_energy/api.py"):
        return "PUBLIC_APPLICATION_API_KEY" in line
    if relative.parts and relative.parts[0] == "tests":
        return any(marker in normalized for marker in SYNTHETIC_MARKERS)
    return normalized in {
        "password",
        "session token",
        "api key",
        "cookie",
        "authorization",
    }


def scan() -> list[str]:
    """Return path-and-line findings without echoing suspected secret values."""
    findings: list[str] = []
    for path in _files_to_scan():
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if any(pattern.search(line) for pattern in SIGNATURES):
                findings.append(f"{path.relative_to(ROOT)}:{line_number}: signature")
            for match in ASSIGNMENT.finditer(line):
                if not _allowed_assignment(path, line, match.group(1)):
                    findings.append(
                        f"{path.relative_to(ROOT)}:{line_number}: assignment"
                    )
    return findings


def main() -> None:
    """Run the credential scan."""
    findings = scan()
    if findings:
        print("Potential credential material found:")
        print("\n".join(findings))
        raise SystemExit(1)
    print("Credential scan passed: only documented public and synthetic values found")


if __name__ == "__main__":
    main()
