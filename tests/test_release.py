"""Tests for deterministic release assembly."""

from __future__ import annotations

import hashlib
from pathlib import Path
from zipfile import ZipFile

import pytest

from scripts.build_release import (
    ARCHIVE_SOURCES,
    FIXED_TIMESTAMP,
    build_archive,
)


def test_release_archive_is_exact_and_reproducible(tmp_path: Path) -> None:
    first = tmp_path / "first" / "contact_energy.zip"
    second = tmp_path / "second" / "contact_energy.zip"

    first_checksum = build_archive(first, "2.0.0")
    second_checksum = build_archive(second, "2.0.0")

    assert first.read_bytes() == second.read_bytes()
    digest = hashlib.sha256(first.read_bytes()).hexdigest()
    assert first_checksum.read_text() == f"{digest}  contact_energy.zip\n"
    assert second_checksum.read_text() == f"{digest}  contact_energy.zip\n"

    with ZipFile(first) as archive:
        assert archive.namelist() == sorted(ARCHIVE_SOURCES)
        assert all(info.date_time == FIXED_TIMESTAMP for info in archive.infolist())
        assert archive.testzip() is None


def test_release_rejects_a_version_mismatch(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Manifest version"):
        build_archive(tmp_path / "contact_energy.zip", "2.0.1")
