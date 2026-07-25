import frappe
from frappe import _
import json

def verify_doorli_webhook():
    secret_key = frappe.request.headers.get("Authorization")
    EXPECTED_SECRET = "Bearer DOORLI_ENTERPRISE_SECRET_2026_xyz"
    if secret_key != EXPECTED_SECRET:
        frappe.throw(_("Unauthorized. Invalid Doorli Enterprise Webhook Secret."), frappe.PermissionError)

def get_or_create_vendor_company(vendor_id):
    """
    Maps the marketplace vendor_id to a Frappe Company record for strict data partitioning.
    If it doesn't exist, it creates a lightweight Company profile.
    """
    if not vendor_id:
        return frappe.defaults.get_user_default("Company")
        
    company_name = f"Vendor_{vendor_id[:8]}"
    
    if not frappe.db.exists("Company", company_name):
        doc = frappe.get_doc({
            "doctype": "Company",
            "company_name": company_name,
            "default_currency": "USD",
            "country": "United States"
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

    try:
        # 1. Resolve Vendor -> Company
        company_id = get_or_create_vendor_company(vendor_id)

        # 2. Ensure Customer Exists
        if not frappe.db.exists("Customer", customer_name):
            customer_doc = frappe.get_doc({
                "doctype": "Customer",
                "customer_name": customer_name,
                "customer_type": "Individual",
                "customer_group": "Commercial"
            })
            customer_doc.insert(ignore_permissions=True)

        # 3. Generate Sales Order mapped to the Vendor's Company
        sales_order = frappe.get_doc({
            "doctype": "Sales Order",
            "customer": customer_name,
            "company": company_id,
            "po_no": marketplace_order_id,
            "transaction_date": frappe.utils.today(),
            "delivery_date": frappe.utils.today(),
            "items": []
        })

        for item in items:
            item_name = item.get("item_name")
            if not frappe.db.exists("Item", item_name):
                new_item = frappe.get_doc({
                    "doctype": "Item",
                    "item_code": item_name,
                    "item_name": item_name,
                    "item_group": "Products",
                    "stock_uom": "Nos",
                    "is_stock_item": 0
                })
                new_item.insert(ignore_permissions=True)

            sales_order.append("items", {
                "item_code": item_name,
                "qty": float(item.get("qty", 1)),
                "rate": float(item.get("price", 0.0))
            })

        sales_order.insert(ignore_permissions=True)
        sales_order.submit()
        frappe.db.commit()

        return {
            "status": "success",
            "message": "Order successfully injected into Doorli Enterprise OS",
            "erp_order_id": sales_order.name
        }

    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), _("Doorli Webhook Sync Failed"))
        return {
            "status": "error",
            "message": str(e)
        }
