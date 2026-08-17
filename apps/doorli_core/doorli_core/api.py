import hmac
import hashlib
import json
import os
import re
import time

import frappe
import requests
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
    expected = _expected_secret()
    timestamp = frappe.request.headers.get("X-Doorli-Timestamp") or ""
    provided = (frappe.request.headers.get("X-Doorli-Signature") or "").replace("sha256=", "", 1).strip()
    valid = False
    try:
        timestamp_value = int(timestamp)
        if abs(int(time.time()) - timestamp_value) <= 300 and provided:
            raw_body = frappe.request.get_data(cache=True, as_text=True) or "{}"
            payload = f"{timestamp_value}.{raw_body}"
            expected_signature = hmac.new(expected.encode(), payload.encode(), hashlib.sha256).hexdigest()
            valid = hmac.compare_digest(provided, expected_signature)
    except (TypeError, ValueError):
        valid = False

    if not valid:
        frappe.local.response.http_status_code = 403
        frappe.throw(
            _("Unauthorized. Invalid or expired Doorli webhook signature."),
            frappe.PermissionError,
        )


def _signed_headers(payload):
    timestamp = str(int(time.time()))
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    signature = hmac.new(
        _expected_secret().encode(), f"{timestamp}.{body}".encode(), hashlib.sha256
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Doorli-Timestamp": timestamp,
        "X-Doorli-Signature": f"sha256={signature}",
    }


def _signed_body(payload):
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


# ---------------------------------------------------------------------------
# Provisioning helpers
# ---------------------------------------------------------------------------

