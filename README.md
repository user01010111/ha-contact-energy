# Contact Energy integration for Home Assistant

An unofficial, independently maintained Home Assistant custom integration for Contact Energy New Zealand, supporting electricity usage and piped-gas billing.

This project is not affiliated with, endorsed by, or supported by Contact Energy. It uses customer-facing endpoints that Contact Energy may change without notice.

## What it provides

- Electricity usage, account sensors, freshness indicators and daily/monthly summaries.
- Piped-gas bill snapshots, meter-reading dates and gas's share of current invoice charges.
- Contract-scoped external statistics for electricity consumption and cost.
- A bounded 3–31 day correction window so delayed or corrected readings can be imported safely.
- Reauthentication through Home Assistant when saved credentials are rejected.

Historical gas imports, tariff inference, payment history, diagnostics downloads and runtime app-bundle scraping are not supported.

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

### Usage freshness and period summaries

Three additional entities help distinguish delayed readings from a connection problem:

- **Latest electricity usage** is the start of the most recent hourly reading received, retained across restarts.
- **Last successful usage fetch** records when every day in the configured history window was fetched successfully. An empty response counts as a successful request, not fresh usage. This timestamp resets on restart or reload.
- **Electricity data status** is `no_data`, `current`, or `delayed`. Data is delayed when the latest received hour ended more than 48 hours ago. Status is checked on refresh; it does not guarantee there are no gaps. An account connection failure makes the entities unavailable instead.

**Yesterday electricity usage/cost** and **Last month electricity usage/cost** cover the previous calendar day and month in Contact's reporting timezone, including daylight-saving changes. A total stays unknown until every hour is present; cost also requires a price for every hour. Attributes show period dates (the end date is exclusive), received/expected hours, priced hours, and a daily average. `change_percent` compares that average with the preceding day or month, and stays unknown if either period is incomplete or the baseline is zero.

These summaries retain up to 93 days of locally collected hourly data without increasing API requests. Missing older history cannot be reconstructed from lifetime totals, so monthly values and comparisons may take one or two full months to become available after an upgrade. The configured 3–31 day refetch window still controls which delayed readings and corrections can be collected.

Period costs use Contact's hourly usage-cost field and reported currency, not an invoice total. They do not establish daily charges, GST treatment, discounts, or whether readings were estimated. A previously known hourly cost is retained if a later response omits it.

### Piped gas billing

Add the integration again and select the gas contract to create six sensors:

- **Last billed gas usage**: kWh from the latest bill, with its start date, inclusive end date and day count.
- **Last billed gas charges**: that contract's charges in NZD including GST. Attributes separate GST, usage charges excluding GST, and fixed charges excluding GST (including gas industry fees).
- **Gas reading quality**: actual, estimated, or unknown, as reported for the bill's current meter reading. This does not imply that earlier readings were actual or that past estimates have been corrected.
- **Previous meter reading date** and **Next meter reading date**: dates reported for that gas contract, not inferred from its billing period. The next date includes `next_reading_is_estimate` when Contact supplies it. Missing or conflicting dates across meters remain unknown.
- **Gas share of current invoice charges**: that gas contract's GST-inclusive charges as a percentage of all current charges on the same invoice, including other services. Attributes show both amounts. Missing totals, negative service totals or a breakdown that does not reconcile leave the percentage unknown without hiding the valid gas bill.

These are bill snapshots, not live readings or cumulative meters. They update every eight hours and are not suitable as gas inputs to the Energy dashboard. Existing electricity entries stay unchanged; gas needs its own entry. The history-days setting applies only to electricity.

The integration checks the last 120 days for the latest invoice, then validates the returned invoice, contract, ICP, billing dates, units and charge totals. No invoice gives unknown bill values; an inconsistent or unsupported bill makes the bill sensors unavailable, with another check at the normal eight-hour interval. Account-reported meter dates remain independent of bill validation. Unrecognised reading types remain unknown. Gas credits, historical/revised bill imports, and bottled LPG are not supported yet.

Gas and electricity billing periods can differ. The percentage describes the split of one invoice, not a same-period consumption comparison. It excludes balances carried forward and payments; bill totals are not calendar-month usage. Payment history and invoice identifiers are not saved as sensor attributes.

