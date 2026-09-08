"""Bounded, per-connection customer imports. No dependency on the CRM app."""

import hashlib
import json
import re
from urllib.parse import urlparse
from uuid import uuid4

import frappe
import requests
from frappe.utils import add_to_date, get_datetime, now_datetime

CONNECTION = "ShipKia API Connection"
CUSTOMER = "ShipKia Customer"
LOG = "ShipKia Sync Log"
FIELDS = {
	"cust_id",
	"customer_name",
	"phone",
	"email",
	"business_name",
	"onboarding_status",
	"signup_date",
	"onboarded_date",
	"updated_at",
}


class APIFailure(ValueError):
	def __init__(self, status_code, retry_after=None):
		super().__init__(f"Customer API returned HTTP {status_code}. Check the connection and rate limits.")
		self.permanent = status_code not in (408, 429) and status_code < 500
		self.retry_minutes = 0
		if retry_after:
			try:
				self.retry_minutes = max(0, (int(retry_after) + 59) // 60)
			except ValueError:
				from datetime import datetime, timezone
				from email.utils import parsedate_to_datetime

				try:
					seconds = (
						parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)
					).total_seconds()
					self.retry_minutes = max(0, int(seconds / 60) + 1)
				except (ValueError, TypeError, OverflowError):
					pass


def identity(connection, cust_id):
	return hashlib.sha256(json.dumps([connection or "", str(cust_id).strip()]).encode()).hexdigest()


def value_at(value, path):
	if path == "$":
		return value
	for key in (path or "").split("."):
		if not isinstance(value, dict) or key not in value:
			return None
		value = value[key]
	return value


def sanitized(value):
	if isinstance(value, dict):
		return {
			key: sanitized(item)
			for key, item in value.items()
			if not re.search(r"password|secret|token|authorization|api.?key|cookie|credential", key, re.I)
		}
	if isinstance(value, list):
		return [sanitized(item) for item in value]
	return value


def validate_connection(doc, required=False):
	if not 5 <= int(doc.sync_interval or 0) <= 1440:
		frappe.throw("Sync interval must be between 5 and 1440 minutes.")
	if not 1 <= int(doc.page_size or 0) <= 500 or not 1 <= int(doc.max_pages or 0) <= 1000:
		frappe.throw("Use a page size of 1–500 and a maximum of 1–1000 pages.")
	if doc.apply_updates and doc.environment != "Production" and not doc.get("allow_testing_updates"):
		frappe.throw("Testing connections support import and preview only.")
	if doc.get("http_method") not in (None, "", "GET", "POST"):
		frappe.throw("Customer list requests support GET or POST.")
	if doc.get("allow_test_http") and doc.environment != "Testing":
		frappe.throw("HTTP is only available for Testing connections.")
	if not (required or doc.enabled):
		return
	url = urlparse(doc.endpoint or "")
	allowed_schemes = (
		{"https", "http"} if doc.environment == "Testing" and doc.get("allow_test_http") else {"https"}
	)
	if (
		url.scheme not in allowed_schemes
		or not url.hostname
		or url.username
		or url.password
		or url.query
		or url.fragment
	):
		frappe.throw(
			"Enter a permitted customer API URL without credentials, query parameters or fragments. Production requires HTTPS."
		)
	if not re.fullmatch(r"[A-Za-z0-9-]+", doc.auth_header or "") or doc.auth_header.lower() in {
		"host",
		"content-length",
	}:
		frappe.throw("Enter a valid authentication header name.")
	if not doc.get_password("api_key", raise_exception=False):
		frappe.throw("Enter the API key in the protected API Key field.")
	try:
		mapping = json.loads(doc.field_mapping or "{}")
	except ValueError:
		frappe.throw("Field Mapping must be a JSON object.")
	if not isinstance(mapping, dict) or not mapping.get("cust_id") or set(mapping) - FIELDS:
		frappe.throw("Field Mapping must contain cust_id and only supported field names.")
	if any(not isinstance(path, str) or not path for path in mapping.values()) or not doc.records_path:
		frappe.throw("Configure the customer array path and nonempty field paths.")
	if doc.pagination_mode == "Numbered Pages" and (not doc.page_parameter or not doc.size_parameter):
		frappe.throw("Configure page and page-size parameter names.")


