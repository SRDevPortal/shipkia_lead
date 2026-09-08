"""Local integration tests with synthetic API responses; no external requests."""

import json
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

import frappe

from shipkia_lead import shipkia_sync as sync


class TestShipKiaSync(TestCase):
	def setUp(self):
		self.user = frappe.session.user
		frappe.set_user("Administrator")
		self.prefix = "api-test-" + uuid4().hex[:10]
		self.connection = self.make_connection("Production")

	def tearDown(self):
		frappe.db.rollback()
		frappe.set_user(self.user)

	def make_connection(self, environment):
		# Environment changes are transaction-local and rolled back by tearDown.
		frappe.db.sql("UPDATE `tabShipKia API Connection` SET enabled=0, active_slot=NULL WHERE enabled=1")
		return frappe.get_doc(
			{
				"doctype": sync.CONNECTION,
				"connection_name": self.prefix + environment,
				"environment": environment,
				"enabled": 1,
				"endpoint": "https://example.invalid/customers",
				"api_key": "test-secret-key",
				"records_path": "data",
				"field_mapping": json.dumps(
					{"cust_id": "id", "phone": "phone", "onboarding_status": "status"}
				),
				"page_size": 2,
				"onboarded_values": "complete",
			}
		).insert()

	def lead(self, suffix, cust_id=None, connection=None, phone=None):
		doc = frappe.get_doc(
			{
				"doctype": "Lead",
				"first_name": self.prefix + suffix,
				"status": "Open",
				"lead_owner": "Administrator",
				"shipkia_connection": connection or self.connection.name,
				"shipkia_cust_id": cust_id,
				"mobile_no": phone,
			}
		)
		return doc.insert()

	def test_scoped_identity_and_redacted_idempotent_import(self):
		testing = self.make_connection("Testing")
		raw = {
			"id": "00123",
			"password": "never-store",
			"nested": {"token": "never-store"},
			"phone": "+447700900001",
		}
		name, changed = sync.store_customer(self.connection, raw)
		self.assertTrue(changed)
		self.assertFalse(sync.store_customer(self.connection, raw)[1])
		other, _ = sync.store_customer(testing, raw)
		self.assertNotEqual(name, other)
		self.assertNotIn("never-store", frappe.db.get_value(sync.CUSTOMER, name, "raw_response"))
		self.assertEqual(frappe.db.get_value(sync.CUSTOMER, name, "cust_id"), "00123")

	def test_composite_crm_uniqueness(self):
		self.lead("one", "00123")
		testing = self.make_connection("Testing")
		self.lead("two", "00123", connection=testing.name)
		with self.assertRaises((frappe.UniqueValidationError, frappe.DuplicateEntryError)):
			self.lead("duplicate", "00123")

	def test_preview_then_apply_does_not_change_sales_status(self):
		lead = self.lead("matched", phone="+447700900002")
		name, _ = sync.store_customer(
			self.connection, {"id": "abc", "phone": "447700900002", "status": "complete"}
		)
		record = frappe.get_doc(sync.CUSTOMER, name)
		self.assertEqual(sync.match_record(record), "Matched")
		self.assertFalse(frappe.db.get_value("Lead", lead.name, "shipkia_cust_id"))
		self.assertTrue(sync.apply_record(record, self.connection))
		lead.reload()
		self.assertEqual(
			(lead.shipkia_cust_id, lead.shipkia_onboarding_status, lead.status), ("abc", "Onboarded", "Open")
		)

	def test_ambiguous_matches_are_not_applied(self):
		self.lead("one", phone="+447700900003")
		self.lead("two", phone="+447700900003")
		name, _ = sync.store_customer(self.connection, {"id": "ambiguous", "phone": "447700900003"})
		record = frappe.get_doc(sync.CUSTOMER, name)
		self.assertFalse(sync.apply_record(record, self.connection))
		self.assertEqual(record.match_result, "Needs Review")

	def test_testing_cannot_apply_and_does_not_match_production(self):
		self.lead("production", cust_id="production-existing", phone="+447700900004")
		testing = self.make_connection("Testing")
		name, _ = sync.store_customer(testing, {"id": "test-only", "phone": "447700900004"})
		record = frappe.get_doc(sync.CUSTOMER, name)
		self.assertEqual(sync.match_record(record), "Unmatched")
		with self.assertRaises(frappe.ValidationError):
			sync.apply_record(record, testing)

	def test_pagination_checkpoint_and_no_overlapping_start(self):
		with patch.object(sync, "enqueue_page") as enqueue, patch.object(frappe.db, "commit"):
			name = sync.start_sync(self.connection.name)
			self.assertEqual(sync.start_sync(self.connection.name), name)
			self.assertEqual(enqueue.call_count, 1)
			with patch.object(
				sync, "fetch_page", side_effect=[([{"id": "1"}, {"id": "2"}], True), ([{"id": "3"}], False)]
			) as fetch:
				sync.run_batch(self.connection.name, name)
				self.assertEqual(fetch.call_count, 2)
			log = frappe.get_doc(sync.LOG, name)
			self.assertEqual((log.status, log.records_seen, log.pages_processed), ("Completed", 3, 2))
			self.connection.reload()
			self.assertEqual(self.connection.checkpoint, log.started_at)
			self.assertFalse(self.connection.active_run)

	def test_failed_request_retries_without_advancing_checkpoint(self):
		with (
			patch.object(sync, "enqueue_page"),
			patch.object(frappe.db, "commit"),
			patch.object(frappe.db, "rollback"),
		):
			name = sync.start_sync(self.connection.name)
			with patch.object(sync, "fetch_page", side_effect=ValueError("Customer API returned HTTP 429.")):
				sync.run_batch(self.connection.name, name)
			self.connection.reload()
			log = frappe.get_doc(sync.LOG, name)
			self.assertEqual(log.status, "Retrying")
			self.assertEqual(log.page, self.connection.first_page)
			self.assertFalse(self.connection.checkpoint)
			self.assertTrue(self.connection.next_sync)

	def test_guest_cannot_sync(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			sync.sync_now(self.connection.name)

	def test_mobile_priority_company_fallback_and_ambiguity(self):
		mobile = self.lead("mobile", phone="+44 7700-900009")
		company = self.lead("company")
		company.company_name = "Fallback Store"
		company.save()
		name, _ = sync.store_customer(self.connection, {"id": "fallback", "phone": "447700900009"})
		record = frappe.get_doc(sync.CUSTOMER, name)
		record.business_name = " fallback store "
		self.assertEqual(sync.candidates(record, "Lead"), [mobile.name])
		record.phone = "447700900008"
		self.assertEqual(sync.candidates(record, "Lead"), [company.name])
		mobile.reload()
		mobile.company_name = "Fallback Store"
		mobile.save()
		self.assertEqual(sync.match_record(record), "Needs Review")
		self.assertFalse(sync.apply_record(record, self.connection))

	def test_explicit_testing_updates_mark_id_onboarded(self):
		testing = self.make_connection("Testing")
		testing.allow_testing_updates = 1
		testing.onboarded_on_id = 1
		testing.save()
		lead = self.lead("testing", phone="+447700900007", connection=testing.name)
		name, _ = sync.store_customer(testing, {"id": "testing-id", "phone": "447700900007"})
		record = frappe.get_doc(sync.CUSTOMER, name)
		self.assertTrue(sync.apply_record(record, testing))
		lead.reload()
		self.assertEqual((lead.shipkia_cust_id, lead.shipkia_onboarding_status, lead.status), ("testing-id", "Onboarded", "Open"))

	def test_api_key_and_total_pages_end_on_full_last_page(self):
		from unittest.mock import MagicMock

		self.connection.auth_header = "x-api-key"
		self.connection.auth_prefix = ""
		self.connection.total_pages_path = "result.pages.totalPages"
		self.connection.records_path = "result.values"
		self.connection.http_method = "POST"
		response = MagicMock()
		response.__enter__.return_value = response
		response.status_code = 200
		response.iter_content.return_value = [b'{"result":{"values":[{"id":"1"},{"id":"2"}],"pages":{"totalPages":1}}}']
		with patch.object(sync.requests, "post", return_value=response) as post:
			rows, more = sync.fetch_page(self.connection, 1)
			self.assertEqual(len(rows), 2)
			self.assertFalse(more)
			self.assertEqual(post.call_args.kwargs["headers"]["x-api-key"], "test-secret-key")
			self.assertNotIn("Authorization", post.call_args.kwargs["headers"])

	def test_testing_post_uses_rows_and_page_without_body_or_cookies(self):
		from unittest.mock import MagicMock

		connection = self.make_connection("Testing")
		connection.http_method = "POST"
		connection.endpoint = "http://api.shipkia.tst/oms/customers/records/list"
		connection.allow_test_http = 1
		connection.size_parameter = "rows"
		connection.page_size = 20
		connection.save()
		response = MagicMock()
		response.__enter__.return_value = response
		response.status_code = 200
		response.iter_content.return_value = [b'{"data":[{"id":"sample"}]}']
		with patch.object(sync.requests, "post", return_value=response) as post, patch.object(sync.requests, "get") as get:
			rows, more = sync.fetch_page(connection, 1)
			get.assert_not_called()
			self.assertEqual(rows, [{"id": "sample"}])
			self.assertFalse(more)
			self.assertEqual(post.call_args.kwargs["params"], {"rows": 20, "page": 1})
			self.assertNotIn("json", post.call_args.kwargs)
			self.assertNotIn("cookies", post.call_args.kwargs)
			self.assertNotIn("verify", post.call_args.kwargs)

	def test_http_requires_testing_override(self):
		self.connection.endpoint = "http://api.shipkia.tst/oms/customers/records/list"
		with self.assertRaises(frappe.ValidationError):
			self.connection.save()
		testing = self.make_connection("Testing")
		testing.endpoint = self.connection.endpoint
		with self.assertRaises(frappe.ValidationError):
			testing.save()
		testing.reload()
		testing.endpoint = "http://api.shipkia.tst/oms/customers/records/list"
		testing.allow_test_http = 1
		testing.save()
		testing.enabled = 0
		testing.save()
		self.connection.reload()
		self.connection.enabled = 1
		self.connection.allow_test_http = 1
		with self.assertRaises(frappe.ValidationError):
			self.connection.save()

	def test_auth_failure_stops_scheduled_requests(self):
		frappe.db.set_value(sync.CONNECTION, self.connection.name, "scheduled_sync", 1)
		with patch.object(sync, "enqueue_page"), patch.object(frappe.db, "commit"), patch.object(frappe.db, "rollback"):
			name = sync.start_sync(self.connection.name)
			with patch.object(sync, "fetch_page", side_effect=sync.APIFailure(401)):
				sync.run_batch(self.connection.name, name)
			self.connection.reload()
			self.assertFalse(self.connection.scheduled_sync)
			self.assertFalse(self.connection.active_run)
			self.assertEqual(frappe.db.get_value(sync.LOG, name, "status"), "Failed")

	def test_rate_limit_honors_retry_after(self):
		from frappe.utils import add_to_date, get_datetime, now_datetime

		before = now_datetime()
		with patch.object(sync, "enqueue_page"), patch.object(frappe.db, "commit"), patch.object(frappe.db, "rollback"):
			name = sync.start_sync(self.connection.name)
			with patch.object(sync, "fetch_page", side_effect=sync.APIFailure(429, "3600")):
				sync.run_batch(self.connection.name, name)
			self.connection.reload()
			self.assertGreaterEqual(get_datetime(self.connection.next_sync), add_to_date(before, minutes=60))

	def test_http_auth_pagination_and_response_validation(self):
		from unittest.mock import MagicMock

		response = MagicMock()
		response.__enter__.return_value = response
		response.status_code = 200
		response.iter_content.return_value = [b'{"data":[{"id":"1"}]}']
		with patch.object(sync.requests, "get", return_value=response) as get:
			rows, more = sync.fetch_page(self.connection, 2)
			self.assertEqual(rows, [{"id": "1"}])
			self.assertFalse(more)
			self.assertEqual(get.call_args.kwargs["params"], {"page": 2, "limit": 2})
			self.assertEqual(get.call_args.kwargs["headers"], {"Accept": "application/json", "Authorization": "Bearer test-secret-key"})
			self.assertFalse(get.call_args.kwargs["allow_redirects"])
			response.status_code = 302
			with self.assertRaises(ValueError):
				sync.fetch_page(self.connection, 2)

	def test_batch_yields_after_three_pages(self):
		with patch.object(sync, "enqueue_page") as enqueue, patch.object(frappe.db, "commit"):
			name = sync.start_sync(self.connection.name)
			with patch.object(
				sync, "fetch_page", side_effect=[([{"id": str(index)}], True) for index in range(3)]
			) as fetch:
				sync.run_batch(self.connection.name, name)
				self.assertEqual(fetch.call_count, 3)
			self.assertEqual(enqueue.call_count, 2)
			self.connection.reload()
			self.assertFalse(self.connection.checkpoint)
			self.assertEqual(frappe.db.get_value(sync.LOG, name, "page"), 4)
