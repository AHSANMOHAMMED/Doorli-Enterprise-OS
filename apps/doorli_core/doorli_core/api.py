import hmac
import json
import os
import re

import frappe
from frappe import _


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _expected_secret():
    """Shared secret required on both Doorli and Enterprise. No baked-in default."""
    secret = os.environ.get("DOORLI_WEBHOOK_SECRET", "").strip()
    if not secret:
        # Fail closed: a misconfigured node must not accept unauthenticated writes.
        frappe.throw(
            _("DOORLI_WEBHOOK_SECRET is not configured on the Enterprise node."),
            frappe.ValidationError,
        )
    return secret.replace("Bearer ", "", 1) if secret.startswith("Bearer ") else secret


def verify_doorli_webhook():
    # Use a custom header, NOT Authorization: Frappe treats `Authorization: Bearer`
    # as an OAuth token and rejects the request before guest methods run.
    header = frappe.request.headers.get("X-Doorli-Secret") or ""
    if not header:
        # Backwards-compatible fallback for older marketplace builds.
        auth = frappe.request.headers.get("Authorization") or ""
        header = auth[len("Bearer "):] if auth.startswith("Bearer ") else auth
    provided = header.strip()
    expected = _expected_secret()
    # Constant-time comparison to avoid leaking the secret via timing.
    if not provided or not hmac.compare_digest(provided, expected):
        frappe.local.response.http_status_code = 403
        frappe.throw(
            _("Unauthorized. Invalid Doorli Enterprise Webhook Secret."),
            frappe.PermissionError,
        )


# ---------------------------------------------------------------------------
# Provisioning helpers
# ---------------------------------------------------------------------------

def _make_abbr(vendor_id, business_name):
    """Deterministic, collision-resistant company abbreviation."""
    base = re.sub(r"[^A-Za-z0-9]", "", (business_name or "V")).upper()[:4] or "VEN"
    suffix = re.sub(r"[^A-Za-z0-9]", "", str(vendor_id)).upper()[:4]
    return (base + suffix)[:10] or "VEN"


def _company_name_for(vendor_id, business_name):
    base = (business_name or f"Vendor {str(vendor_id)[:8]}").strip()
    return base[:120]


def _ensure_erpnext_masters():
    """Backfill master records ERPNext expects when creating a Company/Order.

    On sites where the ERPNext setup wizard never completed, standard trees
    (Customer Group, Territory), Price Lists and the 'Transit' Warehouse Type
    are missing and inserts fail. Seed them idempotently so provisioning and
    order intake are self-healing on a fresh site.
    """
    if not frappe.db.exists("Customer Group", "All Customer Groups") or not frappe.db.exists(
        "Territory", "All Territories"
    ):
        try:
            from erpnext.setup.setup_wizard.operations import install_fixtures as erp_fixtures
            country = frappe.db.get_default("country") or "Sri Lanka"
            erp_fixtures.install(country)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "doorli_core fixture backfill failed")

    for wt in ("Transit",):
        if not frappe.db.exists("Warehouse Type", wt):
            frappe.get_doc({"doctype": "Warehouse Type", "name": wt}).insert(ignore_permissions=True)


def _selling_price_list():
    """Resolve (or create) an enabled selling Price List for Sales Orders."""
    name = frappe.db.get_value("Price List", {"selling": 1, "enabled": 1}, "name")
    if name:
        return name
    if not frappe.db.exists("Price List", "Standard Selling"):
        frappe.get_doc({
            "doctype": "Price List",
            "price_list_name": "Standard Selling",
            "currency": "LKR",
            "selling": 1,
            "buying": 0,
            "enabled": 1,
        }).insert(ignore_permissions=True)
    return "Standard Selling"


