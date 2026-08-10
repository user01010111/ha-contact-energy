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

## Rollback

Stop Home Assistant, restore the pre-upgrade configuration and recorder backups together, restore the previous integration directory, and then start Home Assistant. Restoring only one of configuration, recorder, or integration code can leave the three out of sync.

Do not remove or edit files under `.storage` while Home Assistant is running.
