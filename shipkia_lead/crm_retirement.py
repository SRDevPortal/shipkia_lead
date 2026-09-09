"""Explicit, resumable Frappe CRM data retirement. Never runs from install hooks.

Archive first, map identities second, then rewrite references. Uninstall is a
separate operation after audit() reports no live references to CRM-owned types.
"""
import hashlib
import json
from pathlib import Path

import frappe
from frappe.utils import now_datetime

from shipkia_lead.erpnext_leads import ensure_fields, map_status, status_options

ARCHIVE = "Shipkia Legacy Record"


def key(doctype, name):
    return hashlib.sha256(f"{doctype}\0{name}".encode()).hexdigest()[:32]


def load_snapshot():
    root = Path(frappe.get_site_path("private", "files", "crm-retirement-20260909"))
    return root, json.loads((root / "snapshot.json").read_text())


def _insert_raw(doc):
    """Historical transfer bypasses live assignment, AI, email and provider hooks."""
    doc.owner = doc.owner or "Administrator"
    doc.modified_by = doc.modified_by or doc.owner
    doc.creation = doc.creation or now_datetime()
    doc.modified = doc.modified or doc.creation
    doc.db_insert()


def prepare():
    frappe.reload_doc("shipkia_lead", "doctype", "shipkia_legacy_record", force=True)
    ensure_fields()
    frappe.db.commit()


def archive(snapshot):
    for dt, rows in snapshot["records"].items():
        for row in rows:
            name = str(row.get("name") or dt)
            ident = key(dt, name)
            if frappe.db.exists(ARCHIVE, ident):
                continue
            doc = frappe.get_doc({"doctype": ARCHIVE, "name": ident, "legacy_key": ident,
                "source_doctype": dt, "source_name": name,
                "original_creation": row.get("creation"), "payload": frappe.as_json(row)})
            _insert_raw(doc)
    frappe.db.commit()


def _phone(value):
    from wa_chat_hub.phone_normalization import normalize_phone
    return normalize_phone(value or "")


def _mapped(doctype, name):
    return frappe.db.get_value(ARCHIVE, key(doctype, str(name)), ["target_doctype", "target_name"], as_dict=True)


