import json
import os

import frappe
from frappe import _

# ---------------------------------------------------------------------------
# Doorli Enterprise OS control channel
#
# Exposes whitelisted guest methods that the Doorli super-admin control plane
# calls over the internal webhook secret. Tenant controls are persisted on the
# Company doctype (Custom Fields created idempotently on migrate); global
# module toggles and maintenance flags are persisted on System Settings.
# ---------------------------------------------------------------------------

COMPANY_FIELDS = [
    {"fieldname": "doorli_control_status", "label": "Doorli Status", "fieldtype": "Select",
     "options": "active\nsuspended\nlocked\ncancelled", "default": "active"},
    {"fieldname": "doorli_control_reason", "label": "Doorli Status Reason", "fieldtype": "Data"},
    {"fieldname": "doorli_plan", "label": "Doorli Plan", "fieldtype": "Select",
     "options": "trial\nbasic\nstandard\npremium", "default": "trial"},
    {"fieldname": "doorli_plan_expires_on", "label": "Doorli Plan Expires On", "fieldtype": "Date"},
    {"fieldname": "doorli_ai_enabled", "label": "Doorli AI Enabled", "fieldtype": "Check", "default": 1},
    {"fieldname": "doorli_max_users", "label": "Doorli Max Users", "fieldtype": "Int"},
    {"fieldname": "doorli_quota_override", "label": "Doorli Quota Override", "fieldtype": "JSON"},
    {"fieldname": "doorli_enabled_modules", "label": "Doorli Enabled ERP Modules", "fieldtype": "JSON"},
]

GLOBAL_SETTINGS_FIELD = {
    "fieldname": "doorli_module_toggles",
    "label": "Doorli Module Toggles",
    "fieldtype": "JSON",
}

MAINTENANCE_FIELD = {
    "fieldname": "doorli_maintenance",
    "label": "Doorli Maintenance Mode",
    "fieldtype": "Check",
}

DEFAULT_MODULES = {
    "dashboard": True,
    "stock": True,
    "selling": True,
    "buying": True,
    "auto-service": True,
    "restaurant": True,
    "hr": True,
    "accounting": True,
    "reports": True,
    "my": True,
    "settings": True,
}

# Doorli capability keys map to the module keys used by Enterprise navigation.
DOORLI_MODULE_ALIASES = {
    "pos": "selling",
    "pos_integration": "selling",
    "inventory_management": "stock",
    "accounting_reports": "accounting",
}

MAINTENANCE_KEY = "doorli_maintenance"


def _expected_secret():
    """Shared internal secret (same set as api.py, fail-closed)."""
    secret = os.environ.get("DOORLI_WEBHOOK_SECRET", "").strip()
    if not secret:
        frappe.throw(
            _("DOORLI_WEBHOOK_SECRET is not configured on the Enterprise node."),
            frappe.ValidationError,
        )
    return secret.replace("Bearer ", "", 1) if secret.startswith("Bearer ") else secret


def _as_bool(value, default=True):
    """Coerce form/JSON values into a real boolean (bool('false') is True in Python)."""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value) if value is not None else default


def verify_doorli_webhook():
    header = frappe.request.headers.get("X-Doorli-Secret") or ""
    if not header:
        auth = frappe.request.headers.get("Authorization") or ""
        header = auth[len("Bearer "):] if auth.startswith("Bearer ") else auth
    provided = header.strip()
    expected = _expected_secret()
    # Constant-time comparison.
    import hmac
    if not provided or not hmac.compare_digest(provided, expected):
        frappe.throw(
            _("Unauthorized. Invalid Doorli Enterprise Webhook Secret."),
            frappe.PermissionError,
        )


# ---------------------------------------------------------------------------
# Schema bootstrap (idempotent) + persistence helpers
# ---------------------------------------------------------------------------

