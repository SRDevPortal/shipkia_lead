# ShipKia Lead

## Fresh installs and retries

Requires Frappe/ERPNext v15. Frappe Cloud reads the supported version range
from `pyproject.toml`; it includes Frappe 15.120.0.

Setup creates its own Lead tabs and integration fields. Optional links to
WhatsApp conversations, Vobiz calls and payment intents are created only when
their target DocTypes exist. After adding an optional integration app, run
`bench --site SITE migrate` to add its fields.

Installation and migration check for incomplete core setup and finish missing
fields. Existing complete layouts and unrelated custom fields are preserved.
If installation was interrupted, update to the latest `develop` commit and
retry installation; if Frappe already lists the app as installed, run migration.

Clean-site verification on 2026-09-09 used only Frappe 15.116.0, ERPNext 15.118.0,
Insights 3.12.2 and the two ShipKia apps. Both installed and migrated successfully;
87 combined tests and repeated setup checks passed. This is not an execution
test against Frappe 15.120.0. External API calls were mocked in tests.

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
