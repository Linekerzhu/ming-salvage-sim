import unittest

from ming_sim.negotiation import (
    HANDSHAKE_CONDITIONAL,
    action_kind_from_text,
    core_topic_from_chat,
    evaluate_negotiation,
)


class NegotiationClassificationTests(unittest.TestCase):
    def test_yandang_personnel_topic_is_not_castration(self) -> None:
        text = (
            "那朕也不遮掩了，吏部被魏忠贤一党把持，朕要找个能制衡尚书的厉害角色。\n"
            "王绍徽以阉党居吏部尚书之位，天下铨政尽在其手。"
        )

        self.assertEqual(action_kind_from_text(text), "personnel")

    def test_yandang_personnel_advice_does_not_block_on_castration(self) -> None:
        user_text = "那朕也不遮掩了，吏部被魏忠贤一党把持，朕要找个能制衡尚书的厉害角色。"
        answer = (
            "王绍徽以阉党居吏部尚书之位，天下铨政尽在其手。陛下欲以侍郎制衡尚书，"
            "此意臣虽认同，然不得不直言：侍郎若无明旨授权、无独立考课之权，"
            "不过堂上附署而已，制衡二字谈何容易。"
        )
        result = evaluate_negotiation(
            None,
            user_text,
            answer,
            "caution",
            "侍郎若无明旨授权、无独立考课之权",
        )

        self.assertNotEqual(result.action_kind, "castration")
        self.assertNotIn("净身未明确自愿", result.blockers)
        self.assertEqual(result.handshake_status, HANDSHAKE_CONDITIONAL)

    def test_person_name_qian_does_not_force_money_topic(self) -> None:
        answer = (
            "臣为陛下权衡三人：其一，钱龙锡。年四十八，前礼部尚书。"
            "其二，毕自严。现任南京户部尚书。此二人若用，当另议尚书之缺，"
            "不宜屈就吏部侍郎。"
        )

        self.assertEqual(
            core_topic_from_chat("口述即可", answer, "personnel"),
            "人事任免与官缺",
        )

    def test_explicit_castration_still_classifies_as_castration(self) -> None:
        self.assertEqual(action_kind_from_text("朕欲令卿净身入内廷。"), "castration")

    def test_legacy_xinpan_profile_no_longer_changes_handshake_score(self) -> None:
        user_text = "此事须你承办，替朕密查阉党旧案。"
        answer = "臣愿为陛下密查阉党旧案，三日内先回奏线索。"

        baseline = evaluate_negotiation(None, user_text, answer, "support", "")
        legacy_weighted = evaluate_negotiation(
            None,
            user_text,
            answer,
            "support",
            "",
            xinpan_profile={
                "quadrant": "离心",
                "dao_he": -100,
                "shi_he": -100,
                "fear": 100,
                "hatred": 100,
                "trust_coeff": 0.1,
            },
        )

        self.assertEqual(baseline.psychological_score, legacy_weighted.psychological_score)
        self.assertNotIn("xinpan_quadrant", legacy_weighted.factors)
        self.assertNotIn("xinpan_hatred", legacy_weighted.factors)

    def test_behavior_profile_truth_risks_change_handshake_score(self) -> None:
        user_text = "此事须你承办，替朕密查阉党旧案。"
        answer = "臣愿为陛下密查阉党旧案，三日内先回奏线索。"

        plain = evaluate_negotiation(
            None,
            user_text,
            answer,
            "support",
            "",
            behavior_profile={"preferred_stance": "support", "truth_mode": "直陈为主", "risk_tags": []},
        )
        evasive = evaluate_negotiation(
            None,
            user_text,
            answer,
            "support",
            "",
            behavior_profile={"preferred_stance": "support", "truth_mode": "半真半假", "risk_tags": ["话术不实"]},
        )

        self.assertLess(evasive.psychological_score, plain.psychological_score)
        self.assertEqual(evasive.factors["behavior_truth_mode"], "半真半假")
        self.assertIn("话术不实", evasive.factors["behavior_risk_tags"])


if __name__ == "__main__":
    unittest.main()
