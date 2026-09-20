"""Framework-independent HTTP request handler; no networking required for tests."""
def handle(store, method, path, body=None):
    return 501, {"error": "Not implemented"}
