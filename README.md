# ShipKia Lead

Custom ERPNext Lead distribution, ShipKia account imports and matching, onboarding,
Lead/Customer fields and UI actions. Requires ERPNext. Frappe CRM is not required.

## Installation

Install the package into the bench environment and register it in sites/apps.txt,
then run `bench --site SITE install-app shipkia_lead`. On an existing installation
with the same custom DocTypes, installation force-syncs their metadata to module
Shipkia Lead without renaming tables or records. Existing Custom Fields and
Property Setters are preserved. Fresh sites receive the setup fields and indexes.
Restart web, scheduler and worker processes after installing a new Python app.

Lead and Customer document hooks, list/form scripts and minute scheduler methods
are declared in shipkia_lead/hooks.py. The app adds CRM workspace shortcuts after
migration without exporting or modifying the upstream ERPNext workspace file.

Run the test modules shipkia_lead.test_lead_distribution and
shipkia_lead.test_shipkia_matching in an initialized Frappe test context. Tests
roll back their data. Matching and API settings retain their existing DocType names.

## Upgrade and recovery

Run `bench --site SITE migrate` after updating this app. New schema changes should
be implemented as this app's patches or custom-field setup, never ERPNext edits.
Do not uninstall this app merely to roll back code: uninstalling an app owning
DocTypes can delete records. Restore the saved app code and database backup for
an emergency rollback of the ownership migration.

Migration backups for the development site are documented in MIGRATION.md.
