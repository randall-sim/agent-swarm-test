import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from api import handle
from storage import Store
from frontend import render_inventory


class ReservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "inventory.sqlite")
        self.store = Store(self.path)
        self.store.seed("book", 5)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def reserve(self, quantity=2, request_id="r1", sku="book"):
        return handle(self.store, "POST", "/reservations", dict(sku=sku, quantity=quantity, request_id=request_id))

    def stock(self):
        status, body = handle(self.store, "GET", "/inventory")
        self.assertEqual(status, 200)
        return next(i["available"] for i in body["items"] if i["sku"] == "book")

    def test_durable_idempotency(self):
        status, body = self.reserve()
        self.assertEqual(status, 201)
        record = body["reservation"]
        self.assertIs(type(record["id"]), int)
        self.assertEqual((record["sku"], record["quantity"], record["request_id"]), ("book", 2, "r1"))
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.reserve(), (200, body))
        self.assertEqual(self.stock(), 3)
        self.assertEqual(self.reserve(1)[0], 409)
        self.assertEqual(self.stock(), 3)

    def test_failure_is_atomic_and_retryable(self):
        self.assertEqual(self.reserve(6)[0], 409)
        self.assertEqual(self.stock(), 5)
        self.assertEqual(self.reserve(5)[0], 201)
        self.assertEqual(self.stock(), 0)
        self.assertEqual(self.reserve(1, "r2")[0], 409)

    def test_validation(self):
        for qty in (0, -1, True, 1.5, "2"):
            status, body = self.reserve(qty)
            self.assertEqual(status, 400)
            self.assertIsInstance(body["error"], str)
        self.assertEqual(self.reserve(request_id=" ")[0], 400)
        self.assertEqual(self.reserve(sku="missing")[0], 404)
        self.assertEqual(self.stock(), 5)

    def test_concurrent_connections_do_not_oversell(self):
        def attempt(i):
            store = Store(self.path)
            try:
                return handle(store, "POST", "/reservations", dict(sku="book", quantity=4, request_id=f"c{i}"))[0]
            finally:
                store.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(attempt, range(2)))
        self.assertEqual(sorted(statuses), [201, 409])
        self.assertEqual(self.stock(), 1)

    def test_frontend(self):
        html = render_inventory([dict(sku="<script>x</script>", available=0)])
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("Sold out", html)
        self.assertIn("7", render_inventory([dict(sku="book", available=7)]))
