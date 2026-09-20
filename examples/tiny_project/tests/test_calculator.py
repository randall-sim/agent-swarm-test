import unittest
from calculator import average


class AverageTests(unittest.TestCase):
    def test_values(self):
        self.assertEqual(average([2, 4, 6]), 4)

    def test_empty(self):
        with self.assertRaisesRegex(ValueError, "at least one"):
            average([])


if __name__ == "__main__":
    unittest.main()
