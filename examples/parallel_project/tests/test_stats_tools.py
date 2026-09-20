import unittest
from stats_tools import median


class MedianTests(unittest.TestCase):
    def test_odd(self):
        self.assertEqual(median([9, 1, 3]), 3)

    def test_even(self):
        self.assertEqual(median([9, 1, 3, 5]), 4)

    def test_singleton(self):
        self.assertEqual(median([-2]), -2)

    def test_no_mutation(self):
        values = [3, 1, 2]
        median(values)
        self.assertEqual(values, [3, 1, 2])

    def test_empty(self):
        with self.assertRaises(ValueError):
            median([])
