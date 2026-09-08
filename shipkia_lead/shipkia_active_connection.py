"""Site-wide environment selection; existing customer identities retain provenance."""

import frappe


def active_connection():
	if not frappe.db.table_exists("ShipKia API Connection"):
		return None
	names = frappe.get_all("ShipKia API Connection", filters={"enabled": 1}, pluck="name", limit=2)
	if len(names) > 1:
		frappe.throw("Only one ShipKia connection may be active.")
	return names[0] if names else None


def assign_connection(doc, method=None):
	if not doc.meta.has_field("shipkia_connection"):
		return
	# Never reinterpret a recorded account ID as belonging to another environment.
	before = doc.get_doc_before_save()
	if doc.get("shipkia_cust_id") and not doc.is_new() and not (before and not before.get("shipkia_cust_id")):
		return
	active = active_connection()
	if active:
		doc.shipkia_connection = active


def queue_unlinked():
	"""Adopt historical unlinked CRM records gradually, including after switching environments."""
	from shipkia_lead.shipkia_matching import put
	active = active_connection()
	if not active:
		return
	remaining = 100
	for doctype in ("Lead", "Customer"):
		rows = frappe.db.sql(
			f"""SELECT d.name FROM `tab{doctype}` d
			LEFT JOIN `tabShipKia Match Queue` q
			ON q.reference_doctype=%s AND q.reference_name=d.name
			WHERE (d.shipkia_cust_id IS NULL OR d.shipkia_cust_id='')
			AND (d.shipkia_connection IS NULL OR d.shipkia_connection='' OR d.shipkia_connection!=%s)
			AND (q.name IS NULL OR q.status='Done')
			ORDER BY d.name LIMIT %s""", (doctype, active, remaining), as_dict=True)
		for row in rows:
			put(doctype, row.name)
		remaining -= len(rows)
		if not remaining:
			break


def execute():
	from frappe.custom.doctype.property_setter.property_setter import make_property_setter
	active = active_connection()
	if active:
		frappe.db.set_value("ShipKia API Connection", active, "active_slot", "active", update_modified=False)
	for doctype in ("Lead", "Customer"):
		make_property_setter(doctype, "shipkia_connection", "read_only", 1, "Check")
		make_property_setter(doctype, "shipkia_connection", "description", "Set automatically from the active site connection. Existing ShipKia IDs retain their original environment.", "Small Text")
		frappe.db.add_index(doctype, ["shipkia_cust_id", "shipkia_connection"], "shipkia_unlinked")
	frappe.db.add_index("ShipKia Match Queue", ["reference_doctype", "reference_name"], "source_document")
	frappe.clear_cache()
