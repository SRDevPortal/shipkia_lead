"""Install custom fields on fresh sites; preserve existing integration records."""
import frappe


def after_install():
    # Existing DocTypes may lack a JSON modified timestamp, so force their ownership transfer.
    for name in ("lead_distribution_rule", "lead_distribution_user", "shipkia_api_connection", "shipkia_customer", "shipkia_match_queue", "shipkia_sync_log"):
        frappe.reload_doc("shipkia_lead", "doctype", name, force=True)
    frappe.clear_cache()
    after_migrate()


def ensure_core_setup():
    # A failed install may have written identity fields but not finished setup.
    required = {"Lead": ("shipkia_tab", "shipkia_cust_id", "shipkia_connection", "shipkia_identity",
        "shipkia_onboarding_status", "sk_phone_0", "sk_business_0", "source_medium", "source_campaign_name"),
        "Customer": ("shipkia_cust_id", "shipkia_connection", "shipkia_identity", "shipkia_onboarding_status"),
        "ShipKia Customer": ("sk_phone_key", "sk_business_key")}
    complete = all(frappe.get_meta(dt).has_field(field) and frappe.db.has_column(dt, field)
        for dt, fields in required.items() for field in fields if field != "shipkia_tab")
    complete = complete and frappe.get_meta("Lead").has_field("shipkia_tab")
    if not complete:
        from shipkia_lead.lead_distribution_setup import execute
        developer = frappe.conf.developer_mode
        frappe.conf.developer_mode = 0
        try:
            execute()
        finally:
            frappe.conf.developer_mode = developer


def after_migrate():
    ensure_core_setup()
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
