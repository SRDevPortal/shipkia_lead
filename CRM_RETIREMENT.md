# ERPNext Lead migration — 9 September 2026

The separate Frappe `crm` app has been retired. ERPNext `Lead` is the shared
record for Shipkia, WhatsApp, Meta comments, Vobiz calls and payment links.
The ERPNext CRM workspace remains at `/app/crm`; its lead list is `/app/lead`.
No source changes were made in Frappe, ERPNext or Insights.

## Behavior

- WhatsApp conversations and contacts use `linked_lead`. Inbound processing
  creates or reuses one ERPNext Lead; lead scoring updates that same record.
- Manual lead creation, reference chat, AI lead lookup and integration links
  use ERPNext Lead. Some public API names retain `crm` for compatibility;
  these names do not require the removed application.
- Shipkia supplies compatibility fields through Custom Fields. ERPNext status
  options are authoritative. The original CRM ID and status remain available
  as `legacy_crm_lead` and `legacy_crm_status`.
- Shipkia's distribution engine owns lead assignment. The old
  `new_assignement_system` hooks and schedules are disabled; its settings are
  retained for reference.
- Lead visibility considers ownership, assignment and explicit sharing.
  System Manager and Sales Manager retain broad access; ordinary Frappe role
  permission checks still apply.

## Data and recovery

All 125 source CRM leads have ERPNext targets. Historical records are stored
in 489 immutable `Shipkia Legacy Record` archive entries, including CRM-only
deals, calls, tasks and settings. These are archives, not newly implemented
ERPNext Opportunity or Task workflows. FCRM lead notes were transferred to
ERPNext's native CRM Note table. Chat messages were preserved.

Existing identity matches preserve populated ERPNext values when they differ;
20 conflicts are documented in the private migration report. Ambiguous
identity matches remain separate instead of being merged automatically.

System Managers can open `/app/shipkia-legacy-record` or the Legacy CRM Archive
shortcut in the CRM workspace. The archive contains sensitive historical data.

Bench-relative recovery locations:

- `sites/development.localhost/private/backups/20260909_132410-development_localhost-*`
  contains the pre-cutover database, public/private files and site configuration.
- `sites/development.localhost/private/files/crm-retirement-20260909/`
  contains the original snapshot, migration report and custom source backup.
- `archived/apps/crm-2026-09-09` contains the removed CRM source.

Rollback requires restoring the matching database, files, configuration and
custom source together, reinstating the archived CRM app/package and bench
registration, then migrating and restarting. Reinstalling CRM alone does not
restore the original records. Back up any new user work before rollback.

## Validation

A restored isolated site, `crm-retirement-test.localhost`, was migrated and
tested with CRM uninstalled. Its scheduler is paused and email is muted.

- 233 focused Python tests passed across WhatsApp, Shipkia Lead/Insights,
  Vobiz, Meta status and payment gateway logic.
- 3 reference-chat UI tests passed with Node's test runner.
- Retirement integration tests cover single-lead inbound behavior, reuse,
  scoring, manual-send queuing, retry/delivery status, webhook deduplication,
  AI draft/auto reply and schema/reference integrity.
- External HTTP and background enqueue operations were mocked in tests;
  these results do not confirm actual WhatsApp provider delivery.
- The broader legacy WA suite has unrelated clinic/mobile failures also seen
  against the original source. It is not reported as passing.
- Both the rehearsal and development site completed full `bench migrate`.
  The live audit reports zero blockers, 125 mapped leads and CRM uninstalled.

Logs are in bench `logs/crm-focused-tests.log`, `logs/crm-ui-tests.log`,
`logs/crm-live-full-migrate.log` and `logs/crm-assets-build.log`.

## User acceptance

1. Open `/app/lead` and inspect migrated contact details and original status.
2. Open `/app/wa-chat-hub`, select an existing conversation and open its Lead.
3. Send a controlled test message through your configured WhatsApp provider;
   check reply/delivery, scoring and that repeated messages reuse the Lead.
4. Verify assignment and visibility using a sales user, then inspect Shipkia
   lead sync and Insights using your existing connection.

Implementation lives in `erpnext_leads.py`, `crm_retirement.py`,
`permissions.py` and the custom app hooks. The retirement runner is explicit
and resumable; it is not an install hook and should not be run routinely.
