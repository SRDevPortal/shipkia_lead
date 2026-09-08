app_name = "shipkia_lead"
app_title = "Shipkia Lead"
app_publisher = "ShipKia"
app_description = "ShipKia custom integrations"
app_email = "admin@shipkia.com"
app_license = "MIT"
required_apps = ['erpnext']

after_install = "shipkia_lead.setup.after_install"
after_migrate = "shipkia_lead.setup.after_migrate"
doctype_js = {
    "Lead": ["public/js/lead_field_finder.js", "public/js/shipkia_onboarding.js", "public/js/shipkia_lead_sync.js"],
    "Customer": "public/js/shipkia_onboarding.js",
}
doctype_list_js = {"Lead": "public/js/lead_list.js"}
doc_events = {
    "Lead": {
        "validate": ["shipkia_lead.shipkia_active_connection.assign_connection", "shipkia_lead.shipkia_onboarding.update_status", "shipkia_lead.shipkia_matching.set_keys"],
        "on_change": ["shipkia_lead.lead_distribution.lead_changed", "shipkia_lead.shipkia_matching.changed"],
        "after_delete": "shipkia_lead.lead_distribution.lead_changed",
    },
    "Customer": {
        "validate": ["shipkia_lead.shipkia_active_connection.assign_connection", "shipkia_lead.shipkia_onboarding.update_status", "shipkia_lead.shipkia_matching.set_keys"],
        "on_change": "shipkia_lead.shipkia_matching.changed",
    },
    "ShipKia Customer": {"validate": "shipkia_lead.shipkia_matching.set_keys", "on_change": "shipkia_lead.shipkia_matching.changed"},
    "ToDo": {"after_insert": "shipkia_lead.lead_distribution.todo_changed", "on_update": "shipkia_lead.lead_distribution.todo_changed"},
    "User": {"on_update": "shipkia_lead.lead_distribution.user_changed"},
    "Assignment Rule": {"validate": "shipkia_lead.lead_distribution.validate_other_assignment"},
}
scheduler_events = {"cron": {"* * * * *": ["shipkia_lead.shipkia_sync.schedule_due", "shipkia_lead.shipkia_matching.kick", "shipkia_lead.shipkia_matching.recheck_unmatched", "shipkia_lead.shipkia_active_connection.queue_unlinked"]}}
