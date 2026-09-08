"""Durable coalescing match queue; never calls an external API."""

import hashlib
import re
import time
from uuid import uuid4

import frappe
from frappe.utils import add_to_date, now_datetime

QUEUE = "ShipKia Match Queue"
JOB_KEY = "shipkia-matching-job"
PHONES = ("mobile_no", "phone", "whatsapp_no")
BUSINESSES = ("company_name", "customer_name", "shipkia_business_name", "store_name", "shipkia_store_name")
BATCH_SIZE = 100
UNMATCHED_RECHECK_MINUTES = 15


def key_fields(doctype):
	if doctype == "ShipKia Customer":
		return [("phone", "sk_phone_key", "phone"), ("business_name", "sk_business_key", "business")]
	return [(field, "sk_phone_" + str(i), "phone") for i, field in enumerate(PHONES)] + [
		(field, "sk_business_" + str(i), "business") for i, field in enumerate(BUSINESSES)
	]


def normalize(value, kind):
	value = str(value or "")
	return (re.sub(r"\D", "", value) if kind == "phone" else value.strip().lower()) or None


def set_keys(doc, method=None):
	for source, target, kind in key_fields(doc.doctype):
		if doc.meta.has_field(target):
			doc.set(target, normalize(doc.get(source), kind))


def changed(doc, method=None):
	if frappe.flags.in_migrate or frappe.flags.in_install or doc.flags.shipkia_sync_apply:
		return
	connection_field = "connection" if doc.doctype == "ShipKia Customer" else "shipkia_connection"
	before = doc.get_doc_before_save()
	if not doc.get(connection_field) and not (before and before.get(connection_field)):
		return
	if not frappe.db.table_exists(QUEUE):
		return
	fields = [source for source, _, _ in key_fields(doc.doctype)] + [
		connection_field,
		"cust_id" if doc.doctype == "ShipKia Customer" else "shipkia_cust_id",
		"payload_hash" if doc.doctype == "ShipKia Customer" else "customer",
		"lead_name",
	]
	if before and not any(doc.has_value_changed(field) for field in fields if doc.meta.has_field(field)):
		return
	put(doc.doctype, doc.name)


def put(doctype, name):
	key = hashlib.sha256((doctype + "\0" + name).encode()).hexdigest()
	frappe.db.sql(
		"""INSERT INTO `tabShipKia Match Queue`
		(name, creation, modified, owner, modified_by, docstatus, idx, reference_doctype,
		reference_name, status, generation, attempts, due_at, requested_at)
		VALUES (%s, NOW(6), NOW(6), %s, %s, 0, 0, %s, %s, 'Pending', 1, 0, NOW(6), NOW(6))
		ON DUPLICATE KEY UPDATE generation=generation+1, status='Pending', attempts=0,
		due_at=NOW(6), requested_at=NOW(6), modified=NOW(6), last_error=NULL, result=NULL, finished_at=NULL""",
		(key, frappe.session.user, frappe.session.user, doctype, name),
	)
	if not frappe.flags.sk_match_wakeup:
		frappe.flags.sk_match_wakeup = True

		def reset():
			frappe.flags.sk_match_wakeup = False

		def after_commit():
			reset()
			kick()

		frappe.db.after_commit.add(after_commit)
		frappe.db.after_rollback.add(reset)
	return key


def due(limit=BATCH_SIZE):
	return frappe.db.sql(
		"""SELECT name, reference_doctype, reference_name, generation, attempts
		FROM `tabShipKia Match Queue` WHERE status IN ('Pending', 'Retrying') AND due_at <= %s
		ORDER BY due_at, requested_at LIMIT %s""",
		(now_datetime(), limit),
		as_dict=True,
	)


def kick():
	"""One Redis job for all pending leads, with scheduler recovery on queue failure."""
	if not frappe.db.table_exists(QUEUE):
		return
	try:
		from frappe.utils.background_jobs import is_job_enqueued

		lock = frappe.cache.lock("shipkia-match-enqueue", timeout=10, blocking_timeout=0)
		if not lock.acquire(blocking=False):
			return
		try:
			job = frappe.cache.get_value(JOB_KEY)
			if (job and is_job_enqueued(job)) or not due(1):
				return
			job = "shipkia-match-" + str(uuid4())
			frappe.cache.set_value(JOB_KEY, job, expires_in_sec=300)
			frappe.enqueue(
				"shipkia_lead.shipkia_matching.drain", queue="long", timeout=150, job_id=job, token=job
			)
		finally:
			lock.release()
	except Exception:
		# Durable Pending rows survive Redis downtime. Do not break the lead save.
		return


def recheck_unmatched():
	"""Promote at most 100 overdue unmatched leads per minute, without API calls."""
	if not frappe.db.table_exists(QUEUE):
		return
	# Conditional UPDATE preserves a concurrent manual request/newer generation.
	rows = frappe.db.sql(
		"""SELECT q.name, q.generation FROM `tabShipKia Match Queue` q
		JOIN `tabLead` l ON l.name=q.reference_name
		JOIN `tabShipKia API Connection` c ON c.name=l.shipkia_connection
		WHERE q.status='Done' AND q.result='Unmatched' AND q.due_at<=%s
		AND q.reference_doctype='Lead' AND c.enabled=1
		AND (l.shipkia_cust_id IS NULL OR l.shipkia_cust_id='')
		ORDER BY q.due_at, q.name LIMIT %s""",
		(now_datetime(), BATCH_SIZE), as_dict=True,
	)
	for row in rows:
		frappe.db.sql(
			"""UPDATE `tabShipKia Match Queue` SET status='Pending', generation=generation+1,
			attempts=0, requested_at=NOW(6), modified=NOW(6), finished_at=NULL,
			result=NULL, last_error=NULL
			WHERE name=%s AND generation=%s AND status='Done' AND result='Unmatched'""",
			(row.name, row.generation),
		)
	if rows:
		frappe.db.after_commit.add(kick)
	return len(rows)


