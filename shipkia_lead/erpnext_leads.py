"""ERPNext-only schema shared by custom lead integrations."""
import json
from pathlib import Path

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def ensure_fields():
    path = Path(__file__).parent / "config" / "erpnext_lead_fields.json"
    definitions = json.loads(path.read_text()) if path.exists() else []
    meta = frappe.get_meta("Lead")
    missing = [df for df in definitions if not meta.has_field(df["fieldname"])
        or (not frappe.db.has_column("Lead", df["fieldname"]) and frappe.db.exists("Custom Field", {"dt": "Lead", "fieldname": df["fieldname"]}))]
    # Clean up only metadata from an interrupted schema addition, never stored columns.
    for df in frappe.get_all("Custom Field", filters={"dt": "Lead", "module": "Shipkia Lead"}, fields=["name", "fieldname"]):
        if df.fieldname not in {d["fieldname"] for d in definitions} and not frappe.db.has_column("Lead", df.fieldname):
            frappe.db.delete("Custom Field", {"name": df.name})
    if missing:
        create_custom_fields({"Lead": missing}, update=True)


def sync_compatibility_fields(doc, method=None):
    """Keep older custom integration field names aligned with ERPNext names."""
    for canonical, alias in (("email_id", "email"), ("company_name", "organization")):
        if not doc.meta.has_field(alias):
            continue
        before = doc.get_doc_before_save()
        alias_changed = before and doc.get(alias) != before.get(alias)
        canonical_changed = before and doc.get(canonical) != before.get(canonical)
        if alias_changed and not canonical_changed:
            doc.set(canonical, doc.get(alias))
        elif doc.get(canonical):
            doc.set(alias, doc.get(canonical))
        elif doc.get(alias):
            doc.set(canonical, doc.get(alias))


def status_options():
    return [s for s in str(frappe.get_meta("Lead").get_field("status").options or "").splitlines() if s]


def map_status(value):
    """Preserve the original separately; only emit valid ERPNext statuses."""
    value = str(value or "").strip()
    if value in status_options():
        return value
    lower = value.lower()
    if any(word in lower for word in ("contacted", "nurture", "qualified", "follow")):
        return "Open"
    if any(word in lower for word in ("lost", "unqualified", "not interested")):
        return "Do Not Contact"
    return "Lead"
