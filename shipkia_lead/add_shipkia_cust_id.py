"""Editable, unique ShipKia account identifier on ERPNext parties."""

import json

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter


def execute():
	for doctype in ("Lead", "Customer"):
		meta = frappe.get_meta(doctype)
		anchor = "shipkia_tab" if doctype == "Lead" and meta.has_field("shipkia_tab") else (
			"lead_owner" if doctype == "Lead" else "customer_name"
		)
		create_custom_fields({doctype: [{
			"fieldname": "shipkia_cust_id",
			"label": "ShipKia CUST ID",
			"fieldtype": "Data",
			"insert_after": anchor,
			"unique": 0 if meta.has_field("shipkia_identity") else 1,
			"search_index": 1,
			"in_standard_filter": 1,
			"description": "Customer ID from the ShipKia panel. Must be unique within this record type. Leave blank until known.",
		}]})
		order = frappe.db.get_value("Property Setter", {"doc_type": doctype, "property": "field_order"}, "value")
		if order:
			fields = [name for name in json.loads(order) if name != "shipkia_cust_id"]
			fields.insert(fields.index(anchor) + 1, "shipkia_cust_id")
			make_property_setter(doctype, None, "field_order", json.dumps(fields), "Data", for_doctype=True)
		frappe.clear_cache(doctype=doctype)
	from shipkia_lead.shipkia_onboarding import execute as setup_onboarding

	setup_onboarding()

	if frappe.db.table_exists("ShipKia API Connection"):
		from shipkia_lead.shipkia_sync_setup import execute as setup_sync

		setup_sync()
