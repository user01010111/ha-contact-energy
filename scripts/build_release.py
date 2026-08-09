#!/usr/bin/env python3
"""Build and validate the deterministic HACS release archive."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "contact_energy"
FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
ARCHIVE_SOURCES = {
    "LICENSE": ROOT / "LICENSE",
    "__init__.py": INTEGRATION / "__init__.py",
    "api.py": INTEGRATION / "api.py",
    "brand/icon.png": INTEGRATION / "brand" / "icon.png",
    "config_flow.py": INTEGRATION / "config_flow.py",
    "const.py": INTEGRATION / "const.py",
    "coordinator.py": INTEGRATION / "coordinator.py",
    "manifest.json": INTEGRATION / "manifest.json",
    "models.py": INTEGRATION / "models.py",
    "sensor.py": INTEGRATION / "sensor.py",
    "statistics.py": INTEGRATION / "statistics.py",
    "strings.json": INTEGRATION / "strings.json",
    "translations/en.json": INTEGRATION / "translations" / "en.json",
}


def _manifest() -> dict[str, object]:
    """Load and minimally validate release metadata."""
    manifest = json.loads(ARCHIVE_SOURCES["manifest.json"].read_text())
    if manifest.get("domain") != "contact_energy":
        raise ValueError("Manifest domain must be contact_energy")
    version = manifest.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError("Manifest version must be a non-empty string")
    return manifest


def build_archive(output: Path, expected_version: str) -> Path:
    """Build the exact release archive and return its checksum path."""
    manifest = _manifest()
    if manifest["version"] != expected_version:
        raise ValueError(
            "Manifest version does not match the requested release: "
            f"{manifest['version']} != {expected_version}"
        )

    missing = [name for name, path in ARCHIVE_SOURCES.items() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing release inputs: {', '.join(missing)}")

    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w") as archive:
        for name, source in sorted(ARCHIVE_SOURCES.items()):
            info = ZipInfo(name, FIXED_TIMESTAMP)
            info.compress_type = ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(
                info,
                source.read_bytes(),
                compress_type=ZIP_DEFLATED,
                compresslevel=9,
            )

    validate_archive(output, expected_version)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    checksum = output.with_suffix(f"{output.suffix}.sha256")
    checksum.write_text(f"{digest}  {output.name}\n")
    return checksum


def validate_archive(archive_path: Path, expected_version: str) -> None:
    """Validate archive integrity, layout, metadata, and attribution."""
    with ZipFile(archive_path) as archive:
        names = archive.namelist()
        expected_names = sorted(ARCHIVE_SOURCES)
        if names != expected_names:
            raise ValueError(
                f"Archive members differ: expected {expected_names}, found {names}"
            )
        if corrupt := archive.testzip():
            raise ValueError(f"Archive member failed CRC validation: {corrupt}")
        if archive.read("LICENSE") != (ROOT / "LICENSE").read_bytes():
            raise ValueError("Archive license does not match the repository license")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("domain") != "contact_energy":
            raise ValueError("Archive manifest domain must be contact_energy")
        if manifest.get("version") != expected_version:
            raise ValueError("Archive manifest version does not match the release")
        for info in archive.infolist():
            if info.date_time != FIXED_TIMESTAMP:
                raise ValueError(
                    f"Archive timestamp is not normalized: {info.filename}"
                )


def main() -> None:
    """Parse command-line arguments and build the archive."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    checksum = build_archive(args.output, args.expected_version)
    print(args.output)
    print(checksum)


if __name__ == "__main__":
    main()
