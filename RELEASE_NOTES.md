# Contact Energy v2.0.0

Version 2.0.0 continues this unofficial integration under independent maintenance while preserving the `contact_energy` domain and existing Home Assistant configuration.

Version 2 refreshes an expired session once, supports Home Assistant reauthentication, validates API responses, keeps sensitive values out of logs, scopes identifiers to each contract, and preserves consumption and cost totals across restarts. Tests cover overlapping and corrected usage data.

Important upgrade note: v1's global recorder statistics are left untouched because their contract ownership and lifetime meaning are ambiguous. Version 2 starts new contract-scoped consumption and cost series. Existing users must reselect those series in the Energy dashboard. The legacy free-electricity statistic is discontinued.

See [MIGRATION.md](MIGRATION.md) before upgrading. This project is unofficial and is not affiliated with or endorsed by Contact Energy.
