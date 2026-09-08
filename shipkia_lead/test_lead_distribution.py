"""Transactional integration tests: no existing lead records are changed."""

from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

import frappe

from shipkia_lead import lead_distribution as engine


class TestLeadDistribution(TestCase):
	def setUp(self):
		self.previous_user = frappe.session.user
		frappe.set_user("Administrator")
		self.addCleanup(frappe.db.rollback)
		self.prefix = "slot-test-" + uuid4().hex[:12]
		statuses = frappe.get_meta("Lead").get_field("status").options.splitlines()
		used = set(frappe.get_all(engine.RULE, pluck="status"))
		self.status = next(status for status in statuses if status and status not in used)
		self.next_status = next(status for status in statuses if status and status != self.status)
		self.users = []
		for suffix in ("a", "b"):
			user = frappe.get_doc(
				{
					"doctype": "User",
					"name": f"{self.prefix}-{suffix}@example.invalid",
					"email": f"{self.prefix}-{suffix}@example.invalid",
					"first_name": suffix,
					"enabled": 1,
					"user_type": "System User",
				}
			)
			user.db_insert()
			self.users.append(user.name)
		self.rule = frappe.get_doc(
			{
				"doctype": engine.RULE,
				"name": self.prefix,
				"status": self.status,
				"enabled": 1,
				"slot_limit": 2,
				"dispatch_token": "test",
				"users": [{"user": user} for user in self.users],
			}
		)
		self.rule.db_insert()
		for row in self.rule.users:
			row.db_insert()
		self.leads = []
		for suffix in "ABCDE":
			lead = frappe.get_doc(
				{
					"doctype": "Lead",
					"name": self.prefix + suffix,
					"first_name": "Customer " + suffix,
					"lead_name": "Customer " + suffix,
					"status": self.status,
					"disabled": 0,
					"creation": "1990-01-01 00:00:00",
				}
			)
			lead.db_insert()
			self.leads.append(lead.name)

	def tearDown(self):
		frappe.db.rollback()
		frappe.set_user(self.previous_user)

	def test_capacity_refill_and_assignment(self):
		# Run the actual database distributor and real ToDo assignment.
		engine._distribute(self.rule.name, "test")
		owners = [frappe.db.get_value("Lead", name, "lead_owner") for name in self.leads]
		self.assertEqual(owners[:4], self.users * 2)
		self.assertFalse(owners[4])
		self.assertEqual(engine.get_slot_state(self.rule)[2], 0)
		with patch.object(engine, "set_next_job") as enqueue:
			engine.queue_rule(self.rule.name)
			enqueue.assert_not_called()
		lead = frappe.get_doc("Lead", self.leads[0])
		lead.status = self.next_status
		lead.save(ignore_permissions=True)
		self.assertEqual(engine.get_slot_state(self.rule)[2], 1)
		frappe.db.set_value(engine.RULE, self.rule.name, "dispatch_token", "refill")
		engine._distribute(self.rule.name, "refill")
		self.assertEqual(frappe.db.get_value("Lead", self.leads[4], "lead_owner"), self.users[0])
		self.assertTrue(
			frappe.db.exists(
				"ToDo",
				{
					"reference_type": "Lead",
					"reference_name": self.leads[4],
					"allocated_to": self.users[0],
					"status": "Open",
				},
			)
		)

	def test_disabled_and_stale_job(self):
		engine._distribute(self.rule.name, "wrong-token")
		self.assertFalse(frappe.db.get_value("Lead", self.leads[0], "lead_owner"))
		frappe.db.set_value("Lead", self.leads[0], "disabled", 1)
		engine._distribute(self.rule.name, "test")
		self.assertFalse(frappe.db.get_value("Lead", self.leads[0], "lead_owner"))
		self.assertTrue(frappe.db.get_value("Lead", self.leads[4], "lead_owner"))
