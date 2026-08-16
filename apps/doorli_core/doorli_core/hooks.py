app_name = "doorli_core"
app_title = "Doorli Enterprise OS"
app_publisher = "Doorli"
app_description = "Core logic for Doorli Enterprise ERP"
app_email = "engineering@doorli.com"
app_license = "Proprietary"
app_logo_url = "/assets/doorli_core/images/doorli-mark.svg"

# Bootstrap the super-admin control fields after migrate.
after_migrate = ["doorli_core.control.after_migrate"]

# Inject custom CSS when assets exist
app_include_css = "doorli.bundle.css"
web_include_css = "doorli.bundle.css"
app_include_js = "doorli_workspace.js"

# --- Two-Way Sync ---
# Sales Order lifecycle drives confirmed/cancelled; Delivery Note submission
# drives delivered. All callbacks are queued (see doorli_core.sync).
doc_events = {
    "Sales Order": {
        "on_submit": "doorli_core.sync.on_sales_order_submit",
        "on_cancel": "doorli_core.sync.on_sales_order_cancel",
    },
    "Delivery Note": {
        "on_submit": "doorli_core.sync.on_delivery_note_submit",
    },
}
