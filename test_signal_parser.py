import hashlib
import hmac
import os
import unittest
from decimal import Decimal
from unittest.mock import patch

from telegram_bingx_signal_bot import bingx_request, create_signed_payload, parse_signal_message, send_trade


class ParseSignalMessageTests(unittest.TestCase):
    def test_supports_take_profit_and_stop_loss_labels(self):
        message = "BTC/USDT\nEntry: 64800\nTake Profit: 65400\nStop Loss: 64200"
        signal = parse_signal_message(message)
        self.assertIsNotNone(signal)
        self.assertEqual(signal["symbol"], "BTC-USDT")
        self.assertEqual(str(signal["entry"]), "64800")
        self.assertEqual(str(signal["take1"]), "65400")
        self.assertEqual(str(signal["take2"]), "65400")

    def test_supports_take_1_and_take_2_labels(self):
        message = "ETHUSDT\nEntry: 3500\nTake 1: 3600\nTake 2: 3700\nStop: 3400"
        signal = parse_signal_message(message)
        self.assertIsNotNone(signal)
        self.assertEqual(signal["symbol"], "ETH-USDT")
        self.assertEqual(str(signal["take1"]), "3600")
        self.assertEqual(str(signal["take2"]), "3700")

    def test_supports_russian_take_1_and_take_2_with_bullet_separator(self):
        message = "ZKC\nВход 0.04411\nТейк 1 0.0458744 · Тейк 2 0.0476388\nСтоп 0.039699"
        signal = parse_signal_message(message)
        self.assertIsNotNone(signal)
        self.assertEqual(signal["symbol"], "ZKC-USDT")
        self.assertEqual(str(signal["entry"]), "0.04411")
        self.assertEqual(str(signal["take1"]), "0.0458744")
        self.assertEqual(str(signal["take2"]), "0.0476388")

    def test_send_trade_uses_market_order_for_entry(self):
        signal = {
            "symbol": "BIGTIMEUSDT",
            "entry": Decimal("0.008094"),
            "take1": Decimal("0.00841776"),
            "take2": Decimal("0.00874152"),
            "stop": Decimal("0.0072846"),
            "side": "BUY",
        }
        captured = []

        def fake_bingx_request(method, path, api_key, secret_key, payload=None):
            captured.append(payload)
            return {"code": 0, "msg": "success"}

        with patch.dict(os.environ, {
            "BINGX_API_KEY": "test-key",
            "BINGX_SECRET_KEY": "test-secret",
            "BINGX_POSITION_SIZE": "1",
            "BINGX_QUOTE_ASSET": "USDT",
        }, clear=False):
            with patch("telegram_bingx_signal_bot.bingx_request", side_effect=fake_bingx_request):
                send_trade(signal)

        market_order = next(order for order in captured if order.get("type") == "MARKET")
        self.assertEqual(market_order["type"], "MARKET")
        self.assertNotIn("price", market_order)

    def test_bingx_request_rejects_non_zero_api_code_even_on_http_200(self):
        class DummyResponse:
            status_code = 200
            text = '{"code": 40004, "msg": "Balance not enough"}'

            def json(self):
                return {"code": 40004, "msg": "Balance not enough"}

        with patch("requests.request", return_value=DummyResponse()):
            with self.assertRaises(RuntimeError):
                bingx_request("POST", "/openApi/swap/v2/order", "api", "secret", {"symbol": "BTCUSDT"})

    def test_create_signed_payload_matches_bingx_query_string_contract(self):
        payload = {
            "symbol": "BTC-USDT",
            "side": "BUY",
            "positionSide": "LONG",
            "type": "MARKET",
            "quantity": 5,
            "takeProfit": '{"type":"TAKE_PROFIT_MARKET","stopPrice":31968.0,"price":31968.0,"workingType":"MARK_PRICE"}'
        }
        with patch("telegram_bingx_signal_bot.time.time", return_value=1700000000.123):
            signed = create_signed_payload("api-key", "secret-key", payload)

        self.assertIn("signature=", signed["payload"])
        self.assertIn("timestamp=1700000000123", signed["payload"])
        self.assertNotIn("X-BX-SIGNATURE", signed["payload"])

        params_list = [f"{key}={payload[key]}" for key in sorted(payload)]
        params_str = "&".join(params_list) + "&timestamp=1700000000123"
        expected = hmac.new("secret-key".encode("utf-8"), params_str.encode("utf-8"), hashlib.sha256).hexdigest()
        self.assertTrue(signed["payload"].endswith(f"&signature={expected}"))
        self.assertIn("takeProfit=%7B%22type%22%3A%22TAKE_PROFIT_MARKET%22%2C%22stopPrice%22%3A31968.0%2C%22price%22%3A31968.0%2C%22workingType%22%3A%22MARK_PRICE%22%7D", signed["payload"])

    def test_bingx_request_uses_signed_query_string_not_json_body(self):
        class DummyResponse:
            status_code = 200
            text = '{"code": 0, "msg": "success"}'

            def json(self):
                return {"code": 0, "msg": "success"}

        with patch("requests.request", return_value=DummyResponse()) as mocked:
            result = bingx_request("POST", "/openApi/swap/v2/trade/order", "api", "secret", {"symbol": "BTCUSDT", "side": "BUY"})

        self.assertEqual(result["code"], 0)
        self.assertIn("?", mocked.call_args.args[1])
        self.assertIn("signature=", mocked.call_args.args[1])
        self.assertEqual(mocked.call_args.kwargs["headers"], {"X-BX-APIKEY": "api"})

    def test_send_trade_configures_cross_margin_and_max_leverage_before_entry(self):
        signal = {
            "symbol": "FETUSDT",
            "entry": Decimal("0.48"),
            "take1": Decimal("0.50"),
            "take2": Decimal("0.52"),
            "stop": Decimal("0.46"),
            "side": "BUY",
        }
        captured = []

        def fake_bingx_request(method, path, api_key, secret_key, payload=None):
            captured.append({"method": method, "path": path, "payload": payload})
            return {"code": 0, "msg": "success"}

        with patch.dict(os.environ, {
            "BINGX_API_KEY": "test-key",
            "BINGX_SECRET_KEY": "test-secret",
            "BINGX_POSITION_SIZE": "1",
            "BINGX_QUOTE_ASSET": "USDT",
            "BINGX_MAX_LEVERAGE": "125",
            "STOP_LOSS": "2.5",
        }, clear=False):
            with patch("telegram_bingx_signal_bot.bingx_request", side_effect=fake_bingx_request):
                send_trade(signal)

        self.assertEqual(captured[0]["path"], "/openApi/swap/v2/trade/marginType")
        self.assertEqual(captured[0]["payload"]["marginType"], "CROSSED")
        self.assertEqual(captured[1]["path"], "/openApi/swap/v2/position/leverage")
        self.assertEqual(captured[1]["payload"]["leverage"], "125")

        market_order = next(order for order in captured if order["payload"].get("type") == "MARKET")
        self.assertEqual(market_order["payload"]["type"], "MARKET")

        stop_order = next(order for order in captured if order["payload"].get("stopPrice") is not None)
        self.assertEqual(Decimal(stop_order["payload"]["stopPrice"]), Decimal("0.468"))

    def test_send_trade_omits_reduce_only_in_hedge_mode(self):
        signal = {
            "symbol": "CRO-USDT",
            "entry": Decimal("0.10"),
            "take1": Decimal("0.11"),
            "take2": Decimal("0.12"),
            "stop": Decimal("0.09"),
            "side": "BUY",
        }
        captured = []

        def fake_bingx_request(method, path, api_key, secret_key, payload=None):
            captured.append(payload)
            return {"code": 0, "msg": "success"}

        with patch.dict(os.environ, {
            "BINGX_API_KEY": "test-key",
            "BINGX_SECRET_KEY": "test-secret",
            "BINGX_POSITION_SIZE": "1",
            "BINGX_QUOTE_ASSET": "USDT",
        }, clear=False):
            with patch("telegram_bingx_signal_bot.bingx_request", side_effect=fake_bingx_request):
                send_trade(signal)

        for order in captured:
            self.assertNotIn("reduceOnly", order)


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
