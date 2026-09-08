(() => {
 const settings = frappe.listview_settings["Lead"] || {};
 const previous = settings.onload;
 settings.onload = function(listview) {
  if (previous) previous.call(this, listview);
		listview.page.add_inner_button(__("Sync with ShipKia"), () => {
			const names = listview.get_checked_items().map((doc) => doc.name);
			if (!names.length || names.length > 100) {
				frappe.msgprint(__("Select between 1 and 100 leads first."));
				return;
			}
			frappe.call({
				method: "shipkia_lead.shipkia_matching.sync_leads",
				args: { names }, freeze: true,
				callback: (r) => {
					if (r.message) frappe.msgprint(__("Queued {0} leads to check against imported ShipKia customers. Refresh the list shortly to see updates.", [r.message.queued]));
				},
			});
		});

 };
 frappe.listview_settings["Lead"] = settings;
})();
