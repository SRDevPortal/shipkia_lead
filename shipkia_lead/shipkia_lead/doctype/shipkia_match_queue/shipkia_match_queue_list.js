frappe.listview_settings["ShipKia Match Queue"] = {
    onload(listview) {
        listview.page.add_inner_button(__("Queue Health"), async () => {
            const r = await frappe.call({method: "shipkia_lead.shipkia_matching.health"});
            const data = r.message;
            frappe.msgprint({title: __("ShipKia Matching Queue"),
                message: data.counts.map(row => `${__(row.status)}: ${row.quantity}`).join("<br>") +
                    "<br>" + __("Oldest pending: {0}", [data.oldest_pending || __("None")])});
        });
    },
};