@frappe.whitelist(methods=["POST"])
def sync_leads(names):
	"""Queue selected leads using normal Lead write permissions, never arbitrary documents."""
	if isinstance(names, str):
		names = frappe.parse_json(names)
	if not isinstance(names, list) or not 1 <= len(names) <= BATCH_SIZE:
		frappe.throw("Select between 1 and 100 leads.")
	if any(not isinstance(name, str) or not name for name in names):
		frappe.throw("Invalid lead selection.")
	docs = []
	for name in dict.fromkeys(names):
		doc = frappe.get_doc("Lead", name)
		doc.check_permission("write")
		from shipkia_lead.shipkia_active_connection import assign_connection
		assign_connection(doc)
		if not doc.shipkia_connection:
			frappe.throw("Activate a ShipKia connection in settings first.")
		if not frappe.db.get_value("ShipKia API Connection", doc.shipkia_connection, "enabled"):
			frappe.throw(f"The ShipKia Connection on lead {doc.name} is disabled.")
		docs.append(doc)
	for doc in docs:
		put("Lead", doc.name)
	return {"queued": len(docs)}


def find_imported(doc):
	from shipkia_lead.shipkia_sync import identity

	connection = doc.get("shipkia_connection")
	if doc.get("shipkia_cust_id"):
		name = identity(connection, doc.shipkia_cust_id)
		return [name] if frappe.db.exists("ShipKia Customer", name) else []
	for kind, field in (("phone", "sk_phone_key"), ("business", "sk_business_key")):
		values = {
			normalize(doc.get(source), group) for source, _, group in key_fields(doc.doctype) if group == kind
		} - {None}
		if values:
			names = frappe.get_all(
				"ShipKia Customer",
				filters={"connection": connection, field: ["in", sorted(values)]},
				pluck="name",
				limit=3,
			)
			if names:
				return names
	return []


def process(row):
	from shipkia_lead.shipkia_sync import apply_record, match_record

	if not frappe.db.exists(row.reference_doctype, row.reference_name):
		return "Record no longer exists"
	doc = frappe.get_doc(row.reference_doctype, row.reference_name)
	if doc.doctype in ("Lead", "Customer"):
		from shipkia_lead.shipkia_active_connection import assign_connection
		previous = doc.shipkia_connection
		assign_connection(doc)
		if previous != doc.shipkia_connection:
			doc.flags.shipkia_sync_apply = True
			doc.save(ignore_permissions=True)
	connection_name = (
		doc.get("connection") if doc.doctype == "ShipKia Customer" else doc.get("shipkia_connection")
	)
	if not connection_name:
		return "No connection selected"
	connection = frappe.get_doc("ShipKia API Connection", connection_name, for_update=True)
	if not connection.enabled:
		return "Connection disabled; retry after enabling"
	if doc.doctype == "ShipKia Customer":
		doc.reload()
		record = doc
	else:
		# Reload after obtaining the connection lock, before selecting a match.
		doc.reload()
		if doc.shipkia_connection != connection_name:
			return "Connection changed; newer request will retry"
		names = find_imported(doc)
		if len(names) != 1:
			return "Needs Review: multiple imported customers" if names else "Unmatched"
		record = frappe.get_doc("ShipKia Customer", names[0])
	if connection.apply_updates:
		apply_record(record, connection)
	else:
		match_record(record)
	return record.match_result


def finish(row, result=None, error=False):
	attempts = (row.attempts or 0) + int(error)
	status = ("Failed" if attempts >= 5 else "Retrying") if error else "Done"
	frappe.db.sql(
		"""UPDATE `tabShipKia Match Queue` SET status=%s, result=%s, attempts=%s,
		last_error=%s, due_at=%s, finished_at=%s, modified=NOW(6)
		WHERE name=%s AND generation=%s""",
		(
			status,
			result,
			attempts,
			"Matching failed; check record validation and retry." if error else None,
			add_to_date(now_datetime(), minutes=min(60, 2**attempts) if error else UNMATCHED_RECHECK_MINUTES),
			now_datetime() if status in ("Done", "Failed") else None,
			row.name,
			row.generation,
		),
	)


def drain(token=None):
	lock = frappe.cache.lock("shipkia-match-worker", timeout=180, blocking_timeout=0)
	if not lock.acquire(blocking=False):
		return
	started = time.monotonic()
	try:
		for row in due():
			if time.monotonic() - started >= 20:
				break
			try:
				result = process(row)
				finish(row, result)
				frappe.db.commit()
			except Exception:
				frappe.db.rollback()
				finish(row, error=True)
				frappe.db.commit()
	finally:
		lock.release()
		if frappe.cache.get_value(JOB_KEY) == token:
			frappe.cache.delete_value(JOB_KEY)
		kick()


@frappe.whitelist()
def retry(name):
	frappe.only_for("System Manager")
	row = frappe.get_doc(QUEUE, name)
	return put(row.reference_doctype, row.reference_name)


@frappe.whitelist()
def health():
	frappe.only_for("System Manager")
	return {
		"counts": frappe.get_all(QUEUE, fields=["status", "count(name) as quantity"], group_by="status"),
		"oldest_pending": frappe.db.get_value(
			QUEUE, {"status": ["in", ["Pending", "Retrying"]]}, "requested_at", order_by="requested_at asc"
		),
	}
