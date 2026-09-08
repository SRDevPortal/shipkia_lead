frappe.ui.form.on("ShipKia Customer", {
    refresh(frm) {
        if (frm.is_new()) return;
        const match = async (apply) => {
            const r = await frappe.call({ method: "shipkia_lead.shipkia_sync.preview_match", args: { name: frm.doc.name, apply }, freeze: true });
            frappe.msgprint(__(r.message.status) + ": " + __(r.message.note || ""));
            frm.reload_doc();
        };
        frm.add_custom_button(__("Preview Match"), () => match(0));
        frm.add_custom_button(__("Apply to CRM"), () => match(1));
    },
});
