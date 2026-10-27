# Release notes

## v2.1.0

- Add separately selectable piped-gas contracts with last-billed usage, GST-inclusive charges and reading quality. Bill dates and charge components are retained; no cumulative gas statistics or historical bill imports are created.
- Show gas meter-reading dates and the gas share of reconciled current invoice charges, including GST. Missing or conflicting meter dates and unsupported invoice totals remain unknown.
- For electricity contracts with multiple meters, show unknown when their reading dates disagree instead of taking the first meter's date.
- Add electricity data freshness indicators and complete previous-day/month usage and cost summaries, with coverage and daily-average comparisons.
- Retain up to 93 days of observed hours for summaries without increasing API requests or changing existing lifetime totals. Older missing history remains unknown.
- Use Contact's reporting calendar for history requests and correction cutoffs, regardless of Home Assistant's timezone.
- Retry statistics storage on every refresh, including unchanged data. Ignore expired corrections and invalid negative usage costs without discarding valid energy or earlier prices.
- Handle oversized numeric responses and repair the local development launcher's import path.

Back up Home Assistant before updating, then restart. Existing electricity entries, entity IDs and accumulated statistics are preserved. Add the integration again to select a gas contract.

Gas support is limited to the current validated bill; credits and retrospective corrections remain unsupported. Gas snapshots are not cumulative Energy dashboard inputs. Electricity summaries stay unknown until every hour of their period has been collected; monthly comparisons may need two complete months.

## v2.0.0

Version 2.0.0 continues this unofficial integration under independent maintenance while preserving the `contact_energy` domain and existing Home Assistant configuration.

Version 2 refreshes an expired session once, supports Home Assistant reauthentication, validates API responses, keeps sensitive values out of integration logs, scopes identifiers to each contract, and preserves consumption and cost totals across restarts. Tests cover overlapping and corrected usage data.

Each configured contract is identified by its ICP on the Home Assistant device and statistic display names. The integration-list title uses only the final six ICP characters, and all internal registry and recorder identifiers remain opaque. Multiple customer accounts with the same contract number are handled independently.

Important upgrade note: v1's global recorder statistics are left untouched because their contract ownership and lifetime meaning are ambiguous. Version 2 starts new contract-scoped consumption and cost series. Existing users must reselect those series in the Energy dashboard. The legacy free-electricity statistic is discontinued.

See [MIGRATION.md](MIGRATION.md) before upgrading. This project is unofficial and is not affiliated with or endorsed by Contact Energy.