def fetch_page(connection, page, since=None):
	params = {}
	if connection.pagination_mode == "Numbered Pages":
		params[connection.page_parameter] = page
		params[connection.size_parameter] = connection.page_size
	if since and connection.updated_since_parameter:
		# Overlap avoids missing a record updated at the previous boundary.
		params[connection.updated_since_parameter] = str(add_to_date(since, minutes=-2))
	key = connection.get_password("api_key")
	header = ((connection.auth_prefix or "").strip() + " " + key).strip()
	try:
		request = requests.post if connection.get("http_method") == "POST" else requests.get
		with request(
			connection.endpoint,
			headers={"Accept": "application/json", connection.auth_header: header},
			params=params,
			timeout=(5, 20),
			allow_redirects=False,
			stream=True,
		) as response:
			if response.status_code != 200:
				raise APIFailure(response.status_code, response.headers.get("Retry-After"))
			body = bytearray()
			for chunk in response.iter_content(65536):
				body.extend(chunk)
				if len(body) > 5 * 1024 * 1024:
					raise ValueError("API page exceeds 5 MB. Reduce page size.")
			payload = json.loads(body)
	except requests.RequestException:
		raise ValueError(
			"Customer API request failed or timed out. Check connectivity and credentials."
		) from None
	rows = value_at(payload, connection.records_path)
	if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
		raise ValueError("The configured customer array path did not return a list of objects.")
	if len(rows) > 500:
		raise ValueError("API returned over 500 customers in one page. Correct pagination settings.")
	if connection.get("total_pages_path"):
		total_pages = value_at(payload, connection.total_pages_path)
		if isinstance(total_pages, bool) or not isinstance(total_pages, int) or total_pages < 0:
			raise ValueError("Total Pages path must contain a nonnegative JSON integer.")
		more = page < connection.first_page + total_pages - 1
	elif connection.has_more_path:
		more = value_at(payload, connection.has_more_path)
		if not isinstance(more, bool):
			raise ValueError("Has More path must contain a JSON boolean.")
	else:
		more = connection.pagination_mode == "Numbered Pages" and len(rows) >= connection.page_size
	if connection.pagination_mode == "Single Page" and more:
		raise ValueError("The API indicates additional pages. Configure pagination before syncing.")
	return rows, more


def store_customer(connection, raw):
	mapping = json.loads(connection.field_mapping)
	values = {field: value_at(raw, path) for field, path in mapping.items()}
	cust_id = values.get("cust_id")
	if isinstance(cust_id, bool) or not isinstance(cust_id, (str, int)) or not str(cust_id).strip():
		raise ValueError(
			"A customer has no valid CUST ID at the configured path. No checkpoint was advanced."
		)
	values["cust_id"] = str(cust_id).strip()
	for field, value in values.items():
		if value is not None and (isinstance(value, (dict, list)) or len(str(value)) > 140):
			raise ValueError(f"Customer field {field} must be a scalar of at most 140 characters.")
	name = identity(connection.name, values["cust_id"])
	clean = json.dumps(sanitized(raw), sort_keys=True, ensure_ascii=False)
	# Never persist the configured credential even if an API echoes it under an unusual key.
	secret = connection.get_password("api_key", raise_exception=False)
	if secret:
		clean = clean.replace(secret, "[REDACTED]")
	digest = hashlib.sha256(json.dumps(values, sort_keys=True).encode() + clean.encode()).hexdigest()
	existing = frappe.db.get_value(CUSTOMER, name, "payload_hash")
	if existing == digest:
		return name, False
	doc = (
		frappe.get_doc(CUSTOMER, name)
		if existing
		else frappe.get_doc({"doctype": CUSTOMER, "name": name, "connection": connection.name})
	)
	doc.update(values)
	doc.update(
		{
			"payload_hash": digest,
			"raw_response": clean,
			"last_synced": now_datetime(),
			"match_result": "Unmatched",
		}
	)
	if existing:
		doc.save(ignore_permissions=True)
	else:
		doc.insert(ignore_permissions=True, set_name=name)
	return name, True


def candidates(record, doctype):
	from shipkia_lead.shipkia_matching import key_fields, normalize

	base = {"shipkia_connection": record.connection}
	exact = frappe.get_all(
		doctype,
		filters={"shipkia_identity": identity(record.connection, record.cust_id)},
		pluck="name",
		limit=3,
	)
	if exact:
		return exact
	meta = frappe.get_meta(doctype)
	for kind, raw in (("phone", record.phone), ("business", record.business_name)):
		value = normalize(raw, kind)
		if not value:
			continue
		matches = set()
		for _, target, group in key_fields(doctype):
			if group == kind and meta.has_field(target):
				matches.update(
					frappe.get_all(doctype, filters={**base, target: value}, pluck="name", limit=3)
				)
				if len(matches) > 1:
					return sorted(matches)[:3]
		if matches:
			return sorted(matches)
	return []


