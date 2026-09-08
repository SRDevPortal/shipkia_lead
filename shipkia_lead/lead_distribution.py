"""Event-driven, bounded lead assignment. Lead Owner is the slot holder."""

from uuid import uuid4

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

RULE = "Lead Distribution Rule"
BATCH_SIZE = 25
LEASE_SECONDS = 600


def user_changed(doc, method=None):
	if (
		frappe.flags.in_install
		or frappe.flags.in_migrate
		or not frappe.db.table_exists(RULE)
		or not doc.has_value_changed("enabled")
		or not doc.enabled
	):
		return
	for status in frappe.get_all(RULE, filters={"enabled": 1}, pluck="status"):
		request_after_commit(status)


def lead_changed(doc, method=None):
	if frappe.flags.in_erpnext_lead_distribution:
		return
	before = doc.get_doc_before_save()
	if (
		method != "after_delete"
		and before
		and not any(
			doc.has_value_changed(field) for field in ("status", "lead_owner", "disabled", "customer")
		)
	):
		return
	statuses = {doc.status}
	if before:
		statuses.add(before.status)
	for status in sorted(filter(None, statuses)):
		request_after_commit(status)


def request_after_commit(status):
	"""Coalesce imports/multiple saves into one callback per status per transaction."""
	if (
		frappe.flags.in_erpnext_lead_distribution
		or frappe.flags.in_install
		or frappe.flags.in_migrate
		or not frappe.db.table_exists(RULE)
	):
		return
	pending = frappe.flags.erpnext_lead_distribution_statuses
	if pending is None:
		pending = frappe.flags.erpnext_lead_distribution_statuses = set()

		def reset():
			frappe.flags.erpnext_lead_distribution_statuses = None

		frappe.db.after_rollback.add(reset)
		frappe.db.after_commit.add(reset)
	if status in pending:
		return
	pending.add(status)

	def request():
		pending.discard(status)
		try:
			for name in frappe.get_all(RULE, filters={"status": status, "enabled": 1}, pluck="name"):
				queue_rule(name)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title="Lead distribution trigger failed")

	frappe.db.after_commit.add(request)


def queue_rule(name):
	"""DB row serializes requests; only the first trigger creates a job.

	Called after lead transactions commit, so rule -> lead lock order cannot
	deadlock with a status edit. The token is also a fence against stale jobs.
	"""
	rule = frappe.get_doc(RULE, name, for_update=True)
	if not rule.enabled:
		return
	if rule.dispatch_token:
		age = (now_datetime() - get_datetime(rule.dispatch_requested_at)).total_seconds()
		if age < LEASE_SECONDS:
			return
		from frappe.utils.background_jobs import is_job_enqueued

		if is_job_enqueued(rule.dispatch_token):
			return
	_, _, capacity = get_slot_state(rule)
	if not capacity:
		return
	set_next_job(rule)


def set_next_job(rule):
	token = str(uuid4())
	frappe.db.set_value(
		RULE,
		rule.name,
		{
			"dispatch_token": token,
			"dispatch_requested_at": now_datetime(),
			"last_error": "",
		},
		update_modified=False,
	)

	def enqueue():
		try:
			frappe.enqueue(
				"shipkia_lead.lead_distribution.distribute",
				queue="default",
				timeout=120,
				rule_name=rule.name,
				token=token,
				job_id=token,
			)
		except Exception:
			# Preserve a visible, retryable request if Redis is unavailable.
			frappe.db.set_value(
				RULE,
				rule.name,
				"last_error",
				_("Could not queue distribution. Use Retry after the queue is available."),
				update_modified=False,
			)
			frappe.db.commit()
			frappe.log_error(title="Lead distribution enqueue failed")

	frappe.db.after_commit.add(enqueue)


def assignment_conflict():
	if frappe.db.exists("Assignment Rule", {"document_type": "Lead", "disabled": 0}):
		return _("Disable standard Lead Assignment Rules before enabling Lead Distribution.")


def validate_other_assignment(doc, method=None):
	if frappe.flags.in_install or frappe.flags.in_migrate or not frappe.db.table_exists(RULE):
		return
	active = (
		(doc.document_type == "Lead" and not doc.disabled)
		if doc.doctype == "Assignment Rule"
		else doc.enabled
	)
	if active and frappe.db.exists(RULE, {"enabled": 1}):
		frappe.throw(_("Disable Lead Distribution rules before enabling another lead assignment engine."))


def choose_user(users, counts, limit, last_user=None):
	"""Least occupied status bucket, with rotating ties."""
	if last_user in users:
		index = users.index(last_user) + 1
		users = users[index:] + users[:index]
	available = [user for user in users if counts.get(user, 0) < limit]
	return min(available, key=lambda user: counts.get(user, 0)) if available else None


def distribute(rule_name, token):
	try:
		_distribute(rule_name, token)
	except Exception:
		frappe.db.rollback()
		# Do not spin on a bad lead or failed integration hook.
		frappe.db.set_value(
			RULE,
			rule_name,
			"last_error",
			_("Distribution failed. Check Error Log, correct the lead, then use Retry."),
			update_modified=False,
		)
		frappe.db.commit()
		frappe.log_error(title="Lead distribution failed")
		raise


