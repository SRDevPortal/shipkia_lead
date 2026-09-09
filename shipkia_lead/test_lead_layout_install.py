"""Exercise installer layout generation without pre-existing custom tab fields."""
import json
from datetime import datetime
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import frappe
from shipkia_lead import lead_distribution_setup as setup


class Field(frappe._dict):
    def as_dict(self):
        return dict(self)


class TestLeadLayoutInstall(TestCase):
    def run_layout(self, with_shipkia_tab=False):
        import erpnext
        source = Path(erpnext.__file__).parent / 'crm/doctype/lead/lead.json'
        fields = [Field(f) for f in json.loads(source.read_text())['fields']]
        fields.extend([
            Field(fieldname='shipkia_monthly_shipments', fieldtype='Data'),
            Field(fieldname='shipkia_lead_source', fieldtype='Data'),
        ])
        if with_shipkia_tab:
            fields.append(Field(fieldname='shipkia_tab', fieldtype='Tab Break'))
        class Meta:
            def __init__(self):
                self.fields = fields
            def has_field(self, name):
                return any(f.fieldname == name for f in self.fields)
        meta = Meta()
        def create_fields(mapping):
            for field in mapping['Lead']:
                if not meta.has_field(field['fieldname']):
                    fields.append(Field(field))
        class Workspace:
            shortcuts = [frappe._dict(link_to='Lead Distribution Rule')]
            def as_dict(self):
                return {}
        with patch.object(setup.frappe, 'get_meta', return_value=meta), \
            patch.object(setup, 'now_datetime', return_value=datetime(2026, 9, 9)), \
            patch.object(setup.frappe, 'get_doc', return_value=Workspace()), \
            patch.object(setup.frappe, 'get_all', return_value=[]), \
            patch.object(setup.frappe.db, 'exists', return_value=True), \
            patch.object(setup.frappe, 'clear_cache'), \
            patch.object(setup, 'create_custom_fields', side_effect=create_fields), \
            patch.object(setup, 'make_property_setter') as setter, \
            patch.object(Path, 'mkdir'), patch.object(Path, 'write_text'), \
            patch('shipkia_lead.lead_distribution.after_migrate'), \
            patch('shipkia_lead.remove_lead_business_tab.execute'), \
            patch('shipkia_lead.add_shipkia_cust_id.execute'):
            setup.execute()
        orders = [json.loads(call.args[3]) for call in setter.call_args_list if call.args[2] == 'field_order']
        self.assertEqual(len(orders), 1)
        order = orders[0]
        self.assertEqual(len(order), len(set(order)))
        self.assertEqual(set(order), {f.fieldname for f in fields})
        self.assertEqual(order.count('shipkia_tab'), 1)
        self.assertLess(order.index('shipkia_tab'), order.index('shipkia_monthly_shipments'))

    def test_fresh_site_without_shipkia_tab(self):
        self.run_layout()

    def test_existing_shipkia_tab_is_not_duplicated(self):
        self.run_layout(with_shipkia_tab=True)
