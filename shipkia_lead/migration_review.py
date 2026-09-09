"""Read-only Frappe CRM retirement inventory. Never migrates or deletes records."""

import csv
import json
import os
from collections import Counter
from pathlib import Path

import frappe


def execute():
	if "crm" not in frappe.get_installed_apps():
		return {"crm_installed": False, "migration_required": False, "lead_doctype": "Lead"}
	root = Path(frappe.get_site_path("private", "files", "erpnext-lead-migration", "review"))
	root.mkdir(parents=True, exist_ok=True)
	modules = frappe.get_all("Module Def", filters={"app_name": "crm"}, pluck="name")
	doctypes = frappe.get_all(
		"DocType", filters={"module": ["in", modules]}, fields=["name", "issingle", "istable"]
	)
	names = [d.name for d in doctypes]
	counts = {d.name: frappe.db.count(d.name) for d in doctypes if not d.issingle}
	links = []
	for table, parent in [("DocField", "parent"), ("Custom Field", "dt")]:
		for field in frappe.get_all(
			table,
			filters={"fieldtype": "Link", "options": ["in", names]},
			fields=[parent, "fieldname", "options"],
		):
			dt = field[parent]
			if dt in names:
				continue
			meta = frappe.get_meta(dt)
			count = (
				int(bool(frappe.db.get_single_value(dt, field.fieldname)))
				if meta.issingle
				else frappe.db.count(dt, {field.fieldname: ["is", "set"]})
			)
			links.append(
				{"doctype": dt, "field": field.fieldname, "target": field.options, "populated_records": count}
			)
	for field in frappe.get_all(
		"DocField", filters={"fieldtype": "Dynamic Link"}, fields=["parent", "fieldname", "options"]
	):
		if field.parent in names or not field.options:
			continue
		meta = frappe.get_meta(field.parent)
		if (
			meta.issingle
			or not meta.has_field(field.options)
			or not frappe.db.table_exists(field.parent)
			or not frappe.db.has_column(field.parent, field.options)
		):
			continue
		count = frappe.db.count(field.parent, {field.options: ["in", names]})
		if count:
			links.append(
				{
					"doctype": field.parent,
					"field": field.fieldname,
					"target": "Dynamic: Frappe CRM",
					"populated_records": count,
				}
			)
	for dt, type_field, name_field in [
		("File", "attached_to_doctype", "attached_to_name"),
		("Version", "ref_doctype", "docname"),
	]:
		count = frappe.db.count(dt, {type_field: ["in", names]})
		links.append(
			{"doctype": dt, "field": name_field, "target": "Frappe CRM reference", "populated_records": count}
		)
	leads = frappe.get_all(
		"CRM Lead",
		fields=["name", "lead_name", "status", "email", "mobile_no", "phone", "lead_owner"],
		order_by="creation",
	)
	targets = frappe.get_all("Lead", fields=["name", "email_id", "mobile_no", "phone"])

	def normalize(value):
		return "".join(c for c in (value or "") if c.isdigit())

	rows = []
	for lead in leads:
		phones = {normalize(lead.mobile_no), normalize(lead.phone)} - {""}
		matches = [
			t.name
			for t in targets
			if (lead.email and lead.email.lower() == (t.email_id or "").lower())
			or phones.intersection({normalize(t.mobile_no), normalize(t.phone)} - {""})
		]
		rows.append(
			{
				"source_id": lead.name,
				"source_name": lead.lead_name,
				"original_status": lead.status,
				"owner": lead.lead_owner,
				"possible_existing_leads": ";".join(matches),
				"action": "Review possible duplicate" if matches else "Create after status mapping approved",
			}
		)
	with (root / "lead-migration-dry-run.csv").open("w", newline="", encoding="utf-8") as output:
		writer = csv.DictWriter(output, fieldnames=list(rows[0]) if rows else ["source_id"])
		writer.writeheader()
		writer.writerows(rows)
	dependencies = {}
	for app in frappe.get_installed_apps():
		if app in ("crm", "frappe"):
			continue
		app_root = Path(frappe.get_app_path(app))
		files = []
		paths = []
		for folder, dirs, filenames in os.walk(app_root):
			dirs[:] = [
				d for d in dirs if d not in {"node_modules", "dist", "test-results", "__pycache__", ".git"}
			]
			paths.extend(Path(folder) / filename for filename in filenames)
		for path in paths:
			if path.suffix not in (".py", ".js", ".json", ".vue") or any(
				p in {"node_modules", "dist", "test-results", "__pycache__"} for p in path.parts
			):
				continue
			if path.name == "migration_review.py":
				continue
			content = path.read_text(encoding="utf-8", errors="replace")
			if any(
				token in content
				for token in (
					'"CRM Lead"',
					"'CRM Lead'",
					'"CRM Deal"',
					"from crm.",
					"import crm.",
					"/app/frappe-crm",
				)
			):
				files.append(str(path.relative_to(app_root)))
		if files:
			dependencies[app] = files
	result = {
		"record_counts": counts,
		"erpnext_leads": len(targets),
		"lead_statuses": dict(Counter(l.status for l in leads)),
		"possible_duplicate_rows": sum(bool(r["possible_existing_leads"]) for r in rows),
		"external_links": links,
		"code_references_for_review": dependencies,
		"migration_applied": False,
		"uninstall_safe": False,
	}
	(root / "inventory.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
	lines = [
		"# Frappe CRM migration review",
		"",
		"Status: NOT READY TO UNINSTALL. No source records have been migrated or deleted.",
		"",
		"The independent ERPNext slot distributor and lead layout are installed at /app/crm. No ERPNext rule has been enabled automatically.",
		"",
		"## Data to preserve",
		"",
		f"Existing ERPNext leads: {len(targets)}. Frappe CRM leads: {len(leads)}. Possible matching ERPNext leads require review for {result['possible_duplicate_rows']} source rows.",
		"",
		"| Document | Records |",
		"| --- | ---: |",
	]
	lines += [f"| {dt} | {count} |" for dt, count in sorted(counts.items()) if count]
	lines += [
		"",
		"## External document links",
		"",
		"These populated references must be mapped to replacement records before uninstall.",
		"",
		"| Document.field | Target | Populated records |",
		"| --- | --- | ---: |",
	]
	lines += [f"| {l['doctype']}.{l['field']} | {l['target']} | {l['populated_records']} |" for l in links]
	lines += [
		"",
		"## Installed apps needing code review",
		"",
		"Static references include setup scripts and tests as well as runtime code; each needs classification. Full paths are in inventory.json.",
		"",
	]
	lines += [f"- {app}: {len(files)} files" for app, files in dependencies.items()]
	lines += [
		"",
		"## Features removed with the CRM app",
		"",
		"The app also owns Lead Syncing (including Facebook syncing schedules), telephony settings, product synchronization, CRM permissions and custom Contact / Email Template behavior. Review their replacements before uninstall. The uninstall hook removes Email Template custom fields enabled and reference_doctype; preserve values if used.",
		"",
		"## Proposed migration sequence",
		"",
		"1. Keep the database and file backup made before these changes (20260908_131123).",
		"2. Review lead-migration-dry-run.csv. Resolve matching contacts without blindly merging records. Preserve source IDs, original statuses, owners, source details and timestamps.",
		"3. Approve status mapping: New / New Lead / Test Fresh -> Lead; Contacted / Test Contacted -> Open; Nurture -> Open with original status retained. Qualified needs review: qualification alone must not invent an Opportunity. Keep custom status text in Original Frappe CRM Status.",
		"4. Migrate deals to Opportunities with reviewed stage, currency, value and party mappings. Preserve organizations, notes, tasks, calls, attachments, comments, shares and assignment history. Retain status history in an accessible archive if no equivalent ERPNext field exists.",
		"5. Retarget WhatsApp, Meta, Vobiz, payments, duplicate detection and other listed integrations to Lead / Opportunity and the new IDs. Update status/source links and API endpoints. Disable the obsolete assignment engine permanently after cutover.",
		"6. Reconcile counts, field values, assignments and links; test incoming leads and linked integrations. Configure ERPNext distribution rules and change demo users' default app from crm to erpnext.",
		"7. Review the completed reconciliation, take another backup, then uninstall crm and verify /app/crm and dependent apps. Do not force-remove required dependencies.",
		"",
		"## Validation of the ERPNext distributor",
		"",
		"Two database integration tests passed: capacity and refill with real ToDo assignments, no queue request when full, disabled lead exclusion and stale job rejection. Tests rolled back their own fixtures; existing records were retained. Production worker delivery and integration cutover are not yet validated.",
		"",
		"New Lead Owner defaults to empty so unassigned leads can enter distribution. Manual assignments count toward slots but are not blocked by the automatic limit. Distribution reacts to changes and runs bounded batches; there is no polling job.",
		"",
	]
	(root / "review.md").write_text("\n".join(lines), encoding="utf-8")
	print(str(root / "review.md"))