def migrate_leads(snapshot):
    """Only explicit or unique exact identity matches are reused; ambiguous rows stay distinct."""
    conflicts = []
    meta = frappe.get_meta("Lead")
    for row in snapshot["records"].get("CRM Lead", []):
        old_id = row["name"]
        prior = _mapped("CRM Lead", old_id)
        if prior and prior.target_name:
            continue
        explicit = frappe.db.get_value("Lead", {"legacy_crm_lead": old_id}, "name")
        # A pre-existing chat pair is stronger evidence than a shared phone number.
        if not explicit and frappe.db.has_column("Chat Conversation", "linked_crm_lead"):
            paired = frappe.db.sql("""SELECT DISTINCT contact.linked_lead
                FROM `tabChat Conversation` c JOIN `tabChat Contact` contact ON contact.name=c.contact
                JOIN `tabLead` l ON l.name=contact.linked_lead
                WHERE c.linked_crm_lead=%s AND COALESCE(contact.linked_lead,'')!=''""", old_id)
            if len(paired) == 1:
                explicit = paired[0][0]
        candidates = []
        phones = {_phone(row.get(f)) for f in ("mobile_no", "phone")} - {""}
        email = str(row.get("email") or "").strip().lower()
        source_name = ' '.join(str(row.get('lead_name') or row.get('first_name') or '').lower().split())
        for candidate in frappe.get_all("Lead", fields=["name", "lead_name", "mobile_no", "phone", "email_id", "legacy_crm_lead"], limit_page_length=0):
            if candidate.legacy_crm_lead and candidate.legacy_crm_lead != old_id:
                continue
            matching_phone = bool(phones & ({_phone(candidate.mobile_no), _phone(candidate.phone)} - {""}))
            matching_email = bool(email and email == str(candidate.email_id or "").strip().lower())
            name_matches = bool(source_name and source_name == ' '.join(str(candidate.lead_name or '').lower().split()))
            if name_matches and (matching_phone or matching_email):
                candidates.append(candidate.name)
        target = explicit or (candidates[0] if len(candidates) == 1 else None)
        if len(candidates) > 1 and not explicit:
            conflicts.append({"source": old_id, "candidates": candidates, "action": "preserved_as_separate_lead"})
        values = {}
        for df in meta.fields:
            field = df.fieldname
            if df.fieldtype in {"Table", "Table MultiSelect", "Section Break", "Column Break", "Tab Break", "HTML", "Button"}:
                continue
            if field in row and row[field] not in (None, ""):
                value = row[field]
                if df.fieldtype == "Link" and df.options and not frappe.db.exists(df.options, value):
                    continue  # The complete original value remains in the immutable archive.
                if df.fieldtype == "Select" and df.options and str(value) not in df.options.splitlines():
                    continue
                values[field] = value
        values.update(legacy_crm_lead=old_id, legacy_crm_status=row.get("status"),
            email_id=row.get("email"), company_name=row.get("organization"),
            first_name=row.get("first_name") or row.get("lead_name") or old_id,
            status=map_status(row.get("status")))
        values["lead_name"] = row.get("lead_name") or values["first_name"]
        values["title"] = values["lead_name"]
        if target:
            existing = frappe.get_doc("Lead", target)
            updates = {}
            for field, value in values.items():
                if value in (None, ""):
                    continue
                current = existing.get(field)
                if current in (None, "", 0):
                    updates[field] = value
                elif current != value and field not in {"title", "lead_name", "status"}:
                    conflicts.append({"source": old_id, "target": target, "field": field, "action": "kept_existing_value_original_archived"})
            frappe.db.set_value("Lead", target, updates, update_modified=False)
        else:
            target = "LEAD-CRM-" + hashlib.sha256(old_id.encode()).hexdigest()[:16]
            doc = frappe.get_doc({"doctype": "Lead", "name": target, **values,
                "owner": row.get("owner"), "creation": row.get("creation"),
                "modified": row.get("modified"), "modified_by": row.get("modified_by")})
            _insert_raw(doc)
        frappe.db.set_value(ARCHIVE, key("CRM Lead", old_id), {"target_doctype": "Lead", "target_name": target}, update_modified=False)
        frappe.db.commit()
    return conflicts


def migrate_sources(snapshot):
    for row in snapshot["records"].get("CRM Lead Source", []):
        name = row["name"]
        if not frappe.db.exists("Lead Source", name):
            _insert_raw(frappe.get_doc({"doctype": "Lead Source", "name": name, "source_name": name}))
        frappe.db.set_value(ARCHIVE, key("CRM Lead Source", name), {"target_doctype": "Lead Source", "target_name": name}, update_modified=False)


def target_for(dt, name):
    if not name:
        return None
    mapped = _mapped(dt, name)
    if mapped and mapped.target_name:
        return mapped.target_doctype, mapped.target_name
    ident = key(dt, str(name))
    if dt == "CRM Lead" and not frappe.db.exists(ARCHIVE, ident) and frappe.db.exists("Lead", name):
        return "Lead", name
    if frappe.db.exists(ARCHIVE, ident):
        return ARCHIVE, ident
    raise RuntimeError(f"Unmapped legacy reference: {dt} / {name}")


