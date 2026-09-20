import tempfile
import unittest
from pathlib import Path
from api import handle
from storage import Store
from frontend import render_board


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "board.sqlite")
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def create(self, title=" First task "):
        status, body = handle(self.store, "POST", "/tasks", {"title": title})
        self.assertEqual(status, 201)
        task = body["task"]
        self.assertIs(type(task["id"]), int)
        self.assertIs(task["done"], False)
        return task

    def test_roundtrip_and_persistence(self):
        first = self.create()
        second = self.create("Second")
        self.assertEqual(first["title"], "First task")
        status, body = handle(self.store, "PATCH", f'/tasks/{first["id"]}', {"done": True})
        self.assertEqual(status, 200)
        self.assertIs(body["task"]["done"], True)
        self.store.close()
        self.store = Store(self.path)
        status, body = handle(self.store, "GET", "/tasks")
        self.assertEqual(status, 200)
        self.assertEqual([t["id"] for t in body["tasks"]], [first["id"], second["id"]])
        self.assertIs(body["tasks"][0]["done"], True)

    def test_validation_does_not_write(self):
        for title in (" ", None, 42, "x" * 121):
            status, body = handle(self.store, "POST", "/tasks", {"title": title})
            self.assertEqual(status, 400)
            self.assertIsInstance(body["error"], str)
        self.assertEqual(handle(self.store, "GET", "/tasks"), (200, {"tasks": []}))

    def test_boolean_and_missing(self):
        task = self.create()
        self.assertEqual(handle(self.store, "PATCH", f'/tasks/{task["id"]}', {"done": "false"})[0], 400)
        self.assertEqual(handle(self.store, "PATCH", "/tasks/99999", {"done": True})[0], 404)
        self.assertEqual(handle(self.store, "GET", "/unknown")[0], 404)

    def test_frontend_escapes_and_matches_api(self):
        task = self.create('<script>alert("x")</script>')
        html = render_board([task])
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("data-task-id", html)
        self.assertIn(str(task["id"]), html)
        self.assertIn("open", html.lower())
        self.assertIn("done", render_board([{**task, "done": True}]).lower())
        self.assertIn("No tasks", render_board([]))
