"""Repository policy tests for metadata and GitHub workflows."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from scripts.check_credentials import scan

ROOT = Path(__file__).parents[1]
ACTION_SHA = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def test_json_and_yaml_files_parse() -> None:
    for path in ROOT.rglob("*.json"):
        if ".venv" not in path.parts:
            json.loads(path.read_text())
    for pattern in ("*.yml", "*.yaml"):
        for path in ROOT.rglob(pattern):
            if ".venv" not in path.parts:
                yaml.safe_load(path.read_text())


def test_every_action_is_pinned_and_workflows_are_least_privilege() -> None:
    workflows = ROOT / ".github" / "workflows"
    for path in workflows.glob("*.yml"):
        workflow = yaml.safe_load(path.read_text())
        assert workflow.get("permissions") == {"contents": "read"}
        for job_name, job in workflow["jobs"].items():
            for step in job.get("steps", []):
                if action := step.get("uses"):
                    assert ACTION_SHA.fullmatch(action), (path.name, action)
            if job_name != "publish":
                assert job.get("permissions") in (None, {"contents": "read"})

    release = yaml.safe_load((workflows / "release.yml").read_text())
    assert release["jobs"]["publish"]["permissions"] == {"contents": "write"}
    assert "contents" not in release["jobs"]["build"].get("permissions", {})


def test_home_assistant_configuration_check_has_runtime_dependencies() -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "lint.yml").read_text())
    pytest_steps = workflow["jobs"]["pytest"]["steps"]
    check_config = next(
        step
        for step in pytest_steps
        if step["name"] == "Check Home Assistant configuration"
    )
    assert (
        check_config["run"]
        == "python3 -m homeassistant --script check_config -c config"
    )
    requirements = (ROOT / "requirements.txt").read_text().splitlines()
    assert "colorlog==6.10.1" in requirements


def test_bug_template_requires_specific_redaction() -> None:
    template = (ROOT / ".github" / "ISSUE_TEMPLATE" / "bug.yml").read_text()
    for term in (
        "email",
        "physical address",
        "ICP",
        "account ID",
        "contract ID",
        "password",
        "session token",
        "API key",
        "cookie",
        "header",
        "usage URL",
    ):
        assert term in template
    assert "smallest excerpt" in template
    assert "ha-managemyhealth" not in template


def test_metadata_and_hacs_release_contract() -> None:
    manifest = json.loads(
        (ROOT / "custom_components" / "contact_energy" / "manifest.json").read_text()
    )
    assert manifest == {
        "domain": "contact_energy",
        "name": "Contact Energy",
        "codeowners": ["@user01010111"],
        "config_flow": True,
        "dependencies": ["recorder"],
        "documentation": "https://github.com/user01010111/ha-contact-energy",
        "integration_type": "service",
        "iot_class": "cloud_polling",
        "issue_tracker": ("https://github.com/user01010111/ha-contact-energy/issues"),
        "requirements": [],
        "version": "2.0.0",
    }

    hacs = json.loads((ROOT / "hacs.json").read_text())
    assert hacs["filename"] == "contact_energy.zip"
    assert hacs["hide_default_branch"] is True
    assert hacs["homeassistant"] == "2026.8.1"
    assert hacs["zip_release"] is True


def test_english_translation_matches_source_strings() -> None:
    strings = json.loads(
        (ROOT / "custom_components" / "contact_energy" / "strings.json").read_text()
    )
    translation = json.loads(
        (
            ROOT / "custom_components" / "contact_energy" / "translations" / "en.json"
        ).read_text()
    )
    assert translation == strings


def test_links_and_issue_labels_are_current() -> None:
    bug = yaml.safe_load((ROOT / ".github" / "ISSUE_TEMPLATE" / "bug.yml").read_text())
    feature = yaml.safe_load(
        (ROOT / ".github" / "ISSUE_TEMPLATE" / "feature_request.yml").read_text()
    )
    assert bug["labels"] == ["bug"]
    assert feature["labels"] == ["enhancement"]

    for path in (
        ROOT / "README.md",
        ROOT / "CONTRIBUTING.md",
        ROOT / ".github" / "ISSUE_TEMPLATE" / "bug.yml",
        ROOT / ".github" / "ISSUE_TEMPLATE" / "feature_request.yml",
    ):
        text = path.read_text()
        if path.name != "README.md":
            assert "notf0und" not in text
        assert "user01010111/ha-contact-energy" in text


def test_release_workflow_validates_without_rewriting_source() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text()
    assert "tags:" in workflow
    assert '"v*"' in workflow
    assert 'test "${RELEASE_TAG}" = "v${manifest_version}"' in workflow
    assert "scripts/build_release.py" in workflow
    assert "contact_energy.zip.sha256" in workflow
    assert "Set manifest version" not in workflow
    assert 'manifest["version"] =' not in workflow


def test_lint_script_checks_without_rewriting() -> None:
    script = (ROOT / "scripts" / "lint").read_text()
    assert "ruff check ." in script
    assert "ruff format --check ." in script
    assert "--fix" not in script


def test_repository_contains_no_unexpected_credential_material() -> None:
    assert scan() == []


def test_manifest_does_not_redeclare_aiohttp() -> None:
    manifest = json.loads(
        (ROOT / "custom_components" / "contact_energy" / "manifest.json").read_text()
    )
    assert manifest["requirements"] == []
