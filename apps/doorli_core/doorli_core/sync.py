import os

import frappe
import requests

from doorli_core.api import _expected_secret


def _marketplace_url():
    return os.environ.get(
        "DOORLI_MARKETPLACE_ORDER_STATUS_URL",
        "https://doorli.me/api/v1/erp-webhooks/order-status",
    )


def _enqueue_status(marketplace_order_id, erp_order_id, status, company=None):
    """Queue a reverse status callback so ERPNext transactions never block on HTTP."""
    if not marketplace_order_id:
        # Orders not originating from the marketplace have no callback target.
        return
    frappe.enqueue(
        "doorli_core.sync.push_status_to_marketplace",
        queue="short",
        # Only fire after the DB transaction commits, and retry on transient failure.
        enqueue_after_commit=True,
        job_id=f"doorli-status-{marketplace_order_id}-{status}",
        deduplicate=True,
        marketplace_order_id=marketplace_order_id,
        erp_order_id=erp_order_id,
        status=status,
        company=company,
    )


# --- Document event hooks -------------------------------------------------

def on_sales_order_submit(doc, method=None):
    """Submitted Sales Order → marketplace 'confirmed'."""
    _enqueue_status(doc.get("po_no"), doc.name, "confirmed", doc.get("company"))


def on_sales_order_cancel(doc, method=None):
    """Cancelled Sales Order → marketplace 'cancelled'."""
    _enqueue_status(doc.get("po_no"), doc.name, "cancelled", doc.get("company"))


def on_delivery_note_submit(doc, method=None):
    """Submitted Delivery Note → marketplace 'delivered'."""
    marketplace_order_id = _delivery_note_marketplace_id(doc)
    _enqueue_status(marketplace_order_id, doc.name, "delivered", doc.get("company"))


def _delivery_note_marketplace_id(doc):
    """Resolve the originating marketplace order id from a Delivery Note's Sales Orders."""
    for item in doc.get("items", []):
        so = item.get("against_sales_order")
        if so:
            po_no = frappe.db.get_value("Sales Order", so, "po_no")
            if po_no:
                return po_no
    return None


# --- Background job -------------------------------------------------------

def push_status_to_marketplace(marketplace_order_id, erp_order_id, status, company=None):
    """POST a status update to Doorli. Raises on failure so RQ retries the job."""
    secret = _expected_secret()
    payload = {
        "erp_order_id": erp_order_id,
        "marketplace_order_id": marketplace_order_id,
        "status": status,
        "vendor_company": company,
    }
    headers = {
        "Authorization": f"Bearer {secret}",
        "Content-Type": "application/json",
    }

    resp = requests.post(_marketplace_url(), json=payload, headers=headers, timeout=10)
    # 2xx is success; the marketplace treats repeat statuses as idempotent no-ops.
    if resp.status_code >= 400:
        frappe.log_error(
            f"Marketplace callback {status} for {marketplace_order_id} "
            f"failed: {resp.status_code} {resp.text[:500]}",
            "Doorli Sync",
        )
        # Re-raise to let the queue retry transient/marketplace-side failures.
        resp.raise_for_status()