def retarget_references(snapshot):
    old_types = set(snapshot["doctypes"])
    counts = {}
    # Link fields recorded before source metadata was changed.
    for field in snapshot["links"]:
        dt, fname, old_target = field["doctype"], field["fieldname"], field["target"]
        if not frappe.db.exists("DocType", dt):
            continue
        meta = frappe.get_meta(dt)
        if old_target == "CRM Lead Status":
            new_options, new_kind = "\n" + "\n".join(status_options()), "Select"
        else:
            new_options = {"CRM Lead": "Lead", "CRM Lead Source": "Lead Source"}.get(old_target, ARCHIVE)
            new_kind = "Link"
        # Old column stays available until the new linked_lead column is populated.
        if not meta.issingle and not frappe.db.has_column(dt, fname):
            continue
        rows = ([{"name": dt, fname: frappe.db.get_single_value(dt, fname)}] if meta.issingle else
            frappe.get_all(dt, filters={fname: ["is", "set"]}, fields=["name", fname], limit_page_length=0))
        actual_field = "linked_lead" if dt == "Chat Conversation" and fname == "linked_crm_lead" else fname
        for row in rows:
            value = row.get(fname)
            if not value:
                continue
            mapped_value = map_status(value) if old_target == "CRM Lead Status" else target_for(old_target, value)[1]
            if meta.issingle:
                frappe.db.set_single_value(dt, actual_field, mapped_value)
            else:
                frappe.db.set_value(dt, row["name"], actual_field, mapped_value, update_modified=False)
            counts[dt + "." + actual_field] = counts.get(dt + "." + actual_field, 0) + 1
        # Custom Fields on default apps are changed in the DB, never their source.
        cf = frappe.db.get_value("Custom Field", {"dt": dt, "fieldname": fname}, "name")
        if cf:
            frappe.db.set_value("Custom Field", cf, {"options": new_options, "fieldtype": new_kind})

    definitions = []
    for dt in frappe.get_all("DocType", pluck="name"):
        if dt in old_types or dt == ARCHIVE or not frappe.db.table_exists(dt):
            continue
        meta = frappe.get_meta(dt)
        if meta.issingle:
            continue
        for df in meta.fields:
            if df.fieldtype == "Dynamic Link" and meta.has_field(df.options) and frappe.db.has_column(dt, df.options):
                definitions.append((dt, df.options, df.fieldname))
        for type_field, name_field in [("linked_reference_doctype", "linked_reference_name"),
            ("source_doctype", "source_name"), ("reference_doctype", "reference_name")]:
            if meta.has_field(type_field) and meta.has_field(name_field):
                definitions.append((dt, type_field, name_field))
    definitions += [("File", "attached_to_doctype", "attached_to_name"), ("Version", "ref_doctype", "docname")]
    for dt, type_field, name_field in set(definitions):
        for row in frappe.get_all(dt, filters={type_field: ["in", list(old_types)]}, fields=["name", type_field, name_field], limit_page_length=0):
            if not row.get(name_field):
                # Preserve orphaned history as accessible archive instead of letting
                # the CRM uninstaller delete it by reference_doctype.
                ident = key(dt, row.name)
                if not frappe.db.exists(ARCHIVE, ident):
                    _insert_raw(frappe.get_doc({"doctype": ARCHIVE, "name": ident, "legacy_key": ident,
                        "source_doctype": dt, "source_name": row.name,
                        "payload": frappe.as_json(frappe.get_doc(dt, row.name).as_dict())}))
                frappe.db.set_value(dt, row.name, {type_field: ARCHIVE, name_field: ident}, update_modified=False)
                continue
            mapped_type, mapped_name = target_for(row[type_field], row[name_field])
            frappe.db.set_value(dt, row.name, {type_field: mapped_type, name_field: mapped_name}, update_modified=False)
            counts[dt + "." + name_field] = counts.get(dt + "." + name_field, 0) + 1
    # Canonical conversation/contact links after dynamic references have moved.
    if frappe.db.table_exists("Chat Conversation"):
        for row in frappe.get_all("Chat Conversation", filters={"linked_reference_doctype": "Lead"}, fields=["name", "contact", "linked_reference_name"], limit_page_length=0):
            frappe.db.set_value("Chat Conversation", row.name, "linked_lead", row.linked_reference_name, update_modified=False)
            if row.contact:
                frappe.db.set_value("Chat Contact", row.contact, {"linked_lead": row.linked_reference_name, "source_doctype": "Lead", "source_name": row.linked_reference_name}, update_modified=False)
    frappe.clear_cache()
    frappe.db.commit()
    return counts


