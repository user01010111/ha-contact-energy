# Contact Energy integration for Home Assistant

An unofficial, independently maintained Home Assistant custom integration for Contact Energy New Zealand, supporting electricity usage and piped-gas billing.

This project is not affiliated with, endorsed by, or supported by Contact Energy. It uses customer-facing endpoints that Contact Energy may change without notice.

## What it provides

- Electricity usage, account sensors, freshness indicators and daily/monthly summaries.
- Piped-gas monthly usage reports, bill snapshots, meter-reading dates and gas's share of current invoice charges.
- Contract-scoped external statistics for electricity consumption and cost.
- A bounded 3–31 day correction window so delayed or corrected readings can be imported safely.
- Reauthentication through Home Assistant when saved credentials are rejected.

Gas recorder statistics, historical bill imports, tariff inference, payment history, diagnostics downloads and runtime app-bundle scraping are not supported.

### Entities

Each configured electricity contract includes these core entities. Usage summaries, billing and optional history sensors are described below.

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

The **Plan** sensor shows Contact's label for the selected electricity or gas contract. It checks once a day and after reload; missing or unsupported plan details leave only this sensor unknown. Temporary connection failures retain the last known label and retry on a later scheduled refresh. The label is not a tariff, a free-power schedule or an estimate of savings.

Three additional entities help distinguish delayed readings from a connection problem:

- **Latest electricity usage** is the start of the most recent hourly reading received, retained across restarts.
- **Last successful usage fetch** records when every day in the configured history window was fetched successfully. An empty response counts as a successful request, not fresh usage. This timestamp resets on restart or reload.
- **Electricity data status** is `no_data`, `current`, or `delayed`. Data is delayed when the latest received hour ended more than 48 hours ago. Status is checked on refresh; it does not guarantee there are no gaps. An account connection failure makes the entities unavailable instead.

**Latest complete electricity day** shows the newest day with every consumption hour present, including daylight-saving changes. Its companion sensors show total usage, free usage, paid usage and reported cost for that same date. This handles delayed readings without calling older data “yesterday”. Check the date: a complete day can still be old. Without a complete day in retained history, all five sensors stay unknown.

Its `first_observed_complete` attribute records when this integration first saw all consumption hours for that date. `age_hours_at_first_observation` measures from the end of that day. This survives restarts, but is not Contact's publication time: polling, downtime and initial backfill affect it. `period_predates_tracking` flags days that ended before tracking started.

**Last month electricity usage/cost**, **Last month free electricity** and **Last month paid electricity** cover the previous calendar month, not the billing period. A total stays unknown until every hour is present; free/paid usage also requires an explicit uncharged amount for every hour, and cost requires a price and consistent currency for every hour. Missing free usage or prices never make the latest-day sensors select a different date.

Free usage comes only from Contact's `unchargedValue`; off-peak usage and missing prices are not assumed to be free. Paid kWh is total minus free kWh. The free/paid sensors include a `free_percentage` attribute, unknown when total usage is zero. Period dates and hourly coverage are also shown. These are snapshots, not additional Energy dashboard consumption sources: free usage is already included in total electricity usage. Dollar savings are not estimated without a verified tariff.

The **Yesterday electricity usage/cost** sensors start disabled on new installations; existing enabled entities stay enabled. The new **Yesterday free electricity** sensor starts disabled on both new installations and upgrades. They report only the previous calendar day and stay unknown when it is incomplete; enable them in the entity settings if useful. Yesterday and last-month total/cost sensors also retain daily-average comparisons with the preceding day/month; an incomplete period or zero baseline gives an unknown `change_percent`.

**Month to date** and **Latest 7/30 complete days** provide usage and cost snapshots, with free/paid breakdowns in their attributes. They end at the latest complete day, shown as `covered_through`; they do not include partial days or fill gaps with zero. The rolling sensors require every day in that fixed window, not just any seven or thirty available days.

Comparisons use the preceding 7/30 days, or the same number of days from the start of the previous month. `change_percent` compares daily averages and stays unknown if either period is incomplete, the previous value is zero, or currencies differ. Month-to-date waits for a complete day in the current month. If the previous month has too few days for an equal-length comparison, only the current value is shown.

These summaries retain up to 93 days of locally collected hourly data without increasing API requests. Missing older history cannot be reconstructed from lifetime totals, so monthly values and comparisons may take one or two full months to become available after an upgrade. The configured 3–31 day refetch window still controls which delayed readings and corrections can be collected.

Period costs use Contact's hourly usage-cost field and reported currency, not an invoice total. They do not establish daily charges, GST treatment, discounts, or whether readings were estimated. A previously known hourly cost is retained if a later response omits it.

**Last billed electricity charges** shows the selected contract's current invoice charges including GST, with its billing dates, pre-GST charges and GST amount as attributes. It uses the bill's service totals, not the account balance, payments or missing tariff-line prices. It checks once a day and after reload, in the background refresh. Temporary lookup failures retain the last validated snapshot and retry on a later scheduled refresh. No invoice gives an unknown value; an unsupported or mismatched bill makes only this sensor unavailable. It is a bill snapshot, not a cumulative cost statistic or a claim that hourly usage costs include GST. Credits, revised bills and previous-bill comparisons are not supported.

### History and usage patterns

Two optional entities are disabled by default; enable them in the entity settings if useful:

- **Electricity usage history** exposes up to 93 dated daily rows and the completed calendar months intersecting that window. Its state counts complete consumption days. The `daily` and `monthly` attributes include total/free/paid kWh and hourly coverage. Incomplete values remain unknown; corrections replace the original consumption date. The oldest month may be incomplete where the retained window cuts through it. These arrays are excluded from recorder attribute storage and are not extra Energy inputs.
- **Electricity usage profile** averages complete days in the 28-day window ending at the latest complete day. Its state counts sampled days. Attributes contain average kWh by clock hour (0–23) and weekday (0 = Monday), with sample counts. A repeated daylight-saving hour contributes two hourly samples; a skipped hour contributes none. Partial days are excluded, and unsampled weekdays remain unknown. These are observed habits, not tariff or free-power schedules.