def _make_abbr(vendor_id, business_name):
    """Deterministic, collision-resistant company abbreviation."""
    base = re.sub(r"[^A-Za-z0-9]", "", (business_name or "V")).upper()[:4] or "VEN"
    suffix = hashlib.sha1(str(vendor_id).encode()).hexdigest()[:6].upper()
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
        try:
            items = json.loads(items)
        except (TypeError, ValueError):
            frappe.local.response.http_status_code = 400
            return {"status": "error", "message": "items must be valid JSON"}

    if not isinstance(items, list):
        frappe.local.response.http_status_code = 400
        return {"status": "error", "message": "items must be an array"}

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
    from doorli_core.control import tenancy_allows_selling, module_enabled, maintenance_active
    if not tenancy_allows_selling(company):
        status = frappe.db.get_value("Company", company, "doorli_control_status")
        frappe.local.response.http_status_code = 409
        return {
            "status": "error",
            "message": f"Tenant {company} is {status} by Doorli super-admin; order intake disabled",
        }
    if not module_enabled("selling", company) or maintenance_active():
        frappe.local.response.http_status_code = 409
        return {
            "status": "error",
            "message": "Selling module disabled or Enterprise is in maintenance mode; order intake disabled",
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
            "currency": kwargs.get("currency") or "LKR",
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
            try:
                quantity = float(item.get("qty", 1))
                rate = float(item.get("price", 0.0))
            except (TypeError, ValueError):
                frappe.local.response.http_status_code = 400
                return {"status": "error", "message": "item qty and price must be numeric"}
            if quantity <= 0 or rate < 0:
                frappe.local.response.http_status_code = 400
                return {"status": "error", "message": "item qty must be positive and price cannot be negative"}
            sales_order.append("items", {
                "item_code": item_code,
                "qty": quantity,
                "rate": rate,
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
# Marketplace status callback
# ---------------------------------------------------------------------------
@frappe.whitelist(allow_guest=True)
def update_order_status(**kwargs):
    """Accept marketplace status updates for an Enterprise Sales Order.

    The callback is deliberately idempotent: repeated status notifications
    append no duplicate state mutation and return the canonical order id.
    """
    verify_doorli_webhook()
    frappe.set_user("Administrator")
    erp_order_id = kwargs.get("erp_order_id") or ""
    marketplace_order_id = kwargs.get("marketplace_order_id") or ""
    vendor_company = kwargs.get("vendor_company") or kwargs.get("company") or ""
    status = (kwargs.get("status") or "").strip().lower()
    if not status or not (erp_order_id or marketplace_order_id):
        frappe.local.response.http_status_code = 400
        return {"status": "error", "message": "erp_order_id or marketplace_order_id and status are required"}
    order_name = None
    if erp_order_id:
        order_name = frappe.db.get_value(
            "Sales Order",
            {"name": erp_order_id, **({"company": vendor_company} if vendor_company else {})},
            "name",
        )
    if not order_name and marketplace_order_id:
        order_name = frappe.db.get_value(
            "Sales Order",
            {"po_no": marketplace_order_id, **({"company": vendor_company} if vendor_company else {})},
            "name",
        )
    if not order_name:
        frappe.local.response.http_status_code = 404
        return {"status": "error", "message": "Sales Order not found"}
    try:
        order = frappe.get_doc("Sales Order", order_name)
        if status not in {"confirmed", "processing", "delivered", "completed", "cancelled"}:
            frappe.local.response.http_status_code = 400
            return {"status": "error", "message": f"Unsupported status: {status}"}

        # Replays must not create duplicate comments or repeat cancellation.
        marker = f"Doorli marketplace status: {status}"
        if frappe.db.exists("Comment", {"reference_doctype": "Sales Order", "reference_name": order.name, "content": ["like", f"%{marker}%"]}):
            return {"status": "success", "erp_order_id": order.name, "marketplace_order_id": marketplace_order_id, "order_status": status, "idempotent": True}

        if status == "cancelled" and order.docstatus == 1:
            order.cancel()
        elif status in {"delivered", "completed"} and order.docstatus == 1:
            order.db_set("status", "Completed")
        elif status == "processing" and order.docstatus == 1:
            order.db_set("status", "To Deliver and Bill")
        order.add_comment("Info", marker)
        frappe.db.commit()
        return {"status": "success", "erp_order_id": order.name, "marketplace_order_id": marketplace_order_id, "order_status": status, "idempotent": False}
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), _("Doorli update_order_status failed"))
        frappe.local.response.http_status_code = 500
        return {"status": "error", "message": str(e)}


@frappe.whitelist(allow_guest=True)
def get_inventory(**kwargs):
    """Return tenant-scoped stock for the marketplace inventory overlay."""
    verify_doorli_webhook()
    company = kwargs.get("company") or ""
    item_code = kwargs.get("item_code") or ""
    warehouse = kwargs.get("warehouse") or ""
    if not company or not item_code:
        frappe.local.response.http_status_code = 400
        return {"status": "error", "message": "company and item_code are required"}
    if not frappe.db.exists("Company", company):
        frappe.local.response.http_status_code = 404
        return {"status": "error", "message": "Unknown company"}
    filters = {"company": company, "item_code": item_code}
    if warehouse:
        filters["warehouse"] = warehouse
    rows = frappe.get_all("Stock Ledger Entry", filters=filters, fields=["actual_qty", "warehouse"])
    quantity = sum(float(row.get("actual_qty") or 0) for row in rows)
    return {"status": "success", "data": [{"actual_qty": quantity, "warehouse": warehouse or None}]}


@frappe.whitelist(allow_guest=True)
def sync_products(**kwargs):
    """Export a company-scoped Item catalog to the Marketplace product webhook."""
    verify_doorli_webhook()
    company = (kwargs.get("company") or "").strip()
    if not company or not frappe.db.exists("Company", company):
        frappe.local.response.http_status_code = 400 if company else 400
        return {"status": "error", "message": "A valid company is required"}

    target = os.environ.get("DOORLI_MARKETPLACE_PRODUCT_SYNC_URL", "").strip()
    if not target:
        frappe.local.response.http_status_code = 503
        return {"status": "error", "message": "Marketplace product sync URL is not configured"}

    raw_codes = kwargs.get("item_codes") or []
    if isinstance(raw_codes, str):
        raw_codes = [code.strip() for code in raw_codes.split(",") if code.strip()]
    filters = {"disabled": 0, "is_stock_item": 1}
    if raw_codes:
        filters["name"] = ["in", list(dict.fromkeys(raw_codes))]

    items = frappe.get_all(
        "Item",
        filters=filters,
        fields=["name", "item_code", "item_name", "description", "item_group", "stock_uom", "standard_rate", "barcode", "disabled"],
        order_by="name asc",
        limit_page_length=10000,
    )
    stock_rows = frappe.db.sql(
        """SELECT b.item_code, COALESCE(SUM(b.actual_qty), 0) AS quantity
           FROM `tabBin` b INNER JOIN `tabWarehouse` w ON w.name = b.warehouse
           WHERE w.company = %s GROUP BY b.item_code""",
        company,
        as_dict=True,
    )
    stock = {row["item_code"]: float(row.get("quantity") or 0) for row in stock_rows}
    products = [{
        "erp_tenant_id": company,
        "erp_item_id": item["name"],
        "sku": item.get("item_code") or item["name"],
        "barcode": item.get("barcode"),
        "name": item.get("item_name") or item["name"],
        "description": item.get("description"),
        "price": float(item.get("standard_rate") or 0),
        "unit": item.get("stock_uom"),
        "category": item.get("item_group"),
        "stock_quantity": stock.get(item["name"], 0),
        "is_active": not bool(item.get("disabled")),
    } for item in items]

    if not products:
        return {"status": "success", "company": company, "synced": 0, "failed": 0}

    try:
        payload = {"products": products}
        response = requests.post(
            target,
            data=_signed_body(payload).encode("utf-8"),
            headers=_signed_headers(payload),
            timeout=15,
        )
        response.raise_for_status()
        result = response.json()
        return {"status": "success", "company": company, "exported": len(products), "marketplace": result}
    except Exception as exc:
        frappe.log_error(frappe.get_traceback(), _("Doorli product catalog sync failed"))
        frappe.local.response.http_status_code = 502
        return {"status": "error", "company": company, "exported": len(products), "message": str(exc)}

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
