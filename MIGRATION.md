# Migrating to v2.0.0

Version 2.0.0 continues the `contact_energy` integration under independent maintenance and keeps the existing domain and configuration keys.

## Before upgrading

1. Back up the Home Assistant configuration directory and recorder database.
2. Record the current Contact Energy entity IDs and Energy dashboard selections.
3. Confirm that the rollback copy is stored outside the live configuration directory.

## What migrates automatically

- The existing Contact Energy integration entry is reused.
- Existing entity IDs are preserved.
- Raw account, contract, and ICP registry identifiers are replaced in place with opaque contract-scoped identifiers.
- The integration-list title shows only the final six ICP characters, while the device and new statistic display names show the full ICP so multiple properties remain identifiable.

The full ICP is display metadata, not part of the active entity, device, configuration, or statistic identifier. Treat Home Assistant screenshots and exported registry data containing an ICP as private account information.

## What requires manual selection

Legacy v1 statistics are not modified or deleted. They were global moving-window series, so v2 cannot determine their contract ownership or turn them into trustworthy lifetime totals. Version 2 starts new contract-scoped consumption and cost statistics.

After the first successful v2 refresh:

1. Open Settings → Dashboards → Energy → Electricity grid.
2. Replace the old Contact Energy consumption selection with the new contract-scoped consumption statistic.
3. Replace the old cost selection with the new contract-scoped cost statistic if used.
4. Remove the legacy free-electricity selection. Version 2 does not create that statistic.

Old recorder rows remain available for historical inspection until removed under the user's normal recorder policy.

## Usage appears frozen after upgrading

An unchanged Energy graph does not necessarily mean the integration has stopped downloading usage. The dashboard can still be pointing at the retained v1 statistics, which v2 no longer updates.

1. Open Settings → Devices & services and check the Contact Energy entry. If it has failed to load, read the setup error before changing dashboard selections. For duplicate errors, follow the section below.
2. If the integration is loaded, check the Energy dashboard's consumption and cost selections against the new statistics for the correct ICP. Follow [What requires manual selection](#what-requires-manual-selection); do not select a retained v1 series.
3. If the new series also has no recent usage, compare with the usage available in Contact MyAccount. The integration refreshes every eight hours, and Contact can publish readings after a delay. Account and billing entities can work even when hourly usage is delayed or unavailable.
4. If MyAccount has newer usage and the integration remains stale after its next refresh, reload the existing Contact Energy entry once from its menu in Settings → Devices & services. If the problem continues, report it with the integration and Home Assistant versions, whether setup succeeds, and the smallest relevant redacted error excerpt.

Entity IDs such as `sensor.contact_energy_...` and the integration's external Energy statistics are separate identifiers. Recreating entity IDs does not switch the Energy dashboard to the new statistics. Purging recorder history is not required for this upgrade.

## Duplicate entries block setup

If setup reports that registry migration found a duplicate entity, device, or config entry, both old and new identifiers may already be registered. Migration stops before changing them rather than guessing which entry to keep. This differs from a successfully loaded integration whose Energy dashboard still uses an old statistic.

Before removing anything:

1. Back up the Home Assistant configuration and recorder database. Record the current entity IDs and their dashboard, automation, and script references.
2. In Settings → Devices & services, inspect the Contact Energy entries and their associated devices and entities. Confirm which account/property each belongs to and which installation is being retained. An unavailable entity, an ICP suffix in a title, or a numbered entity ID alone does not establish that it is obsolete. Multiple properties or accounts can legitimately have separate entries.
3. Remove only entries confirmed to belong to a retired installation, using Home Assistant's UI where removal is available. Do not remove the integration entry you are retaining, purge statistics, or regenerate all entity IDs. Check and update any references to an obsolete entity before removing it.
4. Reload the retained integration once. Confirm that setup succeeds, then select its new consumption and cost statistics in the Energy dashboard.

If ownership is unclear or Home Assistant does not offer removal, stop and [open a support issue](https://github.com/user01010111/ha-contact-energy/issues) rather than editing `.storage`. Include the previous integration/fork and upgrade steps, plus the duplicate-error text. Remove credentials, addresses, ICPs, account/contract IDs, tokens, headers, and complete usage URLs from logs or screenshots. Do not upload configuration storage or database files.

## Rollback

Stop Home Assistant, restore the pre-upgrade configuration and recorder backups together, restore the previous integration directory, and then start Home Assistant. Restoring only one of configuration, recorder, or integration code can leave the three out of sync.

Do not remove or edit files under `.storage` while Home Assistant is running.
