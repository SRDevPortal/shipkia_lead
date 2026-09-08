# ShipKia customer sync

Open CRM → ShipKia Connections, ShipKia Customers, or ShipKia Sync Logs.
The local Testing and Production connections are disabled drafts. Testing is configured
for POST to http://api.shipkia.tst/oms/customers/records/list with rows=20 and page=1.
No usable API key or customer-response mapping has been supplied. Production has no endpoint. No ShipKia API call has been made during implementation.

## Configure a connection

1. Confirm whether API credentials represent distinct customer datasets.
   Use separate connections for separate datasets; rotate the key on the same
   connection for replacement credentials to the same dataset.
2. Enter the full customer-list endpoint (no query string or credentials). Production
   requires HTTPS. Testing can explicitly enable Allow HTTP for Testing. Select GET
   or POST; POST sends an empty body with pagination in the query string.
   Enter the API key in the encrypted Password field. Configure the header name
   and prefix according to the API documentation; do not put keys in the URL.
3. Configure the customer array path, field mapping, and pagination from the real
   API contract. For example, **only if the response matches this structure**:

   ```json
   {"data":{"customers":[{"id":"00123","name":"Example","mobile":"+447700900001","status":"complete"}]}}
   ```

   Array path: `data.customers`. Field mapping:

   ```json
   {"cust_id":"id","customer_name":"name","phone":"mobile","onboarding_status":"status"}
   ```

   Optional mappings: email, business_name, signup_date, onboarded_date, updated_at.
   Paths are dictionary keys separated by dots, not arbitrary JSONPath expressions.
4. Specify exact panel status values meaning onboarding is complete, one per line.
   A connection-linked account with no confirmed completion status is Signed Up.
   The existing ID-only manual behavior for records without a connection remains.
5. Use Test Connection. It fetches one page without saving customer records.
   Enable the connection and use Sync Now to import. Review imported customers
   and their sanitized JSON before enabling production updates.

The implemented HTTP adapter supports GET or empty-body POST requests, a single credential header,
numbered pages or a documented single-page response. Cursor/token login/offset
APIs require an adapter extension after reviewing their actual contract. Redirects
are rejected so credentials are not forwarded to a different endpoint. Pages
are bounded to 5 MB and 500 records. `Has More` may use a configured boolean path;
otherwise a short numbered page ends the run. A maximum-page limit prevents
unbounded fetching. Confirm page size is honored before enabling a schedule.

## Matching and applying

ShipKia Customer stores the imported information separately. Records are keyed by
connection plus CUST ID; the same ID in Testing and Production is distinct.
Credential values and common sensitive JSON keys are excluded from raw-response
storage. Imported data and actions are available only to System Managers.

Set **ShipKia API Connection** on the existing Lead/Customer first. Preview Match
searches only records assigned to that connection: CUST ID first, then exact email
or phone digits (with optional leading +). Other phone formatting may require
manual correction; numbers are never matched using country-agnostic suffixes.
No Lead/Customer is created automatically. Multiple matches, conflicting CUST IDs,
or an unrelated Lead/Customer pair are marked Needs Review and are not applied.

Apply to CRM rechecks the match and updates CUST ID, panel status and last update
time. It does not overwrite names, email, phone, lead owners or sales statuses.
Testing connections cannot apply. Production automatic updates are off by default.
Unmatched records require assigning the correct connection or correcting identity
details on the CRM record, then running Preview Match again.

Uniqueness in Lead and Customer is now based on connection + CUST ID, backed by a
unique identity field. IDs on records without a connection remain unique within
that unlinked group. API/import writes must use normal Frappe document saves;
direct SQL updates bypass document validation.

## Scheduling and failure recovery

- Enable Scheduled Sync separately from Enabled. Default interval: 15 minutes
  after completion. A scheduler tick checks local connection metadata each minute;
  it does not call ShipKia unless a run is due.
- First run imports all pages. If an updated-since parameter is configured, later
  runs use the prior successful start time minus a two-minute overlap. API timestamp
  format/timezone semantics must be confirmed against the contract.
- Without an incremental API filter, subsequent runs fetch all pages again.
  Unchanged imported customer payloads are not rewritten.
- Only one run per connection; workers handle at most three pages before yielding.
  Data and progress commit at page boundaries. The checkpoint advances only after
  the whole run completes. Reprocessing is idempotent.
- Network/server/rate-limit failures retry with increasing delays, honoring a
  supplied Retry-After header. After five failed attempts, or a non-retryable HTTP
  error, scheduled syncing stops for that connection. Review the log and fix the
  configuration, then Sync Now and re-enable Scheduled Sync when ready.
- Abandoned queue requests are eligible for recovery after five minutes. Frappe's
  scheduler, Redis and a worker listening to the long queue must be running.
- A failed run may have imported earlier successful pages. Those records are kept;
  the old successful checkpoint is retained so the next run safely reprocesses them.

## Validation and rollout

Automated database tests use synthetic API responses and roll back their records.
They cover identity isolation, duplicate prevention, redaction, idempotency,
matching, production-only application, authorization, pagination, checkpointing,
worker batch limits, retries and HTTP request construction.

Real ShipKia endpoint/authentication/pagination details and separate-dataset
confirmation are still required. Frappe Cloud has not received these changes.
Deploy the ERPNext changes through the normal release and run site migration to
load the DocTypes, custom fields, uniqueness migration and scheduler hook.

The supplied testing curl contained an already-expired access token. It was not
persisted or used. The browser refresh cookie is not copied into customer requests;
a documented refresh endpoint is required to implement automatic renewal.

## Verified Testing workflow update

Testing can explicitly enable Allow Updates to Testing-linked CRM Records. Matching reuses an existing connection+CUST ID first; new matches use normalized full mobile digits, then an exact company/store name only when there is no mobile match. Ambiguous mobile matches do not fall through to names. Email is no longer a fallback. Matching remains limited to the selected connection.

Treat Matched Customer ID as Onboarded is an explicit business rule, separate from interpreting panel status values. It is enabled for the local Testing verification at the user's request. Scheduled imports remain off.