[Dashboard example](dashboard-example.yaml) uses built-in cards. Replace its placeholder entity IDs with yours. [Optional stacked charts](dashboard-charts-example.yaml) use ApexCharts Card's raw data generator; install that frontend card separately if wanted. Do not change the examples to sum or fill missing values, or chart the recorder history of the latest-day snapshot: that would use update dates rather than consumption dates. Keep total electricity usage as the single grid-consumption source in the Energy dashboard.

Each chart bar represents a consumption date, not an hour of use. The examples compensate for the card's timezone adjustment so clock changes do not shift the date labels.

The optional chart configuration targets ApexCharts Card 2.2.3. Check date labels against the native table, particularly across daylight-saving changes or when the browser and Home Assistant use different timezone settings. A blank bar can mean zero usage or unknown coverage; the dated history table distinguishes them.

### Piped gas usage and billing

Add the integration again and select the gas contract. Alongside the **Plan** sensor, it provides:

- **Last month reported gas usage**: Contact's reported kWh for the previous calendar month, with up to 12 completed months in its `monthly` attribute. Missing months stay unknown. It checks daily and after reload; temporary failures retain the last successful report, dated by `last_successful_fetch`. Malformed reports make only this sensor unavailable.
- **Last billed gas usage**: kWh from the latest bill, with its start date, inclusive end date and day count.
- **Last billed gas charges**: that contract's charges in NZD including GST. Attributes separate GST, usage charges excluding GST, and fixed charges excluding GST (including gas industry fees).
- **Gas reading quality**: actual, estimated, or unknown, as reported for the bill's current meter reading. This does not imply that earlier readings were actual or that past estimates have been corrected.
- **Previous meter reading date** and **Next meter reading date**: dates reported for that gas contract, not inferred from its billing period. The next date includes `next_reading_is_estimate` when Contact supplies it. Missing or conflicting dates across meters remain unknown.
- **Gas share of current invoice charges**: that gas contract's GST-inclusive charges as a percentage of all current charges on the same invoice, including other services. Attributes show both amounts. Missing totals, negative service totals or a breakdown that does not reconcile leave the percentage unknown without hiding the valid gas bill.

Monthly usage follows Contact's current usage chart. It can differ from a dated bill graph and is not a billing-period allocation or proof of actual meter readings. Monthly costs and actual/estimated labels are not supplied. Corrections replace the affected month; the monthly array is excluded from recorder history.

The bill sensors update every eight hours. Neither bill snapshots nor monthly reports are live readings or cumulative meters, and they are not suitable as gas inputs to the Energy dashboard. Existing electricity entries stay unchanged; gas needs its own entry. The history-days setting applies only to electricity.

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

The history setting accepts 3–31 days. Contact normally reports usage after a delay, so the integration revisits a bounded recent window every eight hours, newest days first. Individual missing or malformed days are skipped. Connection failures, rate limits or three consecutive failures defer the remaining older days to the next refresh.

Home Assistant stores the submitted credentials in the integration configuration. The integration does not expose credentials through logs, diagnostics, entities, or statistics.

### What to expect after setup

Account and gas billing entities populate during the first successful refresh. Electricity history, plan details and electricity bill charges follow in a background refresh. Hourly electricity data can be delayed by Contact Energy, so the selected history range may initially contain fewer complete days than requested. The accumulated usage and cost statistics remain stable across overlapping refreshes and Home Assistant restarts.

Account balance and billing values belong to the customer account. If several electricity contracts share one account, those account-level values can appear on each contract device while usage and cost statistics remain separate.

## Upgrading from v2.0.0

Back up Home Assistant, update the integration and restart. Existing electricity entries, entity IDs and accumulated statistics are preserved. Add the integration again to select a gas contract; gas is not added automatically.

Daily and monthly summaries stay unknown until their full periods have been collected. To roll back, restore the pre-upgrade backup, particularly if you have added a gas entry that v2.0.0 cannot handle.

## Upgrading from v1.0.0

Back up Home Assistant before upgrading. V2 preserves the existing integration entry and entity IDs, but creates separate consumption and cost statistics for each electricity contract.

In the Energy dashboard, select the new statistics for the correct ICP. The old series remain available but no longer receive updates. Remove the old free-electricity selection; the separate yesterday sensor does not replace that legacy statistic.

See the [migration guide](MIGRATION.md) for upgrade and rollback instructions, [usage that appears frozen](MIGRATION.md#usage-appears-frozen-after-upgrading), and [duplicate entries blocking setup](MIGRATION.md#duplicate-entries-block-setup).

Warnings about billing sensors losing their state class are explained [here](MIGRATION.md#billing-sensors-no-longer-have-a-state-class). Deleting their old statistics is optional.

## Statistics behavior

The integration uses the API fields `value` and `dollarValue` for consumption and cost. Lifetime sums are persisted locally while a bounded recent window remains replaceable, so overlapping refreshes and Home Assistant restarts do not inflate totals.

Costs use the reported currency, normally NZD. A missing cost value does not erase a previously imported cost, and data with a conflicting currency is rejected from the cost series.

## Troubleshooting and limitations

- This integration uses undocumented customer-facing endpoints. Contact Energy can change or withdraw them without notice.
- Usage is delayed source data, not a real-time power reading.
- Gas shows the latest validated bill and Contact's calendar-month usage reports. Neither is a live or cumulative meter reading suitable for the Energy dashboard's gas total.
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
