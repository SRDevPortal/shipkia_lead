import frappe


def execute():
	frappe.db.add_index("ShipKia Match Queue", ["status", "result(32)", "due_at"], "unmatched_due")
