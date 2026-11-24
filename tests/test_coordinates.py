"""Casos representativos para el conversor local, sin datos de clientes."""

import unittest

from SolarCAD_V3 import convert_google_coordinates


class CoordinateTests(unittest.TestCase):
    def test_public_point_with_puerto_rico_grid(self):
        lat, lon, x, y = convert_google_coordinates("18.4655, -66.1057")
        self.assertEqual((lat, lon), ("18.4655", "-66.1057"))
        self.assertAlmostEqual(float(x), 234606.185, places=2)
        self.assertAlmostEqual(float(y), 269999.539, places=2)

    def test_accepts_semicolon_and_whitespace(self):
        self.assertEqual(convert_google_coordinates("18.4655; -66.1057"),
                         convert_google_coordinates("18.4655 -66.1057"))

    def test_rejects_swapped_invalid_and_outside_area(self):
        for value in ("-66.1057, 18.4655", "18.4655, 66.1057",
                      "18.4655, -99.5", "18,4655, -66,1057", "18.5 and -66.1", ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                convert_google_coordinates(value)


if __name__ == "__main__":
    unittest.main()
