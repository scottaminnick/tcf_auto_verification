import unittest

import tcf_participants


class ParticipantParserTests(unittest.TestCase):
    def test_direct_codes_and_cwsu_city_names(self):
        result = tcf_participants.parse_participants(
            "ZDC, ZTL\nNWS - CWSU Kansas City\nCWSU Los Angeles")
        self.assertEqual(result.cwsus, ("ZDC", "ZKC", "ZLA", "ZTL"))

    def test_airline_and_awc_aliases_are_grouped_as_organizations(self):
        result = tcf_participants.parse_participants(
            "AWC, FedEx, Delta, United Airlines, American Airlines")
        self.assertEqual(
            result.organizations,
            ("AAL", "AWC", "Delta", "FedEx", "UAL"),
        )

    def test_include_orgs_false_removes_organization_aliases(self):
        result = tcf_participants.parse_participants(
            "AWC, FedEx, CWSU Boston", include_orgs=False)
        self.assertEqual(result.cwsus, ("ZBW",))
        self.assertEqual(result.organizations, ())

    def test_strict_short_aliases_avoid_ambiguous_matches(self):
        strict = tcf_participants.parse_participants("KC, DC, MIA")
        permissive = tcf_participants.parse_participants(
            "KC, DC, MIA", strict_short=False)
        self.assertEqual(strict.cwsus, ())
        self.assertEqual(permissive.cwsus, ("ZDC", "ZKC", "ZMA"))

    def test_unmapped_tokens_are_retained_for_review(self):
        result = tcf_participants.parse_participants(
            "CWSU Seattle\nMystery Participant")
        self.assertEqual(result.cwsus, ("ZSE",))
        self.assertIn("Mystery Participant", result.unknown)


if __name__ == "__main__":
    unittest.main()
