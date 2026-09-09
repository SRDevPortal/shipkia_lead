"""Lead ownership visibility shared by Desk, WhatsApp and custom integrations."""
import frappe


def unrestricted(user=None):
    user = user or frappe.session.user
    return user == "Administrator" or bool(
        {"System Manager", "Sales Manager"}.intersection(frappe.get_roles(user))
    )


def lead_query(user=None):
    user = user or frappe.session.user
    if unrestricted(user):
        return ""
    value = frappe.db.escape(user)
    return f"""(`tabLead`.lead_owner = {value} OR `tabLead`.owner = {value}
        OR EXISTS (SELECT 1 FROM `tabDocShare` s WHERE s.share_doctype='Lead'
            AND s.share_name=`tabLead`.name AND s.`read`=1 AND (s.user={value} OR s.everyone=1))
        OR EXISTS (SELECT 1 FROM `tabToDo` t WHERE t.reference_type='Lead'
            AND t.reference_name=`tabLead`.name AND t.allocated_to={value} AND t.status='Open'))"""


def lead_permission(doc, user=None, ptype=None):
    if unrestricted(user):
        return None
    user = user or frappe.session.user
    if doc.is_new() or user in (doc.owner, doc.lead_owner):
        return None
    if frappe.db.exists("DocShare", {"share_doctype": "Lead", "share_name": doc.name, "user": user, "read": 1}):
        return None
    if frappe.db.exists("DocShare", {"share_doctype": "Lead", "share_name": doc.name, "everyone": 1, "read": 1}):
        return None
    if frappe.db.exists("ToDo", {"reference_type": "Lead", "reference_name": doc.name, "allocated_to": user, "status": "Open"}):
        return None
    return False
