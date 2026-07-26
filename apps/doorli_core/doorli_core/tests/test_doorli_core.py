"""Contract tests for doorli_core.

Run inside a bench:
    bench --site enterprise.doorli.me run-tests --app doorli_core
"""
import os
import unittest
from unittest.mock import patch

import frappe

from doorli_core import api, sync


class TestSecretAndHelpers(unittest.TestCase):
    """Pure-logic tests that need no database."""

    def test_make_abbr_is_deterministic_and_bounded(self):
        a = api._make_abbr("vendor-123456", "Acme Traders")
        b = api._make_abbr("vendor-123456", "Acme Traders")
        self.assertEqual(a, b)
        self.assertLessEqual(len(a), 10)
        self.assertTrue(a.isalnum())

    def test_company_name_truncated(self):
        name = api._company_name_for("v1", "X" * 300)
        self.assertLessEqual(len(name), 120)

    def test_missing_secret_fails_closed(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DOORLI_WEBHOOK_SECRET", None)
            with self.assertRaises(frappe.exceptions.ValidationError):
                api._expected_secret()

    def test_secret_strips_bearer_prefix(self):
        with patch.dict(os.environ, {"DOORLI_WEBHOOK_SECRET": "Bearer abc123"}):
            self.assertEqual(api._expected_secret(), "abc123")


class TestWebhookAuth(unittest.TestCase):
    def setUp(self):
        os.environ["DOORLI_WEBHOOK_SECRET"] = "unit_test_secret"

    def _with_auth(self, header_value):
        class _Req:
            headers = {"Authorization": header_value}
        return patch.object(frappe, "request", _Req(), create=True)

    def test_rejects_wrong_secret(self):
        with self._with_auth("Bearer nope"):
            with self.assertRaises(frappe.exceptions.PermissionError):
                api.verify_doorli_webhook()

    def test_accepts_correct_secret(self):
        with self._with_auth("Bearer unit_test_secret"):
            api.verify_doorli_webhook()  # should not raise


class TestProvisioningAndOrders(unittest.TestCase):
    """Integration tests that exercise the database (require a test site)."""

    def setUp(self):
        os.environ["DOORLI_WEBHOOK_SECRET"] = "unit_test_secret"
        frappe.request = type("R", (), {"headers": {"Authorization": "Bearer unit_test_secret"}})()
        self.vendor_id = "vendor-test-0001"

    def test_provision_is_idempotent(self):
        r1 = api.provision_vendor(vendor_id=self.vendor_id, business_name="Test Vendor Co")
        self.assertEqual(r1["status"], "success")
        company = r1["company"]
        self.assertTrue(frappe.db.exists("Company", company))
        r2 = api.provision_vendor(vendor_id=self.vendor_id, business_name="Test Vendor Co")
        self.assertEqual(r2["company"], company)

    def test_create_order_requires_provisioned_company(self):
        res = api.create_order(
            idempotency_key="mp-req-1",
            company="No Such Company ZZZ",
            items=[{"item_code": "x", "qty": 1, "price": 10}],
        )
        self.assertEqual(res["status"], "error")

    def test_order_replay_is_idempotent(self):
        prov = api.provision_vendor(vendor_id=self.vendor_id, business_name="Test Vendor Co")
        company = prov["company"]
        payload = dict(
            idempotency_key="mp-replay-1",
            marketplace_order_id="mp-replay-1",
            company=company,
            vendor_id=self.vendor_id,
            customer_name="Buyer One",
            items=[{"item_code": f"{self.vendor_id}:SKU1", "item_name": "Thing", "qty": 2, "price": 100}],
        )
        first = api.create_order(**payload)
        self.assertEqual(first["status"], "success")
        second = api.create_order(**payload)
        self.assertEqual(second["status"], "success")
        self.assertEqual(first["erp_order_id"], second["erp_order_id"])

    def tearDown(self):
        frappe.db.rollback()


class TestCallbackPayload(unittest.TestCase):
    def test_enqueue_skips_orders_without_marketplace_id(self):
        with patch.object(frappe, "enqueue") as enq:
            sync._enqueue_status(None, "SO-1", "confirmed", "Acme")
            enq.assert_not_called()

    def test_enqueue_fires_for_marketplace_orders(self):
        with patch.object(frappe, "enqueue") as enq:
            sync._enqueue_status("mp-1", "SO-1", "delivered", "Acme")
            enq.assert_called_once()
            kwargs = enq.call_args.kwargs
            self.assertEqual(kwargs["marketplace_order_id"], "mp-1")
            self.assertEqual(kwargs["status"], "delivered")
            self.assertTrue(kwargs["enqueue_after_commit"])
