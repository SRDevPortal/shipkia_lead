"""Derive panel onboarding from the manually recorded ShipKia customer ID."""

import json

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter


def update_status(doc, method=None):
	if not doc.meta.has_field("shipkia_onboarding_status"):
		return
	doc.shipkia_cust_id = (doc.get("shipkia_cust_id") or "").strip() or None
	if doc.meta.has_field("shipkia_identity"):
		from shipkia_lead.shipkia_sync import identity
		doc.shipkia_identity = identity(doc.get("shipkia_connection"), doc.shipkia_cust_id) if doc.shipkia_cust_id else None
		if doc.get_doc_before_save() and not doc.flags.shipkia_sync_apply and any(doc.has_value_changed(field) for field in ("shipkia_cust_id", "shipkia_connection")):
			doc.shipkia_panel_status = None
			doc.shipkia_panel_synced_at = None
	if not doc.shipkia_cust_id:
		doc.shipkia_onboarding_status = "Not Onboarded"
	elif doc.get("shipkia_connection"):
		settings = frappe.db.get_value("ShipKia API Connection", doc.shipkia_connection, ["onboarded_values", "onboarded_on_id"], as_dict=True) or frappe._dict()
		values = settings.onboarded_values or ""
		doc.shipkia_onboarding_status = "Onboarded" if settings.onboarded_on_id or (doc.get("shipkia_panel_status") and doc.shipkia_panel_status in values.splitlines()) else "Signed Up"
	else:
		doc.shipkia_onboarding_status = "Onboarded"


def execute():
	for doctype in ("Lead", "Customer"):
		create_custom_fields({doctype: [{
			"fieldname": "shipkia_onboarding_status",
			"label": "ShipKia Onboarding Status",
			"fieldtype": "Select",
			"options": "Not Onboarded\nOnboarded",
			"default": "Not Onboarded",
			"insert_after": "shipkia_cust_id",
			"read_only": 1,
			"in_standard_filter": 1,
			"in_list_view": 1,
			"description": "Onboarded when a ShipKia CUST ID is entered. This is not a live verification from the ShipKia panel.",
		}]})
		order = frappe.db.get_value("Property Setter", {"doc_type": doctype, "property": "field_order"}, "value")
		if order:
			fields = [name for name in json.loads(order) if name != "shipkia_onboarding_status"]
			fields.insert(fields.index("shipkia_cust_id") + 1, "shipkia_onboarding_status")
			make_property_setter(doctype, None, "field_order", json.dumps(fields), "Data", for_doctype=True)
		for row in frappe.get_all(doctype, fields=["name", "shipkia_cust_id", "shipkia_onboarding_status"]):
			record = frappe.get_doc(doctype, row.name)
			update_status(record)
			status = record.shipkia_onboarding_status
			if row.shipkia_onboarding_status != status:
				frappe.db.set_value(doctype, row.name, "shipkia_onboarding_status", status, update_modified=False)
		frappe.clear_cache(doctype=doctype)
