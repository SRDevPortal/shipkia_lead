from unittest.mock import patch

import frappe

from shipkia_lead import shipkia_matching as matching
from shipkia_lead import shipkia_sync as sync
from shipkia_lead.test_shipkia_sync import TestShipKiaSync


class TestShipKiaMatching(TestShipKiaSync):
	def test_api_import_defers_matching(self):
		self.connection.apply_updates = 1
		self.connection.save()
		with patch.object(sync, "enqueue_page"), patch.object(frappe.db, "commit"):
			name = sync.start_sync(self.connection.name)
			with (
				patch.object(sync, "fetch_page", return_value=([{"id": "deferred"}], False)),
				patch.object(
					sync, "apply_record", side_effect=AssertionError("Inline matching is forbidden")
				),
			):
				sync.run_batch(self.connection.name, name)
			self.assertEqual(frappe.db.get_value(sync.LOG, name, "status"), "Completed")
			self.assertTrue(
				frappe.db.exists(
					matching.QUEUE,
					{
						"reference_doctype": sync.CUSTOMER,
						"reference_name": sync.identity(self.connection.name, "deferred"),
						"status": "Pending",
					},
				)
			)

	def test_worker_batch_limit_and_save_does_not_enqueue_immediately(self):
		from unittest.mock import MagicMock

		with patch.object(frappe, "enqueue") as enqueue:
			for i in range(120):
				matching.put("Lead", self.prefix + str(i))
			enqueue.assert_not_called()
		self.assertEqual(len(matching.due()), 100)
		original_cache_get = frappe.cache.get_value
		lock = MagicMock()
		lock.acquire.return_value = True
		with (
			patch.object(frappe.cache, "lock", return_value=lock),
			patch.object(
				frappe.cache,
				"get_value",
				side_effect=lambda key, *args, **kwargs: (
					"other-job" if key == matching.JOB_KEY else original_cache_get(key, *args, **kwargs)
				),
			),
			patch.object(matching, "kick"),
			patch.object(frappe.db, "commit"),
			patch.object(matching, "process", return_value="Unmatched") as process,
		):
			matching.drain("test-job")
			self.assertEqual(process.call_count, 100)
		self.assertEqual(
			frappe.db.count(
				matching.QUEUE, {"reference_name": ["like", self.prefix + "%"], "status": "Pending"}
			),
			20,
		)

	def test_new_lead_matches_local_customer_without_api(self):
		self.connection.apply_updates = 1
		self.connection.onboarded_on_id = 1
		self.connection.save()
		sync.store_customer(self.connection, {"id": "local", "phone": "447700900009"})
		lead = self.lead("local", phone="+44 7700-900009")
		row = frappe.get_all(
			matching.QUEUE, filters={"reference_doctype": "Lead", "reference_name": lead.name}, fields=["*"]
		)[0]
		with (
			patch.object(sync.requests, "get", side_effect=AssertionError("No API calls allowed")),
			patch.object(sync.requests, "post", side_effect=AssertionError("No API calls allowed")),
		):
			self.assertEqual(matching.process(row), "Applied")
			matching.finish(row, "Applied")
		lead.reload()
		self.assertEqual((lead.shipkia_cust_id, lead.shipkia_onboarding_status), ("local", "Onboarded"))
		self.assertEqual(frappe.db.get_value(matching.QUEUE, row.name, "status"), "Done")

	def test_coalescing_and_new_generation_not_lost(self):
		name = matching.put("Lead", self.prefix)
		for _ in range(50):
			self.assertEqual(matching.put("Lead", self.prefix), name)
		row = frappe.get_doc(matching.QUEUE, name)
		self.assertEqual(row.generation, 51)
		matching.put("Lead", self.prefix)
		matching.finish(row, "Old result")
		self.assertEqual(frappe.db.get_value(matching.QUEUE, name, "status"), "Pending")

	def test_retry_is_persistent_and_eventually_failed(self):
		name = matching.put("Lead", self.prefix)
		for attempt in range(1, 6):
			matching.finish(frappe.get_doc(matching.QUEUE, name), error=True)
			row = frappe.get_doc(matching.QUEUE, name)
			self.assertEqual(row.attempts, attempt)
			self.assertEqual(row.status, "Failed" if attempt == 5 else "Retrying")
		matching.retry(name)
		self.assertEqual(frappe.db.get_value(matching.QUEUE, name, "status"), "Pending")

	def test_unrelated_lead_edit_does_not_requeue(self):
		lead = self.lead("quiet")
		row = frappe.get_all(
			matching.QUEUE,
			filters={"reference_doctype": "Lead", "reference_name": lead.name},
			fields=["name", "generation"],
		)[0]
		lead.reload()
		lead.job_title = "Unrelated edit"
		lead.save()
		self.assertEqual(frappe.db.get_value(matching.QUEUE, row.name, "generation"), row.generation)

	def test_indexed_candidates_do_not_normalize_in_sql(self):
		lead = self.lead("indexed", phone="+447700900005")
		name, _ = sync.store_customer(self.connection, {"id": "indexed", "phone": "447700900005"})
		original = frappe.db.sql
		queries = []

		def sql(query, *args, **kwargs):
			queries.append(str(query))
			return original(query, *args, **kwargs)

		with patch.object(frappe.db, "sql", side_effect=sql):
			self.assertEqual(sync.candidates(frappe.get_doc(sync.CUSTOMER, name), "Lead"), [lead.name])
		self.assertFalse(any("REGEXP_REPLACE" in query or "LOWER(TRIM" in query for query in queries))

	def test_manual_sync_permissions_and_validation(self):
		lead = self.lead("manual")
		self.assertEqual(matching.sync_leads([lead.name, lead.name]), {"queued": 1})
		with self.assertRaises(frappe.ValidationError):
			matching.sync_leads([lead.name] * 101)
		with patch.object(type(lead), "check_permission", side_effect=frappe.PermissionError):
			with self.assertRaises(frappe.PermissionError):
				matching.sync_leads([lead.name])
		self.connection.enabled = 0
		self.connection.save()
		with self.assertRaises(frappe.ValidationError):
			matching.sync_leads([lead.name])

	def test_unmatched_due_recheck_then_applies_without_api(self):
		from frappe.utils import add_to_date, now_datetime
		self.connection.apply_updates = 1
		self.connection.onboarded_on_id = 1
		self.connection.save()
		lead = self.lead("recheck", phone="447700900088")
		name = matching.put("Lead", lead.name)
		row = frappe.get_doc(matching.QUEUE, name)
		self.assertEqual(matching.process(row), "Unmatched")
		matching.finish(row, "Unmatched")
		matching.recheck_unmatched()
		self.assertEqual(frappe.db.get_value(matching.QUEUE, name, "status"), "Done")
		frappe.db.set_value(matching.QUEUE, name, "due_at", add_to_date(now_datetime(), minutes=-1))
		with patch.object(sync.requests, "post", side_effect=AssertionError("No API")):
			matching.recheck_unmatched()
			self.assertEqual(frappe.db.get_value(matching.QUEUE, name, "status"), "Pending")
			sync.store_customer(self.connection, {"id": "retry-local", "phone": "447700900088"})
			self.assertEqual(matching.process(frappe.get_doc(matching.QUEUE, name)), "Applied")
		lead.reload()
		self.assertEqual(lead.shipkia_cust_id, "retry-local")
		self.assertEqual(lead.shipkia_onboarding_status, "Onboarded")

	def test_recheck_is_bounded_and_preserves_review(self):
		from frappe.utils import add_to_date, now_datetime
		lead = self.lead("bounded")
		# Synthetic queue rows stay inside this rolled-back test transaction.
		for i in range(105):
			name = matching.put("Lead", self.prefix + str(i))
			frappe.db.set_value(matching.QUEUE, name, {
				"reference_name": lead.name, "status": "Done", "result": "Unmatched",
				"due_at": add_to_date(now_datetime(), minutes=-1),
			})
		self.assertEqual(matching.recheck_unmatched(), 100)
		self.assertEqual(matching.recheck_unmatched(), 5)
		name = matching.put("Lead", lead.name)
		matching.finish(frappe.get_doc(matching.QUEUE, name), "Needs Review")
		frappe.db.set_value(matching.QUEUE, name, "due_at", add_to_date(now_datetime(), minutes=-1))
		matching.recheck_unmatched()
		self.assertEqual(frappe.db.get_value(matching.QUEUE, name, "status"), "Done")

	def test_active_connection_auto_defaults_and_enforces_one(self):
		from shipkia_lead.shipkia_active_connection import active_connection
		lead = self.lead("auto")
		lead.shipkia_connection = None
		lead.save()
		self.assertEqual(lead.shipkia_connection, self.connection.name)
		self.assertEqual(active_connection(), self.connection.name)
		other = frappe.copy_doc(self.connection)
		other.connection_name = self.prefix + "other"
		with self.assertRaises(frappe.ValidationError):
			other.insert()

	def test_switch_moves_unmatched_but_preserves_existing_id(self):
		unmatched = self.lead("switch")
		linked = self.lead("linked", cust_id="preserve-id")
		previous = self.connection.name
		current = self.make_connection("Testing")
		unmatched.reload()
		unmatched.save()
		linked.reload()
		linked.save()
		self.assertEqual(unmatched.shipkia_connection, current.name)
		self.assertEqual(linked.shipkia_connection, previous)
		self.assertEqual(linked.shipkia_cust_id, "preserve-id")

	def test_manual_sync_adopts_legacy_unlinked_lead(self):
		lead = self.lead("legacy-auto")
		frappe.db.set_value("Lead", lead.name, "shipkia_connection", None)
		self.assertEqual(matching.sync_leads([lead.name]), {"queued": 1})
		row = frappe.get_doc(matching.QUEUE, matching.put("Lead", lead.name))
		self.assertEqual(matching.process(row), "Unmatched")
		lead.reload()
		self.assertEqual(lead.shipkia_connection, self.connection.name)

	def test_cust_id_on_first_save_gets_active_environment(self):
		lead = frappe.get_doc({"doctype": "Lead", "first_name": self.prefix + "id-first",
			"status": "Open", "lead_owner": "Administrator", "shipkia_cust_id": "new-id"}).insert()
		self.assertEqual(lead.shipkia_connection, self.connection.name)

	def test_historical_adoption_queues_without_inline_matching(self):
		from shipkia_lead.shipkia_active_connection import queue_unlinked
		lead = self.lead("adoption")
		name = matching.put("Lead", lead.name)
		matching.finish(frappe.get_doc(matching.QUEUE, name), "Unmatched")
		frappe.db.set_value("Lead", lead.name, "shipkia_connection", None)
		with patch.object(matching, "process", side_effect=AssertionError("No inline matching")):
			queue_unlinked()
		self.assertEqual(frappe.db.get_value(matching.QUEUE, name, "status"), "Pending")
