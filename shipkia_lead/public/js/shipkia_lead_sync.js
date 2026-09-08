frappe.ui.form.on("Lead", {
    refresh(frm) {
        if (frm.is_new() || !frm.perm.some((p) => p.write)) return;
        frm.add_custom_button(__("Sync with ShipKia"), () => {
            if (frm.is_dirty()) {
                frappe.msgprint(__("Save your changes before syncing."));
                return;
            }
            frappe.call({
                method: "shipkia_lead.shipkia_matching.sync_leads",
                args: { names: [frm.doc.name] }, freeze: true,
                callback: (r) => {
                    if (r.message) frappe.msgprint(__("Queued a check against imported ShipKia customers. Reload this lead shortly to see its ShipKia CUST ID and onboarding status."));
                },
            });
        });
    },
});