## Requirements

- Home Assistant 2026.8.1 or newer.
- A Contact Energy account with MyAccount access and an electricity or piped-gas contract.

## Installation

### HACS custom repository

1. Install [HACS](https://hacs.xyz/docs/use/).
2. Open the repository through the button below, or add `user01010111/ha-contact-energy` as an Integration custom repository.
3. Install Contact Energy and restart Home Assistant.

[![Open your Home Assistant instance and add this repository to HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=user01010111&repository=ha-contact-energy&category=integration)

### Manual installation

Copy `custom_components/contact_energy` into the `custom_components` directory under your Home Assistant configuration directory, then restart Home Assistant.

## Configuration

Open Settings → Devices & services → Add integration, select Contact Energy, and enter the credentials for the Contact Energy customer account. If the account has multiple supported contracts, the selector shows the fuel type, supply address and ICP for each available contract.

One integration entry monitors one contract. Repeat Add integration for every contract or customer account that should appear in Home Assistant. Contract identity uses the account, contract, and ICP together, so an identical contract number on another account does not cause a false duplicate.

The integration-list title shows only the final six characters of the ICP because Home Assistant can include that title in routine logs. The device page and Energy statistic names show the full ICP so properties remain distinguishable. Internal entry, entity, device, and statistic identifiers remain opaque. Treat screenshots containing a full ICP or supply address as private account information.

The history setting accepts 3–31 days. Contact normally reports usage after a delay, so the integration revisits a bounded recent window every eight hours. Missing or temporarily unavailable days do not stop later days from importing.

Home Assistant stores the submitted credentials in the integration configuration. The integration does not expose credentials through logs, diagnostics, entities, or statistics.

### What to expect after setup

Account and billing entities are populated during the first successful refresh. Hourly electricity data can be delayed by Contact Energy, so the selected history range may initially contain fewer complete days than requested. The accumulated usage and cost statistics remain stable across overlapping refreshes and Home Assistant restarts.

Account balance and billing values belong to the customer account. If several electricity contracts share one account, those account-level values can appear on each contract device while usage and cost statistics remain separate.

## Upgrading from v2.0.0

Back up Home Assistant, update the integration and restart. Existing electricity entries, entity IDs and accumulated statistics are preserved. Add the integration again to select a gas contract; gas is not added automatically.

Daily and monthly summaries stay unknown until their full periods have been collected. To roll back, restore the pre-upgrade backup, particularly if you have added a gas entry that v2.0.0 cannot handle.

## Upgrading from v1.0.0

Back up Home Assistant before upgrading. V2 preserves the existing integration entry and entity IDs, but creates separate consumption and cost statistics for each electricity contract.

In the Energy dashboard, select the new statistics for the correct ICP. The old series remain available but no longer receive updates. Remove the old free-electricity selection; v2 does not provide it.

See the [migration guide](MIGRATION.md) for upgrade and rollback instructions, [usage that appears frozen](MIGRATION.md#usage-appears-frozen-after-upgrading), and [duplicate entries blocking setup](MIGRATION.md#duplicate-entries-block-setup).

Warnings about billing sensors losing their state class are explained [here](MIGRATION.md#billing-sensors-no-longer-have-a-state-class). Deleting their old statistics is optional.

## Statistics behavior

The integration uses the API fields `value` and `dollarValue` for consumption and cost. Lifetime sums are persisted locally while a bounded recent window remains replaceable, so overlapping refreshes and Home Assistant restarts do not inflate totals.

Costs use the reported currency, normally NZD. A missing cost value does not erase a previously imported cost, and data with a conflicting currency is rejected from the cost series.

## Troubleshooting and limitations

- This integration uses undocumented customer-facing endpoints. Contact Energy can change or withdraw them without notice.
- Usage is delayed source data, not a real-time power reading.
- Gas reports the latest validated bill, not live or historical consumption. It cannot feed the Energy dashboard's gas total.
- If setup reports that Contact Energy is unavailable, confirm that MyAccount is working before retrying. Avoid repeated login attempts when the Contact service is degraded.
- If saved credentials are rejected later, use the Reconfigure action shown by Home Assistant and enter the current credentials once.

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
