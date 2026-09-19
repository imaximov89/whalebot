import unittest

from telegram_bingx_signal_bot import parse_signal_message


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


if __name__ == "__main__":
    unittest.main()
