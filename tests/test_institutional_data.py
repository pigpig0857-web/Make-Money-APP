"""Official parser shape, date, accounting and missing-data invariants."""

import unittest
from datetime import date
from unittest.mock import patch

import requests

from backend.services.institutional_fetcher import parse_report, get_institutional_data


def twse_payload():
    fields = [""] * 19
    fields[0], fields[10], fields[11] = "證券代號", "投信買賣超股數", "自營商買賣超股數"
    row = ["0"] * 19
    row[0], row[1] = "2330", "台積電"
    row[4], row[7], row[10], row[11], row[18] = "1,000", "100", "-200", "50", "950"
    return {"stat": "OK", "date": "20261001", "fields": fields, "data": [row]}


def tpex_payload():
    fields = ["買賣超股數"] * 24
    fields[0], fields[1], fields[23] = "代號", "名稱", "三大法人買賣超股數合計"
    row = ["0"] * 24
    row[0], row[1] = "3293", "鈊象"
    row[4], row[7], row[10], row[13] = "1,000", "100", "1,100", "-200"
    row[16], row[19], row[22], row[23] = "20", "30", "50", "950"
    return {"stat": "ok", "tables": [{"date": "115/10/01", "fields": fields, "data": [row]}]}


class InstitutionalTests(unittest.TestCase):
    def test_twse_and_tpex_use_consistent_foreign_total(self):
        for market, payload, code in [("TWSE", twse_payload(), "2330"), ("TPEx", tpex_payload(), "3293")]:
            record = parse_report(payload, market, date(2026, 10, 1))[code]
            self.assertEqual(record["foreign_net"], 1100)
            self.assertEqual(record["total_net"], 950)
            self.assertEqual(record["trade_date"], date(2026, 10, 1))

    def test_wrong_date_or_layout_is_rejected(self):
        payload = twse_payload()
        with self.assertRaises(ValueError):
            parse_report(payload, "TWSE", date(2026, 9, 30))
        payload["fields"][11] = "未知欄位"
        with self.assertRaises(ValueError):
            parse_report(payload, "TWSE", date(2026, 10, 1))

    def test_missing_value_and_wrong_total_do_not_turn_into_zero(self):
        for value in ["--", "錯誤", "951"]:
            payload = twse_payload()
            payload["data"][0][18] = value
            self.assertEqual(parse_report(payload, "TWSE", date(2026, 10, 1)), {})

    def test_missing_days_do_not_look_like_complete_20_days(self):
        day1, day2 = date(2026, 9, 30), date(2026, 10, 1)
        record = parse_report(twse_payload(), "TWSE", day2)["2330"]
        with patch("backend.services.institutional_fetcher.load_institutional_flows", return_value=[]), patch("backend.services.institutional_fetcher.fetch_daily_report", side_effect=[{"2330": record}, {}]), patch("backend.services.institutional_fetcher.save_institutional_flows") as save:
            result = get_institutional_data("2330.TW", [day1, day2])
        self.assertTrue(result["available"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["expected_days"], 2)
        self.assertEqual(len(result["rows"]), 1)
        save.assert_called_once()

    def test_network_failure_keeps_stored_history_without_fake_days(self):
        day1, day2 = date(2026, 9, 30), date(2026, 10, 1)
        record = parse_report(twse_payload(), "TWSE", day2)["2330"]
        with patch("backend.services.institutional_fetcher.load_institutional_flows", return_value=[record]), patch("backend.services.institutional_fetcher.fetch_daily_report", side_effect=requests.Timeout):
            result = get_institutional_data("2330.TW", [day1, day2])
        self.assertEqual(result["rows"], [record])
        self.assertFalse(result["complete"])


if __name__ == "__main__":
    unittest.main()
