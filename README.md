# Contact Energy integration for Home Assistant

An unofficial, independently maintained Home Assistant custom integration for electricity data from Contact Energy New Zealand.

This project is not affiliated with, endorsed by, or supported by Contact Energy. It uses customer-facing endpoints that Contact Energy may change without notice.

## What it provides

- Electricity usage and account sensors.
- Contract-scoped external statistics for electricity consumption and cost.
- A bounded 3–31 day correction window so delayed or corrected readings can be imported safely.
- Reauthentication through Home Assistant when saved credentials are rejected.

Gas, tariff inference, payment history, diagnostics downloads, and runtime app-bundle scraping are not part of v2.0.0.

### Entities

Each configured electricity contract creates these entities:

| Entity | Value |
| --- | --- |
| Electricity usage | Accumulated electricity consumption in kWh |
| Account balance | Current account balance in NZD |
| Next bill amount | Expected next bill amount in NZD |
| Next bill date | Expected date of the next bill |
| Payment due | Amount currently due in NZD |
| Payment due date | Due date for the current payment |
| Previous meter reading date | Date of the previous meter reading |
| Next meter reading date | Expected date of the next meter reading |

The integration also writes contract-scoped consumption and cost statistics for Home Assistant's Energy dashboard. It does not attach payment history or customer identifiers to entity attributes.

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

Open Settings → Devices & services → Add integration, select Contact Energy, and enter the credentials for the Contact Energy customer account. If the account has multiple electricity contracts, the selector shows the supply address and ICP for each available contract.

One integration entry monitors one electricity contract. Repeat Add integration for every contract or customer account that should appear in Home Assistant. Contract identity uses the account, contract, and ICP together, so an identical contract number on another account does not cause a false duplicate.

The integration-list title shows only the final six characters of the ICP because Home Assistant can include that title in routine logs. The device page and Energy statistic names show the full ICP so properties remain distinguishable. Internal entry, entity, device, and statistic identifiers remain opaque. Treat screenshots containing a full ICP or supply address as private account information.

The history setting accepts 3–31 days. Contact normally reports usage after a delay, so the integration revisits a bounded recent window every eight hours. Missing or temporarily unavailable days do not stop later days from importing.

Home Assistant stores the submitted credentials in the integration configuration. The integration does not expose credentials through logs, diagnostics, entities, or statistics.

### What to expect after setup

Account and billing entities are populated during the first successful refresh. Hourly electricity data can be delayed by Contact Energy, so the selected history range may initially contain fewer complete days than requested. The accumulated usage and cost statistics remain stable across overlapping refreshes and Home Assistant restarts.

Account balance and billing values belong to the customer account. If several electricity contracts share one account, those account-level values can appear on each contract device while usage and cost statistics remain separate.

## Upgrading from v1.0.0

The v2 migration reuses the existing integration entry and preserves entity IDs. It replaces raw account, contract, and ICP registry identifiers with opaque contract-scoped identifiers while retaining the ICP in authenticated device and statistic display names.

v1 created global, moving-window statistics that could collide across contracts. v2 does not rewrite those recorder rows because their contract ownership and lifetime meaning cannot be established safely. It leaves them untouched and starts new contract-scoped energy and cost series. After upgrading, open the Energy dashboard configuration and select the new Contact Energy consumption and cost statistics. The old free-electricity statistic is discontinued because `offpeakValue` is not authoritative evidence of free usage.

See [MIGRATION.md](MIGRATION.md) for the upgrade checklist and rollback notes.

## Statistics behavior

The integration uses the API fields `value` and `dollarValue` for consumption and cost. Lifetime sums are persisted locally while a bounded recent window remains replaceable, so overlapping refreshes and Home Assistant restarts do not inflate totals.

Costs are recorded in NZD. A missing cost value does not erase a previously imported cost, and data with a conflicting currency is rejected from the cost series.

## Troubleshooting and limitations

- This integration uses undocumented customer-facing endpoints. Contact Energy can change or withdraw them without notice.
- Usage is delayed source data, not a real-time power reading.
- Only electricity contracts are supported in v2.0.0.
- If setup reports that Contact Energy is unavailable, confirm that MyAccount is working before retrying. Avoid repeated login attempts when the Contact service is degraded.
- If saved credentials are rejected later, use the Reconfigure action shown by Home Assistant and enter the current credentials once.
- After upgrading from v1, select the new contract-scoped consumption and cost statistics in the Energy dashboard. The legacy series are deliberately left untouched.

## Authentication and API identifier

An expired session is refreshed once and the failed request is retried once. If the refreshed session is also rejected, Home Assistant starts a reauthentication flow for the existing integration entry. Temporary network, rate-limit, and service failures use Home Assistant's normal coordinator retry behavior.

The integration treats the shipped `x-api-key` as a public application identifier because Contact distributes it in the MyAccount web app. Contact has not formally documented its classification. Customer data still requires separate account and session authentication, and a changed identifier must be updated in a release.

## Privacy when reporting bugs

Submit only the smallest relevant log excerpt. Remove email addresses, physical addresses, ICPs, account IDs, contract IDs, passwords, session tokens, API keys, cookies, headers, and complete usage URLs before posting anything publicly.

Use [GitHub Issues](https://github.com/user01010111/ha-contact-energy/issues) for ordinary support. Use the private route described in [SECURITY.md](.github/SECURITY.md) for suspected vulnerabilities.

## Branding

The packaged meter-and-lightning icon is original neutral artwork for this unofficial integration. The repository does not include Contact Energy's official logo or imply that Contact Energy endorses the project.

## Attribution and license

This maintained fork is based on [`codyc1515/ha-contact-energy`](https://github.com/codyc1515/ha-contact-energy) and retains its Git history and original MIT copyright notice. Later maintenance was published through [`notf0und/ha-contact-energy`](https://github.com/notf0und/ha-contact-energy).

The project is available under the [MIT License](LICENSE). Historical attribution does not imply endorsement of this maintained fork by prior contributors or by Contact Energy.
