# Background ShipKia matching

Lead and Customer saves now update hidden normalized phone/business keys and
write one durable **ShipKia Match Queue** entry when relevant details change.
New and unmatched CRM records automatically use the active site connection. The save does not
match records or call ShipKia's API. Changes to unrelated fields do not queue work.

After the transaction commits, one shared background job wakes the matching
worker. Bulk imports coalesce wakeups within their transaction, and repeated
triggers for a record reuse its queue row. A generation counter prevents a worker
from clearing a newer pending edit while it finishes an older request.

The worker uses the local ShipKia Customer data. Existing connection + CUST ID
links take priority; new matches use normalized phone, then exact normalized
company/store name. Country codes are retained. Duplicate matches require review.
The connection's Enabled, Apply Matched Records Automatically, Testing update
permission and onboarding rule still control the final action.

Phone and business comparisons use stored keys and compound indexes beginning
with the connection. Matching no longer runs REGEXP_REPLACE or LOWER/TRIM over
CRM records. Query plans on the development site confirmed indexed phone lookups
on both Lead and ShipKia Customer.

## Limits and recovery

- At most one matching worker per site, protected by an expiring Redis lock.
- At most 100 queue entries or 20 seconds between tasks per batch. One task may
  finish after that budget; the job timeout is 150 seconds and lock TTL is 180.
- Work commits one record at a time. Pending rows survive process/Redis failures.
- Another bounded job handles remaining due work. A once-per-minute local
  scheduler check recovers missed wakeups; it makes no ShipKia API requests.
- Failed tasks retry with increasing delays, up to five attempts. Use Retry
  Matching on a Failed entry after correcting the record.
- Completed queue rows are retained and reused on subsequent edits, not appended
  for every edit. This keeps one row per source document, not one row per event.

Open **CRM → ShipKia Matching Queue** (`/app/shipkia-match-queue`). Queue Health
shows status counts and the oldest pending request. Results include Applied,
Matched, Unmatched, and Needs Review. Pending includes a task currently being
processed; there is no per-record realtime polling added to Lead forms.

## API imports are separate

An import now saves the customer data and queues changed customers. It does not
perform CRM matching inside the API worker. Import completion means customers
were fetched; consult the matching queue for the subsequent CRM update results.
The old import log's Records Applied counter no longer measures those asynchronous
updates. An unchanged imported customer does not generate more work: a new or
edited CRM lead has its own trigger, so it can match that existing local customer.

API scheduling was not enabled by this change. On the local Testing connection,
new leads can match customers already imported without waiting for another API
sync. Newly created panel accounts still need a customer import first. Unmatched
leads are checked again when a matching imported customer arrives or their own
matching fields change. Completed unmatched Lead checks are also eligible for a bounded local recheck after 15 minutes.

Changing a connection from preview to applying updates does not retroactively
queue every historical row. Use Retry Matching for selected queue entries or
Preview/Apply on the imported customer. Changing a lead's connection also queues
a new check. Direct SQL/db_set updates bypass normal save/index hooks and should
not be used for matching fields; standard REST document saves and Data Import
use the normal hooks.

## Verification

See the local site's `private/files/shipkia-matching-validation/`:

- Database and mocked-HTTP regression tests cover queue coalescing, retry state,
  generation fencing, batch size, deferred imports, permissions, identity and
  existing sync behavior.
- A transaction-only benchmark wrote 2,000 entries and repeated all 2,000
  triggers; the queue remained at 2,000 rows and selection was bounded to 100.
  These benchmark rows were rolled back without changing existing data.
- A retained live test lead was saved with no CUST ID, then the ordinary worker
  filled its ID and Onboarded status from a locally imported customer. API sync
  log count did not change.

This is functional verification plus a queue-write benchmark, not a concurrent
production load test or a guarantee against all server slowdowns. Worker throughput
and backlog age should guide later tuning.

## Manual sync and unmatched rechecks

Lead forms have a **Sync with ShipKia** button. The Lead list has the same button
for 1?100 checked rows. Save form edits first. The endpoint checks write permission
on every selected Lead and requires an enabled ShipKia Connection before queuing
any of the selection. Refresh the form/list after the worker completes. No browser
polling is added. Queue results remain available to System Managers.

Every minute, recheck_unmatched promotes at most 100 overdue Done/Unmatched Lead
queue rows. Each unsuccessful check becomes eligible again after 15 minutes;
backlogs can increase the actual delay. An indexed due query avoids rescanning all
Leads. Concurrent manual requests are preserved by a conditional generation check.
Disabled connections, already-linked leads, Failed and Needs Review results are
excluded. Existing unmatched leads that have never entered the queue must first
be selected for manual sync or saved with matching details/connection.

Both manual and scheduled matching use imported ShipKia Customer records only.
They do not fetch fresh panel data or change the separate API import schedule.
Connection settings still determine whether matching previews or applies updates.

## One active environment per site

Enable **Active Site Connection** on the desired ShipKia API Connection. Only one
connection can be enabled: validation and a unique database slot enforce this.
Disable the old connection before enabling the other environment. Credentials,
API checkpoints and imported customers remain separate on each connection.

Lead/Customer connection fields are now read-only and fill on save. Manual Lead
sync also works without selecting a connection. A minute scheduler queues at most
100 historical unlinked Lead/Customer records for adoption by the existing matching
worker. No API call or inline matching runs on that scheduler or on form save.

Unmatched records move to the newly active environment on save or in this bounded
adoption pass. Records with existing CUST IDs retain their original connection;
legacy IDs with unknown provenance are not silently assigned to a new environment.
Records belonging to an inactive environment remain visible but do not sync.
No active connection means automatic matching pauses; manual sync explains that
an administrator must activate a connection. API import scheduling is still a
separate setting on the active connection.