def migrate_notes(snapshot):
    for row in snapshot["records"].get("FCRM Note", []):
        if row.get("parenttype") != "CRM Lead":
            continue
        target = target_for("CRM Lead", row.get("parent"))[1]
        name = "crm-note-" + key("FCRM Note", row["name"])
        if frappe.db.exists("CRM Note", name):
            continue
        doc = frappe.get_doc({"doctype": "CRM Note", "name": name, "parent": target,
            "parenttype": "Lead", "parentfield": "notes", "idx": row.get("idx") or 1,
            "note": row.get("note") or row.get("content") or "",
            "added_by": row.get("added_by") or row.get("owner"),
            "added_on": row.get("added_on") or row.get("creation"), "creation": row.get("creation")})
        _insert_raw(doc)


def retarget_configuration():
    # Executable, administrator-owned configuration only; message/audit payloads stay historical.
    types = ["WA AI Policy Bundle", "WA AI Workflow", "WA AI Intent", "WA AI Intent Route", "WA AI Agent Profile",
        "WA AI Department Profile", "WA MCP Tool Endpoint", "WA MCP Server", "WA Chat Hub Settings",
        "WA AI Doctype Permission", "WA AI DocType Permission", "WA AI Doc Type Permission", "WA AI Tool Permission",
        "WA Channel Context", "WA Channel Pipeline Map", "WA Channel Account Prompt Map", "Chat Channel Account",
        "Server Script", "Client Script", "Notification", "Webhook"]
    changed = 0
    def convert(value):
        if isinstance(value, str):
            value = value.replace("CRM Lead Source", "Lead Source")
            if "CRM Lead Status" in value:
                # Any active status-query configuration needs explicit review.
                return value
            return value.replace("CRM Lead", "Lead").replace("linked_crm_lead", "linked_lead").replace("/app/crm-lead", "/app/lead")
        return value
    for dt in types:
        if not frappe.db.exists("DocType", dt):
            continue
        meta = frappe.get_meta(dt)
        dt = meta.name
        if not meta.issingle and not frappe.db.table_exists(dt):
            continue
        rows = [frappe.get_single(dt).as_dict()] if meta.issingle else frappe.get_all(dt, fields=["*"], limit_page_length=0)
        for row in rows:
            updates = {}
            for df in meta.fields:
                value = row.get(df.fieldname)
                if isinstance(value, str) and df.fieldtype not in {"Password", "Attach", "Attach Image"}:
                    new = convert(value)
                    if new != value:
                        updates[df.fieldname] = new
            if updates:
                if meta.issingle:
                    frappe.db.set_single_value(dt, updates)
                else:
                    frappe.db.set_value(dt, row["name"], updates, update_modified=False)
                changed += 1
    # Endpoint names are Link targets used by agent allowlists; keep them in sync.
    old_tool, new_tool = "get_linked_crm_lead_profile", "get_linked_lead_profile"
    if frappe.db.exists("WA MCP Tool Endpoint", old_tool):
        frappe.rename_doc("WA MCP Tool Endpoint", old_tool, new_tool,
            force=True, merge=bool(frappe.db.exists("WA MCP Tool Endpoint", new_tool)))
    # Permissions can collapse to duplicate Lead rows after conversion.
    if frappe.db.exists("DocType", "WA Chat Hub Settings"):
        settings = frappe.get_single("WA Chat Hub Settings")
        rows = settings.get("ai_doctype_permissions") or []
        seen = set()
        kept = []
        for row in rows:
            dt = row.doctype_name
            if dt == "CRM Lead Status" or dt in seen:
                frappe.db.delete(row.doctype, {"name": row.name})
                continue
            seen.add(dt)
            kept.append(row)
        from wa_chat_hub.security import ensure_default_ai_doctype_permissions
        ensure_default_ai_doctype_permissions()
    if frappe.db.exists("DocType", "New Assignement System Settings"):
        frappe.db.set_single_value("New Assignement System Settings", "enabled", 0)
    for user in frappe.get_all("User", filters={"default_app": "crm"}, pluck="name"):
        frappe.db.set_value("User", user, "default_app", "erpnext")
    frappe.clear_cache()
    frappe.db.commit()
    return changed


