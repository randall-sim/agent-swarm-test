"""Implement the SQLite adapter; agree its methods with the API worker first."""
class Store:
    def __init__(self, path):
        self.path = path

    def close(self):
        pass
