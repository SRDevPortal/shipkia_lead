"""Install CRM account links, scoped uniqueness, and workspace shortcuts."""

import json

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter

from shipkia_lead.shipkia_sync import identity


def execute():
	for doctype in ("Lead", "Customer"):
		fields = [
			{
				"fieldname": "shipkia_connection",
				"label": "ShipKia API Connection",
				"read_only": 1,
				"fieldtype": "Link",
				"options": "ShipKia API Connection",
				"insert_after": "shipkia_cust_id",
				"in_standard_filter": 1,
			},
			{
				"fieldname": "shipkia_panel_status",
				"label": "ShipKia Panel Status",
				"fieldtype": "Data",
				"read_only": 1,
				"insert_after": "shipkia_connection",
				"in_standard_filter": 1,
			},
			{
				"fieldname": "shipkia_panel_synced_at",
				"label": "Last ShipKia Update",
				"fieldtype": "Datetime",
				"read_only": 1,
				"insert_after": "shipkia_panel_status",
			},
			{
				"fieldname": "shipkia_identity",
				"label": "ShipKia Identity",
				"fieldtype": "Data",
				"hidden": 1,
				"read_only": 1,
				"unique": 1,
				"insert_after": "shipkia_panel_synced_at",
			},
		]
		create_custom_fields({doctype: fields})
		for row in frappe.get_all(doctype, fields=["name", "shipkia_connection", "shipkia_cust_id"]):
			key = (
				identity(row.shipkia_connection, row.shipkia_cust_id)
				if (row.shipkia_cust_id or "").strip()
				else None
			)
			frappe.db.set_value(doctype, row.name, "shipkia_identity", key, update_modified=False)
		# Install the new uniqueness constraint before retiring the old ID-only one.
		custom = frappe.get_doc("Custom Field", {"dt": doctype, "fieldname": "shipkia_cust_id"})
		custom.unique = 0
		custom.description = "Unique within the selected ShipKia API connection. Unlinked IDs remain unique among unlinked records."
		custom.save(ignore_permissions=True)
		indexes = frappe.db.sql(f"SHOW INDEX FROM `tab{doctype}`", as_dict=True)
		groups = {}
		for row in indexes:
			if not row.Non_unique:
				groups.setdefault(row.Key_name, []).append(row.Column_name)
		for index, columns in groups.items():
			if columns == ["shipkia_cust_id"]:
				frappe.db.sql(f"ALTER TABLE `tab{doctype}` DROP INDEX `{index.replace('`', '``')}`")
		make_property_setter(
			doctype, "shipkia_onboarding_status", "options", "Not Onboarded\nSigned Up\nOnboarded", "Text"
		)
		order = frappe.db.get_value(
			"Property Setter", {"doc_type": doctype, "property": "field_order"}, "value"
		)
		if order:
			names = [row["fieldname"] for row in fields]
			ordered = [field for field in json.loads(order) if field not in names]
			position = ordered.index("shipkia_cust_id") + 1
			ordered[position:position] = names
			make_property_setter(doctype, None, "field_order", json.dumps(ordered), "Data", for_doctype=True)
		frappe.clear_cache(doctype=doctype)
	workspace = frappe.get_doc("Workspace", "CRM")
	content = frappe.parse_json(workspace.content or "[]")
	changed = False
	for label, target in (
		("ShipKia Connections", "ShipKia API Connection"),
		("ShipKia Customers", "ShipKia Customer"),
		("ShipKia Sync Logs", "ShipKia Sync Log"),
	):
		if not any(row.link_to == target for row in workspace.shortcuts):
			workspace.append(
				"shortcuts", {"type": "DocType", "label": label, "link_to": target, "color": "Blue"}
			)
			content.append(
				{"id": frappe.scrub(target), "type": "shortcut", "data": {"shortcut_name": label, "col": 3}}
			)
			changed = True
	if changed:
		workspace.content = frappe.as_json(content)
		workspace.save(ignore_permissions=True)

	if frappe.db.table_exists("ShipKia Match Queue"):
		from shipkia_lead.shipkia_matching_setup import execute as setup_matching
		setup_matching()
