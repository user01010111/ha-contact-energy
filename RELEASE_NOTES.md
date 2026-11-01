# Release notes

## v2.2.0

- Show the latest complete electricity day and its total, free, paid and reported cost values together. Delayed readings keep their consumption date; they are not labelled as yesterday.
- Add month-to-date and rolling 7/30-day summaries with equal-length previous-period comparisons. Missing hours, prices or free-usage fields stay unknown.
- Show free and paid usage for the previous calendar month. Free usage comes only from Contact's explicit uncharged values, not off-peak labels or inferred tariffs.
- Add optional dated history and hourly/weekday profiles, with dashboard examples. History is bounded to 93 collected days; the optional charts need ApexCharts Card. These do not create additional Energy dashboard inputs.
- Record when a complete day was first observed. This includes polling and backfill delays, so it is not Contact's exact publication time.
- Show the selected contract's plan name and current electricity bill charges, including GST and billing dates. Plan names do not establish tariff rates or free-power schedules; bill snapshots are not cumulative cost statistics.
- Fetch recent electricity days first without increasing the history window or request rate. Optional electricity bill and plan lookups run after the initial setup refresh.
- Retain the last known plan and electricity bill during temporary lookup failures, retrying on a later scheduled refresh without bypassing rate-limit delays.
- Add reported gas usage for the previous calendar month and up to 12 dated months, matching Contact's usage chart. Keep these separate from bill periods, charges and meter-reading quality; missing months stay unknown.

Existing entries, entity IDs and accumulated energy/cost statistics are preserved. Yesterday usage/cost sensors start disabled on new installations; already-enabled sensors stay enabled. The new Yesterday free electricity sensor starts disabled on both new installations and upgrades. Enable the history and profile sensors if using the examples. Complete months and comparisons may take time to fill as data is collected.

Gas billing remains current-bill-only. Gas recorder statistics, historical bill comparisons, credits and revised bills are not supported. Back up Home Assistant before updating, then restart.

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