def _ensure_custom_fields():
    """Create Company + System Settings custom fields once, on migrate and on demand."""
    frappe.set_user("Administrator")
    try:
        existing = {
            (row[0], row[1]) for row in frappe.db.sql(
                "SELECT `dt`, `fieldname` FROM `tabCustom Field`", as_list=True
            )
        } if frappe.db.table_exists("Custom Field") else set()
    except Exception:
        existing = set()

    for field in COMPANY_FIELDS:
        if ("Company", field["fieldname"]) in existing:
            continue
        frappe.get_doc({
            "doctype": "Custom Field",
            "dt": "Company",
            "fieldname": field["fieldname"],
            "label": field["label"],
            "fieldtype": field["fieldtype"],
            "options": field.get("options", ""),
            "default": field.get("default"),
            "insert_after": "company_name",
        }).insert(ignore_permissions=True)

    if ("System Settings", GLOBAL_SETTINGS_FIELD["fieldname"]) not in existing:
        frappe.get_doc({
            "doctype": "Custom Field",
            "dt": "System Settings",
            "fieldname": GLOBAL_SETTINGS_FIELD["fieldname"],
            "label": GLOBAL_SETTINGS_FIELD["label"],
            "fieldtype": GLOBAL_SETTINGS_FIELD["fieldtype"],
            "insert_after": "system_name",
        }).insert(ignore_permissions=True)

    if ("System Settings", MAINTENANCE_FIELD["fieldname"]) not in existing:
        frappe.get_doc({
            "doctype": "Custom Field",
            "dt": "System Settings",
            "fieldname": MAINTENANCE_FIELD["fieldname"],
            "label": MAINTENANCE_FIELD["label"],
            "fieldtype": MAINTENANCE_FIELD["fieldtype"],
            "insert_after": "system_name",
        }).insert(ignore_permissions=True)

    frappe.db.commit()


def _singles_value(fieldname):
    """Read a single-value setting straight from tabSingles (bypasses the
    get_single_value Redis cache, which may hold stale values across requests)."""
    try:
        rows = frappe.db.sql(
            "SELECT `value` FROM `tabSingles` WHERE `doctype`=%s AND `field`=%s",
            ("System Settings", fieldname),
        )
        return rows[0][0] if rows else None
    except Exception:
        return None


def _read_modules():
    raw = _singles_value(GLOBAL_SETTINGS_FIELD["fieldname"])
    if raw in (None, ""):
        return dict(DEFAULT_MODULES)
    try:
        data = json.loads(raw)
    except Exception:
        data = {}
    merged = dict(DEFAULT_MODULES)
    if isinstance(data, dict):
        for k, v in data.items():
            if k in merged:
                merged[k] = _as_bool(v)
    return merged


def _write_modules(modules):
    frappe.db.set_single_value("System Settings", GLOBAL_SETTINGS_FIELD["fieldname"], json.dumps(modules))
    frappe.db.commit()


def _tenant_modules(raw, defaults=None):
    """Merge tenant overrides onto global module defaults."""
    modules = dict(defaults or DEFAULT_MODULES)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = None
    if isinstance(raw, dict):
        for key in modules:
            if key in raw:
                modules[key] = _as_bool(raw[key])
    return modules


def get_tenancy(company):
    doc = frappe.db.get_value("Company", company, [
        "name", "doorli_control_status", "doorli_control_reason",
        "doorli_plan", "doorli_ai_enabled", "doorli_plan_expires_on", "doorli_enabled_modules",
    ], as_dict=True)
    if not doc:
        return {
            "tenantId": company,
            "name": company,
            "status": "active",
            "plan": "trial",
            "aiEnabled": True,
            "enabledModules": dict(DEFAULT_MODULES),
        }
    return {
        "tenantId": doc["name"],
        "name": doc["name"],
        "status": doc.get("doorli_control_status") or "active",
        "reason": doc.get("doorli_control_reason") or "",
        "plan": doc.get("doorli_plan") or "trial",
        "aiEnabled": bool(doc.get("doorli_ai_enabled", 1)),
        "plan_expires_on": doc.get("doorli_plan_expires_on"),
        "maxUsers": doc.get("doorli_max_users"),
        "quotaOverride": doc.get("doorli_quota_override"),
        "enabledModules": _tenant_modules(doc.get("doorli_enabled_modules"), _read_modules()),
    }


def tenancy_allows_selling(company):
    """Orders from a suspended/locked/cancelled tenant are rejected at intake."""
    status = (frappe.db.get_value("Company", company, "doorli_control_status") or "active")
    return status in ("active", "")


def module_enabled(module_key, company=None):
    global_modules = _read_modules()
    if company:
        raw = frappe.db.get_value("Company", company, "doorli_enabled_modules")
        return _as_bool(_tenant_modules(raw, global_modules).get(module_key, True))
    return _as_bool(global_modules.get(module_key, True))


def maintenance_active():
    raw = _singles_value(MAINTENANCE_KEY)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                raw = parsed.get("active", False)
        except Exception:
            pass
    return _as_bool(raw, False)


# ---------------------------------------------------------------------------
# Control API methods (called by the marketplace control plane with the hook secret)
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def control_status(**kwargs):
    verify_doorli_webhook()
    _ensure_custom_fields()

    company = kwargs.get("company") or kwargs.get("tenantId") or None
    if company:
        companies = [get_tenancy(company)] if frappe.db.exists("Company", company) else []
    else:
        names = frappe.get_all("Company", pluck="name")
        companies = [get_tenancy(n) for n in names]

    return {
        "success": True,
        "providers": ["simple", "enterprise"],
        "provider": "enterprise",
            "globalModules": _read_modules(),
            "globalModuleToggles": _read_modules(),
            "maintenance": {"active": maintenance_active()},
            "settings": {"maintenance": maintenance_active()},
        "tenants": companies,
    }


