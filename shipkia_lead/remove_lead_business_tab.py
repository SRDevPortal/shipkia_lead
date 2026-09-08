"""Keep business fields under Others instead of a separate Lead tab."""

import json
from pathlib import Path

import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter
from frappe.utils import now_datetime


def execute():
	meta = frappe.get_meta("Lead")
	section = meta.get_field("organization_section")
	if not section or section.fieldtype != "Tab Break":
		return
	fields = list(meta.fields)
	order = [field.fieldname for field in fields]
	start = order.index("organization_section")
	end = next(
		(index for index in range(start + 1, len(fields)) if fields[index].fieldtype == "Tab Break"),
		len(fields),
	)
	block = order[start:end]
	remaining = [field for field in fields if field.fieldname not in block]
	others = next(index for index, field in enumerate(remaining) if field.fieldname == "contact_info_tab")
	insertion = next(
		(index for index in range(others + 1, len(remaining)) if remaining[index].fieldtype == "Tab Break"),
		len(remaining),
	)
	updated = [field.fieldname for field in remaining]
	updated[insertion:insertion] = block
	assert len(updated) == len(set(updated)) and set(updated) == set(order)
	backup = Path(frappe.get_site_path("private", "files", "erpnext-lead-migration"))
	backup.mkdir(parents=True, exist_ok=True)
	(backup / (now_datetime().strftime("%Y%m%d-%H%M%S-%f") + "-business-tab.json")).write_text(
		frappe.as_json({"field_order": order, "organization_section": section.as_dict()}), encoding="utf-8"
	)
	make_property_setter("Lead", "organization_section", "fieldtype", "Section Break", "Select")
	make_property_setter("Lead", None, "field_order", json.dumps(updated), "Data", for_doctype=True)
	frappe.clear_cache(doctype="Lead")
