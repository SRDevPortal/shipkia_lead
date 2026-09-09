"""Install custom fields on fresh sites; preserve existing integration records."""
import frappe


def after_install():
    # Existing DocTypes may lack a JSON modified timestamp, so force their ownership transfer.
    for name in ("lead_distribution_rule", "lead_distribution_user", "shipkia_api_connection", "shipkia_customer", "shipkia_match_queue", "shipkia_sync_log"):
        frappe.reload_doc("shipkia_lead", "doctype", name, force=True)
    frappe.clear_cache()
    if not frappe.get_meta("Lead").has_field("shipkia_identity"):
        from shipkia_lead.lead_distribution_setup import execute
        developer = frappe.conf.developer_mode
        frappe.conf.developer_mode = 0
        try:
            execute()
        finally:
            frappe.conf.developer_mode = developer
    after_migrate()


def after_migrate():
    from shipkia_lead.erpnext_leads import ensure_fields
    ensure_fields()
    from shipkia_lead.lead_distribution import after_migrate as indexes
    indexes()
    # Restore app shortcuts after upstream ERPNext synchronizes its CRM workspace.
    workspace = frappe.get_doc("Workspace", "CRM")
    content = frappe.parse_json(workspace.content or "[]")
    changed = False
    for label, target in [("Lead Distribution", "Lead Distribution Rule"), ("ShipKia Connections", "ShipKia API Connection"), ("ShipKia Customers", "ShipKia Customer"), ("ShipKia Sync Logs", "ShipKia Sync Log"), ("ShipKia Matching Queue", "ShipKia Match Queue"), ("Legacy CRM Archive", "Shipkia Legacy Record")]:
        if not any(row.link_to == target for row in workspace.shortcuts):
            workspace.append("shortcuts", {"type": "DocType", "label": label, "link_to": target, "color": "Blue"})
            content.append({"id": frappe.scrub(target), "type": "shortcut", "data": {"shortcut_name": label, "col": 3}})
            changed = True
    if changed:
        workspace.content = frappe.as_json(content)
        developer = frappe.conf.developer_mode
        frappe.conf.developer_mode = 0
        try:
            workspace.save(ignore_permissions=True)
        finally:
            frappe.conf.developer_mode = developer
    frappe.clear_cache()