@frappe.whitelist(allow_guest=True)
def control_tenant(**kwargs):
    verify_doorli_webhook()
    frappe.set_user("Administrator")
    _ensure_custom_fields()

    company = kwargs.get("company") or kwargs.get("tenantId") or ""
    if not company or not frappe.db.exists("Company", company):
        frappe.local.response.http_status_code = 400
        return {"success": False, "error": f"Unknown company {company}"}

    data = {}
    if "status" in kwargs:
        status = kwargs.get("status")
        if status not in ("active", "suspended", "locked", "cancelled"):
            frappe.local.response.http_status_code = 400
            return {"success": False, "error": f"Invalid status {status}"}
        data["doorli_control_status"] = status
    if "statusReason" in kwargs:
        data["doorli_control_reason"] = str(kwargs.get("statusReason"))[:200]
    if "plan" in kwargs:
        data["doorli_plan"] = str(kwargs.get("plan"))
    if "planExpiresAt" in kwargs and kwargs.get("planExpiresAt"):
        data["doorli_plan_expires_on"] = str(kwargs.get("planExpiresAt"))[:10]
    if "aiEnabled" in kwargs:
        data["doorli_ai_enabled"] = 1 if _as_bool(kwargs.get("aiEnabled")) else 0
    if "maxUsers" in kwargs and kwargs.get("maxUsers") is not None:
        data["doorli_max_users"] = int(kwargs.get("maxUsers"))

    if data:
        frappe.db.set_value("Company", company, data)
        frappe.db.commit()

    return {"success": True, "tenant": get_tenancy(company)}


@frappe.whitelist(allow_guest=True)
def control_module(**kwargs):
    verify_doorli_webhook()
    frappe.set_user("Administrator")
    _ensure_custom_fields()

    module_key = kwargs.get("moduleKey") or kwargs.get("module_key") or ""
    module_key = DOORLI_MODULE_ALIASES.get(module_key, module_key)
    if module_key not in DEFAULT_MODULES:
        frappe.local.response.http_status_code = 400
        return {"success": False, "error": f"Unknown module {module_key}"}

    company = kwargs.get("company") or kwargs.get("tenantId") or None
    enabled = _as_bool(kwargs.get("isEnabled", kwargs.get("enabled", True)))
    if company:
        if not frappe.db.exists("Company", company):
            frappe.local.response.http_status_code = 400
            return {"success": False, "error": f"Unknown company {company}"}
        current = _tenant_modules(frappe.db.get_value("Company", company, "doorli_enabled_modules"))
        current[module_key] = enabled
        frappe.db.set_value("Company", company, "doorli_enabled_modules", json.dumps(current))
        frappe.db.commit()
        return {"success": True, "tenant": get_tenancy(company)}

    modules = _read_modules()
    modules[module_key] = enabled
    _write_modules(modules)
    return {"success": True, "globalModules": _read_modules()}


@frappe.whitelist(allow_guest=True)
def control_quota(**kwargs):
    verify_doorli_webhook()
    frappe.set_user("Administrator")
    _ensure_custom_fields()

    company = kwargs.get("company") or kwargs.get("tenantId") or ""
    if not company or not frappe.db.exists("Company", company):
        frappe.local.response.http_status_code = 400
        return {"success": False, "error": f"Unknown company {company}"}

    data = {}
    if kwargs.get("plan"):
        data["doorli_plan"] = str(kwargs.get("plan"))
    if kwargs.get("maxUsers") is not None and kwargs.get("maxUsers") != "":
        data["doorli_max_users"] = int(kwargs.get("maxUsers"))

    if data:
        frappe.db.set_value("Company", company, data)
        frappe.db.commit()

    return {"success": True, "tenant": get_tenancy(company)}


@frappe.whitelist(allow_guest=True)
def control_settings(**kwargs):
    verify_doorli_webhook()
    frappe.set_user("Administrator")
    _ensure_custom_fields()

    value = kwargs.get("value")
    if kwargs.get("key") == "maintenance":
        if isinstance(value, dict):
            value = json.dumps(value)
        frappe.db.set_single_value("System Settings", MAINTENANCE_KEY, str(value))
        frappe.db.commit()

    return {"success": True, "settings": {"maintenance": maintenance_active()}}


# ---------------------------------------------------------------------------
# Hooks entrypoint
# ---------------------------------------------------------------------------

def after_migrate():
    _ensure_custom_fields()
