import unittest
from date_tools import is_leap_year


class LeapYearTests(unittest.TestCase):
    def test_divisible_by_four(self):
        self.assertTrue(is_leap_year(2024))

    def test_regular_year(self):
        self.assertFalse(is_leap_year(2023))

    def test_century(self):
        self.assertFalse(is_leap_year(1900))

    def test_four_centuries(self):
        self.assertTrue(is_leap_year(2000))
