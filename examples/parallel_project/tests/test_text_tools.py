import unittest
from text_tools import slugify


class SlugTests(unittest.TestCase):
    def test_words(self):
        self.assertEqual(slugify("Hello World"), "hello-world")

    def test_punctuation(self):
        self.assertEqual(slugify("  API v2: ready?!  "), "api-v2-ready")

    def test_empty(self):
        self.assertEqual(slugify(""), "")
        self.assertEqual(slugify("---"), "")

    def test_runs(self):
        self.assertEqual(slugify("a___b   c"), "a-b-c")