def run():
    if frappe.session.user != "Administrator":
        frappe.throw("Administrator required", frappe.PermissionError)
    root, snapshot = load_snapshot()
    prepare()
    archive(snapshot)
    migrate_sources(snapshot)
    conflicts = migrate_leads(snapshot)
    # Sync only custom-app schemas; no upstream file changes.
    from frappe.model.sync import sync_for
    for app in ["wa_chat_hub", "vobiz_ai", "vobiz_click_to_call", "meta_comment_ai", "payment_orchestrator", "new_assignement_system"]:
        if app in frappe.get_installed_apps():
            sync_for(app, force=True)
    frappe.clear_cache()
    references = retarget_references(snapshot)
    migrate_notes(snapshot)
    configuration = retarget_configuration()
    frappe.db.commit()
    report = {"mapped_leads": frappe.db.count(ARCHIVE, {"source_doctype": "CRM Lead", "target_doctype": "Lead"}),
        "source_leads": len(snapshot["records"].get("CRM Lead", [])), "archived_records": frappe.db.count(ARCHIVE),
        "references": references, "configuration_records": configuration, "conflicts": conflicts,
        "crm_uninstalled": "crm" not in frappe.get_installed_apps()}
    (root / "migration-report.json").write_text(frappe.as_json(report))
    return {k: v for k,v in report.items() if k != "conflicts"} | {"preserved_conflicts": len(conflicts)}


def audit():
    """Read-only gates for removing CRM; reports counts and identifiers, no payloads."""
    _, snapshot = load_snapshot()
    old_types = set(snapshot["doctypes"])
    blockers = []
    for dt in frappe.get_all("DocType", pluck="name"):
        if dt in old_types or dt == ARCHIVE:
            continue
        meta = frappe.get_meta(dt)
        if not meta.issingle and not frappe.db.table_exists(dt):
            continue
        for df in meta.fields:
            if df.fieldtype in {"Link", "Table", "Table MultiSelect"} and df.options in old_types:
                blockers.append({"kind": "schema", "doctype": dt, "field": df.fieldname, "target": df.options})
            if df.fieldtype == "Dynamic Link" and meta.has_field(df.options) and not meta.issingle:
                count = frappe.db.count(dt, {df.options: ["in", list(old_types)]})
                if count:
                    blockers.append({"kind": "dynamic_reference", "doctype": dt, "field": df.fieldname, "count": count})
        if not meta.issingle:
            for field in ("linked_reference_doctype", "source_doctype", "reference_doctype"):
                if meta.has_field(field):
                    count = frappe.db.count(dt, {field: ["in", list(old_types)]})
                    if count:
                        blockers.append({"kind": "typed_reference", "doctype": dt, "field": field, "count": count})
    for app in frappe.get_installed_apps():
        if app != 'crm' and 'crm' in frappe.get_hooks('required_apps', app_name=app):
            blockers.append({"kind": "required_app", "app": app})
    for dt, field in [("Server Script", "script"), ("Client Script", "script"), ("WA MCP Tool Endpoint", "endpoint_url")]:
        if not frappe.db.exists("DocType", dt) or not frappe.get_meta(dt).has_field(field):
            continue
        fields = ['name', field] + (["disabled"] if frappe.get_meta(dt).has_field("disabled") else [])
        for row in frappe.get_all(dt, fields=fields, limit_page_length=0):
            if row.get('disabled'):
                continue
            value = str(row.get(field) or '')
            if 'crm.api.' in value or 'crm.fcrm.' in value or '"CRM Lead"' in value or "'CRM Lead'" in value:
                blockers.append({"kind": "configuration", "doctype": dt, "name": row.name})
    mapped = frappe.db.count(ARCHIVE, {"source_doctype": "CRM Lead", "target_doctype": "Lead"})
    if mapped != len(snapshot['records']['CRM Lead']):
        blockers.append({"kind": "lead_count", "mapped": mapped})
    return {"blockers": blockers, "mapped_leads": mapped, "archived_records": frappe.db.count(ARCHIVE), "crm_installed": 'crm' in frappe.get_installed_apps()}
