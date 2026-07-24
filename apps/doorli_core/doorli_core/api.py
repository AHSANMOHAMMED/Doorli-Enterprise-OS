import frappe
from frappe import _
import json

# =========================================================================
# SECURITY GATEWAY
# =========================================================================
def verify_doorli_webhook():
    """
    Ensures that the request is coming from the official Doorli Next.js backend.
    """
    secret_key = frappe.request.headers.get("Authorization")
    
    # In production, this should be a strong cryptographic secret configured in Site Settings
    # For now, we hardcode the initial secret key
    EXPECTED_SECRET = "Bearer DOORLI_ENTERPRISE_SECRET_2026_xyz"
    
    if secret_key != EXPECTED_SECRET:
        frappe.throw(_("Unauthorized. Invalid Doorli Enterprise Webhook Secret."), frappe.PermissionError)

# =========================================================================
# ORDER CREATION ENDPOINT
# =========================================================================
@frappe.whitelist(allow_guest=True)
def create_order(**kwargs):
    """
    Catches orders from the Doorli Marketplace and injects them into ERPNext.
    Expected JSON payload:
    {
        "marketplace_order_id": "uuid",
        "vendor_id": "tenant_uuid",
        "customer_name": "John Doe",
        "customer_phone": "+1234567890",
        "items": [
            {"item_name": "Burger", "qty": 2, "price": 10.50}
        ],
        "total_amount": 21.00
    }
    """
    # 1. Security Check
    verify_doorli_webhook()
    
    # 2. Parse the payload
    # Frappe auto-parses form-data and json into kwargs
    marketplace_order_id = kwargs.get("marketplace_order_id")
    customer_name = kwargs.get("customer_name", "Walk-in Customer")
    items = kwargs.get("items", [])
    
    if isinstance(items, str):
        items = json.loads(items)

    try:
        # 3. Ensure Customer Exists in ERP
        if not frappe.db.exists("Customer", customer_name):
            customer_doc = frappe.get_doc({
                "doctype": "Customer",
                "customer_name": customer_name,
                "customer_type": "Individual",
                "customer_group": "Commercial"
            })
            customer_doc.insert(ignore_permissions=True)
            frappe.db.commit()

        # 4. Generate Sales Order in ERPNext
        sales_order = frappe.get_doc({
            "doctype": "Sales Order",
            "customer": customer_name,
            "po_no": marketplace_order_id,
            "transaction_date": frappe.utils.today(),
            "delivery_date": frappe.utils.today(),
            "items": []
        })

        for item in items:
            item_name = item.get("item_name")
            
            # Auto-create Item if it doesn't exist in ERP inventory
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
                frappe.db.commit()

            sales_order.append("items", {
                "item_code": item_name,
                "qty": float(item.get("qty", 1)),
                "rate": float(item.get("price", 0.0))
            })

        # Insert and Submit the Sales Order
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