def match_record(record, persist=True):
	leads, customers = candidates(record, "Lead"), candidates(record, "Customer")
	conflict = len(leads) > 1 or len(customers) > 1
	if len(leads) == len(customers) == 1:
		conflict = not (
			frappe.db.get_value("Customer", customers[0], "lead_name") == leads[0]
			or frappe.db.get_value("Lead", leads[0], "customer") == customers[0]
		)
	for dt, names in (("Lead", leads), ("Customer", customers)):
		if len(names) == 1:
			current = frappe.db.get_value(dt, names[0], "shipkia_cust_id")
			conflict = conflict or bool(current and current != record.cust_id)
	status = "Needs Review" if conflict else ("Matched" if leads or customers else "Unmatched")
	updates = {
		"match_result": status,
		"linked_lead": leads[0] if len(leads) == 1 else None,
		"linked_customer": customers[0] if len(customers) == 1 else None,
		"match_note": "Conflicting matches; no CRM updates allowed."
		if conflict
		else "Matched within this connection."
		if status == "Matched"
		else "No match in this connection. Assign the correct connection on the existing CRM record first.",
	}
	changed = any((record.get(key) or "") != (value or "") for key, value in updates.items())
	record.update(updates)
	if persist and changed:
		record.save(ignore_permissions=True)
	return status


def apply_record(record, connection):
	if connection.environment != "Production" and not connection.get("allow_testing_updates"):
		frappe.throw("Testing data cannot update CRM records.")
	match_fields = ("match_result", "linked_lead", "linked_customer", "match_note")
	before_match = {field: record.get(field) for field in match_fields}
	if match_record(record, persist=False) != "Matched":
		if any(record.get(field) != before_match[field] for field in match_fields):
			record.save(ignore_permissions=True)
		return False
	for dt, name in (("Lead", record.linked_lead), ("Customer", record.linked_customer)):
		if not name:
			continue
		doc = frappe.get_doc(dt, name, for_update=True)
		if doc.shipkia_connection != connection.name or (
			doc.shipkia_cust_id and doc.shipkia_cust_id != record.cust_id
		):
			frappe.throw("CRM identity changed. Preview the match again.")
		updates = {
			"shipkia_cust_id": record.cust_id,
			"shipkia_panel_status": record.onboarding_status or "",
			"shipkia_panel_synced_at": record.last_synced,
		}
		if any(str(doc.get(key) or "") != str(value or "") for key, value in updates.items()):
			doc.update(updates)
			doc.flags.shipkia_sync_apply = True
			doc.save(ignore_permissions=True)
	record.match_result = "Applied"
	if any(record.get(field) != before_match[field] for field in match_fields):
		record.save(ignore_permissions=True)
	return True


@frappe.whitelist()
def preview_match(name, apply=False):
	frappe.only_for("System Manager")
	record = frappe.get_doc(CUSTOMER, name)
	connection = frappe.get_doc(CONNECTION, record.connection)
	if frappe.utils.cint(apply):
		apply_record(record, connection)
	else:
		match_record(record)
	return {"status": record.match_result, "note": record.match_note}


@frappe.whitelist()
def test_connection(name):
	frappe.only_for("System Manager")
	connection = frappe.get_doc(CONNECTION, name)
	validate_connection(connection, required=True)
	rows, more = fetch_page(connection, connection.first_page)
	return {
		"customers_in_page": len(rows),
		"has_more": more,
		"message": "API response parsed. No customer records were saved.",
	}


def enqueue_page(connection, log):
	job_id = "shipkia-" + str(uuid4())
	frappe.db.set_value(LOG, log.name, {"job_id": job_id, "status": "Queued"})
	frappe.enqueue(
		"shipkia_lead.shipkia_sync.run_batch",
		queue="long",
		timeout=180,
		enqueue_after_commit=True,
		job_id=job_id,
		connection_name=connection.name,
		log_name=log.name,
		job_token=job_id,
	)


@frappe.whitelist()
def sync_now(name):
	frappe.only_for("System Manager")
	return start_sync(name)


def start_sync(name):
	connection = frappe.get_doc(CONNECTION, name, for_update=True)
	if not connection.enabled:
		frappe.throw("Enable the connection before syncing.")
	if connection.active_run:
		return connection.active_run
	validate_connection(connection, required=True)
	log = frappe.get_doc(
		{
			"doctype": LOG,
			"connection": name,
			"status": "Queued",
			"started_at": now_datetime(),
			"since": connection.checkpoint,
			"page": connection.first_page,
		}
	).insert(ignore_permissions=True)
	frappe.db.set_value(CONNECTION, name, {"active_run": log.name, "last_error": ""})
	enqueue_page(connection, log)
	return log.name


