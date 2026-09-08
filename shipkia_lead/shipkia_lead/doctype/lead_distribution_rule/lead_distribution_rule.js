frappe.ui.form.on('Lead Distribution Rule', {
  async setup(frm) {
    await frappe.model.with_doctype('Lead')
    const statuses = (frappe.meta.get_docfield('Lead', 'status').options || '')
      .split('\n').filter((status) => status && !['Converted', 'Do Not Contact'].includes(status))
    frm.set_df_property('status', 'options', statuses)
    frm.set_query('user', 'users', () => ({
      filters: { enabled: 1, user_type: 'System User', name: ['not in', ['Guest', 'Administrator']] },
    }))
  },
  refresh(frm) {
    if (!frm.is_new() && frm.doc.enabled) {
      frm.add_custom_button(__('Retry Distribution'), () => frappe.call({
        method: 'shipkia_lead.lead_distribution.retry_distribution',
        args: { name: frm.doc.name },
        callback: () => frm.reload_doc(),
      }))
    }
  },
})
