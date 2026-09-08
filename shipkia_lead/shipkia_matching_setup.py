import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from shipkia_lead.shipkia_matching import key_fields, set_keys


def execute():
	for doctype in ("Lead", "Customer", "ShipKia Customer"):
		meta = frappe.get_meta(doctype)
		fields = [
			{"fieldname": target, "label": target, "fieldtype": "Data", "hidden": 1, "read_only": 1}
			for source, target, _ in key_fields(doctype)
			if meta.has_field(source)
		]
		create_custom_fields({doctype: fields})
		connection = "connection" if doctype == "ShipKia Customer" else "shipkia_connection"
		for field in fields:
			frappe.db.add_index(doctype, [connection, field["fieldname"]], field["fieldname"] + "_lookup")
		# One-time migration, paged to bound memory. No matching in the migration.
		last = ""
		while True:
			names = frappe.get_all(
				doctype, filters={"name": [">", last]}, pluck="name", order_by="name", limit=500
			)
			if not names:
				break
			for name in names:
				doc = frappe.get_doc(doctype, name)
				set_keys(doc)
				frappe.db.set_value(
					doctype,
					name,
					{field["fieldname"]: doc.get(field["fieldname"]) for field in fields},
					update_modified=False,
				)
			last = names[-1]
	frappe.db.add_index("ShipKia Match Queue", ["status", "due_at", "requested_at"], "matching_due")
	from shipkia_lead.shipkia_unmatched_setup import execute as setup_unmatched

	setup_unmatched()
	from shipkia_lead.shipkia_active_connection import execute as setup_active

	setup_active()
	frappe.clear_cache()
