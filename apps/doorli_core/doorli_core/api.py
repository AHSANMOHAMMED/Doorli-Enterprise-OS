import frappe
from frappe import _
import json
import os


def verify_doorli_webhook():
    secret_key = frappe.request.headers.get("Authorization") or ""
    expected = os.environ.get("DOORLI_WEBHOOK_SECRET", "DOORLI_ENTERPRISE_SECRET_2026_xyz")
    if expected.startswith("Bearer "):
        expected_header = expected
    else:
        expected_header = f"Bearer {expected}"
    if secret_key != expected_header:
        frappe.throw(_("Unauthorized. Invalid Doorli Enterprise Webhook Secret."), frappe.PermissionError)


def get_or_create_vendor_company(vendor_id):
    """
    Maps the marketplace vendor_id to a Frappe Company record for data partitioning.
    """
    if not vendor_id:
        return frappe.defaults.get_user_default("Company")

    company_name = f"Vendor_{str(vendor_id)[:8]}"

    if not frappe.db.exists("Company", company_name):
        doc = frappe.get_doc({
            "doctype": "Company",
            "company_name": company_name,
            "default_currency": "LKR",
            "country": "Sri Lanka",
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()

    return company_name


@frappe.whitelist(allow_guest=True)
def create_order(**kwargs):
    verify_doorli_webhook()

    marketplace_order_id = kwargs.get("marketplace_order_id")
    vendor_id = kwargs.get("vendor_id")
    customer_name = kwargs.get("customer_name", "Walk-in Customer")
    items = kwargs.get("items", [])

    if isinstance(items, str):
        items = json.loads(items)

    if marketplace_order_id:
        existing = frappe.db.get_value("Sales Order", {"po_no": marketplace_order_id}, "name")
        if existing:
            return {
                "status": "success",
                "message": "Order already exists (idempotent)",
                "erp_order_id": existing,
            }

    try:
        company_id = get_or_create_vendor_company(vendor_id)

        if not frappe.db.exists("Customer", customer_name):
            customer_doc = frappe.get_doc({
                "doctype": "Customer",
                "customer_name": customer_name,
                "customer_type": "Individual",
                "customer_group": "Commercial",
            })
            customer_doc.insert(ignore_permissions=True)

        sales_order = frappe.get_doc({
            "doctype": "Sales Order",
            "customer": customer_name,
            "company": company_id,
            "po_no": marketplace_order_id,
            "currency": "LKR",
            "transaction_date": frappe.utils.today(),
            "delivery_date": frappe.utils.today(),
            "items": [],
        })

        for item in items:
            item_name = item.get("item_name")
            if not item_name:
                continue
            if not frappe.db.exists("Item", item_name):
                new_item = frappe.get_doc({
                    "doctype": "Item",
                    "item_code": item_name,
                    "item_name": item_name,
                    "item_group": "Products",
                    "stock_uom": "Nos",
                    "is_stock_item": 0,
                })
                new_item.insert(ignore_permissions=True)

            sales_order.append("items", {
                "item_code": item_name,
                "qty": float(item.get("qty", 1)),
                "rate": float(item.get("price", 0.0)),
            })

        sales_order.insert(ignore_permissions=True)
        sales_order.submit()
        frappe.db.commit()

        return {
            "status": "success",
            "message": "Order successfully injected into Doorli Enterprise OS",
            "erp_order_id": sales_order.name,
        }

    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), _("Doorli Webhook Sync Failed"))
        return {
            "status": "error",
            "message": str(e),
        }


def create_vendor_user(email, first_name, company_name):
    """
    Creates a User account for a vendor so they can log in.
    Automatically assigns them to their specific Company.
    """
    if not frappe.db.exists("User", email):
        user = frappe.get_doc({
            "doctype": "User",
            "email": email,
            "first_name": first_name,
            "send_welcome_email": 1,
            "roles": [{"role": "Sales User"}],
        })
        user.insert(ignore_permissions=True)

        perm = frappe.get_doc({
            "doctype": "User Permission",
            "user": email,
            "allow": "Company",
            "for_value": company_name,
        })
        perm.insert(ignore_permissions=True)
        frappe.db.commit()
