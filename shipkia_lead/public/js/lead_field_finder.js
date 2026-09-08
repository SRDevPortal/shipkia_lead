frappe.ui.form.on("Lead", {
  refresh(frm) {
    frm.add_custom_button(__("Find Field"), () => {
      let tab = __("Overview");
      const options = [];
      for (const df of frm.meta.fields) {
        if (df.fieldtype === "Tab Break") {
          tab = __(df.label || df.fieldname);
          continue;
        }
        const control = frm.fields_dict[df.fieldname];
        if (
          !control ||
          control.df.hidden ||
          control.get_status() === "None" ||
          ["Section Break", "Column Break", "HTML", "Button"].includes(
            df.fieldtype,
          )
        )
          continue;
        options.push({
          label: `${__(df.label || df.fieldname)} (${tab})`,
          value: df.fieldname,
        });
      }
      frappe.prompt(
        {
          fieldname: "field",
          fieldtype: "Autocomplete",
          label: __("Field name"),
          options,
          reqd: 1,
          description: __(
            "Type a field name, such as Status, Lead Owner or Country.",
          ),
        },
        ({ field }) => {
          if (options.some((option) => option.value === field)) {
            frm.scroll_to_field(field);
          } else {
            frappe.msgprint(__("Please select a field from the suggestions."));
          }
        },
        __("Find Field"),
        __("Go to Field"),
      );
    });
  },
});
