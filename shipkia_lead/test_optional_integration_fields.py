from unittest import TestCase
from unittest.mock import Mock, patch

from shipkia_lead import erpnext_leads
from shipkia_lead import setup


class TestOptionalIntegrationFields(TestCase):
    def collect_fields(self, installed):
        meta = Mock()
        meta.has_field.return_value = False
        with patch.object(erpnext_leads.frappe, 'get_meta', return_value=meta), \
            patch.object(erpnext_leads.frappe.db, 'exists', side_effect=lambda dt, name: dt == 'DocType' and name in installed), \
            patch.object(erpnext_leads.frappe.db, 'delete') as delete, \
            patch.object(erpnext_leads, 'create_custom_fields') as create:
            erpnext_leads.ensure_fields()
        delete.assert_not_called()
        return {f['fieldname'] for f in create.call_args.args[0]['Lead']}

    def test_only_erpnext_does_not_require_optional_apps(self):
        fields = self.collect_fields({'User', 'Lead'})
        self.assertIn('legacy_crm_lead', fields)
        self.assertIn('sr_duplicate_of', fields)
        self.assertNotIn('shipkia_wa_conversation', fields)
        self.assertNotIn('vobiz_latest_call_log', fields)
        self.assertNotIn('po_last_payment_intent', fields)

    def test_optional_fields_are_added_when_apps_become_available(self):
        fields = self.collect_fields({'User', 'Lead', 'Chat Conversation', 'Vobiz Call Log', 'Payment Intent'})
        self.assertTrue({'shipkia_wa_conversation', 'vobiz_latest_call_log', 'po_last_payment_intent'} <= fields)

    def test_partial_install_is_repaired_even_with_identity_present(self):
        meta = Mock()
        meta.has_field.side_effect = lambda name: name != 'sk_phone_0'
        with patch.object(setup.frappe, 'get_meta', return_value=meta), \
            patch.object(setup.frappe.db, 'has_column', return_value=True), \
            patch('shipkia_lead.lead_distribution_setup.execute') as repair:
            setup.ensure_core_setup()
        repair.assert_called_once()

    def test_completed_install_does_not_rewrite_layout(self):
        meta = Mock()
        meta.has_field.return_value = True
        with patch.object(setup.frappe, 'get_meta', return_value=meta), \
            patch.object(setup.frappe.db, 'has_column', return_value=True), \
            patch('shipkia_lead.lead_distribution_setup.execute') as repair:
            setup.ensure_core_setup()
        repair.assert_not_called()
