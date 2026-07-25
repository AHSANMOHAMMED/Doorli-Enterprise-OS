app_name = "doorli_core"
app_title = "Doorli Enterprise OS"
app_publisher = "Doorli"
app_description = "Core logic for Doorli Enterprise ERP"
app_email = "engineering@doorli.com"
app_license = "Proprietary"

# --- Doorli White-Labeling Overrides ---
app_logo_url = "/assets/doorli_core/images/logo.png"

# Inject our custom proprietary CSS into every single page
app_include_css = "/assets/doorli_core/css/doorli.bundle.css"
web_include_css = "/assets/doorli_core/css/doorli.bundle.css"

# Override the login page logo
update_website_context = [
    {"logo": "/assets/doorli_core/images/logo.png"}
]

# Ensure we remove all telemetry and "Powered by" text in standard views
website_context = {
    "favicon": "/assets/doorli_core/images/logo.png",
    "splash_image": "/assets/doorli_core/images/logo.png",
}

# --- Two-Way Sync ---
# Fire the reverse webhook whenever a Sales Order is updated
doc_events = {
    "Sales Order": {
        "on_update": "doorli_core.sync.send_status_to_marketplace"
    }
}
