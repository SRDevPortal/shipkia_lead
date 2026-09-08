"""Opt-in live check of the demo ERPNext rule. Keeps all test records and logs."""

import json
import time
from pathlib import Path

import frappe
from frappe.utils import now_datetime

from shipkia_lead.lead_distribution import get_slot_state


def execute():
	frappe.only_for("System Manager")
	rule = frappe.get_doc("Lead Distribution Rule", "Lead")
	users, counts, capacity = get_slot_state(rule)
	assert rule.enabled and rule.slot_limit == 5 and len(users) == 2 and capacity == 10
	assert all(user.startswith("demo-20260908-061223-") for user in users)
	assert not frappe.db.count("Lead", {"status": "Lead", "disabled": 0, "lead_owner": ["is", "not set"]})
	original = {row.name: row.lead_owner for row in frappe.get_all("Lead", fields=["name", "lead_owner"])}
	run = now_datetime().strftime("%Y%m%d-%H%M%S")
	root = Path(frappe.get_site_path("private", "files", "lead-distribution-check", run))
	root.mkdir(parents=True, exist_ok=True)
	created = []
	events = []

	def log(event, **data):
		entry = {"time": str(now_datetime()), "event": event, **data}
		events.append(entry)
		with (root / "events.jsonl").open("a", encoding="utf-8") as output:
			output.write(json.dumps(entry, default=str) + "\n")
		print(json.dumps(entry, default=str), flush=True)

	def snapshot():
		frappe.db.rollback()  # Release read snapshots so committed worker results are visible.
		return frappe.get_all("Lead", filters={"name": ["in", created]},
			fields=["name", "lead_name", "status", "lead_owner", "_assign"], order_by="creation, name")

	def wait_for_assigned(quantity):
		deadline = time.monotonic() + 40
		while time.monotonic() < deadline:
			rows = snapshot()
			if sum(bool(row.lead_owner) for row in rows) == quantity:
				return rows
			time.sleep(1)
		raise AssertionError(f"Worker did not assign {quantity} leads within 40 seconds")

	passed = False
	try:
		log("baseline", users=users, slots_per_user=5, existing_leads=len(original))
		for letter in "ABCDEFGHIJK":
			doc = frappe.get_doc({"doctype": "Lead", "first_name": f"Slot Check {run} Customer {letter}",
				"status": "Lead", "lead_owner": "", "company": frappe.defaults.get_global_default("company")})
			doc.insert()
			created.append(doc.name)
		(root / "manifest.json").write_text(json.dumps({"leads": created, "existing_owners": original}, indent=2), encoding="utf-8")
		frappe.db.commit()  # Real hooks -> Redis -> normal worker; no direct distributor invocation.
		log("created", leads=created)
		rows = wait_for_assigned(10)
		counts = {user: sum(row.lead_owner == user for row in rows) for user in users}
		assert all(count == 5 for count in counts.values()), counts
		waiting = [row.name for row in rows if not row.lead_owner]
		assert waiting == [created[-1]], waiting
		log("full_slots", counts=counts, waiting=waiting, rows=rows)
		before_request = frappe.db.get_value("Lead Distribution Rule", "Lead", "dispatch_requested_at")
		doc = frappe.get_doc("Lead", waiting[0])
		doc.job_title = "Unrelated field edit check"
		doc.save()
		frappe.db.commit()
		assert frappe.db.get_value("Lead Distribution Rule", "Lead", "dispatch_requested_at") == before_request
		log("unrelated_edit", result="No new dispatch request; waiting lead remains unassigned")
		released = next(row for row in rows if row.lead_owner)
		doc = frappe.get_doc("Lead", released.name)
		doc.status = "Open"
		doc.save()
		frappe.db.commit()
		log("status_changed", lead=doc.name, old_status="Lead", new_status="Open", owner=released.lead_owner)
		rows = wait_for_assigned(11)
		refilled = next(row for row in rows if row.name == waiting[0])
		assert refilled.lead_owner == released.lead_owner
		counts = {user: sum(row.lead_owner == user and row.status == "Lead" for row in rows) for user in users}
		assert all(count == 5 for count in counts.values())
		for row in rows:
			assert frappe.db.exists("ToDo", {"reference_type": "Lead", "reference_name": row.name,
				"allocated_to": row.lead_owner, "status": "Open"})
		for name, owner in original.items():
			assert frappe.db.get_value("Lead", name, "lead_owner") == owner
		assert not frappe.db.get_value("Lead Distribution Rule", "Lead", "last_error")
		log("refill_verified", waiting_lead=refilled.name, assigned_to=refilled.lead_owner,
			counts=counts, assignment_records_verified=11, original_owners_unchanged=True)
		passed = True
	except Exception:
		frappe.db.rollback()
		log("failed", traceback=frappe.get_traceback())
		raise
	finally:
		rows = snapshot() if created else []
		(root / "final-leads.json").write_text(json.dumps(rows, default=str, indent=2), encoding="utf-8")
		lines = ["# ERPNext lead distribution live check", "", f"Result: {'PASS' if passed else 'FAIL'}", "",
			"Used the normal after-commit hook, Redis queue and worker. No direct distributor call.",
			"All test records retained. Test leads occupy the demo agents' slots after this run.", "",
			"| Lead | Customer | Status | Lead Owner |", "| --- | --- | --- | --- |"]
		lines += [f"| {row.name} | {row.lead_name} | {row.status} | {row.lead_owner or 'Waiting'} |" for row in rows]
		lines += ["", "See events.jsonl for timestamped creation, slot-limit, status-change and refill evidence."]
		(root / "report.md").write_text("\n".join(lines), encoding="utf-8")
		print("REPORT: " + str(root / "report.md"), flush=True)
