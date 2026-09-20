import os
import unittest
from decimal import Decimal
from unittest.mock import patch

from telegram_bingx_signal_bot import bingx_request, parse_signal_message, send_trade


class ParseSignalMessageTests(unittest.TestCase):
    def test_supports_take_profit_and_stop_loss_labels(self):
        message = "BTC/USDT\nEntry: 64800\nTake Profit: 65400\nStop Loss: 64200"
        signal = parse_signal_message(message)
        self.assertIsNotNone(signal)
        self.assertEqual(signal["symbol"], "BTCUSDT")
        self.assertEqual(str(signal["entry"]), "64800")
        self.assertEqual(str(signal["take1"]), "65400")
        self.assertEqual(str(signal["take2"]), "65400")

    def test_supports_take_1_and_take_2_labels(self):
        message = "ETHUSDT\nEntry: 3500\nTake 1: 3600\nTake 2: 3700\nStop: 3400"
        signal = parse_signal_message(message)
        self.assertIsNotNone(signal)
        self.assertEqual(signal["symbol"], "ETHUSDT")
        self.assertEqual(str(signal["take1"]), "3600")
        self.assertEqual(str(signal["take2"]), "3700")

    def test_supports_russian_take_1_and_take_2_with_bullet_separator(self):
        message = "ZKC\nВход 0.04411\nТейк 1 0.0458744 · Тейк 2 0.0476388\nСтоп 0.039699"
        signal = parse_signal_message(message)
        self.assertIsNotNone(signal)
        self.assertEqual(signal["symbol"], "ZKCUSDT")
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

        self.assertEqual(captured[0]["type"], "MARKET")
        self.assertNotIn("price", captured[0])

    def test_bingx_request_rejects_non_zero_api_code_even_on_http_200(self):
        class DummyResponse:
            status_code = 200
            text = '{"code": 40004, "msg": "Balance not enough"}'

            def json(self):
                return {"code": 40004, "msg": "Balance not enough"}

        with patch("requests.request", return_value=DummyResponse()):
            with self.assertRaises(RuntimeError):
                bingx_request("POST", "/openApi/swap/v2/order", "api", "secret", {"symbol": "BTCUSDT"})


if __name__ == "__main__":
    unittest.main()