def _provision_company(vendor_id, business_name, currency="LKR"):
    """Create (idempotently) an isolated Company with a standard chart of accounts."""
    company_name = _company_name_for(vendor_id, business_name)

    if frappe.db.exists("Company", company_name):
        return company_name

    _ensure_erpnext_masters()

    company = frappe.get_doc({
        "doctype": "Company",
        "company_name": company_name,
        "abbr": _make_abbr(vendor_id, business_name),
        "default_currency": currency or "LKR",
        "country": "Sri Lanka",
        "create_chart_of_accounts_based_on": "Standard Template",
        "chart_of_accounts": "Standard",
    })
    # ERPNext builds the CoA, default receivable/payable, cost centers and
    # warehouses in Company.on_update; enabling perpetual inventory is optional.
    company.insert(ignore_permissions=True)
    return company.name


def create_vendor_user(email, first_name, company_name):
    """Create a vendor login scoped (via User Permission) to their own Company."""
    if not email:
        return
    if not frappe.db.exists("User", email):
        user = frappe.get_doc({
            "doctype": "User",
            "email": email,
            "first_name": first_name or email,
            "send_welcome_email": 0,
            "roles": [{"role": "Sales User"}],
        })
        user.insert(ignore_permissions=True)

    if not frappe.db.exists("User Permission", {"user": email, "allow": "Company", "for_value": company_name}):
        perm = frappe.get_doc({
            "doctype": "User Permission",
            "user": email,
            "allow": "Company",
            "for_value": company_name,
        })
        perm.insert(ignore_permissions=True)


@frappe.whitelist(allow_guest=True)
def provision_vendor(**kwargs):
    """Admin-triggered: create an isolated Company for an Enterprise vendor."""
    verify_doorli_webhook()
    # Guest has already been authenticated via the shared secret; elevate so we
    # can create Company / User / Permission records.
    frappe.set_user("Administrator")

    vendor_id = kwargs.get("vendor_id")
    business_name = kwargs.get("business_name") or ""
    admin_email = kwargs.get("admin_email") or ""
    currency = kwargs.get("currency") or "LKR"

    if not vendor_id:
        frappe.local.response.http_status_code = 400
        return {"status": "error", "message": "vendor_id is required"}

    try:
        company_name = _provision_company(vendor_id, business_name, currency)
        if admin_email:
            create_vendor_user(admin_email, business_name or company_name, company_name)
        frappe.db.commit()
        return {"status": "success", "company": company_name}
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), _("Doorli provision_vendor failed"))
        frappe.local.response.http_status_code = 500
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# Order intake
# ---------------------------------------------------------------------------

def _namespaced_customer(company, customer_name):
    """Keep customers isolated per Company so vendors never share records."""
    display = (customer_name or "Walk-in Customer").strip() or "Walk-in Customer"
    record_name = f"{display} [{company}]"[:140]
    if not frappe.db.exists("Customer", record_name):
        frappe.get_doc({
            "doctype": "Customer",
            "customer_name": record_name,
            "customer_type": "Individual",
            "customer_group": "Commercial",
            "territory": "All Territories",
        }).insert(ignore_permissions=True)
    return record_name


def _ensure_item(item_code, item_name):
    if not frappe.db.exists("Item", item_code):
        frappe.get_doc({
            "doctype": "Item",
            "item_code": item_code,
            "item_name": item_name or item_code,
            "item_group": "Products",
            "stock_uom": "Nos",
            "is_stock_item": 0,
        }).insert(ignore_permissions=True)


