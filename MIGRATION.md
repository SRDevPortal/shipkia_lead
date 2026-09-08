# Default-app split migration - 2026-09-08

Created and installed shipkia_lead and shipkia_insights on development.localhost.
This migration preserves DocType names, record names and encrypted credential keys.

Before migration:
- Full DB and public/private file backup prefix:
  sites/development.localhost/private/backups/20260908_174513-development_localhost
- Source backup, original diffs, record identities and secret fingerprints:
  sites/development.localhost/private/files/app-split-backup/

The source backup is private and is not included in either app package. No
credentials are written to app source files or these instructions.

ERPNext's modified hooks, patches, Lead list and workspace were restored to HEAD;
its verified migrated custom files were removed after copying and testing.
Insights' modified data_warehouse.py was restored to HEAD; its regression tests
moved to shipkia_insights. Existing third-party applications were not migrated.

61 regression tests passed with the default apps restored. Form/list scripts are
served from shipkia_lead assets. Background services were restarted to load new
editable packages, and package-loading failures from the transition were requeued.
The original worker registry entry may remain until its Redis TTL expires; the
old process was stopped with the bench supervisor.

No business records, connection credentials or dashboards were deleted. Technical
scheduler registrations moved to the new dotted method paths. Baseline upstream
ERPNext schedules retain their prior stopped/enabled states.

## Recovery

Prefer restoring app code while retaining app ownership. Do not uninstall either
custom app as a rollback shortcut. For a full pre-migration rollback, stop the local
services, restore the pre-migration DB/files and saved default-app source changes,
restore the previous installed-app configuration and restart services. Restoring
that DB snapshot also rolls back subsequent business edits, so perform a new backup
and review the data delta before doing so.