def _distribute(rule_name, token):
	rule = frappe.get_doc(RULE, rule_name, for_update=True)
	if rule.dispatch_token != token:
		return
	frappe.db.set_value(RULE, rule.name, "dispatch_token", None, update_modified=False)
	if not rule.enabled:
		return
	if conflict := assignment_conflict():
		frappe.db.set_value(RULE, rule.name, "last_error", conflict, update_modified=False)
		return
	users, counts, capacity = get_slot_state(rule)
	if not capacity:
		return  # Full slots never scan the waiting lead backlog.
	lead = frappe.qb.DocType("Lead")
	query = (
		frappe.qb.from_(lead)
		.select(lead.name)
		.where(
			(lead.status == rule.status)
			& (lead.disabled == 0)
			& (lead.customer.isnull() | (lead.customer == ""))
			& (lead.lead_owner.isnull() | (lead.lead_owner == ""))
			& (lead._assign.isnull() | (lead._assign == "") | (lead._assign == "[]"))
		)
		.orderby(lead.creation)
		.orderby(lead.name)
		.limit(min(capacity, BATCH_SIZE))
		.for_update()
	)
	waiting = query.run(pluck=True)
	previous_flag = frappe.flags.in_erpnext_lead_distribution
	frappe.flags.in_erpnext_lead_distribution = True
	try:
		for name in waiting:
			user = choose_user(users, counts, rule.slot_limit, rule.last_user)
			if not user:
				break
			doc = frappe.get_doc("Lead", name)
			doc.lead_owner = user
			doc.save(ignore_permissions=True)
			from frappe.desk.form.assign_to import _add

			_add({"doctype": "Lead", "name": name, "assign_to": [user]}, ignore_permissions=True)
			counts[user] = counts.get(user, 0) + 1
			rule.last_user = user
	finally:
		frappe.flags.in_erpnext_lead_distribution = previous_flag
	frappe.db.set_value(
		RULE,
		rule.name,
		{
			"last_user": rule.last_user,
			"last_error": "",
		},
		update_modified=False,
	)
	if len(waiting) == BATCH_SIZE and capacity > len(waiting):
		set_next_job(rule)  # Yield the worker between bounded batches.


def get_slot_state(rule):
	users = (
		frappe.get_all(
			"User",
			filters={
				"name": ["in", [row.user for row in rule.users]],
				"enabled": 1,
				"user_type": "System User",
			},
			pluck="name",
			order_by="name",
		)
		if rule.users
		else []
	)
	if not users:
		return [], {}, 0
	counts = {
		row.lead_owner: row.quantity
		for row in frappe.get_all(
			"Lead",
			filters={
				"status": rule.status,
				"disabled": 0,
				"customer": ["is", "not set"],
				"lead_owner": ["in", users],
			},
			fields=["lead_owner", "count(name) as quantity"],
			group_by="lead_owner",
		)
	}
	capacity = sum(max(0, rule.slot_limit - counts.get(user, 0)) for user in users)
	return users, counts, capacity


def after_migrate():
	frappe.db.add_index("Lead", ["status", "disabled", "lead_owner", "creation"], "lead_distribution_slots")


def todo_changed(doc, method=None):
	if doc.reference_type != "Lead" or not doc.reference_name or frappe.flags.in_erpnext_lead_distribution:
		return
	if method != "after_insert" and not doc.has_value_changed("status"):
		return
	lead = frappe.db.get_value("Lead", doc.reference_name, ["status", "lead_owner"], as_dict=True)
	if not lead:
		return
	if method == "after_insert" and doc.status == "Open":
		frappe.db.set_value("Lead", doc.reference_name, "lead_owner", doc.allocated_to, update_modified=False)
	elif doc.status == "Cancelled" and lead.lead_owner == doc.allocated_to:
		remaining = frappe.get_all(
			"ToDo",
			filters={"reference_type": "Lead", "reference_name": doc.reference_name, "status": "Open"},
			pluck="allocated_to",
			order_by="creation desc",
			limit=1,
		)
		frappe.db.set_value(
			"Lead",
			doc.reference_name,
			"lead_owner",
			remaining[0] if remaining else None,
			update_modified=False,
		)
	request_after_commit(lead.status)


@frappe.whitelist()
def retry_distribution(name: str):
	frappe.only_for(["System Manager", "Sales Manager"])
	doc = frappe.get_doc(RULE, name, for_update=True)
	doc.check_permission("write")
	if not doc.enabled:
		frappe.throw(_("Enable the rule before retrying distribution."))
	if doc.dispatch_token:
		from frappe.utils.background_jobs import is_job_enqueued

		if is_job_enqueued(doc.dispatch_token):
			return {"queued": True}
		frappe.db.set_value(RULE, name, "dispatch_token", None, update_modified=False)
	request_after_commit(doc.status)
	return {"queued": True}
