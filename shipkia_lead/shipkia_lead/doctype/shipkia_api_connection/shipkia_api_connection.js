frappe.ui.form.on("ShipKia API Connection", {
    refresh(frm) {
        if (frm.is_new()) return;
        const call = async (method) => {
            if (frm.is_dirty()) return frappe.msgprint(__("Save the connection first."));
            const r = await frappe.call({ method: `shipkia_lead.shipkia_sync.${method}`, args: { name: frm.doc.name }, freeze: true });
            if (method === "sync_now") frappe.set_route("Form", "ShipKia Sync Log", r.message);
            else frappe.msgprint(__("Connection test succeeded. Customers in first page: {0}. Nothing was imported.", [r.message.customers_in_page]));
        };
        frm.add_custom_button(__("Test Connection"), () => call("test_connection"));
        frm.add_custom_button(__("Sync Now"), () => call("sync_now"));
        frm.add_custom_button(__("View Customers"), () => frappe.set_route("List", "ShipKia Customer", { connection: frm.doc.name }));
        frm.add_custom_button(__("View Logs"), () => frappe.set_route("List", "ShipKia Sync Log", { connection: frm.doc.name }));
    },
});
