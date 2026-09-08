"""ERPNext Lead configuration. No dependency on the separate Frappe CRM app."""

import json
from pathlib import Path

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter
from frappe.utils import now_datetime


def execute():
	backup = Path(frappe.get_site_path("private", "files", "erpnext-lead-migration"))
	backup.mkdir(parents=True, exist_ok=True)
	(backup / (now_datetime().strftime("%Y%m%d-%H%M%S-%f") + "-layout.json")).write_text(
		frappe.as_json(
			{
				"fields": [f.as_dict() for f in frappe.get_meta("Lead").fields],
				"workspace": frappe.get_doc("Workspace", "CRM").as_dict(),
				"property_setters": frappe.get_all(
					"Property Setter", filters={"doc_type": "Lead"}, fields=["*"]
				),
			}
		),
		encoding="utf-8",
	)
	fields = [
		{
			"fieldname": "lead_overview_tab",
			"label": "Overview",
			"fieldtype": "Tab Break",
			"insert_after": "naming_series",
		},
		{
			"fieldname": "lead_origin_tab",
			"label": "Lead Source",
			"fieldtype": "Tab Break",
			"insert_after": "other_info_tab",
		},
	]
	previous = "lead_origin_tab"
	for name, label, kind, options in [
		(
			"source_medium",
			"Acquisition Type",
			"Select",
			"\nPaid Ads\nOrganic\nReferral\nDirect\nMessaging\nOther",
		),
		("source_landing_page", "Landing Page URL", "Data", "URL"),
		("source_referrer", "Referrer URL", "Data", "URL"),
		("source_campaign_name", "Campaign Name", "Data", None),
		("source_campaign_id", "Campaign ID", "Data", None),
		("source_notes", "Source Notes", "Small Text", None),
		("facebook_lead_id", "Facebook Lead ID", "Data", None),
		("facebook_form_id", "Facebook Form ID", "Data", None),
		("google_click_id", "Google Click ID (GCLID)", "Data", None),
		("interakt_contact_id", "Interakt Contact ID", "Data", None),
		("interakt_conversation_id", "Interakt Conversation ID", "Data", None),
		("legacy_crm_lead", "Original Frappe CRM Lead ID", "Data", None),
		("legacy_crm_status", "Original Frappe CRM Status", "Data", None),
	]:
		if not frappe.get_meta("Lead").has_field(name):
			field = {"fieldname": name, "label": label, "fieldtype": kind, "insert_after": previous}
			if options is not None:
				field["options"] = options
			if name.startswith("legacy_"):
				field["read_only"] = 1
			fields.append(field)
		previous = name
	create_custom_fields({"Lead": fields})
	for source in ("Facebook", "Google", "Organic", "Interakt"):
		if not frappe.db.exists("Lead Source", source):
			frappe.get_doc({"doctype": "Lead Source", "source_name": source}).insert(ignore_permissions=True)
	meta = frappe.get_meta("Lead")
	tabs = [
		("lead_overview_tab", "Overview"),
		("contact_info_tab", "Others"),
		("lead_origin_tab", "Lead Source"),
		("organization_section", "Business"),
		("shipkia_tab", "ShipKia"),
		("qualification_tab", "Qualification"),
		("other_info_tab", "Additional Settings"),
		("activities_tab", "Activities"),
		("notes_tab", "Notes"),
		("dashboard_tab", "Dashboard"),
	]
	known = {f.fieldname for f in meta.fields}
	buckets = {name: [] for name, _ in tabs if name in known}
	core = {
		"lead_overview_tab": "status lead_owner lead_score lead_temperature column_break_1 first_name last_name lead_name email_id mobile_no",
		"contact_info_tab": "salutation middle_name gender phone phone_ext whatsapp_no website column_break_20 country state city territory column_break_16 address_section address_html column_break_38 contact_html column_break2",
		"lead_origin_tab": "source source_medium source_landing_page source_referrer source_campaign_name source_campaign_id campaign_name source_notes facebook_lead_id facebook_form_id google_click_id interakt_contact_id interakt_conversation_id",
		"organization_section": "company_name job_title no_of_employees annual_revenue industry market_segment fax column_break_28 column_break_31 shipkia_business_section shipkia_business_type shipkia_instagram_url shipkia_shopify_store_url",
		"other_info_tab": "naming_series company language lead_lan type request_type customer disabled unsubscribed blog_subscriber image title col_break123 column_break_22 column_break_50 legacy_crm_lead legacy_crm_status",
	}
	used = set(buckets)
	for tab, names in core.items():
		buckets[tab] = [n for n in names.split() if n in known]
		used.update(buckets[tab])
	current = "lead_overview_tab"
	for field in meta.fields:
		name = field.fieldname
		if field.fieldtype == "Tab Break":
			current = name
		if name in used:
			continue
		if name.startswith("shipkia_"):
			target = (
				"lead_origin_tab"
				if any(
					x in name
					for x in (
						"source",
						"campaign",
						"adset",
						"ad_id",
						"ad_name",
						"form_id",
						"utm_",
						"platform",
						"raw_payload",
					)
				)
				else "shipkia_tab"
			)
		else:
			target = current
		buckets.setdefault(target, [])
		if name != target:
			buckets[target].append(name)
		used.add(name)
	order = [n for tab, values in buckets.items() for n in [tab, *values]]
	assert len(order) == len(set(order)) and set(order) == known
	for tab, label in tabs:
		if tab in known:
			make_property_setter("Lead", tab, "fieldtype", "Tab Break", "Select")
			make_property_setter("Lead", tab, "label", label, "Data")
	for name, label in (
		("lead_score", "Lead Score"),
		("lead_temperature", "Lead Temperature"),
		("lead_lan", "Lead Language"),
		("source", "Lead Source"),
	):
		if name in known:
			make_property_setter("Lead", name, "label", label, "Data")
	make_property_setter("Lead", "lead_owner", "default", "", "Data")
	make_property_setter("Lead", None, "field_order", json.dumps(order), "Data", for_doctype=True)
	workspace = frappe.get_doc("Workspace", "CRM")
	if not any(row.link_to == "Lead Distribution Rule" for row in workspace.shortcuts):
		workspace.append(
			"shortcuts",
			{
				"type": "DocType",
				"label": "Lead Distribution",
				"link_to": "Lead Distribution Rule",
				"color": "Blue",
			},
		)
		content = frappe.parse_json(workspace.content or "[]")
		content.insert(
			0,
			{
				"id": "lead-distribution",
				"type": "shortcut",
				"data": {"shortcut_name": "Lead Distribution", "col": 3},
			},
		)
		workspace.content = frappe.as_json(content)
		workspace.save(ignore_permissions=True)
	from shipkia_lead.lead_distribution import after_migrate

	after_migrate()
	frappe.clear_cache(doctype="Lead")
	from shipkia_lead.remove_lead_business_tab import execute as remove_business_tab

	remove_business_tab()
	from shipkia_lead.add_shipkia_cust_id import execute as add_shipkia_cust_id

	add_shipkia_cust_id()
