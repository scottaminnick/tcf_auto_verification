"""Regression coverage for exact TCF issuance resolution and provenance."""

import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

import geopandas as gpd

import tcf_pipeline


def _iem_response(issue_stamp, product="CFP02"):
    return f"""
    <meta property="og:image"
          content="https://mesonet.agron.iastate.edu/wx/afos/{issue_stamp}_{product}.png">
    <pre>AREA 3 0 0 300 0 0 3 400 1000 410 1000 405 990</pre>
    """


class _Response:
    status_code = 200

    def __init__(self, text):
        self.text = text


class TCFForecastProvenanceTests(unittest.TestCase):
    def test_iem_fallback_is_rejected_then_exact_retry_succeeds(self):
        """A 17Z response can never populate or satisfy the 19Z identity."""
        replies = [
            _Response(_iem_response("202609051700")),
            _Response(_iem_response("202609051900")),
        ]
        with patch("requests.get", side_effect=replies) as get:
            with self.assertRaisesRegex(RuntimeError, "fallback TCF"):
                tcf_pipeline.fetch_iem_cow_tcf(date(2026, 9, 5), 19, 4)

            forecast = tcf_pipeline.fetch_iem_cow_tcf(
                date(2026, 9, 5), 19, 4)

        self.assertEqual(get.call_count, 2, "failed fallback must not be reused")
        provenance = forecast.attrs["forecast_provenance"]
        requested = datetime(2026, 9, 5, 19)
        valid = datetime(2026, 9, 5, 23)
        self.assertEqual(provenance["product"], "CFP02")
        self.assertEqual(provenance["issue_time"], requested)
        self.assertEqual(provenance["forecast_hour"], 4)
        self.assertEqual(provenance["valid_time"], valid)
        self.assertFalse(provenance["fallback_used"])
        self.assertIs(
            tcf_pipeline.validate_forecast_provenance(
                forecast, requested, 4, valid),
            provenance,
        )

    def test_cached_17z_geometry_cannot_validate_as_19z(self):
        forecast = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
        issue_17z = datetime(2026, 9, 5, 17)
        forecast.attrs["forecast_provenance"] = {
            "product": "CFP02",
            "issue_time": issue_17z,
            "forecast_hour": 4,
            "valid_time": issue_17z + timedelta(hours=4),
            "source_identifier": "synthetic cached 17Z object",
            "fallback_used": True,
        }
        with self.assertRaisesRegex(ValueError, "does not match"):
            tcf_pipeline.validate_forecast_provenance(
                forecast, datetime(2026, 9, 5, 19), 4,
                datetime(2026, 9, 5, 23))

    def test_unsupported_forecast_hour_does_not_alias_f04(self):
        with self.assertRaisesRegex(ValueError, "unsupported"):
            tcf_pipeline.tcf_product_for_forecast_hour(5)


if __name__ == "__main__":
    unittest.main()