def run_batch(connection_name, log_name, job_token=None):
	connection = frappe.get_doc(CONNECTION, connection_name, for_update=True)
	if connection.active_run != log_name:
		return
	log = frappe.get_doc(LOG, log_name, for_update=True)
	if job_token and log.job_id != job_token:
		return
	if not connection.enabled:
		frappe.db.set_value(LOG, log_name, {"status": "Stopped", "finished_at": now_datetime()})
		frappe.db.set_value(CONNECTION, connection_name, "active_run", None)
		return
	try:
		log.status = "Running"
		log.save(ignore_permissions=True)
		for _ in range(3):
			if (log.pages_processed or 0) >= connection.max_pages:
				raise ValueError("Maximum pages reached. Review pagination; checkpoint not advanced.")
			rows, more = fetch_page(connection, log.page, log.since)
			for raw in rows:
				_name, changed = store_customer(connection, raw)
				log.records_seen = (log.records_seen or 0) + 1
				log.records_changed = (log.records_changed or 0) + int(changed)
				# Changed customers are queued by their on_change hook.
				# Matching and CRM updates run separately from API import jobs.
			log.page += 1
			log.pages_processed = (log.pages_processed or 0) + 1
			log.attempt = 0
			log.last_error = ""
			if not more:
				log.status = "Completed"
				log.finished_at = now_datetime()
				log.save(ignore_permissions=True)
				frappe.db.set_value(
					CONNECTION,
					connection_name,
					{
						"active_run": None,
						"last_success": now_datetime(),
						"checkpoint": log.started_at,
						"last_error": "",
						"next_sync": add_to_date(now_datetime(), minutes=connection.sync_interval),
					},
				)
				frappe.db.commit()
				return
			log.save(ignore_permissions=True)
			frappe.db.commit()
			connection = frappe.get_doc(CONNECTION, connection_name, for_update=True)
			if connection.active_run != log_name:
				return
			if not connection.enabled:
				frappe.db.set_value(LOG, log_name, {"status": "Stopped", "finished_at": now_datetime()})
				frappe.db.set_value(CONNECTION, connection_name, "active_run", None)
				frappe.db.commit()
				return
			log.reload()
			if job_token and log.job_id != job_token:
				return
		enqueue_page(connection, log)
		frappe.db.commit()
	except Exception as exc:
		frappe.db.rollback()
		connection = frappe.get_doc(CONNECTION, connection_name, for_update=True)
		if connection.active_run != log_name:
			return
		log = frappe.get_doc(LOG, log_name)
		attempt = 5 if getattr(exc, "permanent", False) else (log.attempt or 0) + 1
		# Never log API bodies, URLs, headers, or request exception tracebacks.
		message = (
			str(exc)[:300]
			if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError)
			else "Sync processing failed. Check API mapping, CRM validation and connection settings."
		)
		frappe.db.set_value(
			LOG,
			log_name,
			{"attempt": attempt, "status": "Failed" if attempt >= 5 else "Retrying", "last_error": message},
		)
		frappe.db.set_value(
			CONNECTION,
			connection_name,
			{
				"active_run": None if attempt >= 5 else log_name,
				"last_error": message,
				"next_sync": add_to_date(
					now_datetime(), minutes=max(min(60, 2**attempt), getattr(exc, "retry_minutes", 0))
				),
			},
		)
		if attempt >= 5:
			frappe.db.set_value(CONNECTION, connection_name, "scheduled_sync", 0)
			frappe.db.set_value(LOG, log_name, "finished_at", now_datetime())
		frappe.db.commit()


def schedule_due():
	if not frappe.db.table_exists(CONNECTION):
		return
	from frappe.utils.background_jobs import is_job_enqueued

	for row in frappe.get_all(
		CONNECTION, filters={"enabled": 1}, fields=["name", "active_run", "scheduled_sync", "next_sync"]
	):
		connection = frappe.get_doc(CONNECTION, row.name, for_update=True)
		if connection.next_sync and get_datetime(connection.next_sync) > now_datetime():
			continue
		if connection.active_run:
			log = frappe.get_doc(LOG, connection.active_run)
			if log.status == "Retrying" or (
				get_datetime(log.modified) < add_to_date(now_datetime(), minutes=-5)
				and not is_job_enqueued(log.job_id)
			):
				enqueue_page(connection, log)
		elif connection.scheduled_sync:
			start_sync(connection.name)
	frappe.db.commit()
