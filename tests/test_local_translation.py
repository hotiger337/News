import unittest
from unittest.mock import patch

from newsbot.local_translation import validate
from newsbot.sources import translate


class LocalTranslationTests(unittest.TestCase):
    def test_no_key_routes_to_local_model(self):
        with (
            patch.dict("os.environ", {"TRANSLATOR_KEY": ""}),
            patch(
                "newsbot.local_translation.translate_local", return_value="한글"
            ) as local,
        ):
            self.assertEqual(
                translate("CoreWeave considers $30 million", "en", ["CoreWeave"]),
                "한글",
            )
            self.assertEqual(local.call_args.args[2], ["CoreWeave", "$30 million"])

    def test_protected_values(self):
        validate(
            "CoreWeave considers $30 million",
            "코어위브는 $ 30 백만 투자를 고려합니다",
            ["CoreWeave", "$30 million"],
        )
        with self.assertRaises(ValueError):
            validate(
                "CoreWeave considers $30 million",
                "코어위브는 $ 130 백만 투자를 고려합니다",
                ["CoreWeave", "$30 million"],
            )

    def test_direction_reversal(self):
        with self.assertRaises(ValueError):
            validate("Leaders aim to avert collapse", "정상들은 붕괴를 목표로 한다", [])
        validate(
            "Leaders aim to avert collapse", "정상들은 붕괴를 막는 것을 목표로 한다", []
        )
