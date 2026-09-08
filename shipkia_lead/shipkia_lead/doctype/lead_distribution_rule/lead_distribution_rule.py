import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from shipkia_lead.lead_distribution import assignment_conflict, request_after_commit


class LeadDistributionRule(Document):
	def validate(self):
		options = frappe.get_meta("Lead").get_field("status").options.split("\n")
		if self.status not in options or self.status in ("Converted", "Do Not Contact"):
			frappe.throw(_("Select an active ERPNext Lead status."))
		if cint(self.slot_limit) < 1:
			frappe.throw(_("Slots per User must be a positive integer."))
		seen = set()
		for row in self.users:
			if row.user in seen:
				frappe.throw(_("Each user can only be added once."))
			seen.add(row.user)
			user = frappe.db.get_value("User", row.user, ["enabled", "user_type"], as_dict=True)
			if (
				not user
				or not user.enabled
				or user.user_type != "System User"
				or row.user in ("Guest", "Administrator")
			):
				frappe.throw(_("Select enabled system users other than Administrator."))
			if not set(frappe.get_roles(row.user)) & {"Sales User", "Sales Manager", "System Manager"}:
				frappe.throw(_("User {0} needs a CRM sales role.").format(row.user))
		if self.enabled and not seen:
			frappe.throw(_("Add at least one user before enabling distribution."))
		if self.enabled and (conflict := assignment_conflict()):
			frappe.throw(conflict)
		if not self.is_new() and self.has_value_changed("status"):
			frappe.throw(_("Create a separate rule to distribute another status."))

	def on_update(self):
		if self.enabled:
			request_after_commit(self.status)
