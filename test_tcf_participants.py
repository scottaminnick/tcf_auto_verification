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


class VerificationParticipantTests(unittest.TestCase):
    def test_required_participants_appear_without_chat_text(self):
        result = tcf_participants.parse_verification_participants("")
        self.assertEqual(result.codes, ("AWC", "NAM"))
        self.assertEqual(result.organizations, ("AWC", "NAM"))
        self.assertEqual(result.cwsus, ())
        self.assertEqual(result.unknown, ())

    def test_missing_required_participants_preserve_chat_results(self):
        result = tcf_participants.parse_verification_participants(
            "CWSU Boston, ZTL, FedEx, Mystery Participant")
        self.assertEqual(result.codes, ("AWC", "FedEx", "NAM", "ZBW", "ZTL"))
        self.assertEqual(result.cwsus, ("ZBW", "ZTL"))
        self.assertEqual(result.organizations, ("AWC", "FedEx", "NAM"))
        self.assertEqual(result.unknown, ("Mystery Participant",))

    def test_required_participants_are_deduplicated_and_not_unmapped(self):
        result = tcf_participants.parse_verification_participants(
            "AWC, awc, NAM, nam, Wes Adkins, ZDC")
        self.assertEqual(result.codes, ("AWC", "NAM", "ZDC"))
        self.assertEqual(result.unknown, ())

    def test_other_organizations_option_keeps_required_participants(self):
        result = tcf_participants.parse_verification_participants(
            "AWC, NAM, FedEx, CWSU Seattle", include_orgs=False)
        self.assertEqual(result.codes, ("AWC", "NAM", "ZSE"))
        self.assertEqual(result.organizations, ("AWC", "NAM"))
        self.assertEqual(result.unknown, ("FedEx",))

    def test_short_alias_option_still_applies(self):
        strict = tcf_participants.parse_verification_participants("KC, DC, MIA")
        permissive = tcf_participants.parse_verification_participants(
            "KC, DC, MIA", strict_short=False)
        self.assertEqual(strict.codes, ("AWC", "NAM"))
        self.assertEqual(permissive.codes, ("AWC", "NAM", "ZDC", "ZKC", "ZMA"))


if __name__ == "__main__":
    unittest.main()
