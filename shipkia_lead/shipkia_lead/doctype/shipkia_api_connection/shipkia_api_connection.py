import frappe
from frappe.model.document import Document

from shipkia_lead.shipkia_sync import validate_connection


class ShipKiaAPIConnection(Document):
	def validate(self):
		validate_connection(self)
		self.active_slot = "active" if self.enabled else None
		if self.enabled and frappe.db.exists("ShipKia API Connection", {"enabled": 1, "name": ["!=", self.name]}):
			frappe.throw("Disable the current active ShipKia connection before activating another environment.")
		before = self.get_doc_before_save()
		if before and (before.active_run or frappe.db.exists("ShipKia Customer", {"connection": self.name})):
			for field in (
				"environment",
				"endpoint",
				"http_method",
				"allow_test_http",
				"records_path",
				"field_mapping",
				"pagination_mode",
				"page_parameter",
				"size_parameter",
				"page_size",
				"first_page",
				"updated_since_parameter",
			):
				if self.has_value_changed(field):
					frappe.throw(
						"Create a new connection to change dataset or mapping after importing. This preserves customer identity and checkpoints."
					)
