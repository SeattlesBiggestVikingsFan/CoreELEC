import unittest

from verify_candidate import require_installable


class InstallationGateTests(unittest.TestCase):
    def test_missing_unknown_and_negative_certification_are_blocked(self):
        for certification in (None, {}, {"passed": False}, {"passed": 1},
                              {"passed": True, "irrelevant": "unused"}):
            with self.subTest(certification=certification):
                if certification == {"passed": True, "irrelevant": "unused"}:
                    require_installable({"installation_certification": certification})
                else:
                    with self.assertRaisesRegex(RuntimeError, "INSTALLATION BLOCKED"):
                        require_installable({"installation_certification": certification})

    def test_known_legacy_mismatch_is_not_silently_accepted(self):
        with self.assertRaisesRegex(RuntimeError, "DOVI"):
            require_installable({"installation_certification": {
                "passed": False,
                "blockers": ["DOVI module_layout and register_dv_functions CRC mismatch"],
                "same_mismatch_in_historical_kernel": True,
            }})


if __name__ == "__main__":
    unittest.main()
