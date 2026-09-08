frappe.ui.form.on("ShipKia Match Queue", {
    refresh(frm) {
        if (frm.is_new()) return;
        frm.add_custom_button(__("Retry Matching"), async () => {
            await frappe.call({method: "shipkia_lead.shipkia_matching.retry", args: {name: frm.doc.name}});
            frm.reload_doc();
        });
    },
});
