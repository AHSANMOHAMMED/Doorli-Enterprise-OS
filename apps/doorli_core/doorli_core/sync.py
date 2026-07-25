import frappe
import requests
import json

def send_status_to_marketplace(doc, method):
    """
    Reverse webhook: Triggered on Sales Order update.
    If the status is Completed or Delivered, we beam a notification back to the Doorli Next.js app.
    """
    if doc.status not in ["Completed", "Delivered"]:
        return
        
    # The URL of your Next.js marketplace API
    MARKETPLACE_API_URL = "https://doorli.me/api/erp-sync"
    
    # We should secure this with a secret in a real environment
    SECRET = "DOORLI_ENTERPRISE_SECRET_2026_xyz"
    
    payload = {
        "erp_order_id": doc.name,
        "marketplace_order_id": doc.po_no,
        "status": doc.status,
        "vendor_company": doc.company
    }
    
    headers = {
        "Authorization": f"Bearer {SECRET}",
        "Content-Type": "application/json"
    }
    
    try:
        # Fire and forget. In production, this should ideally be in a background job queue.
        requests.post(MARKETPLACE_API_URL, json=payload, headers=headers, timeout=5)
    except Exception as e:
        frappe.log_error(f"Reverse Webhook Failed: {str(e)}", "Doorli Sync")
