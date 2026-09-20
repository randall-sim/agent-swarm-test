"""Replace this placeholder with a transactional SQLite adapter."""
class Store:
    def __init__(self, path):
        self.path = path

    def seed(self, sku, quantity):
        pass

    def close(self):
        pass
