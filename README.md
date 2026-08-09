# Contact Energy integration for Home Assistant

An unofficial, independently maintained Home Assistant custom integration for electricity data from Contact Energy New Zealand.

This project is not affiliated with, endorsed by, or supported by Contact Energy. It uses customer-facing endpoints that Contact Energy may change without notice.

## What it provides

- Current electricity usage and account sensors.
- Contract-scoped external statistics for electricity consumption and cost.
- A bounded 3–31 day correction window so delayed or corrected readings can be imported safely.
- Reauthentication through Home Assistant when saved credentials are rejected.

Gas, tariff inference, payment history, diagnostics downloads, and runtime app-bundle scraping are not part of v2.0.0.

## Requirements

- Home Assistant 2026.8.1 or newer.
- A Contact Energy electricity account with MyAccount access.

## Installation

### HACS custom repository

1. Install [HACS](https://hacs.xyz/docs/use/).
2. Open the repository through the button below, or add `user01010111/ha-contact-energy` as an Integration custom repository.
3. Install Contact Energy and restart Home Assistant.

[![Open your Home Assistant instance and add this repository to HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=user01010111&repository=ha-contact-energy&category=integration)

### Manual installation

Copy `custom_components/contact_energy` into the `custom_components` directory under your Home Assistant configuration directory, then restart Home Assistant.

## Configuration

Open Settings → Devices & services → Add integration, select Contact Energy, and enter the credentials for the Contact Energy customer account. If the account has multiple electricity contracts, select the contract to monitor.

The history setting accepts 3–31 days. Contact normally reports usage after a delay, so the integration revisits a bounded recent window every eight hours. Missing or temporarily unavailable days do not stop later days from importing.

Home Assistant stores the submitted credentials in the integration configuration. The integration does not expose credentials through logs, diagnostics, entities, or statistics.

## Upgrading from v1.0.0

The v2 migration reuses the existing integration entry and preserves entity IDs. It replaces raw account, contract, and ICP registry identifiers with opaque contract-scoped identifiers.

v1 created global, moving-window statistics that could collide across contracts. v2 does not rewrite those recorder rows because their contract ownership and lifetime meaning cannot be established safely. It leaves them untouched and starts new contract-scoped energy and cost series. After upgrading, open the Energy dashboard configuration and select the new Contact Energy consumption and cost statistics. The old free-electricity statistic is discontinued because `offpeakValue` is not authoritative evidence of free usage.

See [MIGRATION.md](MIGRATION.md) for the upgrade checklist and rollback notes.

## Statistics behavior

The integration uses the API fields `value` and `dollarValue` for consumption and cost. Lifetime sums are persisted locally while a bounded recent window remains replaceable, so overlapping refreshes and Home Assistant restarts do not inflate totals.

Costs are recorded in NZD. A missing cost value does not erase a previously imported cost, and data with a conflicting currency is rejected from the cost series.

## Authentication and API identifier

An expired session is refreshed once and the failed request is retried once. If the refreshed session is also rejected, Home Assistant starts a reauthentication flow for the existing integration entry. Temporary network, rate-limit, and service failures use Home Assistant's normal coordinator retry behavior.

The integration treats the shipped `x-api-key` as a public application identifier because Contact distributes it in the MyAccount web app. Contact has not formally documented its classification. Customer data still requires separate account and session authentication, and a changed identifier must be updated in a release.

## Privacy when reporting bugs

Submit only the smallest relevant log excerpt. Remove email addresses, physical addresses, ICPs, account IDs, contract IDs, passwords, session tokens, API keys, cookies, headers, and complete usage URLs before posting anything publicly.

Use [GitHub Issues](https://github.com/user01010111/ha-contact-energy/issues) for ordinary support. Use the private route described in [SECURITY.md](.github/SECURITY.md) for suspected vulnerabilities.

## Attribution and license

This maintained fork is based on [`codyc1515/ha-contact-energy`](https://github.com/codyc1515/ha-contact-energy) and retains its Git history and original MIT copyright notice. Later maintenance was published through [`notf0und/ha-contact-energy`](https://github.com/notf0und/ha-contact-energy).

The project is available under the [MIT License](LICENSE). Historical attribution does not imply endorsement of this maintained fork by prior contributors or by Contact Energy.