@frappe.whitelist(allow_guest=True)
def create_order(**kwargs):
    verify_doorli_webhook()
    # Guest has already been authenticated via the shared secret; elevate for
    # Sales Order / Item / Customer writes.
    frappe.set_user("Administrator")

    idempotency_key = kwargs.get("idempotency_key") or kwargs.get("marketplace_order_id")
    marketplace_order_id = kwargs.get("marketplace_order_id") or idempotency_key
    company = kwargs.get("company")
    vendor_id = kwargs.get("vendor_id")
    customer_name = kwargs.get("customer_name", "Walk-in Customer")
    items = kwargs.get("items", [])

    if isinstance(items, str):
        items = json.loads(items)

    if not idempotency_key:
        frappe.local.response.http_status_code = 400
        return {"status": "error", "message": "idempotency_key/marketplace_order_id is required"}

    if not items:
        frappe.local.response.http_status_code = 400
        return {"status": "error", "message": "items are required"}

    # Require a provisioned Company. Do not silently create one at order time.
    if not company or not frappe.db.exists("Company", company):
        frappe.local.response.http_status_code = 400
        return {
            "status": "error",
            "message": "Unknown or unprovisioned company; provision the vendor first",
        }

    # Super-admin control plane gate: reject intake for suspended/locked tenants
    # and for tenants cut off by module policy.
    from doorli_core.control import tenancy_allows_selling, module_enabled
    if not tenancy_allows_selling(company):
        status = frappe.db.get_value("Company", company, "doorli_control_status")
        frappe.local.response.http_status_code = 409
        return {
            "status": "error",
            "message": f"Tenant {company} is {status} by Doorli super-admin; order intake disabled",
        }
    if not module_enabled("selling"):
        frappe.local.response.http_status_code = 409
        return {
            "status": "error",
            "message": "Selling module disabled by Doorli super-admin; order intake disabled",
        }

    # Idempotency: a repeat callback returns the existing order rather than duplicating.
    existing = frappe.db.get_value(
        "Sales Order", {"po_no": idempotency_key, "company": company}, "name"
    )
    if existing:
        return {
            "status": "success",
            "message": "Order already exists (idempotent)",
            "erp_order_id": existing,
        }

    try:
        _ensure_erpnext_masters()
        customer = _namespaced_customer(company, customer_name)

        sales_order = frappe.get_doc({
            "doctype": "Sales Order",
            "customer": customer,
            "company": company,
            "po_no": idempotency_key,
            "currency": "LKR",
            "conversion_rate": 1,
            "selling_price_list": _selling_price_list(),
            "price_list_currency": "LKR",
            "plc_conversion_rate": 1,
            "transaction_date": frappe.utils.today(),
            "delivery_date": frappe.utils.today(),
            "items": [],
        })

        for item in items:
            item_code = item.get("item_code") or item.get("item_name")
            if not item_code:
                continue
            _ensure_item(item_code, item.get("item_name"))
            sales_order.append("items", {
                "item_code": item_code,
                "qty": float(item.get("qty", 1)),
                "rate": float(item.get("price", 0.0)),
            })

        if not sales_order.items:
            frappe.local.response.http_status_code = 400
            return {"status": "error", "message": "No valid items supplied"}

        sales_order.insert(ignore_permissions=True)
        sales_order.submit()
        frappe.db.commit()

        return {
            "status": "success",
            "message": "Order successfully injected into Doorli Enterprise OS",
            "erp_order_id": sales_order.name,
        }

    except frappe.DuplicateEntryError:
        # Lost an idempotency race — return the winner instead of erroring.
        frappe.db.rollback()
        winner = frappe.db.get_value(
            "Sales Order", {"po_no": idempotency_key, "company": company}, "name"
        )
        if winner:
            return {"status": "success", "message": "Order already exists (idempotent)", "erp_order_id": winner}
        frappe.local.response.http_status_code = 409
        return {"status": "error", "message": "Duplicate order"}

    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), _("Doorli Webhook Sync Failed"))
        frappe.local.response.http_status_code = 500
        return {"status": "error", "message": str(e)}

# ---------------------------------------------------------------------------
# Super-admin control plane passthroughs.
# The marketplace control plane calls doorli_core.api.control_* (see
# services/api/src/lib/control.ts) which delegate to the doorli_core.control
# channel that persists tenant / module / maintenance state.
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def control_status(**kwargs):
    from doorli_core import control as _control
    return _control.control_status(**kwargs)


@frappe.whitelist(allow_guest=True)
def control_tenant(**kwargs):
    from doorli_core import control as _control
    return _control.control_tenant(**kwargs)


@frappe.whitelist(allow_guest=True)
def control_module(**kwargs):
    from doorli_core import control as _control
    return _control.control_module(**kwargs)


@frappe.whitelist(allow_guest=True)
def control_settings(**kwargs):
    from doorli_core import control as _control
    return _control.control_settings(**kwargs)


@frappe.whitelist(allow_guest=True)
def control_quota(**kwargs):
    from doorli_core import control as _control
    return _control.control_quota(**kwargs)
