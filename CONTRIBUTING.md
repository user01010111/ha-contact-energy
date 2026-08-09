# Contributing

Bug reports, focused fixes, tests, and documentation improvements are welcome. Please keep changes narrow enough to review and avoid including real Contact Energy account data in issues, fixtures, commits, or pull requests.

## Before opening an issue

Search the [existing issues](https://github.com/user01010111/ha-contact-energy/issues) and use the supplied form. Redact email addresses, physical addresses, ICPs, account and contract identifiers, credentials, sessions, cookies, headers, API keys, and complete usage URLs.

Suspected security vulnerabilities must follow [SECURITY.md](.github/SECURITY.md), not the public issue tracker.

## Development setup

The devcontainer installs Python 3.14 and the pinned development requirements. From an equivalent local Python 3.14 environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Run the same checks used in CI:

```bash
scripts/lint
.venv/bin/python -m pytest
.venv/bin/hass --script check_config -c config
```

`scripts/lint` only checks files. To apply Ruff's safe fixes and formatter explicitly, run:

```bash
.venv/bin/python -m ruff check . --fix
.venv/bin/python -m ruff format .
```

## Pull requests

Create a branch from `main`, add regression coverage for behavior changes, update relevant documentation, and describe the user-visible effect and validation performed. Do not commit generated caches, Home Assistant configuration state, credentials, recorder databases, or captured API bodies.

All contributions are licensed under the repository's [MIT License](LICENSE).
