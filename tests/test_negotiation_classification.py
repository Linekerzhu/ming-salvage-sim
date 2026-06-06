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


if __name__ == "__main__":
    unittest.main()
