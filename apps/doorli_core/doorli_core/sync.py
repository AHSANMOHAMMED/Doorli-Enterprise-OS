import frappe
import requests
import os


def send_status_to_marketplace(doc, method):
    """
    Reverse webhook: Triggered on Sales Order update.
    Maps ERPNext statuses onto Doorli marketplace statuses.
    """
    # ERPNext Sales Order statuses that mean the order is done for marketplace delivery
    if doc.status not in ("Completed", "Closed"):
        return

    marketplace_status = "delivered"
    marketplace_api_url = os.environ.get(
        "DOORLI_MARKETPLACE_ORDER_STATUS_URL",
        "https://doorli.me/api/v1/erp-webhooks/order-status",
    )
    secret = os.environ.get("DOORLI_WEBHOOK_SECRET", "DOORLI_ENTERPRISE_SECRET_2026_xyz")
    if not secret.startswith("Bearer "):
        secret = f"Bearer {secret}"

    payload = {
        "erp_order_id": doc.name,
        "marketplace_order_id": doc.po_no,
        "status": marketplace_status,
        "vendor_company": doc.company,
    }

    headers = {
        "Authorization": secret,
        "Content-Type": "application/json",
    }

    try:
        # Prefer background enqueue when available; fall back to sync POST.
        requests.post(marketplace_api_url, json=payload, headers=headers, timeout=5)
    except Exception as e:
        frappe.log_error(f"Reverse Webhook Failed: {str(e)}", "Doorli Sync")
