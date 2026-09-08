for (const doctype of ["Lead", "Customer"]) {
    frappe.ui.form.on(doctype, {
        shipkia_cust_id(frm) {
            const has_id = String(frm.doc.shipkia_cust_id || "").trim().length > 0;
            return frm.set_value("shipkia_onboarding_status", has_id ? (frm.doc.shipkia_connection ? "Signed Up" : "Onboarded") : "Not Onboarded");
        },
    });
}
