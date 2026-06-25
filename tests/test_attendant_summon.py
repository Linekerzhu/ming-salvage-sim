"""Attending eunuch summon commands should switch the mobile audience target deterministically."""

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import web_app
from ming_sim import court, court_events, issues, memorials
from ming_sim import eunuch_lore as el
from ming_sim.dialogue_semantics import SemanticDecision
from ming_sim.models import Character
from ming_sim.personnel_actions import is_eunuch_office
import ming_sim.session as session_module


class AttendantSummonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self._env = {
            key: os.environ.get(key)
            for key in (
                "OPENAI_API_KEY",
                "OPENAI_BASE_URL",
                "OPENAI_MODEL",
                "MING_SIM_SERVER_USERS",
                "MING_SIM_AUTH_USERS",
                "MING_SIM_INVITE_CODE",
                "MING_SIM_ALLOW_REGISTRATION",
                "MING_SIM_ENABLE_DIALOGUE_REGEX_ACTIONS",
                "MING_SIM_ENABLE_DIALOGUE_REGEX_SUMMONS",
                "MING_SIM_ENABLE_DIALOGUE_ANSWER_SUMMON_FALLBACK",
                "MING_SIM_ENABLE_LEGACY_DIALOGUE_REGEX_WORLD_ACTIONS",
                "MING_SIM_ENABLE_RECRUITMENT_REGEX_FALLBACK",
                "MING_SIM_ENABLE_DIALOGUE_LORE_REGEX_FALLBACK",
                "MING_SIM_ENABLE_DIALOGUE_DIRECTIVE_REGEX_FALLBACK",
                "MING_SIM_ENABLE_DIALOGUE_BARGAIN_REGEX_FALLBACK",
                "MING_SIM_ENABLE_DIALOGUE_PENDING_REGEX_RECOVERY",
                "MING_SIM_ENABLE_DIALOGUE_MENTION_REGEX_FALLBACK",
                "MING_SIM_DISABLE_LLM_QUICK_SUGGESTIONS",
                "MING_SIM_ENABLE_LOCAL_QUICK_SUGGESTIONS",
                "MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT",
                "MING_SIM_DISABLE_DIALOGUE_ROUTE_LLM_AUDIT",
                "MING_SIM_DISABLE_DIALOGUE_LORE_LLM_AUDIT",
                "MING_SIM_DISABLE_DIALOGUE_MENTION_LLM_AUDIT",
            )
        }
        self._user_data_dir = web_app.user_data_dir
        self._user_data_path = web_app.user_data_path
        self._load_runtime_llm = web_app.load_runtime_llm
        self._verify_llm_available = session_module.verify_llm_available

        root = Path(self.tmp.name)

        def user_data_dir() -> Path:
            root.mkdir(parents=True, exist_ok=True)
            return root

        def user_data_path(*parts: str) -> str:
            path = root.joinpath(*parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            return str(path)

        web_app.user_data_dir = user_data_dir
        web_app.user_data_path = user_data_path
        web_app.load_runtime_llm = lambda: {}
        session_module.verify_llm_available = lambda _config: None
        os.environ["OPENAI_API_KEY"] = "test-key"
        os.environ["OPENAI_BASE_URL"] = "https://example.test/v1"
        os.environ["OPENAI_MODEL"] = "test-model"
        os.environ.pop("MING_SIM_SERVER_USERS", None)
        os.environ.pop("MING_SIM_AUTH_USERS", None)
        os.environ["MING_SIM_INVITE_CODE"] = ""
        os.environ["MING_SIM_ALLOW_REGISTRATION"] = "0"
        # Most historical summon tests exercise the deterministic compatibility
        # path.  Production defaults keep these regex routes off; tests opt in
        # explicitly so semantic-only cases can disable the old gates per test.
        os.environ["MING_SIM_ENABLE_LEGACY_DIALOGUE_REGEX_WORLD_ACTIONS"] = "1"
        os.environ["MING_SIM_ENABLE_DIALOGUE_REGEX_ACTIONS"] = "1"
        os.environ["MING_SIM_ENABLE_DIALOGUE_REGEX_SUMMONS"] = "1"
        os.environ["MING_SIM_ENABLE_DIALOGUE_ANSWER_SUMMON_FALLBACK"] = "1"
        os.environ.pop("MING_SIM_ENABLE_DIALOGUE_LORE_REGEX_FALLBACK", None)
        os.environ.pop("MING_SIM_ENABLE_DIALOGUE_DIRECTIVE_REGEX_FALLBACK", None)
        os.environ.pop("MING_SIM_ENABLE_DIALOGUE_BARGAIN_REGEX_FALLBACK", None)
        os.environ.pop("MING_SIM_ENABLE_DIALOGUE_PENDING_REGEX_RECOVERY", None)
        os.environ["MING_SIM_ENABLE_DIALOGUE_MENTION_REGEX_FALLBACK"] = "1"
        os.environ["MING_SIM_DISABLE_LLM_QUICK_SUGGESTIONS"] = "1"
        os.environ.pop("MING_SIM_ENABLE_LOCAL_QUICK_SUGGESTIONS", None)
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "1"
        os.environ["MING_SIM_DISABLE_DIALOGUE_ROUTE_LLM_AUDIT"] = "1"
        os.environ["MING_SIM_DISABLE_DIALOGUE_LORE_LLM_AUDIT"] = "1"
        os.environ["MING_SIM_DISABLE_DIALOGUE_MENTION_LLM_AUDIT"] = "1"

    def tearDown(self) -> None:
        web_app._close_all_running_games()
        web_app.user_data_dir = self._user_data_dir
        web_app.user_data_path = self._user_data_path
        web_app.load_runtime_llm = self._load_runtime_llm
        session_module.verify_llm_available = self._verify_llm_available
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def _recruitment_audit(self, *, allow=True, kind="eunuch", confidence=95):
        def audit(phase, payload):
            if phase == "dialogue_pending_recovery":
                return {
                    "allow": allow,
                    "phase": "confirm" if allow else "none",
                    "action_type": "recruitment" if allow else "none",
                    "kind": kind if allow else "",
                    "proposal_evidence": "测试恢复招募提案",
                    "trigger_quote": str(payload.get("user_text") or "")[:80],
                    "public_hint": "",
                    "private_reason": "test semantic recovery",
                    "confidence": confidence if allow else 95,
                }
            if phase != "recruitment_intent":
                return None
            action = payload.get("tool_action") or {}
            action_phase = str(action.get("phase") or "propose")
            return {
                "allow": allow,
                "phase": action_phase if allow else "none",
                "kind": kind if allow else "",
                "requires_confirmation": action_phase == "propose",
                "trigger_quote": str(action.get("trigger_quote") or payload.get("user_text") or "")[:80],
                "public_hint": "",
                "private_reason": "test semantic gate",
                "confidence": confidence if allow else 95,
            }

        return audit

    def _dialogue_action_audit(
        self,
        *,
        action_type: str,
        target: str = "",
        actor: str = "",
        faction: str = "",
        mode: str = "",
        confidence: int = 95,
    ):
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"

        def audit(phase, payload):
            if phase == "recruitment_intent":
                return {
                    "allow": False,
                    "phase": "none",
                    "kind": "",
                    "trigger_quote": str(payload.get("user_text") or "")[:80],
                    "private_reason": "test action path is not recruitment",
                    "confidence": 95,
                }
            if phase != "dialogue_action_intent":
                return None
            action = payload.get("tool_action") or {}
            pending = payload.get("pending_action") or {}
            action_phase = str(action.get("phase") or ("confirm" if pending else "propose"))
            if str(action.get("type") or "") == "semantic_probe":
                action_phase = "propose"
            resolved_type = str(pending.get("type") or action_type)
            return {
                "allow": True,
                "phase": action_phase,
                "action_type": resolved_type,
                "target": target or str(pending.get("target") or action.get("target") or ""),
                "actor": actor or str(pending.get("actor") or action.get("actor") or ""),
                "faction": faction or str(pending.get("faction") or action.get("faction") or ""),
                "mode": mode or str(pending.get("mode") or action.get("mode") or ""),
                "trigger_quote": str(action.get("trigger_quote") or payload.get("user_text") or "")[:120],
                "public_hint": "",
                "private_reason": "test semantic action gate",
                "confidence": confidence,
            }

        return audit

    def _lore_intake_audit(self, *, targets, allow=True, confidence=96):
        os.environ["MING_SIM_DISABLE_DIALOGUE_LORE_LLM_AUDIT"] = "0"

        def audit(phase, payload):
            if phase != "dialogue_eunuch_lore_intake":
                return None
            candidate_names = set(payload.get("candidate_names") or [])
            target_names = [name for name in targets if name in candidate_names]
            return {
                "allow": allow and bool(target_names),
                "target_names": target_names if allow else [],
                "trigger_quote": str(payload.get("text") or "")[:80],
                "private_reason": "test semantic lore intake",
                "confidence": confidence,
            }

        return audit

    def _bargain_attitude_audit(self, *, attitude: str = "accept", allow: bool = True, confidence: int = 95):
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"

        def audit(phase, payload):
            if phase != "dialogue_bargain_attitude":
                return None
            return {
                "allow": allow,
                "attitude": attitude if allow else "none",
                "trigger_quote": str(payload.get("user_text") or "")[:80],
                "private_reason": "test semantic bargain attitude",
                "confidence": confidence,
            }

        return audit

    def test_stream_chat_summon_command_returns_next_minister_without_llm_tool(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            row = game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND name!=? "
                "ORDER BY ability DESC LIMIT 1",
                (attendant,),
            ).fetchone()
            self.assertIsNotNone(row)
            target = str(row["name"])

            events = list(game.chat_stream(attendant, f"传{target}入殿奏对。"))

            self.assertEqual(events[0]["type"], "delta")
            self.assertIn(target, str(events[0]["content"]))
            self.assertEqual(events[-1]["type"], "done")
            payload = events[-1]["payload"]
            self.assertEqual(payload["court_action"], "summon")
            self.assertEqual(payload["next_minister"], target)
            self.assertTrue(any(m["role"] == "user" for m in payload["history"]))
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_chat_history_marks_known_person_mentions(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            row = game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND name!=? "
                "ORDER BY ability DESC LIMIT 1",
                (attendant,),
            ).fetchone()
            self.assertIsNotNone(row)
            target = str(row["name"])
            game.chat_history[attendant] = [{"role": "minister", "content": f"可先问{target}，再看部议。"}]

            history = game._chat_history_payload(attendant)

            self.assertEqual(history[0]["mentions"][0]["name"], target)
            self.assertEqual(history[0]["mentions"][0]["kind"], "character")
            self.assertIn(target, history[0]["mentions"][0]["terms"])
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_chat_mentions_ignore_office_aliases_but_keep_personal_aliases(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"

            office_only = game._chat_message_mentions("司礼监今日递入文书，东厂另有密报。")
            self.assertEqual(office_only, [])

            mixed = game._chat_message_mentions("司礼监今日递入文书，王承恩在旁候旨。")
            self.assertIn(attendant, {item["name"] for item in mixed})
            self.assertNotIn("司礼监", {term for item in mixed for term in item["terms"]})

            personal_alias = game._chat_message_mentions("王伴伴说内书堂有人可用。")
            self.assertIn(attendant, {item["name"] for item in personal_alias})
            self.assertIn("王伴伴", {term for item in personal_alias for term in item["terms"]})
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_attendant_summon_ignores_office_aliases(self):
        game = web_app.WebGame(fresh=True)
        try:
            result = game._attendant_summon_target("王承恩", "叫司礼监来见。")

            self.assertEqual(result, {})
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_dialogue_mentioned_unlisted_person_can_be_registered_and_summoned(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            unknown = next(name for name in ("冯小澄", "张守仁", "陆闻道") if name not in game.content.characters)
            self.assertNotIn(unknown, game.content.characters)
            game._record_unknown_dialogue_mentions(
                attendant,
                f"奴婢听说内书堂有个识字小火者，唤作{unknown}，记性细，可先查一查。",
            )

            events = list(game.chat_stream(attendant, f"传{unknown}入殿奏对。"))

            self.assertEqual(events[-1]["type"], "done")
            payload = events[-1]["payload"]
            self.assertEqual(payload["court_action"], "summon")
            self.assertEqual(payload["next_minister"], unknown)
            self.assertEqual(payload["registered_minister"], unknown)
            self.assertIn(unknown, game.content.characters)
            row = game.db.conn.execute(
                "SELECT office, office_type, summary FROM characters WHERE name=?",
                (unknown,),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertTrue(is_eunuch_office(str(row["office"]), str(row["office_type"])))
            self.assertIn("当前活动存档", str(row["summary"] or ""))
            self.assertIn(unknown, {m["name"] for m in payload["history"][-1].get("mentions", [])})
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_unknown_dialogue_mentions_ignore_office_names(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            unknown = next(name for name in ("陈小贵", "沈小贵", "陆小贵") if name not in game.content.characters)

            game._record_unknown_dialogue_mentions(
                attendant,
                f"南镇抚司有个锦衣卫试百户，叫{unknown}，胆气尚可，可先带到偏殿问话。",
            )

            stored = game._load_unknown_dialogue_mentions()
            self.assertIn(unknown, stored)
            self.assertNotIn("南镇抚司", stored)
            self.assertNotIn("锦衣卫", stored)
            self.assertNotIn("试百户", stored)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_unknown_dialogue_mentions_reject_descriptor_fragments(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            unknown = next(name for name in ("陈稳", "沈诚", "陆恪") if name not in game.content.characters)

            game._record_unknown_dialogue_mentions(
                attendant,
                f"臣十三岁被锦衣卫一个老试百户收作徒弟，后来另听说一个叫{unknown}的百户可用。",
            )

            stored = game._load_unknown_dialogue_mentions()
            self.assertIn(unknown, stored)
            self.assertNotIn("卫一个老", stored)
            self.assertFalse(any("一个" in name for name in stored), stored)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_unknown_dialogue_mentions_drop_legacy_honorific_prefix_names(self):
        game = web_app.WebGame(fresh=True)
        try:
            game.db.kv_set(
                game._dialogue_unknown_mentions_key(),
                '{"蒙王承恩":{"name":"蒙王承恩","source_minister":"小顺子","excerpt":"蒙王承恩公公提携"},'
                '"刘忠":{"name":"刘忠","source_minister":"王承恩","excerpt":"一个叫刘忠的小火者"}}',
            )

            stored = game._load_unknown_dialogue_mentions()

            self.assertNotIn("蒙王承恩", stored)
            self.assertIn("刘忠", stored)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_unknown_dialogue_mentions_capture_palace_nicknames_from_suggestions(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            game._record_unknown_dialogue_mentions(
                attendant,
                "回陛下——内书堂有个叫**小禄子**的，今年刚满十一，记性极好。"
                "还有一个**小顺子**，今年十二，规矩熟。"
                "蒙王承恩公公提携的人不可误记成姓名。",
            )

            stored = game._load_unknown_dialogue_mentions()
            self.assertIn("小禄子", stored)
            self.assertIn("小顺子", stored)
            self.assertNotIn("蒙王承恩", stored)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_unknown_dialogue_mentions_do_not_use_regex_cache_without_llm_or_legacy_opt_in(self):
        os.environ.pop("MING_SIM_ENABLE_DIALOGUE_MENTION_REGEX_FALLBACK", None)
        os.environ["MING_SIM_DISABLE_DIALOGUE_MENTION_LLM_AUDIT"] = "1"
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"

            game._record_unknown_dialogue_mentions(
                attendant,
                "回陛下，内书堂有个叫小禄子的孩子，记性极好，可先查一查。",
            )

            self.assertEqual(game._load_unknown_dialogue_mentions(), {})
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_semantic_unknown_mention_audit_runs_without_regex_cache_fallback(self):
        os.environ.pop("MING_SIM_ENABLE_DIALOGUE_MENTION_REGEX_FALLBACK", None)
        os.environ["MING_SIM_DISABLE_DIALOGUE_MENTION_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            calls = []

            def audit(phase, payload):
                if phase == "dialogue_unknown_mention_intake":
                    calls.append(payload)
                    return {
                        "allow": True,
                        "accepted_names": ["小禄子"],
                        "rejected_names": [],
                        "trigger_quote": "小禄子可先查一查",
                        "private_reason": "语义上是在提出可召见候选。",
                        "confidence": 96,
                    }
                return None

            game.session.dialogue_audit_client = audit
            game._record_unknown_dialogue_mentions(
                attendant,
                "回陛下，内书堂有个叫小禄子的孩子，记性极好，可先查一查。",
            )

            self.assertTrue(calls)
            self.assertIn("小禄子", game._load_unknown_dialogue_mentions())
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_semantic_unknown_mention_denial_blocks_candidate_cache(self):
        os.environ["MING_SIM_DISABLE_DIALOGUE_MENTION_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            self.assertNotIn("小禄子", game.content.characters)

            def audit(phase, payload):
                if phase == "dialogue_unknown_mention_intake":
                    self.assertEqual(payload.get("purpose"), "cache_candidate")
                    self.assertIn("小禄子", payload.get("candidate_names") or [])
                    return {
                        "allow": False,
                        "accepted_names": [],
                        "rejected_names": ["小禄子"],
                        "trigger_quote": "只是讲旧例",
                        "private_reason": "这只是制度解释，不是把小禄子作为可召见候选提出。",
                        "confidence": 96,
                    }
                return None

            game.session.dialogue_audit_client = audit
            game._record_unknown_dialogue_mentions(
                attendant,
                "奴婢只是打个比方，说旧年内书堂有个叫小禄子的孩子常被拿来作例，并非荐人。",
            )

            self.assertNotIn("小禄子", game._load_unknown_dialogue_mentions())
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_semantic_unknown_mention_allow_records_only_accepted_names(self):
        os.environ["MING_SIM_DISABLE_DIALOGUE_MENTION_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"

            def audit(phase, payload):
                if phase == "dialogue_unknown_mention_intake":
                    self.assertEqual(payload.get("purpose"), "cache_candidate")
                    return {
                        "allow": True,
                        "accepted_names": ["小禄子"],
                        "rejected_names": ["小顺子"],
                        "trigger_quote": "小禄子可先带到偏殿问话",
                        "private_reason": "小禄子被明确作为候选提出；小顺子只是比较背景。",
                        "confidence": 95,
                    }
                return None

            game.session.dialogue_audit_client = audit
            game._record_unknown_dialogue_mentions(
                attendant,
                "内书堂有个叫小禄子的，记性细，可先带到偏殿问话；小顺子只是同屋旧识。",
            )

            stored = game._load_unknown_dialogue_mentions()
            self.assertIn("小禄子", stored)
            self.assertNotIn("小顺子", stored)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_semantic_answer_summon_denial_blocks_materialization(self):
        os.environ["MING_SIM_DISABLE_DIALOGUE_MENTION_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            self.assertNotIn("小禄子", game.content.characters)

            def audit(phase, payload):
                if phase == "dialogue_unknown_mention_intake":
                    self.assertEqual(payload.get("purpose"), "answer_summon")
                    return {
                        "allow": False,
                        "accepted_names": [],
                        "rejected_names": ["小禄子"],
                        "trigger_quote": "传内书堂生徒小禄子觐见",
                        "private_reason": "审计否决时不得从 NPC 回答反推召见。",
                        "confidence": 98,
                    }
                return None

            game.session.dialogue_audit_client = audit
            result = game._attendant_answer_summon_target(
                attendant,
                "——传内书堂生徒小禄子觐见。陛下，小禄子今年十一。",
            )

            self.assertEqual(result, {})
            self.assertNotIn("小禄子", game.content.characters)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_attendant_answer_implied_summon_materializes_unlisted_palace_nickname(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            self.assertNotIn("小禄子", game.content.characters)

            result = game._attendant_answer_summon_target(
                attendant,
                "（躬身一礼，转身快步至殿门，朝外唤了一声）\n\n"
                "——传内书堂生徒小禄子觐见。\n\n"
                "陛下，小禄子今年十一，保定府人，原是逃荒到京的孤儿。",
            )

            self.assertEqual(result["name"], "小禄子")
            self.assertTrue(result["generated"])
            self.assertIn("小禄子", game.content.characters)
            row = game.db.conn.execute(
                "SELECT office, office_type, summary, birth_year FROM characters WHERE name=?",
                ("小禄子",),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertTrue(is_eunuch_office(str(row["office"]), str(row["office_type"])))
            self.assertIn("当前活动存档", str(row["summary"] or ""))
            self.assertEqual(int(game.state.year) - int(row["birth_year"]), 11)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_chat_payload_falls_back_when_attendant_roleplays_summon(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            self.assertNotIn("小禄子", game.content.characters)

            payload = game._chat_payload(
                attendant,
                "（躬身一礼，转身朝殿外廊下唤了一声）\n\n"
                "——传内书堂生徒小禄子觐见。\n\n"
                "（回身垂手）陛下，小禄子今年十一，保定府人，胆子小，见了生人不敢抬头。",
            )

            self.assertEqual(payload["court_action"], "summon")
            self.assertEqual(payload["next_minister"], "小禄子")
            self.assertEqual(payload["registered_minister"], "小禄子")
            self.assertIn("小禄子", game.content.characters)
            self.assertFalse(game._load_unknown_dialogue_mentions())
            row = game.db.conn.execute(
                "SELECT office, office_type, birth_year, summary FROM characters WHERE name=?",
                ("小禄子",),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertTrue(is_eunuch_office(str(row["office"]), str(row["office_type"])))
            self.assertEqual(int(game.state.year) - int(row["birth_year"]), 11)
            self.assertIn("当前活动存档", str(row["summary"] or ""))
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_chat_payload_implied_summon_is_legacy_opt_in(self):
        os.environ["MING_SIM_ENABLE_DIALOGUE_REGEX_SUMMONS"] = "0"
        os.environ["MING_SIM_ENABLE_DIALOGUE_ANSWER_SUMMON_FALLBACK"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            self.assertNotIn("小禄子", game.content.characters)

            payload = game._chat_payload(
                attendant,
                "——传内书堂生徒小禄子觐见。小禄子胆子小，该在殿外候着了。",
            )

            self.assertEqual(payload["court_action"], "")
            self.assertEqual(payload["next_minister"], "")
            self.assertNotIn("小禄子", game.content.characters)
            self.assertIn("小禄子", game._load_unknown_dialogue_mentions())
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_chat_payload_answer_summon_does_not_follow_player_regex_switch(self):
        os.environ["MING_SIM_ENABLE_DIALOGUE_REGEX_SUMMONS"] = "1"
        os.environ["MING_SIM_ENABLE_DIALOGUE_ANSWER_SUMMON_FALLBACK"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            self.assertNotIn("小禄子", game.content.characters)

            payload = game._chat_payload(
                attendant,
                "——传内书堂生徒小禄子觐见。小禄子胆子小，该在殿外候着了。",
            )

            self.assertEqual(payload["court_action"], "")
            self.assertEqual(payload["next_minister"], "")
            self.assertNotIn("小禄子", game.content.characters)
            self.assertIn("小禄子", game._load_unknown_dialogue_mentions())
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_chat_payload_routes_when_attendant_tells_unlisted_person_to_enter(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            self.assertNotIn("小禄子", game.content.characters)

            payload = game._chat_payload(
                attendant,
                "（躬身一礼，侧身让出殿门方向，朝外招了招手）\n\n"
                "小禄子，进来吧。陛下问你话，照实答就是。\n\n"
                "（一个瘦小的身影低着头蹭进殿来，跪伏在地，浑身发抖，不敢抬头。）",
            )

            self.assertEqual(payload["court_action"], "summon")
            self.assertEqual(payload["next_minister"], "小禄子")
            self.assertEqual(payload["registered_minister"], "小禄子")
            self.assertIn("小禄子", game.content.characters)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_chat_payload_routes_when_attendant_says_person_waits_outside(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            self.assertNotIn("小禄子", game.content.characters)

            payload = game._chat_payload(
                attendant,
                "回陛下——奴婢方才已传了口谕，小禄子该在殿外候着了。"
                "想必是头一回面圣，腿软不敢进来。奴婢这就去领他进来。",
            )

            self.assertEqual(payload["court_action"], "summon")
            self.assertEqual(payload["next_minister"], "小禄子")
            self.assertEqual(payload["registered_minister"], "小禄子")
            self.assertIn("小禄子", game.content.characters)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_named_selection_from_attendant_shortlist_summons_unlisted_person(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            self.assertNotIn("小禄子", game.content.characters)
            game._record_unknown_dialogue_mentions(
                attendant,
                "回陛下——内书堂里有个叫**小禄子**的，今年刚满十一，保定府人，记性极好。"
                "还有一个**小顺子**，今年十二，规矩熟。",
            )

            events = list(game.chat_stream(attendant, "算啦，换一个，小禄子。"))

            self.assertEqual(events[-1]["type"], "done")
            payload = events[-1]["payload"]
            self.assertEqual(payload["court_action"], "summon")
            self.assertEqual(payload["next_minister"], "小禄子")
            self.assertEqual(payload["registered_minister"], "小禄子")
            self.assertIn("小禄子", game.content.characters)
            row = game.db.conn.execute(
                "SELECT office, office_type, faction, birth_year FROM characters WHERE name=?",
                ("小禄子",),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertTrue(is_eunuch_office(str(row["office"]), str(row["office_type"])))
            self.assertIn(str(row["faction"] or ""), {"内廷", "皇党", "阉党"})
            self.assertEqual(int(game.state.year) - int(row["birth_year"]), 11)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_confirming_named_candidate_from_attendant_shortlist_summons_person(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            self.assertNotIn("小禄子", game.content.characters)
            game._store_pending_dialogue_action(attendant, {"type": "recruitment", "kind": "eunuch"})
            game._record_unknown_dialogue_mentions(
                attendant,
                "回陛下——内书堂里有个叫**小禄子**的，今年刚满十一，保定府人，记性极好。",
            )

            events = list(game.chat_stream(attendant, "好，小禄子。"))

            self.assertEqual(events[-1]["type"], "done")
            payload = events[-1]["payload"]
            self.assertEqual(payload["court_action"], "summon")
            self.assertEqual(payload["next_minister"], "小禄子")
            self.assertEqual(payload["registered_minister"], "小禄子")
            self.assertEqual(str(payload.get("recruited_minister") or ""), "")
            self.assertFalse(game._load_pending_dialogue_action(attendant))
            self.assertIn("小禄子", game.content.characters)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_pending_dialogue_action_storage_uses_semantic_schema(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            game._store_pending_dialogue_action(attendant, {
                "type": "eunuch_care",
                "target": attendant,
                "mode": "urinary",
                "note": "给王承恩请太医调养小解旧患",
                "trigger_quote": "请太医调养",
            })

            pending = game._load_pending_dialogue_action(attendant)
            self.assertEqual(pending["type"], "eunuch_care")
            self.assertEqual(pending["target"], attendant)
            self.assertEqual(pending["mode"], "urinary")
            self.assertEqual(pending["source_quote"], "请太医调养")
            self.assertEqual(pending["trigger_quote"], "请太医调养")
            self.assertEqual(int(pending["created_turn"]), int(game.state.turn))
            self.assertEqual(int(pending["expires_turn"]), int(game.state.turn))
            self.assertEqual(int(pending["turn"]), int(game.state.turn))

            game.state.turn += 1
            self.assertEqual(game._load_pending_dialogue_action(attendant), {})
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_legacy_regex_fallbacks_require_master_world_action_gate(self):
        for key in (
            "MING_SIM_ENABLE_DIALOGUE_REGEX_ACTIONS",
            "MING_SIM_ENABLE_DIALOGUE_REGEX_SUMMONS",
            "MING_SIM_ENABLE_DIALOGUE_ANSWER_SUMMON_FALLBACK",
            "MING_SIM_ENABLE_DIALOGUE_LORE_REGEX_FALLBACK",
            "MING_SIM_ENABLE_DIALOGUE_DIRECTIVE_REGEX_FALLBACK",
            "MING_SIM_ENABLE_DIALOGUE_BARGAIN_REGEX_FALLBACK",
            "MING_SIM_ENABLE_DIALOGUE_PENDING_REGEX_RECOVERY",
            "MING_SIM_ENABLE_DIALOGUE_MENTION_REGEX_FALLBACK",
            "MING_SIM_ENABLE_RECRUITMENT_REGEX_FALLBACK",
        ):
            os.environ[key] = "1"
        os.environ.pop("MING_SIM_ENABLE_LEGACY_DIALOGUE_REGEX_WORLD_ACTIONS", None)
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "1"
        os.environ["MING_SIM_DISABLE_DIALOGUE_LORE_LLM_AUDIT"] = "1"
        os.environ["MING_SIM_DISABLE_DIALOGUE_MENTION_LLM_AUDIT"] = "1"
        game = web_app.WebGame(fresh=True)
        try:
            self.assertFalse(game._dialogue_regex_actions_enabled())
            self.assertFalse(game._dialogue_regex_summons_enabled())
            self.assertFalse(game._dialogue_answer_summon_fallback_enabled())
            self.assertFalse(game._dialogue_lore_regex_fallback_enabled())
            self.assertFalse(game._dialogue_directive_regex_fallback_enabled())
            self.assertFalse(game._dialogue_bargain_regex_fallback_enabled())
            self.assertFalse(game._dialogue_pending_regex_recovery_enabled())
            self.assertFalse(game._dialogue_mention_regex_fallback_enabled())
            self.assertEqual(game._detect_recruitment_intent("宫里可有新的小内侍可用？"), {})

            attendant = "王承恩"
            character = game.session._character(attendant)
            proposed = game._fallback_pending_directive(
                character,
                "替朕拟一道旨意，命户部核出本月辽饷实欠，五日内具奏。",
                "臣以为可照此办理。",
            )
            self.assertIsNone(proposed)
            self.assertEqual(
                int(game.db.conn.execute("SELECT COUNT(*) AS n FROM turn_directives").fetchone()["n"]),
                0,
            )

            el.record_castration(game.db, attendant, forced=False, day=0)
            game.db.conn.execute(
                """
                UPDATE eunuch_lore
                SET bao_container='', bao_preservation='', bao_ritual=''
                WHERE name=?
                """,
                (attendant,),
            )
            game.db.conn.commit()
            absorbed = game._absorb_eunuch_lore_from_text(
                attendant,
                "请把王承恩的宝匣改用黑漆楠木匣，油炸封蜡，钥匙贴身，记入旧档。",
                source_role="user",
            )
            self.assertEqual(absorbed, {})
            lore = game.db.conn.execute(
                "SELECT bao_container, bao_preservation, bao_ritual FROM eunuch_lore WHERE name=?",
                (attendant,),
            ).fetchone()
            self.assertEqual(str(lore["bao_container"] or ""), "")
            self.assertEqual(str(lore["bao_preservation"] or ""), "")
            self.assertEqual(str(lore["bao_ritual"] or ""), "")
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_pronoun_choice_from_single_attendant_candidate_summons_person(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            self.assertNotIn("小禄子", game.content.characters)
            game._record_unknown_dialogue_mentions(
                attendant,
                "回陛下——内书堂里有个叫**小禄子**的，今年刚满十一，保定府人，记性极好。",
            )

            events = list(game.chat_stream(attendant, "就他吧。"))

            self.assertEqual(events[-1]["type"], "done")
            payload = events[-1]["payload"]
            self.assertEqual(payload["court_action"], "summon")
            self.assertEqual(payload["next_minister"], "小禄子")
            self.assertEqual(payload["registered_minister"], "小禄子")
            self.assertIn("小禄子", game.content.characters)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_pronoun_followup_recovers_recent_attendant_implied_summon(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            self.assertNotIn("小禄子", game.content.characters)
            game.chat_history[attendant] = [
                {"role": "minister", "content": "——传内书堂生徒小禄子觐见。小禄子胆子小，已在殿外候着。"}
            ]

            events = list(game.chat_stream(attendant, "人呢？我来和他说话。"))

            self.assertEqual(events[-1]["type"], "done")
            payload = events[-1]["payload"]
            self.assertEqual(payload["court_action"], "summon")
            self.assertEqual(payload["next_minister"], "小禄子")
            self.assertEqual(payload["registered_minister"], "小禄子")
            self.assertIn("小禄子", game.content.characters)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_pronoun_followup_resummons_existing_recent_implied_person(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            self.assertNotIn("小禄子", game.content.characters)
            game._add_runtime_character(
                Character(
                    name="小禄子",
                    office="内书堂识字小火者",
                    office_type="司礼监",
                    faction="内廷",
                    aliases=[],
                    personal_skills=["传旨跑腿"],
                    loyalty=82,
                    ability=48,
                    integrity=55,
                    courage=28,
                    style="新入御前，跪得快，回话先讲见闻，不敢妄议外朝大政",
                    power_id="ming",
                    status="active",
                ),
                "测试补档",
            )
            game.chat_history[attendant] = [
                {"role": "minister", "content": "——传内书堂生徒小禄子觐见。小禄子胆子小，已在殿外候着。"}
            ]

            events = list(game.chat_stream(attendant, "人呢？我来和他说话。"))

            self.assertEqual(events[-1]["type"], "done")
            payload = events[-1]["payload"]
            self.assertEqual(payload["court_action"], "summon")
            self.assertEqual(payload["next_minister"], "小禄子")
            self.assertEqual(payload.get("registered_minister") or "", "")
            self.assertNotIn("小禄子大人", payload["answer"])
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_single_dialogue_mentioned_person_can_be_summoned_by_pronoun(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            unknown = next(name for name in ("冯小澄", "张守仁", "陆闻道") if name not in game.content.characters)
            game._record_unknown_dialogue_mentions(
                attendant,
                f"奴婢听说内书堂有个识字小火者，唤作{unknown}，手脚勤快。",
            )

            events = list(game.chat_stream(attendant, "叫他来见。"))

            self.assertEqual(events[-1]["type"], "done")
            payload = events[-1]["payload"]
            self.assertEqual(payload["court_action"], "summon")
            self.assertEqual(payload["next_minister"], unknown)
            self.assertEqual(payload["registered_minister"], unknown)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_multiple_dialogue_mentioned_people_can_be_summoned_by_ordinal(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            first = next(name for name in ("冯小澄", "张守仁", "陆闻道") if name not in game.content.characters)
            second = "小福子"
            self.assertNotIn(second, game.content.characters)
            game._record_unknown_dialogue_mentions(
                attendant,
                f"奴婢留心到一个叫{first}的试百户，另有一个叫{second}的火者，皆可查访。",
            )

            events = list(game.chat_stream(attendant, "叫第二个来见。"))

            self.assertEqual(events[-1]["type"], "done")
            payload = events[-1]["payload"]
            self.assertEqual(payload["court_action"], "summon")
            self.assertEqual(payload["next_minister"], second)
            self.assertEqual(payload["registered_minister"], second)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_colloquial_summon_can_materialize_unlisted_person_without_prior_mention(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            unknown = "陈贵"
            self.assertNotIn(unknown, game.content.characters)

            events = list(game.chat_stream(attendant, f"让{unknown}来见面。"))

            payload = events[-1]["payload"]
            self.assertEqual(payload["court_action"], "summon")
            self.assertEqual(payload["next_minister"], unknown)
            self.assertEqual(payload["registered_minister"], unknown)
            self.assertIn(unknown, game.content.characters)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_direct_palace_nickname_summon_without_prior_mention_gets_inner_court_profile(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            self.assertNotIn("小禄子", game.content.characters)

            events = list(game.chat_stream(attendant, "带小禄子过来见我"))

            payload = events[-1]["payload"]
            self.assertEqual(payload["court_action"], "summon")
            self.assertEqual(payload["next_minister"], "小禄子")
            self.assertEqual(payload["registered_minister"], "小禄子")
            row = game.db.conn.execute(
                "SELECT office, office_type, faction, birth_year, summary FROM characters WHERE name=?",
                ("小禄子",),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertTrue(is_eunuch_office(str(row["office"]), str(row["office_type"])))
            self.assertIn(str(row["faction"] or ""), {"内廷", "皇党", "阉党"})
            self.assertLessEqual(int(game.state.year) - int(row["birth_year"]), 16)
            self.assertIn("当前活动存档", str(row["summary"] or ""))
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_short_palace_nickname_summon_without_prior_mention_gets_summoned(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            self.assertNotIn("小禄子", game.content.characters)

            events = list(game.chat_stream(attendant, "叫小禄子"))

            self.assertEqual(events[-1]["type"], "done")
            payload = events[-1]["payload"]
            self.assertEqual(payload["court_action"], "summon")
            self.assertEqual(payload["next_minister"], "小禄子")
            self.assertEqual(payload["registered_minister"], "小禄子")
            row = game.db.conn.execute(
                "SELECT office, office_type, faction, birth_year, summary FROM characters WHERE name=?",
                ("小禄子",),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertTrue(is_eunuch_office(str(row["office"]), str(row["office_type"])))
            self.assertIn(str(row["faction"] or ""), {"内廷", "皇党", "阉党"})
            self.assertLessEqual(int(game.state.year) - int(row["birth_year"]), 16)
            self.assertIn("当前活动存档", str(row["summary"] or ""))
            self.assertIn("内廷小名补档", str(row["summary"] or ""))
            self.assertIn("候用小火者", str(row["summary"] or ""))
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_inner_court_water_question_does_not_trigger_recruitment_intent(self):
        game = web_app.WebGame(fresh=True)
        try:
            text = (
                "朕看过「敕谕内官监：近有河间府幼童一名，年约八九岁，拟充王体乾养子」"
                "的复命。真实成效如何，奏报里有没有水分？"
            )

            self.assertEqual(game._detect_recruitment_intent(text), {})
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_semantic_executor_receives_chat_turn_id(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            calls = []

            def capture(minister_name, action, *, chat_turn_id=0):
                calls.append((minister_name, dict(action), int(chat_turn_id or 0)))
                return {"answer": "ok"}

            game._execute_dialogue_action = capture

            response = game._execute_semantic_dialogue_action(
                attendant,
                {"type": "recruitment", "kind": "eunuch", "trigger_quote": "准，招一个小内侍"},
                review={
                    "allow": True,
                    "phase": "confirm",
                    "action_type": "recruitment",
                    "kind": "eunuch",
                    "trigger_quote": "准，招一个小内侍",
                    "confidence": 96,
                },
                chat_turn_id=42,
                decision_type="tool",
            )

            self.assertEqual(response, {"answer": "ok"})
            self.assertEqual(calls[0][0], attendant)
            self.assertEqual(calls[0][1]["type"], "recruitment")
            self.assertEqual(calls[0][1]["kind"], "eunuch")
            self.assertEqual(calls[0][2], 42)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_semantic_executor_requires_allowed_review(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            calls = []

            def capture(minister_name, action, *, chat_turn_id=0):
                calls.append((minister_name, dict(action), int(chat_turn_id or 0)))
                return {"answer": "should not execute"}

            game._execute_dialogue_action = capture
            action = {"type": "recruitment", "kind": "eunuch", "trigger_quote": "准，招一个小内侍"}

            self.assertEqual(game._execute_semantic_dialogue_action(attendant, action), {})
            self.assertEqual(
                game._execute_semantic_dialogue_action(
                    attendant,
                    action,
                    review={
                        "allow": True,
                        "phase": "confirm",
                        "action_type": "recruitment",
                        "kind": "eunuch",
                        "trigger_quote": "准，招一个小内侍",
                        "confidence": 45,
                    },
                    decision_type="tool",
                ),
                {},
            )
            self.assertEqual(calls, [])
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_dialogue_consequence_source_uses_chat_turn_id(self):
        game = web_app.WebGame(fresh=True)
        try:
            response = game._execute_dialogue_consequence_action(
                "王承恩",
                {
                    "type": "dialogue_consequence",
                    "action_type": "custody",
                    "character_status_changes": [{
                        "name": "洪承畴",
                        "status": "imprisoned",
                        "reason": "押入昭狱",
                    }],
                    "trigger_quote": "押入昭狱",
                },
                chat_turn_id=77,
            )

            self.assertIn("已按口谕入档", response.get("answer") or "")
            row = game.db.conn.execute(
                """
                SELECT source_kind, source_id
                FROM character_custodies
                WHERE source_kind='dialogue' AND source_id='chat_turn:77'
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["source_id"], "chat_turn:77")
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_dialogue_consequence_filters_unknown_names_before_apply(self):
        game = web_app.WebGame(fresh=True)
        original_apply = issues.apply_score_extraction
        captured = {}
        try:
            def fake_apply(db, state, extracted):
                captured["extracted"] = {
                    key: [dict(item) for item in (extracted.get(key) or [])]
                    for key in ("character_status_changes", "condition_changes", "punishment_changes")
                }
                return {
                    "character_status_changes": list(extracted.get("character_status_changes") or []),
                    "condition_changes": list(extracted.get("condition_changes") or []),
                    "punishment_changes": list(extracted.get("punishment_changes") or []),
                }

            issues.apply_score_extraction = fake_apply
            response = game._execute_dialogue_consequence_action(
                "王承恩",
                {
                    "type": "dialogue_consequence",
                    "action_type": "punishment",
                    "character_status_changes": [
                        {"name": "洪承畴", "status": "imprisoned", "reason": "押入昭狱"},
                        {"name": "不存在的错档人", "status": "imprisoned", "reason": "夹带错名"},
                    ],
                    "condition_changes": [
                        {"name": "洪承畴", "label": "舌伤", "reason": "割舌禁言"},
                        {"name": "不存在的错档人", "label": "舌伤", "reason": "夹带错名"},
                    ],
                    "punishment_changes": [
                        {"name": "洪承畴", "punishment": "割舌", "reason": "禁言"},
                        {"name": "不存在的错档人", "punishment": "割舌", "reason": "夹带错名"},
                    ],
                    "trigger_quote": "押入昭狱，割舌禁言",
                },
                chat_turn_id=78,
            )

            self.assertIn("已按口谕入档", response.get("answer") or "")
            extracted = captured.get("extracted") or {}
            for key in ("character_status_changes", "condition_changes", "punishment_changes"):
                self.assertEqual([row.get("name") for row in extracted.get(key, [])], ["洪承畴"])
        finally:
            issues.apply_score_extraction = original_apply
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_chat_and_stream_pre_agent_use_unified_user_semantic_engine(self):
        def install_fake_engine(game, target):
            calls = []

            class FakeEngine:
                def evaluate_user_message(
                    self,
                    character,
                    user_text,
                    *,
                    pending_action=None,
                    route_context=None,
                    recent_answers=None,
                ):
                    calls.append({
                        "character": character.name,
                        "user_text": user_text,
                        "pending_action": pending_action,
                        "route_context": dict(route_context or {}),
                        "recent_answers": list(recent_answers or []),
                    })
                    return SemanticDecision(
                        decision_type="route",
                        action_type="summon",
                        phase="confirm",
                        target=target,
                        confidence=96,
                        trigger_quote=user_text,
                    )

            game._dialogue_semantic_engine = lambda: FakeEngine()
            return calls

        attendant = "王承恩"
        target = "韩爌"
        sync_game = None
        stream_game = None
        try:
            sync_game = web_app.WebGame(fresh=True)
            sync_calls = install_fake_engine(sync_game, target)
            sync_payload = sync_game.chat(attendant, f"传{target}入殿。")

            self.assertEqual(sync_payload["court_action"], "summon")
            self.assertEqual(sync_payload["next_minister"], target)
            self.assertEqual(len(sync_calls), 1)
            self.assertEqual(sync_calls[0]["character"], attendant)
            self.assertTrue(sync_calls[0]["route_context"].get("can_route_summon"))

            sync_game.session.close()
            sync_game = None

            stream_game = web_app.WebGame(fresh=True)
            stream_calls = install_fake_engine(stream_game, target)
            events = list(stream_game.chat_stream(attendant, f"传{target}入殿。"))

            self.assertEqual(events[-1]["type"], "done")
            stream_payload = events[-1]["payload"]
            self.assertEqual(stream_payload["court_action"], "summon")
            self.assertEqual(stream_payload["next_minister"], target)
            self.assertEqual(len(stream_calls), 1)
            self.assertEqual(stream_calls[0]["character"], attendant)
            self.assertTrue(stream_calls[0]["route_context"].get("can_route_summon"))
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                if sync_game is not None:
                    stop_worker(sync_game.db_path)
                if stream_game is not None:
                    stop_worker(stream_game.db_path)
            finally:
                if sync_game is not None:
                    sync_game.session.close()
                if stream_game is not None:
                    stream_game.session.close()

    def test_pending_action_compat_entry_uses_unified_semantic_gate(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            game._store_pending_dialogue_action(
                attendant,
                {
                    "type": "recruitment",
                    "kind": "eunuch",
                    "need": "挑一个小内侍",
                    "trigger_quote": "宫里可有新的小内侍可用",
                },
            )
            calls = []

            class FakeEngine:
                def gate_tool_action(
                    self,
                    character,
                    user_text,
                    action,
                    *,
                    phase="",
                    pending_action=None,
                ):
                    calls.append({
                        "character": character.name,
                        "user_text": user_text,
                        "action": dict(action or {}),
                        "phase": phase,
                        "pending_action": dict(pending_action or {}),
                    })
                    return SemanticDecision(
                        decision_type="tool",
                        action_type="recruitment",
                        phase="confirm",
                        kind="eunuch",
                        confidence=96,
                        trigger_quote=user_text,
                        private_reason="test unified pending gate",
                    )

            game._dialogue_semantic_engine = lambda: FakeEngine()

            response = game._dialogue_semantic_pending_action_response(
                attendant,
                "准，去挑一个忠谨小内侍。",
                chat_turn_id=88,
            )

            self.assertIsNotNone(response)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["character"], attendant)
            self.assertEqual(calls[0]["phase"], "confirm")
            self.assertEqual(calls[0]["action"]["type"], "recruitment")
            self.assertEqual(calls[0]["pending_action"]["type"], "recruitment")
            self.assertEqual(game._load_pending_dialogue_action(attendant), {})
            self.assertEqual(response.get("court_action"), "summon")
            self.assertTrue(response.get("recruited_minister"))
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_pending_action_confirm_uses_decision_payload_boundary(self):
        game = web_app.WebGame(fresh=True)
        try:
            actor = "韩爌"
            game._store_pending_dialogue_action(
                actor,
                {
                    "type": "mediation",
                    "actor": actor,
                    "mode": "co_work",
                    "condition": "调停派系旧怨",
                },
            )
            calls = []

            def capture(minister_name, action, *, review=None, chat_turn_id=0, decision_type="tool"):
                calls.append({
                    "minister_name": minister_name,
                    "action": dict(action or {}),
                    "review": dict(review or {}),
                    "chat_turn_id": int(chat_turn_id or 0),
                    "decision_type": decision_type,
                })
                return {"answer": "ok"}

            game._execute_semantic_dialogue_action = capture
            decision = SemanticDecision(
                decision_type="pending",
                action_type="mediation",
                phase="confirm",
                actor=actor,
                mode="co_work",
                payload={"faction": "东林"},
                confidence=96,
                trigger_quote="准，去调停东林旧怨。",
                private_reason="test pending payload boundary",
                raw={"faction": "阉党"},
            )

            response = game._dialogue_pending_action_response_from_decision(
                actor,
                "准，去调停东林旧怨。",
                decision,
                chat_turn_id=101,
            )

            self.assertEqual(response, {"answer": "ok"})
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["decision_type"], "pending")
            self.assertEqual(calls[0]["chat_turn_id"], 101)
            self.assertEqual(calls[0]["action"]["type"], "mediation")
            self.assertEqual(calls[0]["action"]["faction"], "东林")
            self.assertNotEqual(calls[0]["action"]["faction"], str(decision.raw.get("faction")))
            self.assertEqual(calls[0]["review"].get("faction"), "东林")
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_tool_action_merge_uses_decision_payload_boundary(self):
        game = web_app.WebGame(fresh=True)
        try:
            decision = SemanticDecision(
                decision_type="tool",
                action_type="mediation",
                phase="propose",
                actor="韩爌",
                mode="co_work",
                payload={"faction": "东林"},
                confidence=96,
                trigger_quote="调停东林党争",
                private_reason="test tool payload boundary",
                raw={"faction": "阉党"},
            )

            action = game._apply_tool_decision_to_action(
                "韩爌",
                {"type": "mediation", "mode": "co_work"},
                "朕要你调停东林党争。",
                decision,
            )

            self.assertEqual(action.get("type"), "mediation")
            self.assertEqual(action.get("actor"), "韩爌")
            self.assertEqual(action.get("faction"), "东林")
            self.assertNotEqual(action.get("faction"), str(decision.raw.get("faction")))
            self.assertEqual(action.get("trigger_quote"), "调停东林党争")
            self.assertEqual(action.get("semantic_reason"), "test tool payload boundary")
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_secret_order_tool_action_uses_decision_payload_boundary(self):
        game = web_app.WebGame(fresh=True)
        try:
            actor = "温体仁"
            wrong_id = game.db.create_secret_order(
                game.state,
                actor,
                "密查错档",
                "此档不该由本轮语义落入进展。",
                ["错档"],
                deadline_months=3,
            )
            allowed_id = game.db.create_secret_order(
                game.state,
                actor,
                "密查钱谦益",
                "暗查钱谦益起复东林旧臣之议，摸清同党牵连。",
                ["钱谦益", "东林", "起复"],
                deadline_months=3,
            )
            game.state.turn = 2
            game.state.period = 2
            action = {
                "type": "secret_order",
                "phase": "confirm",
                "kind": "progress",
                "mode": "progress",
                "target": actor,
                "assignee": actor,
                "order_id": wrong_id,
                "progress": "工具夹带的错档进展。",
            }

            def audit(phase, payload):
                if phase != "dialogue_action_intent":
                    return None
                tool_action = payload.get("tool_action") or {}
                self.assertEqual(tool_action.get("order_id"), wrong_id)
                return {
                    "allow": True,
                    "phase": "confirm",
                    "action_type": "secret_order",
                    "kind": "progress",
                    "target": actor,
                    "actor": actor,
                    "confidence": 96,
                    "trigger_quote": "照实入档",
                    "private_reason": "审计只准许钱谦益密令写入本月进展。",
                    "payload": {
                        "order_id": allowed_id,
                        "progress": "审计准许：探得钱谦益门生往来频密。",
                        "assignee": actor,
                        "title": "密查钱谦益",
                    },
                }

            os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
            game.session.dialogue_audit_client = audit
            response = game._dialogue_tool_response(
                actor,
                action,
                "臣照实回奏。",
                "说说本月查到什么，照实入档。",
                chat_turn_id=102,
            )

            self.assertIsNotNone(response)
            self.assertEqual(response.get("secret_order_id"), allowed_id)
            self.assertEqual(game.db.get_secret_order(wrong_id)["result"], "")
            self.assertIn("审计准许", game.db.get_secret_order(allowed_id)["result"])
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_secret_order_issue_tool_action_uses_decision_payload_boundary(self):
        game = web_app.WebGame(fresh=True)
        try:
            actor = "温体仁"
            action = {
                "type": "secret_order",
                "phase": "confirm",
                "kind": "issue",
                "mode": "secret_order",
                "target": actor,
                "assignee": actor,
                "title": "密查错档",
                "content": "工具夹带了不该入档的密令内容。",
                "tags": ["错档"],
                "deadline_months": 9,
            }

            def audit(phase, payload):
                if phase != "dialogue_action_intent":
                    return None
                tool_action = payload.get("tool_action") or {}
                self.assertEqual(tool_action.get("title"), "密查错档")
                return {
                    "allow": True,
                    "phase": "confirm",
                    "action_type": "secret_order",
                    "kind": "issue",
                    "target": actor,
                    "actor": actor,
                    "confidence": 96,
                    "trigger_quote": "暗查钱谦益",
                    "private_reason": "审计只准许钱谦益密令建档。",
                    "payload": {
                        "title": "密查钱谦益",
                        "content": "暗查钱谦益起复东林旧臣之议，摸清同党牵连。",
                        "tags": ["钱谦益", "东林", "起复"],
                        "assignee": actor,
                        "deadline_months": 2,
                    },
                }

            os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
            game.session.dialogue_audit_client = audit
            response = game._dialogue_tool_response(
                actor,
                action,
                "臣遵旨密办。",
                "给温体仁下密令，暗查钱谦益起复东林旧臣之议，两月内回奏。",
                chat_turn_id=103,
            )

            self.assertIsNotNone(response)
            order_id = int(response.get("secret_order_id") or 0)
            self.assertGreater(order_id, 0)
            order = game.db.get_secret_order(order_id) or {}
            self.assertEqual(order.get("title"), "密查钱谦益")
            self.assertIn("钱谦益起复", str(order.get("content") or ""))
            self.assertNotIn("错档", str(order.get("title") or "") + str(order.get("content") or ""))
            self.assertEqual(int(order.get("due_turn") or 0), int(game.state.turn) + 2)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_tool_response_uses_unified_semantic_gate_for_propose_and_confirm(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            calls = []

            class FakeEngine:
                def gate_tool_action(
                    self,
                    character,
                    user_text,
                    action,
                    *,
                    phase="",
                    pending_action=None,
                ):
                    calls.append({
                        "character": character.name,
                        "user_text": user_text,
                        "action": dict(action or {}),
                        "phase": phase,
                        "pending_action": dict(pending_action or {}),
                    })
                    return SemanticDecision(
                        decision_type="tool",
                        action_type="recruitment",
                        phase=phase,
                        kind="eunuch",
                        confidence=96,
                        trigger_quote=user_text,
                        private_reason=f"test tool {phase}",
                    )

            game._dialogue_semantic_engine = lambda: FakeEngine()

            proposed = game._dialogue_tool_response(
                attendant,
                {"type": "recruitment", "kind": "eunuch"},
                "陛下若准，奴婢便去挑一个忠谨可用的来。",
                "宫里可有新的小内侍可用？",
            )

            self.assertIsNotNone(proposed)
            self.assertIn("陛下若准", proposed["answer"])
            pending = game._load_pending_dialogue_action(attendant)
            self.assertEqual(pending.get("type"), "recruitment")
            self.assertEqual(pending.get("kind"), "eunuch")

            confirmed = game._dialogue_tool_response(
                attendant,
                {"type": "recruitment", "phase": "confirm"},
                "",
                "准，挑一个。",
                chat_turn_id=99,
            )

            self.assertIsNotNone(confirmed)
            self.assertEqual([call["phase"] for call in calls], ["propose", "confirm"])
            self.assertEqual(calls[0]["pending_action"], {})
            self.assertEqual(calls[1]["pending_action"]["type"], "recruitment")
            self.assertEqual(calls[1]["action"]["kind"], "eunuch")
            self.assertEqual(game._load_pending_dialogue_action(attendant), {})
            self.assertEqual(confirmed.get("court_action"), "summon")
            self.assertTrue(confirmed.get("recruited_minister"))
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_secret_order_tool_response_uses_unified_semantic_gate(self):
        game = web_app.WebGame(fresh=True)
        try:
            actor = "温体仁"
            action = {
                "type": "secret_order",
                "phase": "confirm",
                "target": actor,
                "assignee": actor,
                "title": "密查钱谦益",
                "content": "暗查钱谦益起复东林旧臣之议，摸清同党牵连。",
                "tags": ["钱谦益", "东林", "起复"],
                "deadline_months": 2,
            }

            def deny_audit(phase, payload):
                if phase != "dialogue_action_intent":
                    return None
                return {
                    "allow": False,
                    "phase": "none",
                    "action_type": "none",
                    "confidence": 96,
                    "private_reason": "只是问是否可暗查，不是下密令。",
                }

            os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
            game.session.dialogue_audit_client = deny_audit
            denied = game._dialogue_tool_response(
                actor,
                action,
                "臣可密查。",
                "此事能否暗查？",
            )
            self.assertIsNone(denied)
            self.assertEqual(game.db.list_secret_orders(), [])

            def allow_audit(phase, payload):
                if phase != "dialogue_action_intent":
                    return None
                tool_action = payload.get("tool_action") or {}
                self.assertEqual(tool_action.get("type"), "secret_order")
                return {
                    "allow": True,
                    "phase": "confirm",
                    "action_type": "secret_order",
                    "target": actor,
                    "actor": actor,
                    "confidence": 96,
                    "trigger_quote": "给温体仁下密令",
                    "private_reason": "玩家明确下密令并指定暗查目标。",
                }

            game.session.dialogue_audit_client = allow_audit
            allowed = game._dialogue_tool_response(
                actor,
                action,
                "臣遵旨密办。",
                "给温体仁下密令，暗查钱谦益起复东林旧臣之议，两月内回奏。",
                chat_turn_id=77,
            )

            self.assertIsNotNone(allowed)
            self.assertGreater(int(allowed.get("secret_order_id") or 0), 0)
            self.assertEqual(allowed.get("secret_order_assignee"), actor)
            self.assertEqual(len(game.db.list_secret_orders()), 1)
            effect = allowed.get("dialogue_effect") or {}
            self.assertEqual(effect.get("title"), "密令建档")
            self.assertIn("密令 #", allowed.get("answer") or "")
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_tool_response_requires_trigger_quote_from_current_user_text(self):
        game = web_app.WebGame(fresh=True)
        try:
            actor = "温体仁"
            action = {
                "type": "secret_order",
                "phase": "confirm",
                "target": actor,
                "assignee": actor,
                "title": "密查钱谦益",
                "content": "暗查钱谦益起复东林旧臣之议，摸清同党牵连。",
                "tags": ["钱谦益", "东林", "起复"],
                "deadline_months": 2,
            }

            def allow_with_unsupported_quote(phase, payload):
                if phase != "dialogue_action_intent":
                    return None
                return {
                    "allow": True,
                    "phase": "confirm",
                    "action_type": "secret_order",
                    "target": actor,
                    "actor": actor,
                    "confidence": 96,
                    "trigger_quote": "给温体仁下密令",
                    "private_reason": "审计误把追问当成密令。",
                }

            os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
            game.session.dialogue_audit_client = allow_with_unsupported_quote
            response = game._dialogue_tool_response(
                actor,
                action,
                "臣可密查。",
                "此事能否暗查钱谦益？",
            )

            self.assertIsNone(response)
            self.assertEqual(game.db.list_secret_orders(), [])
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_pending_tool_confirmation_rejects_stale_source_quote(self):
        game = web_app.WebGame(fresh=True)
        try:
            actor = "王承恩"
            pending = {
                "type": "recruitment",
                "kind": "eunuch",
                "source_quote": "宫里可有新的小内侍可用",
                "trigger_quote": "宫里可有新的小内侍可用",
                "proposal_evidence": "拟招一个小内侍。",
            }
            game._store_pending_dialogue_action(actor, pending)
            before = len(game.content.characters)

            def allow_with_stale_quote(phase, payload):
                if phase != "recruitment_intent":
                    return None
                action = payload.get("tool_action") or {}
                self.assertEqual(action.get("trigger_quote"), "先说风险，不要招。")
                self.assertNotIn("source_quote", action)
                return {
                    "allow": True,
                    "phase": "confirm",
                    "kind": "eunuch",
                    "trigger_quote": "宫里可有新的小内侍可用",
                    "private_reason": "审计误用 pending 原句当成本轮确认。",
                    "confidence": 96,
                }

            os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
            game.session.dialogue_audit_client = allow_with_stale_quote
            response = game._dialogue_tool_response(
                actor,
                {"type": "recruitment", "phase": "confirm", "kind": "eunuch"},
                "奴婢可以去招。",
                "先说风险，不要招。",
            )

            self.assertIsNone(response)
            self.assertEqual(len(game.content.characters), before)
            still_pending = game._load_pending_dialogue_action(actor)
            self.assertEqual(still_pending.get("type"), "recruitment")
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_secret_order_rush_tool_response_uses_unified_semantic_gate(self):
        game = web_app.WebGame(fresh=True)
        try:
            actor = "温体仁"
            order_id = game.db.create_secret_order(
                game.state,
                actor,
                "密查钱谦益",
                "暗查钱谦益起复东林旧臣之议，摸清同党牵连。",
                ["钱谦益", "东林", "起复"],
                deadline_months=3,
            )
            before = game.db.get_secret_order(order_id)
            action = {
                "type": "secret_order",
                "phase": "confirm",
                "kind": "rush",
                "mode": "rush",
                "target": actor,
                "assignee": actor,
                "order_id": order_id,
                "deadline_months": 0,
                "reason": "本月即核。",
            }

            def deny_audit(phase, payload):
                if phase != "dialogue_action_intent":
                    return None
                return {
                    "allow": False,
                    "phase": "none",
                    "action_type": "none",
                    "confidence": 96,
                    "private_reason": "只是追问进度，不是催办。",
                }

            os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
            game.session.dialogue_audit_client = deny_audit
            denied = game._dialogue_tool_response(
                actor,
                action,
                "臣可回奏查办进度。",
                "这条密令查到哪了？",
            )
            self.assertIsNone(denied)
            self.assertEqual(game.db.get_secret_order(order_id)["status"], "active")
            self.assertEqual(game.db.get_secret_order(order_id)["due_turn"], before["due_turn"])

            def allow_audit(phase, payload):
                if phase != "dialogue_action_intent":
                    return None
                tool_action = payload.get("tool_action") or {}
                self.assertEqual(tool_action.get("type"), "secret_order")
                self.assertEqual(tool_action.get("kind"), "rush")
                self.assertEqual(tool_action.get("order_id"), order_id)
                return {
                    "allow": True,
                    "phase": "confirm",
                    "action_type": "secret_order",
                    "kind": "rush",
                    "target": actor,
                    "actor": actor,
                    "confidence": 96,
                    "trigger_quote": "本月即核",
                    "private_reason": "玩家明确催办既有密令。",
                }

            game.session.dialogue_audit_client = allow_audit
            allowed = game._dialogue_tool_response(
                actor,
                action,
                "臣遵旨加急。",
                "把这条密令本月即核。",
                chat_turn_id=78,
            )

            self.assertIsNotNone(allowed)
            self.assertEqual(int(allowed.get("secret_order_id") or 0), order_id)
            self.assertEqual(allowed.get("secret_order_assignee"), actor)
            self.assertEqual(game.db.get_secret_order(order_id)["status"], "pending_review")
            effect = allowed.get("dialogue_effect") or {}
            self.assertEqual(effect.get("title"), "密令催办")
            self.assertIn("即核", allowed.get("answer") or "")
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_secret_order_dispatch_strategy_tool_response_uses_unified_semantic_gate(self):
        game = web_app.WebGame(fresh=True)
        try:
            actor = "王承恩"
            game.state.metrics["内库"] = 30
            el.record_castration(
                game.db,
                actor,
                forced=True,
                day=1,
                detail_text="净身房无麻，宝官库石灰封存；近来漏尿尿闭，幻肢痛，按肩会僵住。",
            )
            order_id = game.db.create_secret_order(
                game.state,
                actor,
                "密查净身房封签",
                "夜间久候盯梢刑房封签，拿问口供，查清官库旧案。",
                ["刑房", "封签", "净身房"],
                deadline_months=1,
            )
            action = {
                "type": "secret_order",
                "phase": "confirm",
                "kind": "dispatch_strategy",
                "mode": "relay",
                "strategy": "relay",
                "target": actor,
                "assignee": actor,
                "order_id": order_id,
                "note": "准副手轮值，别硬撑坏事。",
            }

            def deny_audit(phase, payload):
                if phase != "dialogue_action_intent":
                    return None
                return {
                    "allow": False,
                    "phase": "none",
                    "action_type": "none",
                    "confidence": 96,
                    "private_reason": "只是问可选策略，不是准许执行。",
                }

            os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
            game.session.dialogue_audit_client = deny_audit
            denied = game._dialogue_tool_response(
                actor,
                action,
                "奴婢可列几策。",
                "你看旧患差遣怎么稳妥？",
            )
            self.assertIsNone(denied)
            self.assertFalse(str(game.db.get_secret_order(order_id).get("sim_note") or "").strip())
            self.assertEqual(game.state.metrics["内库"], 30)

            def allow_audit(phase, payload):
                if phase != "dialogue_action_intent":
                    return None
                tool_action = payload.get("tool_action") or {}
                self.assertEqual(tool_action.get("type"), "secret_order")
                self.assertEqual(tool_action.get("kind"), "dispatch_strategy")
                self.assertEqual(tool_action.get("strategy"), "relay")
                self.assertEqual(tool_action.get("order_id"), order_id)
                return {
                    "allow": True,
                    "phase": "confirm",
                    "action_type": "secret_order",
                    "kind": "dispatch_strategy",
                    "mode": "relay",
                    "target": actor,
                    "actor": actor,
                    "confidence": 96,
                    "trigger_quote": "副手轮值",
                    "private_reason": "玩家明确准许按净身旧患调整差遣策略。",
                }

            game.session.dialogue_audit_client = allow_audit
            allowed = game._dialogue_tool_response(
                actor,
                action,
                "奴婢遵旨。",
                "准，副手轮值，别硬撑坏事。",
                chat_turn_id=79,
            )

            self.assertIsNotNone(allowed)
            self.assertEqual(int(allowed.get("secret_order_id") or 0), order_id)
            self.assertEqual(allowed.get("secret_order_assignee"), actor)
            self.assertIn("分班轮值", game.db.get_secret_order(order_id)["sim_note"])
            self.assertEqual(game.state.metrics["内库"], 29)
            effect = allowed.get("dialogue_effect") or {}
            self.assertEqual(effect.get("title"), "旧患差遣")
            self.assertIn("旧患风险", allowed.get("answer") or "")
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_secret_order_progress_and_review_tool_response_uses_unified_semantic_gate(self):
        game = web_app.WebGame(fresh=True)
        try:
            actor = "温体仁"
            progress_id = game.db.create_secret_order(
                game.state,
                actor,
                "密查钱谦益",
                "暗查钱谦益起复东林旧臣之议，摸清同党牵连。",
                ["钱谦益", "东林", "起复"],
                deadline_months=3,
            )
            review_id = game.db.create_secret_order(
                game.state,
                actor,
                "密查仓场钱粮",
                "暗查仓场钱粮亏空。",
                ["仓场", "钱粮"],
                deadline_months=3,
            )
            game.state.period = 2
            game.state.turn = 2

            progress_action = {
                "type": "secret_order",
                "phase": "confirm",
                "kind": "progress",
                "mode": "progress",
                "target": actor,
                "assignee": actor,
                "order_id": progress_id,
                "progress": "探得钱谦益门生往来频密，尚须核实名帖。",
            }

            def deny_audit(phase, payload):
                if phase != "dialogue_action_intent":
                    return None
                return {
                    "allow": False,
                    "phase": "none",
                    "action_type": "none",
                    "confidence": 96,
                    "private_reason": "只是问何时办完，不是要求入档进展。",
                }

            os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
            game.session.dialogue_audit_client = deny_audit
            denied = game._dialogue_tool_response(
                actor,
                progress_action,
                "臣尚在查。",
                "此事何时能办完？",
            )
            self.assertIsNone(denied)
            self.assertEqual(game.db.get_secret_order(progress_id)["result"], "")

            def allow_progress_audit(phase, payload):
                if phase != "dialogue_action_intent":
                    return None
                tool_action = payload.get("tool_action") or {}
                self.assertEqual(tool_action.get("kind"), "progress")
                self.assertEqual(tool_action.get("order_id"), progress_id)
                return {
                    "allow": True,
                    "phase": "confirm",
                    "action_type": "secret_order",
                    "kind": "progress",
                    "target": actor,
                    "actor": actor,
                    "confidence": 96,
                    "trigger_quote": "照实入档",
                    "private_reason": "玩家明确要求承办人回奏并落档本月进展。",
                }

            game.session.dialogue_audit_client = allow_progress_audit
            progress_response = game._dialogue_tool_response(
                actor,
                progress_action,
                "臣照实回奏。",
                "说说本月查到什么，照实入档。",
                chat_turn_id=80,
            )
            self.assertIsNotNone(progress_response)
            self.assertEqual((progress_response.get("dialogue_effect") or {}).get("title"), "密令进展")
            self.assertIn("门生往来", game.db.get_secret_order(progress_id)["result"])

            submit_action = {
                "type": "secret_order",
                "phase": "confirm",
                "kind": "submit_review",
                "mode": "submit_review",
                "target": actor,
                "assignee": actor,
                "order_id": review_id,
                "claim": "已查得仓场亏空名册，臣请付核。",
            }

            def allow_submit_audit(phase, payload):
                if phase != "dialogue_action_intent":
                    return None
                tool_action = payload.get("tool_action") or {}
                self.assertEqual(tool_action.get("kind"), "submit_review")
                self.assertEqual(tool_action.get("order_id"), review_id)
                return {
                    "allow": True,
                    "phase": "confirm",
                    "action_type": "secret_order",
                    "kind": "submit_review",
                    "target": actor,
                    "actor": actor,
                    "confidence": 96,
                    "trigger_quote": "提交核议",
                    "private_reason": "玩家明确准许提交核议。",
                }

            game.session.dialogue_audit_client = allow_submit_audit
            submit_response = game._dialogue_tool_response(
                actor,
                submit_action,
                "臣请付核。",
                "若已办到位，就提交核议。",
                chat_turn_id=81,
            )
            self.assertIsNotNone(submit_response)
            self.assertEqual((submit_response.get("dialogue_effect") or {}).get("title"), "密令核议")
            self.assertEqual(game.db.get_secret_order(review_id)["status"], "pending_review")
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_chat_applies_session_secret_order_action_through_web_semantic_gate(self):
        game = web_app.WebGame(fresh=True)
        try:
            actor = "温体仁"
            action = {
                "type": "secret_order",
                "phase": "confirm",
                "target": actor,
                "assignee": actor,
                "title": "密查钱谦益",
                "content": "暗查钱谦益起复东林旧臣之议，摸清同党牵连。",
                "tags": ["钱谦益", "东林", "起复"],
                "deadline_months": 2,
            }
            calls = []

            def fake_chat(minister_name, message, *, source_chat_turn_id=0, supplemental_context="", source_context=None):
                calls.append((minister_name, message, source_chat_turn_id))
                return session_module.ChatTurnResult(
                    answer="臣遵旨密办。",
                    dialogue_action=dict(action),
                )

            def audit(phase, payload):
                if phase == "dialogue_route_intent":
                    return {
                        "allow": False,
                        "intent": "none",
                        "confidence": 95,
                        "private_reason": "not route",
                    }
                if phase != "dialogue_action_intent":
                    return None
                tool_action = payload.get("tool_action") or {}
                if tool_action.get("type") == "semantic_probe":
                    return {
                        "allow": False,
                        "phase": "none",
                        "action_type": "none",
                        "confidence": 95,
                        "private_reason": "let NPC tool path run",
                    }
                return {
                    "allow": True,
                    "phase": "confirm",
                    "action_type": "secret_order",
                    "target": actor,
                    "actor": actor,
                    "confidence": 96,
                    "trigger_quote": "给温体仁下密令",
                    "private_reason": "玩家明确下密令并指定暗查目标。",
                }

            os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
            os.environ["MING_SIM_DISABLE_DIALOGUE_ROUTE_LLM_AUDIT"] = "0"
            game.session.dialogue_audit_client = audit
            game.session.chat = fake_chat  # type: ignore[method-assign]

            payload = game.chat(
                actor,
                "给温体仁下密令，暗查钱谦益起复东林旧臣之议，两月内回奏。",
            )

            self.assertEqual(len(calls), 1)
            self.assertGreater(int(payload.get("secret_order_id") or 0), 0)
            self.assertEqual(payload.get("secret_order_assignee"), actor)
            self.assertEqual((payload.get("dialogue_effect") or {}).get("title"), "密令建档")
            self.assertEqual(len(game.db.list_secret_orders()), 1)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_chat_and_stream_apply_secret_order_progress_with_same_semantic_payload(self):
        actor = "温体仁"
        progress = "探得钱谦益门生往来频密，尚须核实名帖。"
        user_text = "说说本月查到什么，照实入档。"

        def install_audit(game: web_app.WebGame, order_id: int) -> None:
            def audit(phase, payload):
                if phase == "dialogue_route_intent":
                    return {
                        "allow": False,
                        "intent": "none",
                        "confidence": 95,
                        "private_reason": "not route",
                    }
                if phase != "dialogue_action_intent":
                    return None
                tool_action = payload.get("tool_action") or {}
                if tool_action.get("type") == "semantic_probe":
                    return {
                        "allow": False,
                        "phase": "none",
                        "action_type": "none",
                        "confidence": 95,
                        "private_reason": "let NPC tool path run",
                    }
                self.assertEqual(tool_action.get("type"), "secret_order")
                self.assertEqual(tool_action.get("kind"), "progress")
                self.assertEqual(int(tool_action.get("order_id") or 0), order_id)
                return {
                    "allow": True,
                    "phase": "confirm",
                    "action_type": "secret_order",
                    "kind": "progress",
                    "target": actor,
                    "actor": actor,
                    "confidence": 96,
                    "trigger_quote": "照实入档",
                    "private_reason": "玩家明确要求承办人回奏并落档本月进展。",
                }

            os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
            os.environ["MING_SIM_DISABLE_DIALOGUE_ROUTE_LLM_AUDIT"] = "0"
            game.session.dialogue_audit_client = audit

        base_root = Path(self.tmp.name)

        def use_data_root(label: str) -> None:
            root = base_root / label

            def user_data_dir() -> Path:
                root.mkdir(parents=True, exist_ok=True)
                return root

            def user_data_path(*parts: str) -> str:
                path = root.joinpath(*parts)
                path.parent.mkdir(parents=True, exist_ok=True)
                return str(path)

            web_app.user_data_dir = user_data_dir
            web_app.user_data_path = user_data_path

        def make_game(label: str) -> tuple[web_app.WebGame, int, dict]:
            use_data_root(label)
            game = web_app.WebGame(fresh=True)
            order_id = game.db.create_secret_order(
                game.state,
                actor,
                "密查钱谦益",
                "暗查钱谦益起复东林旧臣之议，摸清同党牵连。",
                ["钱谦益", "东林", "起复"],
                deadline_months=3,
            )
            game.state.period = 2
            game.state.turn = 2
            action = {
                "type": "secret_order",
                "phase": "confirm",
                "kind": "progress",
                "mode": "progress",
                "target": actor,
                "assignee": actor,
                "order_id": order_id,
                "progress": progress,
            }
            install_audit(game, order_id)
            return game, order_id, action

        sync_game, sync_order_id, sync_action = make_game("secret-order-sync")
        stream_game, stream_order_id, _stream_action = make_game("secret-order-stream")
        try:
            def fake_chat(minister_name, message, *, source_chat_turn_id=0, supplemental_context="", source_context=None):
                return session_module.ChatTurnResult(
                    answer="臣照实回奏。",
                    dialogue_action=dict(sync_action),
                )

            sync_game.session.chat = fake_chat  # type: ignore[method-assign]
            sync_payload = sync_game.chat(actor, user_text)

            class ToolExec:
                tool_name = "report_secret_order_progress"
                result = "__secret_order_followup__" + json.dumps(
                    {
                        "action": "progress",
                        "type": "secret_order",
                        "kind": "progress",
                        "mode": "progress",
                        "order_id": stream_order_id,
                        "title": "密查钱谦益",
                        "assignee": actor,
                        "progress": progress,
                    },
                    ensure_ascii=False,
                )
                arguments = {}
                tool_args = {}

            class RunContent:
                event = "RunContent"
                content = "臣照实回奏。"

            class RunCompletedEvent:
                tools = [ToolExec()]

            class FakeAgent:
                def run(self, _prompt, **_kwargs):
                    yield RunContent()
                    yield RunCompletedEvent()

            class FakeRegistry:
                session_ids = {}
                campaign_id = "test-campaign"

                def build_draft_line(self):
                    return "无"

                def get(self, _character):
                    return FakeAgent()

            stream_game.session.registry = FakeRegistry()
            events = list(stream_game.chat_stream(actor, user_text))
            self.assertEqual(events[-1]["type"], "done")
            stream_payload = events[-1]["payload"]

            self.assertEqual(sync_payload.get("secret_order_id"), sync_order_id)
            self.assertEqual(stream_payload.get("secret_order_id"), stream_order_id)
            self.assertEqual(sync_payload.get("secret_order_assignee"), actor)
            self.assertEqual(stream_payload.get("secret_order_assignee"), actor)
            self.assertEqual((sync_payload.get("dialogue_effect") or {}).get("title"), "密令进展")
            self.assertEqual((stream_payload.get("dialogue_effect") or {}).get("title"), "密令进展")
            self.assertIn("门生往来", sync_game.db.get_secret_order(sync_order_id)["result"])
            self.assertIn("门生往来", stream_game.db.get_secret_order(stream_order_id)["result"])
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(sync_game.db_path)
                stop_worker(stream_game.db_path)
            finally:
                sync_game.session.close()
                stream_game.session.close()

    def test_ambiguous_who_is_usable_does_not_open_recommendation_pool(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            game.session.dialogue_audit_client = self._recruitment_audit(allow=False)

            response = game._dialogue_tool_response(
                attendant,
                {
                    "type": "recruitment",
                    "phase": "propose",
                    "kind": "recommend",
                    "trigger_quote": "朝中还有谁可用",
                },
                "奴婢试着荐一人。",
                "朝中还有谁可用？先说现有人，不要荐新人。",
            )

            self.assertIsNone(response)
            self.assertEqual(game._load_pending_dialogue_action(attendant), {})
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_recommendation_chain_question_does_not_create_pending_recruitment(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            game.session.dialogue_audit_client = self._recruitment_audit(allow=False)

            response = game._dialogue_tool_response(
                attendant,
                {
                    "type": "recruitment",
                    "phase": "propose",
                    "kind": "recommend",
                    "trigger_quote": "门生举荐链",
                },
                "奴婢可举荐一人。",
                "你怎么看韩爌的门生举荐链，别再荐新人。",
            )

            self.assertIsNone(response)
            self.assertEqual(game._load_pending_dialogue_action(attendant), {})
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_recruitment_confirm_without_pending_is_ignored(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            game.session.dialogue_audit_client = self._recruitment_audit(allow=True, kind="recommend")

            response = game._dialogue_tool_response(
                attendant,
                {
                    "type": "recruitment",
                    "phase": "confirm",
                    "kind": "recommend",
                    "trigger_quote": "准",
                },
                "臣遵旨荐人。",
                "准。",
            )

            self.assertIsNone(response)
            self.assertEqual(game._load_pending_dialogue_action(attendant), {})
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_pending_recruitment_followup_question_does_not_execute(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            game.session.dialogue_audit_client = self._recruitment_audit(allow=False)
            game._store_pending_dialogue_action(attendant, {"type": "recruitment", "kind": "eunuch"})
            before = game.db.conn.execute("SELECT COUNT(*) AS n FROM characters").fetchone()["n"]

            response = game._dialogue_action_response(attendant, "好，你说谁合适？")

            after = game.db.conn.execute("SELECT COUNT(*) AS n FROM characters").fetchone()["n"]
            self.assertIsNone(response)
            self.assertEqual(after, before)
            self.assertEqual(game._load_pending_dialogue_action(attendant).get("kind"), "eunuch")
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_colloquial_recruitment_confirmation_brings_new_eunuch_to_audience(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            game.session.dialogue_audit_client = self._recruitment_audit(allow=True, kind="eunuch")
            first = game._dialogue_tool_response(
                attendant,
                {
                    "type": "recruitment",
                    "phase": "propose",
                    "kind": "eunuch",
                    "trigger_quote": "再招募一个小内侍吧",
                },
                "",
                "算了，你再招募一个小内侍吧",
            )

            self.assertIsNotNone(first)
            self.assertIn("陛下若准", first["answer"])
            self.assertEqual(game._load_pending_dialogue_action(attendant).get("kind"), "eunuch")

            events = list(game.chat_stream(attendant, "好的，先把人带来我看看"))

            self.assertEqual(events[-1]["type"], "done")
            payload = events[-1]["payload"]
            recruited = str(payload.get("recruited_minister") or "")
            self.assertTrue(recruited)
            self.assertEqual(payload["court_action"], "summon")
            self.assertEqual(payload["next_minister"], recruited)
            self.assertIn(recruited, game.content.characters)
            self.assertFalse(game._load_pending_dialogue_action(attendant))
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_recruitment_confirmation_recovers_when_pending_marker_is_missing(self):
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            game.session.dialogue_audit_client = self._recruitment_audit(allow=True, kind="eunuch")
            game.chat_history[attendant] = [
                {
                    "role": "minister",
                    "content": (
                        "奴婢回陛下，内书堂和司礼监下头确有几个识字小火者可挑。"
                        "只是人一入御前，便牵动监房旧例，奴婢不敢擅专。"
                        "陛下若准，奴婢便去挑一个忠谨可用的来。"
                    ),
                }
            ]
            self.assertFalse(game._load_pending_dialogue_action(attendant))

            events = list(game.chat_stream(attendant, "好，你去招募"))

            self.assertEqual(events[-1]["type"], "done")
            payload = events[-1]["payload"]
            recruited = str(payload.get("recruited_minister") or "")
            self.assertTrue(recruited)
            self.assertEqual(payload["court_action"], "summon")
            self.assertEqual(payload["next_minister"], recruited)
            self.assertIn(recruited, game.content.characters)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_name_first_palace_nickname_summon_gets_inner_court_identity_note(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            self.assertNotIn("小顺子", game.content.characters)

            events = list(game.chat_stream(attendant, "小顺子叫来我见见"))

            self.assertEqual(events[-1]["type"], "done")
            payload = events[-1]["payload"]
            self.assertEqual(payload["court_action"], "summon")
            self.assertEqual(payload["next_minister"], "小顺子")
            self.assertEqual(payload["registered_minister"], "小顺子")
            row = game.db.conn.execute(
                "SELECT office, office_type, birth_year, summary FROM characters WHERE name=?",
                ("小顺子",),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertTrue(is_eunuch_office(str(row["office"]), str(row["office_type"])))
            self.assertLessEqual(int(game.state.year) - int(row["birth_year"]), 16)
            self.assertIn("内廷小名补档", str(row["summary"] or ""))
            self.assertIn("候用小火者", str(row["summary"] or ""))
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_named_waiting_complaint_materializes_unlisted_palace_nickname(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            self.assertNotIn("小禄子", game.content.characters)

            events = list(game.chat_stream(attendant, "我叫了小禄子很久都没有人出现"))

            self.assertEqual(events[-1]["type"], "done")
            payload = events[-1]["payload"]
            self.assertEqual(payload["court_action"], "summon")
            self.assertEqual(payload["next_minister"], "小禄子")
            self.assertEqual(payload["registered_minister"], "小禄子")
            self.assertIn("催问补入名册", str(payload["answer"]))
            row = game.db.conn.execute(
                "SELECT office, office_type, summary FROM characters WHERE name=?",
                ("小禄子",),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertTrue(is_eunuch_office(str(row["office"]), str(row["office_type"])))
            self.assertIn("内廷小名补档", str(row["summary"] or ""))
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_roleplayed_summon_answer_cache_recovers_short_where_is_he_followup(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            self.assertNotIn("小禄子", game.content.characters)
            game._record_unknown_dialogue_mentions(
                attendant,
                "——传内书堂生徒小禄子觐见。小禄子胆子小，该在殿外候着了。",
            )
            self.assertIn("小禄子", game._load_unknown_dialogue_mentions())

            events = list(game.chat_stream(attendant, "人呢"))

            payload = events[-1]["payload"]
            self.assertEqual(payload["court_action"], "summon")
            self.assertEqual(payload["next_minister"], "小禄子")
            self.assertEqual(payload["registered_minister"], "小禄子")
            self.assertIn("小禄子", game.content.characters)
            self.assertFalse(game._load_unknown_dialogue_mentions())
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_quoted_waiting_complaint_materializes_unlisted_palace_nickname(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            self.assertNotIn("小禄子", game.content.characters)

            events = list(game.chat_stream(attendant, "我叫了“小禄子”很久都没有人出现"))

            self.assertEqual(events[-1]["type"], "done")
            payload = events[-1]["payload"]
            self.assertEqual(payload["court_action"], "summon")
            self.assertEqual(payload["next_minister"], "小禄子")
            self.assertEqual(payload["registered_minister"], "小禄子")
            self.assertIn("催问补入名册", str(payload["answer"]))
            row = game.db.conn.execute(
                "SELECT office, office_type, summary FROM characters WHERE name=?",
                ("小禄子",),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertTrue(is_eunuch_office(str(row["office"]), str(row["office_type"])))
            self.assertIn("内廷小名补档", str(row["summary"] or ""))
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_waiting_complaint_recovers_recent_roleplayed_summon_without_store(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            self.assertNotIn("小禄子", game.content.characters)
            game.chat_history[attendant] = [
                {
                    "role": "minister",
                    "content": "——传内书堂生徒小禄子觐见。小禄子胆子小，该在殿外候着了。",
                }
            ]

            events = list(game.chat_stream(attendant, "我叫了很久都没人出现"))

            self.assertEqual(events[-1]["type"], "done")
            payload = events[-1]["payload"]
            self.assertEqual(payload["court_action"], "summon")
            self.assertEqual(payload["next_minister"], "小禄子")
            self.assertEqual(payload["registered_minister"], "小禄子")
            self.assertIn("小禄子", game.content.characters)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_old_dialogue_palace_nickname_waiting_profile_migrates_to_inner_court(self):
        game = web_app.WebGame(fresh=True)
        try:
            name = "小禄子"
            self.assertNotIn(name, game.content.characters)
            game.db.add_character(
                game.state,
                Character(
                    name=name,
                    office="待铨（对白寻访）",
                    office_type="待铨",
                    faction="清流",
                    aliases=[],
                    personal_skills=[],
                    loyalty=60,
                    ability=55,
                    integrity=60,
                    courage=50,
                    style="陛下点名，底细待察",
                    power_id="ming",
                    birth_year=1599,
                    status="active",
                    summary="由王承恩对白中提及，后奉旨按线索寻访入京；此人物为当前活动存档内即时补档。",
                ),
                source="对白线索补档",
            )
            game.db.conn.execute(
                "INSERT INTO chat_messages (minister_name, turn, role, content) VALUES (?, ?, ?, ?)",
                ("王承恩", game.state.turn, "minister", "陛下，小禄子今年十一，保定府人，原是逃荒到京的孤儿。"),
            )
            game.db.conn.commit()

            changed = game.db._reconcile_dialogue_palace_nicknames()

            self.assertEqual(changed, 1)
            row = game.db.conn.execute(
                "SELECT office, office_type, faction, birth_year, summary FROM characters WHERE name=?",
                (name,),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertTrue(is_eunuch_office(str(row["office"]), str(row["office_type"])))
            self.assertEqual(str(row["faction"] or ""), "内廷")
            self.assertEqual(int(game.state.year) - int(row["birth_year"]), 11)
            self.assertIn("内廷小名补档", str(row["summary"] or ""))
            office_row = game.db.conn.execute(
                "SELECT office_title, office_type, source FROM character_offices WHERE character_name=?",
                (name,),
            ).fetchone()
            self.assertIsNotNone(office_row)
            self.assertEqual(str(office_row["office_type"] or ""), "司礼监")
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_direct_command_does_not_fold_arrival_words_into_new_name(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            unknown = "陈贵"
            self.assertNotIn(unknown, game.content.characters)

            events = list(game.chat_stream(attendant, f"叫{unknown}来见"))

            payload = events[-1]["payload"]
            self.assertEqual(payload["court_action"], "summon")
            self.assertEqual(payload["next_minister"], unknown)
            self.assertEqual(payload["registered_minister"], unknown)
            self.assertIn(unknown, game.content.characters)
            self.assertNotIn(f"{unknown}来见", game.content.characters)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_attendant_can_pull_dialogue_person_to_emperor_with_colloquial_command(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            unknown = "陈贵"
            self.assertNotIn(unknown, game.content.characters)
            game._record_unknown_dialogue_mentions(
                attendant,
                f"奴婢留心到锦衣卫南镇抚司那边一个叫{unknown}的试百户，武艺扎实。",
            )

            events = list(game.chat_stream(attendant, f"把{unknown}带到朕面前。"))

            payload = events[-1]["payload"]
            self.assertEqual(payload["court_action"], "summon")
            self.assertEqual(payload["next_minister"], unknown)
            self.assertEqual(payload["registered_minister"], unknown)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_markdown_called_unlisted_person_can_be_registered_and_summoned(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            unknown = "陈贵"
            self.assertNotIn(unknown, game.content.characters)
            game._record_unknown_dialogue_mentions(
                attendant,
                f"奴婢留心到锦衣卫南镇抚司那边一个叫**{unknown}**的试百户，武艺扎实，未必不肯近御前。",
            )
            stored = game._load_unknown_dialogue_mentions()
            self.assertIn(unknown, stored)

            events = list(game.chat_stream(attendant, f"叫{unknown}来见"))

            self.assertEqual(events[-1]["type"], "done")
            payload = events[-1]["payload"]
            self.assertEqual(payload["court_action"], "summon")
            self.assertEqual(payload["next_minister"], unknown)
            self.assertEqual(payload["registered_minister"], unknown)
            row = game.db.conn.execute(
                "SELECT office, office_type, summary FROM characters WHERE name=?",
                (unknown,),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertIn("对白中提及", str(row["summary"] or ""))
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_stale_recruitment_pending_does_not_block_named_unlisted_summon(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            unknown = "陈贵"
            self.assertNotIn(unknown, game.content.characters)
            game._store_pending_dialogue_action(attendant, {"type": "recruitment", "kind": "eunuch"})
            game._record_unknown_dialogue_mentions(
                attendant,
                f"奴婢留心到锦衣卫南镇抚司那边一个叫**{unknown}**的试百户，武艺扎实，未必不肯近御前。",
            )

            events = list(game.chat_stream(attendant, f"{unknown}？我要找他聊聊"))

            payload = events[-1]["payload"]
            self.assertEqual(payload["court_action"], "summon")
            self.assertEqual(payload["next_minister"], unknown)
            self.assertEqual(payload["registered_minister"], unknown)
            self.assertFalse(game._load_pending_dialogue_action(attendant))
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_pending_recruitment_confirmation_with_named_summon_pulls_named_person(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            self.assertNotIn("小禄子", game.content.characters)
            game._store_pending_dialogue_action(attendant, {"type": "recruitment", "kind": "eunuch"})

            events = list(game.chat_stream(attendant, "好，带小禄子过来见我"))

            payload = events[-1]["payload"]
            self.assertEqual(payload["court_action"], "summon")
            self.assertEqual(payload["next_minister"], "小禄子")
            self.assertEqual(payload["registered_minister"], "小禄子")
            self.assertEqual(str(payload.get("recruited_minister") or ""), "")
            self.assertFalse(game._load_pending_dialogue_action(attendant))
            row = game.db.conn.execute(
                "SELECT office, office_type, summary FROM characters WHERE name=?",
                ("小禄子",),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertTrue(is_eunuch_office(str(row["office"]), str(row["office_type"])))
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_eunuch_can_handle_named_summon_even_when_not_current_attendant(self):
        game = web_app.WebGame(fresh=True)
        try:
            from ming_sim.eunuch import set_attending_eunuch
            attendant = "王承恩"
            unknown = "陈贵"
            switched = set_attending_eunuch(game.db, "曹化淳")
            self.assertTrue(switched["ok"])
            self.assertNotEqual(game.db.kv_get("upgrade.attending_eunuch"), attendant)
            game._record_unknown_dialogue_mentions(
                attendant,
                f"奴婢留心到锦衣卫南镇抚司那边一个叫**{unknown}**的试百户，武艺扎实。",
            )

            events = list(game.chat_stream(attendant, f"叫{unknown}来见"))

            payload = events[-1]["payload"]
            self.assertEqual(payload["court_action"], "summon")
            self.assertEqual(payload["next_minister"], unknown)
            self.assertEqual(payload["registered_minister"], unknown)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_chat_mentions_strip_organization_title_aliases(self):
        game = web_app.WebGame(fresh=True)
        try:
            character = game.content.characters["王承恩"]
            character.aliases = list(character.aliases or []) + ["司礼监", "司礼监掌印", "司礼太监", "御马监", "京营", "王掌印"]

            org_mentions = game._chat_message_mentions("司礼监掌印今日递文，司礼太监称御马监与京营另有旧案。")
            self.assertEqual(org_mentions, [])

            mixed = game._chat_message_mentions("司礼监掌印今日递文，王承恩在旁候旨。")
            terms = {term for item in mixed for term in item["terms"]}
            self.assertIn("王承恩", terms)
            self.assertNotIn("司礼监", terms)
            self.assertNotIn("司礼监掌印", terms)
            self.assertNotIn("司礼太监", terms)
            self.assertNotIn("御马监", terms)
            self.assertNotIn("京营", terms)

            personal = game._chat_message_mentions("王掌印说司礼监今日有事。")
            personal_terms = {term for item in personal for term in item["terms"]}
            self.assertIn("王掌印", personal_terms)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_chat_mentions_ignore_live_org_alias_samples(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = game.content.characters["王承恩"]
            attendant.aliases = ["王承恩", "王伴伴", "老王", "王公公", "司礼监"]
            liu = Character(
                name="刘忠",
                office="司礼监文书房小火者",
                office_type="司礼监",
                faction="内廷",
                aliases=["司礼监文书房", "刘小火者"],
                personal_skills=[],
                loyalty=55,
                ability=50,
                integrity=45,
                courage=45,
                style="线上样本补档",
                power_id="ming",
            )
            game.content.characters[liu.name] = liu

            mentions = game._chat_message_mentions("司礼监文书房一个叫刘忠的小火者，王承恩也夸过他。")
            terms = {term for item in mentions for term in item["terms"]}

            self.assertIn("刘忠", terms)
            self.assertIn("王承恩", terms)
            self.assertNotIn("司礼监", terms)
            self.assertNotIn("文书房", terms)
            self.assertNotIn("司礼监文书房", terms)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_chat_mentions_strip_title_only_aliases_but_keep_named_titles(self):
        game = web_app.WebGame(fresh=True)
        try:
            generic = game._chat_message_mentions("首辅与次辅俱在内阁，掌印、秉笔在司礼监候旨。")
            generic_terms = {term for item in generic for term in item["terms"]}
            self.assertNotIn("首辅", generic_terms)
            self.assertNotIn("次辅", generic_terms)
            self.assertNotIn("掌印", generic_terms)
            self.assertNotIn("秉笔", generic_terms)
            self.assertNotIn("内阁", generic_terms)
            self.assertNotIn("司礼监", generic_terms)

            named = game._chat_message_mentions("黄首辅问内阁票拟，施次辅与王掌印在旁候旨。")
            named_terms = {term for item in named for term in item["terms"]}
            self.assertIn("黄首辅", named_terms)
            self.assertIn("施次辅", named_terms)
            self.assertIn("王掌印", named_terms)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_chat_mentions_keep_surnamed_office_titles_only(self):
        game = web_app.WebGame(fresh=True)
        try:
            generic = game._chat_message_mentions("太监、知府、御史与监军都在外头候着，司礼监另递一纸。")
            generic_terms = {term for item in generic for term in item["terms"]}
            self.assertNotIn("太监", generic_terms)
            self.assertNotIn("知府", generic_terms)
            self.assertNotIn("御史", generic_terms)
            self.assertNotIn("监军", generic_terms)
            self.assertNotIn("司礼监", generic_terms)

            named = game._chat_message_mentions("曹太监在司礼监递话，卢知府也请见。")
            named_terms = {term for item in named for term in item["terms"]}
            self.assertIn("曹太监", named_terms)
            self.assertIn("卢知府", named_terms)
            self.assertNotIn("司礼监", named_terms)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_chat_mentions_do_not_link_accidental_office_named_character(self):
        game = web_app.WebGame(fresh=True)
        try:
            bogus = Character(
                name="司礼监",
                office="司礼监",
                office_type="司礼监",
                faction="内廷",
                aliases=["司礼监掌印"],
                personal_skills=[],
                loyalty=50,
                ability=50,
                integrity=50,
                courage=50,
                style="误入人物池的官署名",
                power_id="ming",
            )
            game.content.characters[bogus.name] = bogus

            mentions = game._chat_message_mentions("司礼监今日递入文书，司礼监掌印候在外头。")

            self.assertEqual(mentions, [])
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_palace_nickname_unlisted_person_is_recorded_as_dialogue_mention(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            nickname = "小福子"
            game._record_unknown_dialogue_mentions(
                attendant,
                f"内书堂里那个叫**{nickname}**的火者，今年才十五，识字快、手脚勤。",
            )

            self.assertIn(nickname, game._load_unknown_dialogue_mentions())
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_direct_audience_suggestions_surface_personal_stakes(self):
        os.environ["MING_SIM_ENABLE_LOCAL_QUICK_SUGGESTIONS"] = "1"
        game = web_app.WebGame(fresh=True)
        try:
            actor = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' AND name!='王承恩' "
                "ORDER BY ability DESC LIMIT 1"
            ).fetchone()["name"])
            target = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' AND name NOT IN (?, '王承恩') "
                "ORDER BY ability ASC LIMIT 1",
                (actor,),
            ).fetchone()["name"])
            game.session.monthly_followups = []
            game.db.conn.execute(
                "INSERT OR REPLACE INTO npc_agendas "
                "(name, kind, title, target_name, intensity, status) VALUES (?, 'protect', ?, ?, 80, 'active')",
                (actor, "护持本党同气、安插自己人", target),
            )
            court._set_opinion(game.db, actor, target, -72, "旧案相攻", 1)
            game.db.upsert_event_memory(
                game.state,
                subject_type="character",
                subject_id=actor,
                event_type="imperial_favor",
                title="留中保全",
                cause="旧案未究",
                process="御前留中",
                outcome="保住差事",
                sentiment="positive",
                importance=4,
                tags=["旧恩"],
                source_kind="test",
                source_id=f"favor:{actor}",
                expires_turn=None,
            )

            suggestions = game.suggestions_for(game.session._character(actor))
            labels = {str(item["label"]) for item in suggestions}
            texts = {str(item["label"]): str(item["text"]) for item in suggestions}

            self.assertIn("听实话", labels)
            self.assertIn("问边界", labels)
            self.assertIn("问嫌隙", labels)
            self.assertIn("问旧恩", labels)
            self.assertNotIn("设交易", labels)
            self.assertNotIn("拟旨", labels)
            self.assertNotIn("下密令", labels)
            self.assertIn("保门生故旧", texts["听实话"])
            self.assertIn("举荐连坐担保", texts["问边界"])
            banned = {"交账", "问奖励", "交易", "定下一手", "快捷", "按钮"}
            self.assertFalse(any(any(term in str(item["label"]) for term in banned) for item in suggestions), suggestions)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_direct_audience_suggestions_prioritize_unfinished_commitments(self):
        os.environ["MING_SIM_ENABLE_LOCAL_QUICK_SUGGESTIONS"] = "1"
        game = web_app.WebGame(fresh=True)
        try:
            actor = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                "ORDER BY ability DESC LIMIT 1"
            ).fetchone()["name"])
            game.db.create_conversation_goal(
                game.state,
                minister_name=actor,
                action_kind="court_commitment",
                title="三日内清查粮台",
                target_text=f"{actor}须清查粮台并回奏证据。",
                threshold=70,
                score=45,
                status="waiting_conditions",
                condition_status="pending",
                conditions=[{"description": "限期回奏可验证据", "status": "pending"}],
                expires_turn=int(game.state.turn) + 2,
            )

            suggestions = game.suggestions_for(game.session._character(actor))

            self.assertEqual(suggestions[0]["label"], "追旧事")
            self.assertIn("三日内清查粮台", suggestions[0]["text"])
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_llm_suggestions_include_pending_action_and_live_dialogue(self):
        os.environ["MING_SIM_DISABLE_LLM_QUICK_SUGGESTIONS"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            actor = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                "ORDER BY ability DESC LIMIT 1"
            ).fetchone()["name"])
            calls = []

            def audit(phase, payload):
                if phase != "dialogue_suggestions":
                    return None
                calls.append(payload)
                pending = payload.get("pending_action") if isinstance(payload.get("pending_action"), dict) else {}
                label = "看待办" if pending else "问近况"
                text = f"朕要按当前语境追问。待办={pending.get('type', '')}；近话={len(payload.get('live_recent_dialogue') or [])}"
                return {"suggestions": [{"label": label, "text": text, "prefix": True}]}

            game.session.dialogue_audit_client = audit
            game.chat_history[actor] = [{"role": "minister", "content": "臣方才只是先陈利害，尚待圣裁。"}]

            first = game.suggestions_for(game.session._character(actor))
            self.assertEqual(first[0]["label"], "问近况")

            game._store_pending_dialogue_action(actor, {
                "type": "castration",
                "target": actor,
                "scheme_text": "方才所议净身入内廷方案",
            })
            game.chat_history[actor].append({"role": "user", "content": "朕再想想，先把后果说透。"})

            second = game.suggestions_for(game.session._character(actor))

            self.assertEqual(second[0]["label"], "看待办")
            self.assertGreaterEqual(len(calls), 2)
            self.assertEqual(calls[-1]["pending_action"]["type"], "castration")
            live_text = " ".join(str(row.get("content") or "") for row in calls[-1].get("live_recent_dialogue") or [])
            self.assertIn("朕再想想", live_text)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_llm_suggestions_filter_mechanical_labels(self):
        os.environ["MING_SIM_DISABLE_LLM_QUICK_SUGGESTIONS"] = "0"
        os.environ.pop("MING_SIM_ENABLE_LOCAL_QUICK_SUGGESTIONS", None)
        game = web_app.WebGame(fresh=True)
        try:
            actor = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                "ORDER BY ability DESC LIMIT 1"
            ).fetchone()["name"])

            def audit(phase, payload):
                if phase == "dialogue_suggestions":
                    return {
                        "suggestions": [
                            {"label": "确认", "text": "确认执行这个系统动作", "prefix": True},
                            {"label": "交账", "text": "快速对话：交账", "prefix": True},
                            {"label": "问底线", "text": "你先把能办到哪一步、缺什么凭据、要几日回奏说清楚。", "prefix": True},
                        ]
                    }
                return None

            game.session.dialogue_audit_client = audit

            suggestions = game.suggestions_for(game.session._character(actor))

            self.assertEqual(suggestions, [
                {"label": "问底线", "text": "你先把能办到哪一步、缺什么凭据、要几日回奏说清楚。", "prefix": True}
            ])
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_injected_suggestion_audit_runs_without_api_key(self):
        os.environ["MING_SIM_DISABLE_LLM_QUICK_SUGGESTIONS"] = "0"
        os.environ.pop("MING_SIM_ENABLE_LOCAL_QUICK_SUGGESTIONS", None)
        game = web_app.WebGame(fresh=True)
        try:
            actor = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                "ORDER BY ability DESC LIMIT 1"
            ).fetchone()["name"])
            game.session.llm_config.api_key = ""
            calls = []

            def audit(phase, payload):
                if phase != "dialogue_suggestions":
                    return None
                calls.append(payload)
                return {
                    "suggestions": [
                        {
                            "label": "问后果",
                            "text": "你别讲空话，先把这件事会牵动谁、花多少钱、几日能见效说清楚。",
                            "prefix": True,
                        }
                    ]
                }

            game.session.dialogue_audit_client = audit

            suggestions = game.suggestions_for(game.session._character(actor))

            self.assertTrue(calls)
            self.assertEqual(suggestions, [
                {
                    "label": "问后果",
                    "text": "你别讲空话，先把这件事会牵动谁、花多少钱、几日能见效说清楚。",
                    "prefix": True,
                }
            ])
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_dialogue_recruitment_requires_confirmation(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            game.session.dialogue_audit_client = self._recruitment_audit(allow=True, kind="eunuch")
            before_names = {
                str(row["name"])
                for row in game.db.conn.execute("SELECT name FROM characters").fetchall()
            }

            proposal = game._dialogue_tool_response(
                attendant,
                {
                    "type": "recruitment",
                    "phase": "propose",
                    "kind": "eunuch",
                    "trigger_quote": "有没有新的太监可用",
                },
                "",
                "宫中有没有新的太监可用？",
            )
            self.assertIsNotNone(proposal)
            self.assertIn("若准", proposal["answer"])
            mid_names = {
                str(row["name"])
                for row in game.db.conn.execute("SELECT name FROM characters").fetchall()
            }
            self.assertEqual(mid_names, before_names)

            confirm_events = list(game.chat_stream(attendant, "好，你去招募。"))
            self.assertEqual(confirm_events[-1]["type"], "done")
            payload = confirm_events[-1]["payload"]
            recruited = str(payload.get("recruited_minister") or "")
            self.assertTrue(recruited)
            self.assertNotIn(recruited, before_names)
            self.assertEqual(payload["court_action"], "summon")
            self.assertEqual(payload["next_minister"], recruited)
            self.assertTrue(payload["history"][-1].get("stage_directions"))
            row = game.db.conn.execute(
                "SELECT office, office_type, summary FROM characters WHERE name=?",
                (recruited,),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertTrue(is_eunuch_office(str(row["office"]), str(row["office_type"])))
            self.assertIn("举荐来源", str(row["summary"] or ""))
            self.assertGreater(court.get_opinion(game.db, recruited, attendant), 0)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_attendant_inner_office_title_still_uses_eunuch_self_reference(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            game.session.dialogue_audit_client = self._recruitment_audit(allow=True, kind="eunuch")
            game.db.conn.execute(
                "UPDATE characters SET office=?, office_type=? WHERE name=?",
                ("内官监御前", "内官监御前", attendant),
            )
            game.db.conn.commit()

            proposal = game._dialogue_tool_response(
                attendant,
                {
                    "type": "recruitment",
                    "phase": "propose",
                    "kind": "eunuch",
                    "trigger_quote": "再招募一个小内侍吧",
                },
                "",
                "算了，你再招募一个小内侍吧",
            )

            self.assertIsNotNone(proposal)
            self.assertTrue(proposal["answer"].startswith("奴婢回陛下"))
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_attendant_recruitment_cloud_phrase_confirms_and_summons(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            game.session.dialogue_audit_client = self._recruitment_audit(allow=True, kind="eunuch")
            proposal = game._dialogue_tool_response(
                attendant,
                {
                    "type": "recruitment",
                    "phase": "propose",
                    "kind": "eunuch",
                    "trigger_quote": "再招募一个小内侍吧",
                },
                "",
                "算了，你再招募一个小内侍吧",
            )
            self.assertIsNotNone(proposal)
            pending = game._load_pending_dialogue_action(attendant)
            self.assertEqual(pending.get("type"), "recruitment")
            self.assertEqual(pending.get("kind"), "eunuch")

            confirm_events = list(game.chat_stream(attendant, "好，你去招募"))

            payload = confirm_events[-1]["payload"]
            recruited = str(payload.get("recruited_minister") or "")
            self.assertTrue(recruited)
            self.assertEqual(payload["court_action"], "summon")
            self.assertEqual(payload["next_minister"], recruited)
            self.assertFalse(game._load_pending_dialogue_action(attendant))
            row = game.db.conn.execute(
                "SELECT office, office_type FROM characters WHERE name=?",
                (recruited,),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertTrue(is_eunuch_office(str(row["office"]), str(row["office_type"])))
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_dialogue_castration_scheme_requires_confirmation(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            row = game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' "
                "AND office_type NOT IN ('后宫','司礼监','内官监御前') "
                "AND office NOT LIKE '%太监%' AND office NOT LIKE '%宦官%' "
                "ORDER BY ability DESC LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(row)
            name = str(row["name"])
            game.session.dialogue_audit_client = self._dialogue_action_audit(
                action_type="castration",
                target=name,
            )
            game.db.conn.execute(
                "UPDATE characters SET emp_trust=55, grievance=20 WHERE name=?",
                (name,),
            )
            game.db.conn.commit()

            proposal_events = list(game.chat_stream(
                attendant,
                f"把{name}净身入内廷，净身房行事，铜柄宫刀，无麻，宝油炸封蜡，收黄杨木描金匣。",
            ))
            self.assertEqual(proposal_events[-1]["type"], "done")
            self.assertIn("若准", proposal_events[-1]["payload"]["answer"])
            self.assertIn("病历后果", proposal_events[-1]["payload"]["answer"])
            self.assertIn("差遣风险", proposal_events[-1]["payload"]["answer"])
            self.assertNotIn("铜柄宫刀", proposal_events[-1]["payload"]["answer"])
            pending = game._load_pending_dialogue_action(attendant)
            self.assertEqual(pending.get("type"), "castration")
            self.assertEqual(pending.get("target"), name)
            mid_row = game.db.conn.execute(
                "SELECT office, office_type FROM characters WHERE name=?",
                (name,),
            ).fetchone()
            self.assertIsNotNone(mid_row)
            self.assertFalse(is_eunuch_office(str(mid_row["office"]), str(mid_row["office_type"])))

            confirm_events = list(game.chat_stream(
                attendant,
                "准，照办；宝约二两八钱，一大一小，油封后发硬，日后若漏尿尿闭、嗓音尖薄、幻肢痛、贤者模式与性无能，也一并记档。",
            ))
            self.assertEqual(confirm_events[-1]["type"], "done")
            payload = confirm_events[-1]["payload"]
            self.assertEqual(payload["dialogue_effect"]["title"], "内廷改籍")
            self.assertIn("病历后果", payload["answer"])
            self.assertNotIn("强旨净身", payload["dialogue_effect"]["title"])
            self.assertIn("差遣风险", payload["answer"])
            effect_labels = {str(item.get("label") or "") for item in payload["dialogue_effect"].get("effects", [])}
            self.assertFalse(any("方案：" in label or "酷烈" in label for label in effect_labels))
            minister = game.public_character(game.content.characters[name])
            self.assertEqual(minister["office"], "司礼监随堂太监")
            self.assertEqual(minister["office_type"], "司礼监")
            self.assertNotIn("castration", minister)
            self.assertIn("medical_record", minister)
            castration = game._public_castration_payload(name)
            self.assertIsNotNone(castration)
            self.assertTrue(castration["forced"])
            self.assertEqual(castration["method_label"], "净身房登记")
            self.assertEqual(castration["knife_label"], "")
            self.assertEqual(castration["anesthesia_label"], "")
            self.assertEqual(castration["bao_weight_label"], "")
            self.assertEqual(castration["bao_shape_label"], "")
            self.assertEqual(castration["bao_texture_label"], "")
            self.assertIn("惊创", castration["trauma_label"])
            after_row = game.db.conn.execute(
                "SELECT emp_trust, grievance, ability, charm FROM characters WHERE name=?",
                (name,),
            ).fetchone()
            self.assertLess(int(after_row["emp_trust"]), 55)
            self.assertGreater(int(after_row["grievance"]), 20)
            trait_names = {
                str(r["trait"])
                for r in game.db.conn.execute("SELECT trait FROM character_traits WHERE name=?", (name,)).fetchall()
            }
            self.assertIn("内廷奴籍", trait_names)
            self.assertNotIn("惊创未平", trait_names)
            self.assertNotIn("尿路旧患", trait_names)
            self.assertNotIn("情欲异化", trait_names)
            inventory_ids = {str(item["id"]) for item in game.db.list_player_inventory()}
            self.assertIn(f"内廷旧档：{name}", inventory_ids)
            self.assertIn(f"官库宝贝：{name}", inventory_ids)
            self.assertEqual(game._load_pending_dialogue_action(attendant), {})
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_casual_castration_talk_does_not_create_pending_action(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            row = game.db.conn.execute(
                "SELECT name, office, office_type FROM characters "
                "WHERE status='active' AND power_id='ming' "
                "AND office_type NOT IN ('后宫','司礼监','内官监御前') "
                "AND office NOT LIKE '%太监%' AND office NOT LIKE '%宦官%' "
                "ORDER BY ability DESC LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(row)
            name = str(row["name"])
            before_office = str(row["office"])
            before_office_type = str(row["office_type"])
            text = f"只是聊聊{name}若净身入内廷的旧例，不是要办，别惊动净身房。"

            self.assertIsNone(game._dialogue_action_response(attendant, text))
            self.assertEqual(game._load_pending_dialogue_action(attendant), {})

            tool_response = game._dialogue_tool_response(
                attendant,
                {
                    "type": "castration",
                    "phase": "propose",
                    "target": name,
                    "scheme_text": text,
                    "force": True,
                },
                "奴婢遵旨。",
                text,
            )
            self.assertIsNone(tool_response)
            self.assertEqual(game._load_pending_dialogue_action(attendant), {})
            after = game.db.conn.execute(
                "SELECT office, office_type FROM characters WHERE name=?",
                (name,),
            ).fetchone()
            self.assertIsNotNone(after)
            self.assertEqual(str(after["office"]), before_office)
            self.assertEqual(str(after["office_type"]), before_office_type)
            self.assertFalse(is_eunuch_office(str(after["office"]), str(after["office_type"])))
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_content_regex_dialogue_actions_are_legacy_opt_in(self):
        os.environ["MING_SIM_ENABLE_DIALOGUE_REGEX_ACTIONS"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            actor = "韩爌"
            target = "魏忠贤"

            response = game._dialogue_action_response(actor, f"朕想调停你和{target}的旧怨。")

            self.assertIsNone(response)
            self.assertEqual(game._load_pending_dialogue_action(actor), {})
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_legacy_regex_action_response_requires_semantic_allow(self):
        os.environ["MING_SIM_ENABLE_DIALOGUE_REGEX_ACTIONS"] = "1"
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            actor = "韩爌"
            target = "魏忠贤"

            def audit(phase, payload):
                if phase == "dialogue_action_intent":
                    return {
                        "allow": False,
                        "phase": "none",
                        "action_type": "none",
                        "target": "",
                        "trigger_quote": str(payload.get("user_text") or "")[:80],
                        "private_reason": "语义上只是谈旧怨，不建立调停任务。",
                        "confidence": 97,
                    }
                return None

            game.session.dialogue_audit_client = audit

            response = game._dialogue_action_response(actor, f"朕想调停你和{target}的旧怨。")

            self.assertIsNone(response)
            self.assertEqual(game._load_pending_dialogue_action(actor), {})
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_chat_does_not_use_legacy_regex_action_interceptor(self):
        os.environ["MING_SIM_ENABLE_DIALOGUE_REGEX_ACTIONS"] = "1"
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "1"
        os.environ["MING_SIM_DISABLE_DIALOGUE_ROUTE_LLM_AUDIT"] = "1"
        game = web_app.WebGame(fresh=True)
        try:
            actor = "韩爌"
            target = "魏忠贤"
            calls = []

            def fake_chat(minister_name, message, *, source_chat_turn_id=0, supplemental_context=""):
                calls.append((minister_name, message, source_chat_turn_id, supplemental_context))
                return session_module.ChatTurnResult(answer="臣谨听圣裁。")

            game.session.chat = fake_chat  # type: ignore[method-assign]

            payload = game.chat(actor, f"朕想调停你和{target}的旧怨。")

            self.assertEqual(len(calls), 1)
            self.assertEqual(payload["answer"], "臣谨听圣裁。")
            self.assertEqual(game._load_pending_dialogue_action(actor), {})
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_castration_requires_structured_explicit_consent_not_stance_keywords(self):
        game = web_app.WebGame(fresh=True)
        try:
            row = game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' "
                "AND office_type NOT IN ('后宫','司礼监','内官监御前') "
                "AND office NOT LIKE '%太监%' AND office NOT LIKE '%宦官%' "
                "ORDER BY ability DESC LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(row)
            name = str(row["name"])
            game.db.record_minister_stance(
                game.state,
                name,
                topic="净身入内廷",
                stance="support",
                confidence=5,
                summary=f"{name}表面称愿入内廷听差。",
                conditions="净身后补司礼监",
                handshake_status="sealed",
                psychological_score=100,
                psychological={},
            )
            self.assertFalse(issues._castration_consent_recorded(game.db, game.state, name))

            with self.assertRaises(web_app.HTTPException) as ctx:
                game.castrate_official(name, force=False)

            self.assertEqual(ctx.exception.status_code, 409)
            self.assertIn("尚未与", str(ctx.exception.detail))
            after = game.db.conn.execute(
                "SELECT office, office_type FROM characters WHERE name=?",
                (name,),
            ).fetchone()
            self.assertFalse(is_eunuch_office(str(after["office"]), str(after["office_type"])))
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_semantic_action_probe_creates_castration_pending_without_regex(self):
        os.environ["MING_SIM_ENABLE_DIALOGUE_REGEX_ACTIONS"] = "0"
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            row = game.db.conn.execute(
                "SELECT name, office, office_type FROM characters "
                "WHERE status='active' AND power_id='ming' "
                "AND office_type NOT IN ('后宫','司礼监','内官监御前') "
                "AND office NOT LIKE '%太监%' AND office NOT LIKE '%宦官%' "
                "ORDER BY ability DESC LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(row)
            name = str(row["name"])

            def audit(phase, payload):
                if phase == "dialogue_action_intent":
                    self.assertEqual((payload.get("tool_action") or {}).get("type"), "semantic_probe")
                    return {
                        "allow": True,
                        "phase": "propose",
                        "action_type": "castration",
                        "target": name,
                        "trigger_quote": f"好，把{name}净身入内廷",
                        "confidence": 96,
                        "private_reason": "test semantic action probe",
                    }
                return None

            game.session.dialogue_audit_client = audit

            response = game._dialogue_semantic_action_response(attendant, f"好，把{name}净身入内廷。")

            self.assertIsNotNone(response)
            pending = game._load_pending_dialogue_action(attendant)
            self.assertEqual(pending.get("type"), "castration")
            self.assertEqual(pending.get("target"), name)
            after = game.db.conn.execute(
                "SELECT office, office_type FROM characters WHERE name=?",
                (name,),
            ).fetchone()
            self.assertIsNotNone(after)
            self.assertEqual(str(after["office"]), str(row["office"]))
            self.assertEqual(str(after["office_type"]), str(row["office_type"]))
            self.assertFalse(is_eunuch_office(str(after["office"]), str(after["office_type"])))
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_semantic_action_probe_can_apply_custody_punishment_and_medical_record_without_regex(self):
        os.environ["MING_SIM_ENABLE_DIALOGUE_REGEX_ACTIONS"] = "0"
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            actor = "韩爌"

            def audit(phase, payload):
                if phase == "dialogue_action_intent":
                    self.assertEqual((payload.get("tool_action") or {}).get("type"), "semantic_probe")
                    return {
                        "allow": True,
                        "phase": "confirm",
                        "action_type": "punishment",
                        "target": actor,
                        "trigger_quote": "押入昭狱，割舌禁言",
                        "character_status_changes": [{
                            "name": actor,
                            "status": "imprisoned",
                            "agency": "锦衣卫",
                            "facility": "北镇抚司昭狱",
                            "reason": "押入昭狱",
                            "severity": 5,
                        }],
                        "punishment_changes": [{
                            "name": actor,
                            "taxonomy": "ordinary",
                            "punishment": "割舌",
                            "stage": "executed",
                            "severity": 5,
                            "executor": "锦衣卫",
                            "reason": "禁其妄言",
                        }],
                        "confidence": 96,
                        "private_reason": "明确即时口谕。",
                    }
                return None

            game.session.dialogue_audit_client = audit

            response = game._dialogue_semantic_action_response(actor, "押入昭狱，割舌禁言。")

            self.assertIsNotNone(response)
            self.assertIn("口谕", str(response.get("answer") or ""))
            status, reason = game.db.get_character_status(actor)
            self.assertEqual(status, "imprisoned")
            self.assertIn("昭狱", reason)
            condition = game.db.conn.execute(
                "SELECT system, label FROM character_conditions WHERE name=?",
                (actor,),
            ).fetchone()
            self.assertIsNotNone(condition)
            self.assertEqual(str(condition["system"]), "speech")
            self.assertEqual(str(condition["label"]), "舌伤")
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_semantic_action_probe_denial_does_not_create_pending_action(self):
        os.environ["MING_SIM_ENABLE_DIALOGUE_REGEX_ACTIONS"] = "0"
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"

            def audit(phase, payload):
                if phase == "dialogue_action_intent":
                    return {
                        "allow": False,
                        "phase": "none",
                        "action_type": "none",
                        "trigger_quote": "",
                        "confidence": 99,
                        "private_reason": "只是问旧例，不办。",
                    }
                return None

            game.session.dialogue_audit_client = audit

            response = game._dialogue_semantic_action_response(attendant, "只是问问净身旧例，不是要办。")

            self.assertIsNone(response)
            self.assertEqual(game._load_pending_dialogue_action(attendant), {})
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_semantic_recruitment_probe_creates_pending_without_regex(self):
        os.environ["MING_SIM_ENABLE_DIALOGUE_REGEX_ACTIONS"] = "0"
        os.environ["MING_SIM_ENABLE_RECRUITMENT_REGEX_FALLBACK"] = "0"
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            before = game.db.conn.execute("SELECT COUNT(*) AS n FROM characters").fetchone()["n"]

            def audit(phase, payload):
                if phase == "recruitment_intent":
                    action = payload.get("tool_action") or {}
                    self.assertEqual(action.get("type"), "recruitment")
                    self.assertEqual(action.get("phase"), "propose")
                    self.assertFalse(action.get("kind"))
                    return {
                        "allow": True,
                        "phase": "propose",
                        "kind": "eunuch",
                        "requires_confirmation": True,
                        "trigger_quote": "宫里可有新的小内侍可用",
                        "public_hint": "玩家明确要找新的内廷小内侍。",
                        "private_reason": "test semantic recruitment probe",
                        "confidence": 96,
                    }
                return None

            game.session.dialogue_audit_client = audit

            response = game._dialogue_semantic_recruitment_response(attendant, "宫里可有新的小内侍可用？")

            self.assertIsNotNone(response)
            self.assertIn("陛下若准", response["answer"])
            pending = game._load_pending_dialogue_action(attendant)
            self.assertEqual(pending.get("type"), "recruitment")
            self.assertEqual(pending.get("kind"), "eunuch")
            self.assertIn("小内侍", str(pending.get("trigger_quote") or ""))
            after = game.db.conn.execute("SELECT COUNT(*) AS n FROM characters").fetchone()["n"]
            self.assertEqual(after, before)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_unified_semantic_probe_handles_recruitment_without_specialized_retry(self):
        os.environ["MING_SIM_ENABLE_DIALOGUE_REGEX_ACTIONS"] = "0"
        os.environ["MING_SIM_ENABLE_RECRUITMENT_REGEX_FALLBACK"] = "0"
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
        os.environ["MING_SIM_DISABLE_DIALOGUE_ROUTE_LLM_AUDIT"] = "1"
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            before = game.db.conn.execute("SELECT COUNT(*) AS n FROM characters").fetchone()["n"]
            calls = []

            def audit(phase, payload):
                calls.append(phase)
                if phase == "dialogue_action_intent":
                    self.assertEqual((payload.get("tool_action") or {}).get("type"), "semantic_probe")
                    return {
                        "allow": True,
                        "phase": "propose",
                        "action_type": "recruitment",
                        "kind": "eunuch",
                        "requires_confirmation": True,
                        "trigger_quote": "宫里可有新的小内侍可用",
                        "public_hint": "玩家明确要找新的内廷小内侍。",
                        "private_reason": "test unified semantic probe",
                        "confidence": 96,
                    }
                if phase == "recruitment_intent":
                    self.fail("unified semantic probe should not fall through to recruitment_intent")
                return None

            game.session.dialogue_audit_client = audit

            payload = game.chat(attendant, "宫里可有新的小内侍可用？")

            self.assertEqual(calls, ["dialogue_action_intent"])
            self.assertIn("陛下若准", payload["answer"])
            pending = game._load_pending_dialogue_action(attendant)
            self.assertEqual(pending.get("type"), "recruitment")
            self.assertEqual(pending.get("kind"), "eunuch")
            self.assertIn("小内侍", str(pending.get("trigger_quote") or ""))
            after = game.db.conn.execute("SELECT COUNT(*) AS n FROM characters").fetchone()["n"]
            self.assertEqual(after, before)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_semantic_recruitment_probe_denial_does_not_create_pending(self):
        os.environ["MING_SIM_ENABLE_DIALOGUE_REGEX_ACTIONS"] = "0"
        os.environ["MING_SIM_ENABLE_RECRUITMENT_REGEX_FALLBACK"] = "0"
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"

            def audit(phase, payload):
                if phase == "recruitment_intent":
                    return {
                        "allow": False,
                        "phase": "none",
                        "kind": "",
                        "requires_confirmation": True,
                        "trigger_quote": "",
                        "public_hint": "",
                        "private_reason": "只是盘点现有人手，不招新人。",
                        "confidence": 98,
                    }
                return None

            game.session.dialogue_audit_client = audit

            response = game._dialogue_semantic_recruitment_response(
                attendant,
                "宫里现有人手够不够？先查名册，不要招新人。",
            )

            self.assertIsNone(response)
            self.assertEqual(game._load_pending_dialogue_action(attendant), {})
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_semantic_pending_recovery_executes_castration_without_regex(self):
        os.environ["MING_SIM_ENABLE_DIALOGUE_REGEX_ACTIONS"] = "0"
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            row = game.db.conn.execute(
                "SELECT name, office, office_type FROM characters "
                "WHERE status='active' AND power_id='ming' "
                "AND office_type NOT IN ('后宫','司礼监','内官监御前') "
                "AND office NOT LIKE '%太监%' AND office NOT LIKE '%宦官%' "
                "ORDER BY ability DESC LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(row)
            target = str(row["name"])
            game.chat_history[attendant] = [{
                "role": "minister",
                "content": (
                    f"奴婢回陛下，{target}若真要净身入内廷，便是极端身份处置。"
                    "陛下若准，奴婢才敢传净身房行事。"
                ),
            }]

            def audit(phase, payload):
                if phase == "dialogue_pending_recovery":
                    self.assertIn("recent_proposals", payload)
                    return {
                        "allow": True,
                        "phase": "confirm",
                        "action_type": "castration",
                        "target": target,
                        "trigger_quote": "准，照这个方案办",
                        "proposal_evidence": "陛下若准，奴婢才敢传净身房行事",
                        "private_reason": "test semantic pending recovery",
                        "confidence": 96,
                    }
                return None

            game.session.dialogue_audit_client = audit

            response = game._dialogue_semantic_recovery_response(attendant, "准，照这个方案办。")

            self.assertIsNotNone(response)
            after = game.db.conn.execute(
                "SELECT office, office_type FROM characters WHERE name=?",
                (target,),
            ).fetchone()
            self.assertIsNotNone(after)
            self.assertTrue(is_eunuch_office(str(after["office"]), str(after["office_type"])))
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_semantic_pending_recovery_denial_does_not_execute(self):
        os.environ["MING_SIM_ENABLE_DIALOGUE_REGEX_ACTIONS"] = "0"
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            row = game.db.conn.execute(
                "SELECT name, office, office_type FROM characters "
                "WHERE status='active' AND power_id='ming' "
                "AND office_type NOT IN ('后宫','司礼监','内官监御前') "
                "AND office NOT LIKE '%太监%' AND office NOT LIKE '%宦官%' "
                "ORDER BY ability DESC LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(row)
            target = str(row["name"])
            game.chat_history[attendant] = [{
                "role": "minister",
                "content": "奴婢只把旧例说给陛下听，并未敢拟办。",
            }]

            def audit(phase, payload):
                if phase == "dialogue_pending_recovery":
                    return {
                        "allow": False,
                        "phase": "none",
                        "action_type": "none",
                        "trigger_quote": "",
                        "proposal_evidence": "",
                        "private_reason": "最近回复没有待确认方案。",
                        "confidence": 98,
                    }
                return None

            game.session.dialogue_audit_client = audit

            response = game._dialogue_semantic_recovery_response(attendant, "准。")

            self.assertIsNone(response)
            after = game.db.conn.execute(
                "SELECT office, office_type FROM characters WHERE name=?",
                (target,),
            ).fetchone()
            self.assertIsNotNone(after)
            self.assertEqual(str(after["office"]), str(row["office"]))
            self.assertEqual(str(after["office_type"]), str(row["office_type"]))
            self.assertFalse(is_eunuch_office(str(after["office"]), str(after["office_type"])))
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_semantic_pending_recovery_denial_blocks_legacy_regex_action_response(self):
        os.environ["MING_SIM_ENABLE_DIALOGUE_REGEX_ACTIONS"] = "1"
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            before = int(game.db.conn.execute("SELECT COUNT(*) AS n FROM characters").fetchone()["n"])
            game.chat_history[attendant] = [{
                "role": "minister",
                "content": "陛下若准，奴婢可从内书堂挑一个小火者来御前听用。",
            }]

            def audit(phase, payload):
                if phase == "dialogue_pending_recovery":
                    self.assertIn("recent_proposals", payload)
                    return {
                        "allow": False,
                        "phase": "none",
                        "action_type": "none",
                        "kind": "",
                        "trigger_quote": "准",
                        "proposal_evidence": "陛下若准",
                        "private_reason": "只是口头应声，不能恢复旧正则招募。",
                        "confidence": 96,
                    }
                return None

            game.session.dialogue_audit_client = audit

            response = game._dialogue_action_response(attendant, "准。")

            after = int(game.db.conn.execute("SELECT COUNT(*) AS n FROM characters").fetchone()["n"])
            self.assertIsNone(response)
            self.assertEqual(after, before)
            self.assertEqual(game._load_pending_dialogue_action(attendant), {})
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_pending_regex_recovery_ignores_generic_legacy_action_regex(self):
        os.environ["MING_SIM_ENABLE_DIALOGUE_REGEX_ACTIONS"] = "1"
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "1"
        os.environ.pop("MING_SIM_ENABLE_DIALOGUE_PENDING_REGEX_RECOVERY", None)
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            before = int(game.db.conn.execute("SELECT COUNT(*) AS n FROM characters").fetchone()["n"])
            game.chat_history[attendant] = [{
                "role": "minister",
                "content": "陛下若准，奴婢可从内书堂挑一个小火者来御前听用。",
            }]

            response = game._dialogue_action_response(attendant, "准。")

            after = int(game.db.conn.execute("SELECT COUNT(*) AS n FROM characters").fetchone()["n"])
            self.assertIsNone(response)
            self.assertEqual(after, before)
            self.assertEqual(game._load_pending_dialogue_action(attendant), {})
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_pending_regex_recovery_requires_dedicated_legacy_opt_in(self):
        os.environ["MING_SIM_ENABLE_DIALOGUE_REGEX_ACTIONS"] = "1"
        os.environ["MING_SIM_ENABLE_DIALOGUE_PENDING_REGEX_RECOVERY"] = "1"
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "1"
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            before = int(game.db.conn.execute("SELECT COUNT(*) AS n FROM characters").fetchone()["n"])
            game.chat_history[attendant] = [{
                "role": "minister",
                "content": "陛下若准，奴婢可从内书堂挑一个小火者来御前听用。",
            }]

            response = game._dialogue_action_response(attendant, "准。")

            after = int(game.db.conn.execute("SELECT COUNT(*) AS n FROM characters").fetchone()["n"])
            self.assertIsNotNone(response)
            self.assertEqual(after, before + 1)
            self.assertTrue(str(response.get("recruited_minister") or ""))
            self.assertEqual(game._load_pending_dialogue_action(attendant), {})
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_pending_regex_recovery_does_not_bypass_injected_semantic_audit_without_api_key(self):
        os.environ["MING_SIM_ENABLE_DIALOGUE_REGEX_ACTIONS"] = "1"
        os.environ["MING_SIM_ENABLE_DIALOGUE_PENDING_REGEX_RECOVERY"] = "1"
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            before = int(game.db.conn.execute("SELECT COUNT(*) AS n FROM characters").fetchone()["n"])
            game.session.llm_config.api_key = ""
            game.chat_history[attendant] = [{
                "role": "minister",
                "content": "陛下若准，奴婢可从内书堂挑一个小火者来御前听用。",
            }]

            calls = []

            def audit(phase, payload):
                calls.append(phase)
                if phase == "dialogue_pending_recovery":
                    self.assertIn("recent_proposals", payload)
                    return {
                        "allow": False,
                        "phase": "none",
                        "action_type": "none",
                        "kind": "",
                        "trigger_quote": "准",
                        "proposal_evidence": "陛下若准",
                        "private_reason": "语义审计已可用，不能回落到旧关键词恢复。",
                        "confidence": 96,
                    }
                return None

            game.session.dialogue_audit_client = audit

            response = game._dialogue_action_response(attendant, "准。")

            after = int(game.db.conn.execute("SELECT COUNT(*) AS n FROM characters").fetchone()["n"])
            self.assertIsNone(response)
            self.assertIn("dialogue_pending_recovery", calls)
            self.assertEqual(after, before)
            self.assertEqual(game._load_pending_dialogue_action(attendant), {})
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_semantic_action_builder_uses_decision_payload_boundary(self):
        game = web_app.WebGame(fresh=True)
        try:
            decision = SemanticDecision(
                decision_type="action",
                action_type="mediation",
                phase="propose",
                actor="韩爌",
                target="",
                mode="co_work",
                payload={"faction": "东林"},
                confidence=96,
                trigger_quote="调停东林旧怨",
                private_reason="test decision payload boundary",
                raw={"faction": "阉党"},
            )

            action = game._action_from_semantic_decision("韩爌", "朕要你调停东林旧怨。", decision)

            self.assertEqual(action.get("type"), "mediation")
            self.assertEqual(action.get("actor"), "韩爌")
            self.assertEqual(action.get("faction"), "东林")
            self.assertNotEqual(action.get("faction"), str(decision.raw.get("faction")))
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_semantic_route_summons_when_regex_summon_fallback_is_off(self):
        os.environ["MING_SIM_ENABLE_DIALOGUE_REGEX_SUMMONS"] = "0"
        os.environ["MING_SIM_DISABLE_DIALOGUE_ROUTE_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            target = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND name!=? "
                "ORDER BY ability DESC LIMIT 1",
                (attendant,),
            ).fetchone()["name"])

            def audit(phase, payload):
                if phase == "dialogue_route_intent":
                    return {
                        "allow": True,
                        "intent": "summon",
                        "target_name": target,
                        "trigger_quote": f"传{target}入殿",
                        "confidence": 96,
                        "private_reason": "test semantic summon route",
                    }
                return None

            game.session.dialogue_audit_client = audit

            events = list(game.chat_stream(attendant, f"传{target}入殿奏对。"))

            payload = events[-1]["payload"]
            self.assertEqual(payload["court_action"], "summon")
            self.assertEqual(payload["next_minister"], target)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_tool_summon_requires_route_semantic_audit(self):
        os.environ["MING_SIM_DISABLE_DIALOGUE_ROUTE_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            target = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND name!=? "
                "ORDER BY ability DESC LIMIT 1",
                (attendant,),
            ).fetchone()["name"])
            character = game.session._character(attendant)
            seen_payloads = []

            def deny_audit(phase, payload):
                if phase == "dialogue_route_intent":
                    seen_payloads.append(payload)
                    return {
                        "allow": False,
                        "intent": "none",
                        "confidence": 95,
                        "private_reason": "tool call alone is not proof",
                    }
                return None

            game.session.dialogue_audit_client = deny_audit
            self.assertFalse(game.session.dialogue_route_allows_tool_summon(
                character,
                "朕只是问问朝中还有谁可用。",
                target,
                answer=f"臣这就传{target}入殿。",
            ))
            self.assertEqual(seen_payloads[-1]["route_context"]["tool_requested_summon_target"], target)

            def allow_audit(phase, payload):
                if phase == "dialogue_route_intent":
                    return {
                        "allow": True,
                        "intent": "summon",
                        "target_name": target,
                        "trigger_quote": f"传{target}入殿",
                        "confidence": 96,
                        "private_reason": "explicit summon in player text",
                    }
                return None

            game.session.dialogue_audit_client = allow_audit
            self.assertTrue(game.session.dialogue_route_allows_tool_summon(
                character,
                f"传{target}入殿奏对。",
                target,
                answer=f"臣遵旨，传{target}入殿。",
            ))
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_unlisted_registration_without_summon_requires_unknown_mention_semantic_audit(self):
        os.environ["MING_SIM_DISABLE_DIALOGUE_MENTION_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            target = "顾补档"
            self.assertNotIn(target, game.content.characters)
            character = game.session._character(attendant)
            payload = json.dumps(
                {
                    "name": target,
                    "office": "御前候补小内侍",
                    "office_type": "司礼监",
                    "faction": "中立",
                    "aliases": [],
                    "summary": "测试用补档人物",
                    "source": "user_confirmed",
                    "summon_after": False,
                },
                ensure_ascii=False,
            )
            seen_payloads = []

            def deny_audit(phase, audit_payload):
                if phase == "dialogue_unknown_mention_intake":
                    seen_payloads.append(audit_payload)
                    return {
                        "allow": False,
                        "accepted_names": [],
                        "rejected_names": [target],
                        "trigger_quote": "只是问旧例",
                        "confidence": 96,
                        "private_reason": "玩家没有确认或要求把此人补入名册。",
                    }
                return None

            game.session.dialogue_audit_client = deny_audit
            registered, summon_after = game.session._apply_unlisted_person_registration_after_route_audit(
                payload,
                character,
                "朕只是问旧例，不必添人。",
                answer=f"臣记得旧案里有个{target}。",
            )
            self.assertEqual((registered, summon_after), ("", False))
            self.assertNotIn(target, game.content.characters)
            self.assertEqual(seen_payloads[-1]["purpose"], "register_unlisted_person")

            def allow_audit(phase, audit_payload):
                if phase == "dialogue_unknown_mention_intake":
                    self.assertEqual(audit_payload.get("purpose"), "register_unlisted_person")
                    self.assertIn(target, audit_payload.get("candidate_names") or [])
                    return {
                        "allow": True,
                        "accepted_names": [target],
                        "rejected_names": [],
                        "trigger_quote": f"{target}可补入名册备查",
                        "confidence": 95,
                        "private_reason": "NPC 明确介绍且玩家允许补入可查名册。",
                    }
                return None

            game.session.dialogue_audit_client = allow_audit
            registered, summon_after = game.session._apply_unlisted_person_registration_after_route_audit(
                payload,
                character,
                f"{target}可补入名册备查，暂不召见。",
                answer=f"臣遵旨，先将{target}留名备查。",
            )

            self.assertEqual(registered, target)
            self.assertFalse(summon_after)
            self.assertIn(target, game.content.characters)
            row = game.db.conn.execute(
                "SELECT office, office_type, status_reason FROM characters WHERE name=?",
                (target,),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["office"], "御前候补小内侍")
            self.assertEqual(row["status_reason"], "皇帝确认背景补档")
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_unlisted_registration_summon_requires_route_semantic_audit(self):
        os.environ["MING_SIM_DISABLE_DIALOGUE_MENTION_LLM_AUDIT"] = "0"
        os.environ["MING_SIM_DISABLE_DIALOGUE_ROUTE_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            target = "顾语义"
            self.assertNotIn(target, game.content.characters)
            character = game.session._character(attendant)
            payload = json.dumps(
                {
                    "name": target,
                    "office": "御前候补小内侍",
                    "office_type": "司礼监",
                    "faction": "中立",
                    "aliases": [],
                    "summary": "测试用补档人物",
                    "source": "user_confirmed",
                    "summon_after": True,
                },
                ensure_ascii=False,
            )
            seen_payloads = []

            def deny_audit(phase, audit_payload):
                if phase == "dialogue_unknown_mention_intake":
                    self.assertEqual(audit_payload.get("purpose"), "register_unlisted_person")
                    return {
                        "allow": True,
                        "accepted_names": [target],
                        "rejected_names": [],
                        "trigger_quote": f"{target}可留名备查",
                        "confidence": 96,
                        "private_reason": "先允许补档语义，专门测试 route 拦截。",
                    }
                if phase == "dialogue_route_intent":
                    seen_payloads.append(audit_payload)
                    return {
                        "allow": False,
                        "intent": "none",
                        "confidence": 95,
                        "private_reason": "player asked for information, not an audience switch",
                    }
                return None

            game.session.dialogue_audit_client = deny_audit
            registered, summon_after = game.session._apply_unlisted_person_registration_after_route_audit(
                payload,
                character,
                "朕只是问问宫里还有哪些人可用。",
                answer=f"臣以为{target}可留名备查。",
            )
            self.assertEqual((registered, summon_after), ("", False))
            self.assertNotIn(target, game.content.characters)
            self.assertEqual(
                seen_payloads[-1]["route_context"]["tool_requested_summon_target"],
                target,
            )

            def allow_audit(phase, audit_payload):
                if phase == "dialogue_unknown_mention_intake":
                    self.assertEqual(audit_payload.get("purpose"), "register_unlisted_person")
                    return {
                        "allow": True,
                        "accepted_names": [target],
                        "rejected_names": [],
                        "trigger_quote": f"{target}可补入名册并入殿",
                        "confidence": 96,
                        "private_reason": "玩家明确点名补档且召见。",
                    }
                if phase == "dialogue_route_intent":
                    return {
                        "allow": True,
                        "intent": "summon",
                        "target_name": target,
                        "trigger_quote": f"传{target}入殿",
                        "confidence": 96,
                        "private_reason": "player explicitly requested the named person enter audience",
                    }
                return None

            game.session.dialogue_audit_client = allow_audit
            registered, summon_after = game.session._apply_unlisted_person_registration_after_route_audit(
                payload,
                character,
                f"传{target}入殿奏对。",
                answer=f"臣遵旨，传{target}入殿。",
            )

            self.assertEqual(registered, target)
            self.assertTrue(summon_after)
            self.assertIn(target, game.content.characters)
            row = game.db.conn.execute(
                "SELECT office, office_type, status_reason FROM characters WHERE name=?",
                (target,),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["office"], "御前候补小内侍")
            self.assertEqual(row["status_reason"], "皇帝确认背景补档")
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_unlisted_registration_uses_semantic_profile_and_route_boundary(self):
        os.environ["MING_SIM_DISABLE_DIALOGUE_MENTION_LLM_AUDIT"] = "0"
        os.environ["MING_SIM_DISABLE_DIALOGUE_ROUTE_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            target = "顾补正"
            self.assertNotIn(target, game.content.characters)
            character = game.session._character(attendant)
            payload = json.dumps(
                {
                    "name": target,
                    "office": "锦衣卫错档掌案",
                    "office_type": "锦衣卫",
                    "faction": "阉党",
                    "aliases": ["错号"],
                    "summary": "工具夹带了不该入档的身份摘要。",
                    "source": "historical",
                    "summon_after": False,
                },
                ensure_ascii=False,
            )

            def audit(phase, audit_payload):
                if phase == "dialogue_unknown_mention_intake":
                    self.assertEqual(audit_payload.get("purpose"), "register_unlisted_person")
                    self.assertIn(target, audit_payload.get("candidate_names") or [])
                    return {
                        "allow": True,
                        "accepted_names": [target],
                        "accepted_profiles": {
                            target: {
                                "office": "御前候补小内侍",
                                "office_type": "司礼监",
                                "faction": "中立",
                                "aliases": ["小正"],
                                "summary": "审计确认：随王承恩在御前候旨的小内侍。",
                                "source": "user_confirmed",
                            }
                        },
                        "rejected_names": [],
                        "trigger_quote": f"传{target}入殿",
                        "confidence": 96,
                        "private_reason": "玩家明确补档并传入殿，身份以审计 profile 为准。",
                    }
                if phase == "dialogue_route_intent":
                    self.assertEqual(
                        audit_payload["route_context"]["tool_requested_summon_target"],
                        target,
                    )
                    return {
                        "allow": True,
                        "intent": "summon",
                        "target_name": target,
                        "trigger_quote": f"传{target}入殿",
                        "confidence": 96,
                        "private_reason": "玩家明确传此人入殿。",
                    }
                return None

            game.session.dialogue_audit_client = audit
            registered, summon_after = game.session._apply_unlisted_person_registration_after_route_audit(
                payload,
                character,
                f"{target}补入名册，传{target}入殿奏对。",
                answer=f"臣遵旨，传{target}入殿。",
            )

            self.assertEqual(registered, target)
            self.assertTrue(summon_after)
            self.assertIn(target, game.content.characters)
            row = game.db.conn.execute(
                "SELECT office, office_type, faction, aliases, summary, status_reason FROM characters WHERE name=?",
                (target,),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["office"], "御前候补小内侍")
            self.assertEqual(row["office_type"], "司礼监")
            self.assertEqual(row["faction"], "中立")
            self.assertIn("小正", str(row["aliases"] or ""))
            self.assertIn("审计确认", row["summary"])
            self.assertEqual(row["status_reason"], "皇帝确认背景补档")
            self.assertNotIn("错档", row["office"] + row["summary"] + str(row["aliases"] or ""))
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_semantic_route_confirms_pending_action_when_regex_actions_are_off(self):
        os.environ["MING_SIM_ENABLE_DIALOGUE_REGEX_ACTIONS"] = "0"
        os.environ["MING_SIM_DISABLE_DIALOGUE_ROUTE_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            actor = "韩爌"
            target = "魏忠贤"
            court.adjust_opinion(game.db, actor, target, -60, "测试旧怨", day=1, reciprocal=True)
            before = court.get_opinion(game.db, actor, target)
            game._store_pending_dialogue_action(actor, {
                "type": "mediation",
                "actor": actor,
                "target": target,
                "mode": "co_work",
            })

            def audit(phase, payload):
                if phase == "dialogue_route_intent":
                    return {
                        "allow": True,
                        "intent": "confirm_pending",
                        "action_type": "mediation",
                        "trigger_quote": "准，去调停",
                        "confidence": 95,
                        "private_reason": "test semantic pending confirmation",
                    }
                if phase == "dialogue_action_intent":
                    action = payload.get("tool_action") or {}
                    pending = payload.get("pending_action") or {}
                    self.assertEqual(action.get("type"), "mediation")
                    self.assertEqual(action.get("phase"), "confirm")
                    self.assertEqual(pending.get("type"), "mediation")
                    return {
                        "allow": True,
                        "phase": "confirm",
                        "action_type": "mediation",
                        "actor": actor,
                        "target": target,
                        "mode": "co_work",
                        "trigger_quote": "准，去调停",
                        "confidence": 95,
                        "private_reason": "test action semantic pending confirmation",
                    }
                return None

            game.session.dialogue_audit_client = audit

            decision = game._dialogue_route_semantic_decision(actor, "准，去调停。")
            self.assertIsInstance(decision, SemanticDecision)
            self.assertTrue(decision.allow)
            self.assertEqual(decision.decision_type, "route")
            self.assertEqual(decision.action_type, "confirm_pending")
            self.assertEqual(decision.phase, "confirm")
            self.assertEqual(decision.payload.get("pending_action_type"), "mediation")

            response = game._dialogue_route_response_from_decision(actor, "准，去调停。", decision)

            self.assertIsNotNone(response)
            self.assertIn("dialogue_effect", response)
            self.assertGreater(court.get_opinion(game.db, actor, target), before)
            self.assertEqual(game._load_pending_dialogue_action(actor), {})
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_semantic_route_pending_confirmation_requires_action_gate(self):
        os.environ["MING_SIM_ENABLE_DIALOGUE_REGEX_ACTIONS"] = "0"
        os.environ["MING_SIM_DISABLE_DIALOGUE_ROUTE_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            actor = "韩爌"
            target = "魏忠贤"
            court.adjust_opinion(game.db, actor, target, -60, "测试旧怨", day=1, reciprocal=True)
            before = court.get_opinion(game.db, actor, target)
            game._store_pending_dialogue_action(actor, {
                "type": "mediation",
                "actor": actor,
                "target": target,
                "mode": "co_work",
            })
            calls = []

            def audit(phase, payload):
                calls.append(phase)
                if phase == "dialogue_route_intent":
                    return {
                        "allow": True,
                        "intent": "confirm_pending",
                        "action_type": "mediation",
                        "trigger_quote": "准，去调停",
                        "confidence": 95,
                        "private_reason": "route sees a pending confirmation",
                    }
                if phase == "dialogue_action_intent":
                    action = payload.get("tool_action") or {}
                    self.assertEqual(action.get("type"), "mediation")
                    self.assertEqual(action.get("phase"), "confirm")
                    self.assertEqual((payload.get("pending_action") or {}).get("target"), target)
                    return {
                        "allow": False,
                        "phase": "none",
                        "action_type": "mediation",
                        "trigger_quote": "准，去调停",
                        "confidence": 95,
                        "private_reason": "action gate denies stale pending execution",
                    }
                return None

            game.session.dialogue_audit_client = audit

            decision = game._dialogue_route_semantic_decision(actor, "准，去调停。")
            self.assertTrue(decision.allow)

            response = game._dialogue_route_response_from_decision(actor, "准，去调停。", decision)

            self.assertIsNone(response)
            self.assertEqual(court.get_opinion(game.db, actor, target), before)
            self.assertEqual(game._load_pending_dialogue_action(actor).get("type"), "mediation")
            self.assertEqual(calls, ["dialogue_route_intent", "dialogue_action_intent"])
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_castration_confirm_tool_requires_pending_action(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            row = game.db.conn.execute(
                "SELECT name, office, office_type FROM characters "
                "WHERE status='active' AND power_id='ming' "
                "AND office_type NOT IN ('后宫','司礼监','内官监御前') "
                "AND office NOT LIKE '%太监%' AND office NOT LIKE '%宦官%' "
                "ORDER BY ability DESC LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(row)
            name = str(row["name"])
            response = game._dialogue_tool_response(
                attendant,
                {
                    "type": "castration",
                    "phase": "confirm",
                    "target": name,
                    "scheme_text": f"把{name}净身入内廷。",
                    "force": True,
                },
                "奴婢这就去办。",
                "准，照办。",
            )
            self.assertIsNone(response)
            after = game.db.conn.execute(
                "SELECT office, office_type FROM characters WHERE name=?",
                (name,),
            ).fetchone()
            self.assertIsNotNone(after)
            self.assertEqual(str(after["office"]), str(row["office"]))
            self.assertEqual(str(after["office_type"]), str(row["office_type"]))
            self.assertFalse(is_eunuch_office(str(after["office"]), str(after["office_type"])))
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_dialogue_lore_maintenance_surfaces_as_chat_effect(self):
        from ming_sim import eunuch_lore as el

        game = web_app.WebGame(fresh=True)
        try:
            name = "韩爌"
            el.record_castration(game.db, name, forced=True, day=0)
            game.session.dialogue_audit_client = self._lore_intake_audit(targets=[name])

            events = list(game.chat_stream(
                name,
                "以后韩爌的宝用黑漆楠木匣，油炸封蜡，约二两八钱，一大一小；"
                "近来漏尿尿闭，嗓音尖薄，幻肢痛，钥匙贴身。请先记入旧档。",
            ))

            payload = events[-1]["payload"]
            effect = payload["dialogue_effect"]
            self.assertEqual(effect["title"], "内廷旧档补记")
            self.assertIn("韩爌旧档更新", effect["message"])
            labels = {str(item["label"]) for item in effect["effects"]}
            self.assertFalse(any("匣藏" in label or "封存：油炸封蜡" in label for label in labels))
            self.assertTrue(any("尿路：" in label and "尿闭" in label for label in labels))
            self.assertFalse(any("新增特质" in label and "尿路旧患" in label for label in labels))
            self.assertTrue(payload["history"][-1].get("stage_directions"))
            stage_text = " ".join(payload["history"][-1]["stage_directions"])
            self.assertNotIn("钥匙", stage_text)

            castration = el.public_lore_payload(game.db, name)
            self.assertIsNotNone(castration)
            self.assertEqual(castration["container_label"], "黑漆楠木匣")
            self.assertEqual(castration["preservation_label"], "油炸封蜡")
            self.assertIn("漏尿", castration["urine_label"])
            self.assertIn("嗓音尖薄", castration["voice_body_label"])
            self.assertIn("幻肢痛", castration["trauma_label"])
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_dialogue_eunuch_care_requires_confirmation_and_rolls_back(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            row = game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' "
                "AND office_type NOT IN ('后宫','司礼监','内官监御前') "
                "AND office NOT LIKE '%太监%' AND office NOT LIKE '%宦官%' "
                "LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(row)
            name = str(row["name"])
            game.castrate_official(
                name,
                force=True,
                scheme_text="净身房无麻，宝油炸封蜡；近来漏尿尿闭，嗓音尖薄，幻肢痛。",
            )
            game.session.dialogue_audit_client = self._dialogue_action_audit(
                action_type="eunuch_care",
                target=name,
                mode="urinary",
            )
            game.state.metrics["内库"] = 80
            game.db.save_state(game.state)
            game.db.conn.execute(
                "UPDATE characters SET emp_trust=50, grievance=40, ability=55, charm=54 WHERE name=?",
                (name,),
            )
            game.db.conn.commit()
            before_ledger_count = int(game.db.conn.execute("SELECT COUNT(*) c FROM economy_ledger").fetchone()["c"])

            proposal_events = list(game.chat_stream(attendant, f"给{name}请太医治一治尿闭漏尿旧患。"))
            self.assertEqual(proposal_events[-1]["type"], "done")
            self.assertIn("若准", proposal_events[-1]["payload"]["answer"])
            pending = game._load_pending_dialogue_action(attendant)
            self.assertEqual(pending.get("type"), "eunuch_care")
            self.assertEqual(pending.get("target"), name)
            self.assertEqual(pending.get("mode"), "urinary")
            mid_row = game.db.conn.execute(
                "SELECT emp_trust, grievance, ability, charm FROM characters WHERE name=?",
                (name,),
            ).fetchone()
            self.assertEqual(int(mid_row["grievance"]), 40)
            self.assertEqual(game.state.metrics["内库"], 80)

            confirm_events = list(game.chat_stream(attendant, "准，去请太医调养。"))
            self.assertEqual(confirm_events[-1]["type"], "done")
            payload = confirm_events[-1]["payload"]
            self.assertEqual(payload["dialogue_effect"]["title"], "尿路调养")
            self.assertIn("内库-", payload["dialogue_effect"]["message"])
            self.assertIn("stage_direction", payload["dialogue_effect"])
            self.assertTrue(payload["history"][-1].get("stage_directions"))
            self.assertIn("夹腰", " ".join(payload["history"][-1]["stage_directions"]))
            game._restore_chat_history_cache()
            reloaded_history = game._chat_history_payload(attendant)
            self.assertTrue(reloaded_history[-1].get("stage_directions"))
            self.assertIn("夹腰", " ".join(reloaded_history[-1]["stage_directions"]))
            after_row = game.db.conn.execute(
                "SELECT emp_trust, grievance, ability, charm FROM characters WHERE name=?",
                (name,),
            ).fetchone()
            self.assertEqual(int(after_row["emp_trust"]), 52)
            self.assertEqual(int(after_row["grievance"]), 34)
            self.assertEqual(int(after_row["ability"]), 56)
            self.assertEqual(int(after_row["charm"]), 55)
            self.assertLess(game.state.metrics["内库"], 80)
            self.assertIsNotNone(game.db.conn.execute(
                "SELECT 1 FROM character_traits WHERE name=? AND trait='旧患调养'",
                (name,),
            ).fetchone())
            self.assertIsNotNone(game.db.conn.execute(
                "SELECT 1 FROM event_memories WHERE subject_id=? AND event_type='eunuch_care'",
                (name,),
            ).fetchone())

            game.undo_last_chat(attendant)
            restored_row = game.db.conn.execute(
                "SELECT emp_trust, grievance, ability, charm FROM characters WHERE name=?",
                (name,),
            ).fetchone()
            self.assertEqual(int(restored_row["emp_trust"]), 50)
            self.assertEqual(int(restored_row["grievance"]), 40)
            self.assertEqual(int(restored_row["ability"]), 55)
            self.assertEqual(int(restored_row["charm"]), 54)
            self.assertEqual(game.state.metrics["内库"], 80)
            restored_ledger_count = int(game.db.conn.execute("SELECT COUNT(*) c FROM economy_ledger").fetchone()["c"])
            self.assertEqual(restored_ledger_count, before_ledger_count)
            self.assertIsNone(game.db.conn.execute(
                "SELECT 1 FROM character_traits WHERE name=? AND trait='旧患调养'",
                (name,),
            ).fetchone())
            self.assertIsNone(game.db.conn.execute(
                "SELECT 1 FROM event_memories WHERE subject_id=? AND event_type='eunuch_care'",
                (name,),
            ).fetchone())
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_quick_suggestions_do_not_fallback_to_local_buttons_when_llm_empty(self):
        os.environ["MING_SIM_DISABLE_LLM_QUICK_SUGGESTIONS"] = "0"
        os.environ.pop("MING_SIM_ENABLE_LOCAL_QUICK_SUGGESTIONS", None)
        game = web_app.WebGame(fresh=True)
        try:
            actor = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                "ORDER BY ability DESC LIMIT 1"
            ).fetchone()["name"])

            def audit(phase, payload):
                if phase == "dialogue_suggestions":
                    return {"suggestions": []}
                return None

            game.session.dialogue_audit_client = audit
            suggestions = game.suggestions_for(game.session._character(actor))

            self.assertEqual(suggestions, [])
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_missing_llm_key_does_not_enable_local_quick_buttons(self):
        os.environ["MING_SIM_DISABLE_LLM_QUICK_SUGGESTIONS"] = "0"
        os.environ.pop("MING_SIM_ENABLE_LOCAL_QUICK_SUGGESTIONS", None)
        game = web_app.WebGame(fresh=True)
        try:
            game.session.llm_config.api_key = ""
            actor = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                "ORDER BY ability DESC LIMIT 1"
            ).fetchone()["name"])

            suggestions = game.suggestions_for(game.session._character(actor))

            self.assertEqual(suggestions, [])
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_disabling_llm_quick_suggestions_does_not_enable_local_buttons(self):
        os.environ["MING_SIM_DISABLE_LLM_QUICK_SUGGESTIONS"] = "1"
        os.environ.pop("MING_SIM_ENABLE_LOCAL_QUICK_SUGGESTIONS", None)
        game = web_app.WebGame(fresh=True)
        try:
            actor = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                "ORDER BY ability DESC LIMIT 1"
            ).fetchone()["name"])

            suggestions = game.suggestions_for(game.session._character(actor))

            self.assertEqual(suggestions, [])
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_eunuch_old_wound_goal_surfaces_decision_suggestions(self):
        os.environ["MING_SIM_ENABLE_LOCAL_QUICK_SUGGESTIONS"] = "1"
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            goal_id = game.db.create_conversation_goal(
                game.state,
                minister_name=attendant,
                action_kind="eunuch_care",
                title=f"尿路调养求助：{attendant}",
                target_text=f"{attendant}因内廷旧疾主动候见，须让皇帝裁断调养、查验或照常派差。",
                threshold=70,
                score=35,
                status="waiting_conditions",
                condition_status="pending",
                conditions=[
                    {"description": f"召见{attendant}亲口说明尿路旧患。", "status": "pending"},
                    {"description": "裁断调养、查验安置或照常派差。", "status": "pending"},
                ],
                expires_turn=int(game.state.turn) + 2,
                last_delta={
                    "source": "eunuch_complication",
                    "complication": "urinary",
                    "court_decision": {"action": "eunuch_care", "mode": "urinary"},
                },
            )

            suggestions = game.suggestions_for(game.content.characters[attendant])

            self.assertTrue(goal_id)
            labels = [str(item["label"]) for item in suggestions]
            texts = " ".join(str(item["text"]) for item in suggestions)
            self.assertIn("问隐情", labels)
            self.assertIn("问调养", labels)
            self.assertIn("问误事", labels)
            self.assertNotIn("追旧约", labels)
            self.assertIn("奴婢本分", texts)
            self.assertIn("内库调养", texts)
            self.assertIn("误事", texts)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_dialogue_hard_service_requires_confirmation_and_fulfills_old_wound_goal(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            row = game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' "
                "AND office_type NOT IN ('后宫','司礼监','内官监御前') "
                "AND office NOT LIKE '%太监%' AND office NOT LIKE '%宦官%' "
                "LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(row)
            name = str(row["name"])
            game.castrate_official(
                name,
                force=True,
                scheme_text="净身房无麻；近来漏尿尿闭，夜里小解不畅。",
            )
            game.session.dialogue_audit_client = self._dialogue_action_audit(
                action_type="eunuch_hard_service",
                target=name,
                mode="urinary",
            )
            game.db.conn.execute(
                "UPDATE characters SET emp_trust=50, grievance=30, ability=55, charm=54, luck=50 WHERE name=?",
                (name,),
            )
            goal_id = game.db.create_conversation_goal(
                game.state,
                minister_name=name,
                action_kind="eunuch_care",
                title=f"尿路调养求助：{name}",
                target_text=f"{name}因内廷旧疾主动候见。",
                threshold=70,
                score=35,
                status="waiting_conditions",
                condition_status="pending",
                conditions=[{"description": "裁断调养或照常派差。", "status": "pending"}],
                expires_turn=int(game.state.turn) + 2,
                last_delta={"source": "eunuch_complication", "complication": "urinary"},
            )
            game.db.conn.commit()

            proposal_events = list(game.chat_stream(attendant, f"{name}漏尿尿闭旧患不用调养，照常派差。"))
            self.assertEqual(proposal_events[-1]["type"], "done")
            self.assertIn("若仍准", proposal_events[-1]["payload"]["answer"])
            pending = game._load_pending_dialogue_action(attendant)
            self.assertEqual(pending.get("type"), "eunuch_hard_service")
            self.assertEqual(pending.get("target"), name)
            self.assertEqual(pending.get("mode"), "urinary")
            mid_row = game.db.conn.execute(
                "SELECT grievance, ability FROM characters WHERE name=?",
                (name,),
            ).fetchone()
            self.assertEqual(int(mid_row["grievance"]), 30)
            self.assertEqual(int(mid_row["ability"]), 55)

            confirm_events = list(game.chat_stream(attendant, "准，照旧硬派。"))

            payload = confirm_events[-1]["payload"]
            self.assertEqual(payload["dialogue_effect"]["title"], "带患当差")
            self.assertTrue(payload["history"][-1].get("stage_directions"))
            self.assertIn("夹腰", " ".join(payload["history"][-1]["stage_directions"]))
            self.assertIn("旧患硬派", str(payload["dialogue_effect"]["effects"]))
            after_row = game.db.conn.execute(
                "SELECT grievance, ability FROM characters WHERE name=?",
                (name,),
            ).fetchone()
            self.assertGreater(int(after_row["grievance"]), 30)
            self.assertLess(int(after_row["ability"]), 55)
            self.assertIsNotNone(game.db.conn.execute(
                "SELECT 1 FROM character_traits WHERE name=? AND trait='旧患硬派'",
                (name,),
            ).fetchone())
            self.assertIsNotNone(game.db.conn.execute(
                "SELECT 1 FROM event_memories WHERE subject_id=? AND event_type='eunuch_hard_service'",
                (name,),
            ).fetchone())
            fulfilled = game.db.get_conversation_goal(goal_id)
            self.assertEqual(fulfilled["status"], "fulfilled")
            self.assertEqual(fulfilled["last_delta"]["source"], "eunuch_hard_service")
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_dialogue_bao_care_merges_confirmation_scheme_into_lore(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            row = game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' "
                "AND office_type NOT IN ('后宫','司礼监','内官监御前') "
                "AND office NOT LIKE '%太监%' AND office NOT LIKE '%宦官%' "
                "LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(row)
            name = str(row["name"])
            game.castrate_official(
                name,
                force=True,
                scheme_text="奉旨宫刑，宝官库石灰封存，收白签灰瓮；暗记官库封签，终身惦念。",
            )
            game.session.dialogue_audit_client = self._dialogue_action_audit(
                action_type="eunuch_care",
                target=name,
                mode="bao",
            )
            game.state.metrics["内库"] = 80
            game.db.save_state(game.state)

            proposal_events = list(game.chat_stream(attendant, f"替{name}查验宝匣封签。"))
            self.assertEqual(proposal_events[-1]["type"], "done")
            pending = game._load_pending_dialogue_action(attendant)
            self.assertEqual(pending.get("type"), "eunuch_care")
            self.assertEqual(pending.get("mode"), "bao")

            confirm_events = list(game.chat_stream(
                attendant,
                "准，改用锡胆小木匣，香料腌藏，钥匙贴身，补录宝案。",
            ))

            payload = confirm_events[-1]["payload"]
            self.assertIn(payload["dialogue_effect"]["title"], {"宝贝旧念查验", "奏对有动"})
            labels = {str(item.get("label") or "") for item in payload["dialogue_effect"].get("effects", [])}
            self.assertFalse(any("匣藏" in label or "香料腌藏" in label or "钥匙" in label for label in labels))
            self.assertTrue(any("入库：宝案安置" in label or "入库：宝贝旧念安置" in label for label in labels))

            castration = game._public_castration_payload(name)
            self.assertIsNotNone(castration)
            self.assertEqual(castration["container_label"], "锡胆小木匣")
            self.assertEqual(castration["preservation_label"], "香料腌藏")
            self.assertEqual(castration["ritual_label"], "夜半验看，封签贴身")
            inventory_ids = {str(item["id"]) for item in game.db.list_player_inventory()}
            self.assertIn(f"宝案安置：{name}", inventory_ids)
            self.assertFalse(game._load_pending_dialogue_action(attendant))
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_dialogue_bao_leverage_return_requires_confirmation_and_executes(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            row = game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' "
                "AND office_type NOT IN ('后宫','司礼监','内官监御前') "
                "AND office NOT LIKE '%太监%' AND office NOT LIKE '%宦官%' "
                "LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(row)
            name = str(row["name"])
            game.castrate_official(
                name,
                force=True,
                scheme_text="奉旨宫刑，宝官库石灰封存，收白签灰瓮；暗记官库封签，终身惦念。",
            )
            game.session.dialogue_audit_client = self._dialogue_action_audit(
                action_type="bao_leverage",
                target=name,
                mode="return",
            )
            game.db.conn.execute(
                "UPDATE characters SET emp_trust=40, grievance=50, wisdom=55, luck=50 WHERE name=?",
                (name,),
            )
            game.db.conn.commit()

            proposal_events = list(game.chat_stream(attendant, f"把{name}的宝匣赐还给他，钥匙给他自己收。"))

            self.assertEqual(proposal_events[-1]["type"], "done")
            proposal = proposal_events[-1]["payload"]
            self.assertIn("若准", proposal["answer"])
            pending = game._load_pending_dialogue_action(attendant)
            self.assertEqual(pending.get("type"), "bao_leverage")
            self.assertEqual(pending.get("target"), name)
            self.assertEqual(pending.get("mode"), "return")
            mid = game.db.conn.execute("SELECT emp_trust, grievance FROM characters WHERE name=?", (name,)).fetchone()
            self.assertEqual(int(mid["emp_trust"]), 40)
            self.assertEqual(int(mid["grievance"]), 50)

            confirm_events = list(game.chat_stream(
                attendant,
                "准，改用锡胆小木匣，香料腌藏，钥匙贴身。",
            ))

            payload = confirm_events[-1]["payload"]
            self.assertEqual(payload["dialogue_effect"]["title"], "赐还宝贝")
            self.assertIn("信任+", payload["dialogue_effect"]["message"])
            self.assertIn("怨望-", payload["dialogue_effect"]["message"])
            self.assertIn("筹码值", payload["dialogue_effect"]["message"])
            labels = {str(item.get("label") or "") for item in payload["dialogue_effect"].get("effects", [])}
            self.assertTrue(any("御赐宝贝" in label or "宝贝安顿" in label for label in labels))
            self.assertTrue(any("旧念筹码" in label for label in labels))
            self.assertTrue(any("体面安置越足" in label for label in labels))
            self.assertFalse(any("宝匣" in label or "钥匙" in label or "锡胆小木匣" in label for label in labels))
            castration = game._public_castration_payload(name)
            self.assertIsNotNone(castration)
            self.assertEqual(castration["bao_status"], "kept")
            self.assertEqual(castration["container_label"], "锡胆小木匣")
            self.assertEqual(castration["preservation_label"], "香料腌藏")
            self.assertIn("赐还", castration["ritual_label"])
            inventory_ids = {str(item["id"]) for item in game.db.list_player_inventory()}
            self.assertIn(f"御赐宝贝：{name}", inventory_ids)
            self.assertFalse(game._load_pending_dialogue_action(attendant))
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_stage_directions_are_split_from_minister_chat_payload(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            game.chat_history[attendant] = [
                {
                    "role": "minister",
                    "content": (
                        "（躬身一礼，低声）\n\n"
                        "【动作】夹腰退半步，摸了摸袖中钥匙。\n"
                        "*嗓音一尖，肩背微缩*\n"
                        "——传内书堂生徒小禄子觐见。\n\n"
                        "奴婢回陛下，小禄子在殿外候着。"
                    ),
                }
            ]

            payload = game._chat_history_payload(attendant)

            self.assertIn("stage_directions", payload[0])
            self.assertIn("躬身一礼", payload[0]["stage_directions"][0])
            self.assertTrue(any("夹腰退半步" in line for line in payload[0]["stage_directions"]))
            self.assertTrue(any("肩背微缩" in line for line in payload[0]["stage_directions"]))
            self.assertTrue(any("小禄子觐见" in line for line in payload[0]["stage_directions"]))
            self.assertEqual(payload[0]["content"], "奴婢回陛下，小禄子在殿外候着。")
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_chat_payload_persists_clean_dialogue_and_keeps_stage_cues(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            payload = game._chat_payload(
                attendant,
                "（躬身一礼，低声）\n\n"
                "——传内书堂生徒小禄子觐见。\n\n"
                "奴婢回陛下，小禄子在殿外候着。",
            )

            last = payload["history"][-1]
            self.assertEqual(last["content"], "奴婢回陛下，小禄子在殿外候着。")
            self.assertTrue(any("躬身一礼" in line for line in last["stage_directions"]))
            self.assertTrue(any("小禄子觐见" in line for line in last["stage_directions"]))
            stored = game.db.conn.execute(
                "SELECT content, stage_directions FROM chat_messages WHERE minister_name=? AND role='minister' ORDER BY id DESC LIMIT 1",
                (attendant,),
            ).fetchone()
            self.assertEqual(str(stored["content"]), "奴婢回陛下，小禄子在殿外候着。")
            self.assertIn("小禄子觐见", str(stored["stage_directions"]))

            game._restore_chat_history_cache()
            self.assertEqual(game._recent_attendant_implied_summon_name(attendant), "小禄子")
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_eunuch_chat_payload_adds_profile_stage_direction_without_polluting_bubble(self):
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            payload = game._chat_payload(attendant, "奴婢晓得。小的先去值房问问，不敢替外朝乱断。")

            last = payload["history"][-1]
            self.assertEqual(last["content"], "奴婢晓得。小的先去值房问问，不敢替外朝乱断。")
            self.assertTrue(last.get("stage_directions"))
            self.assertTrue(any(
                marker in " ".join(last["stage_directions"])
                for marker in ("垂手", "夹腰", "嗓音", "失神", "封签", "宝贝", "肩背")
            ))
            stored = game._chat_history_payload(attendant)[-1]
            self.assertTrue(stored.get("stage_directions"))
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_castrated_official_public_profile_shows_inner_court_lore(self):
        game = web_app.WebGame(fresh=True)
        try:
            row = game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' "
                "AND office_type NOT IN ('后宫','司礼监','内官监御前') "
                "AND office NOT LIKE '%太监%' AND office NOT LIKE '%宦官%' "
                "LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(row)
            name = str(row["name"])

            result = game.castrate_official(
                name,
                force=True,
                scheme_text="净身房行事，铜柄宫刀，无麻；宝约二两八钱，一大一小，油封后发硬，油炸封蜡，收黄杨木描金匣。",
            )
            minister = result["minister"]

            self.assertEqual(minister["office"], "司礼监随堂太监")
            self.assertEqual(minister["office_type"], "司礼监")
            self.assertEqual(minister["faction"], "内廷")
            self.assertNotIn("castration", minister)
            self.assertIn("medical_record", minister)
            medical_titles = " ".join(
                str(item.get("title") or "")
                for group in minister["medical_record"].get("groups", [])
                for item in group.get("items", [])
            )
            self.assertIn("左侧睾丸：缺失", medical_titles)
            self.assertIn("绝育", medical_titles)
            castration = game._public_castration_payload(name)
            self.assertIsNotNone(castration)
            self.assertTrue(castration["forced"])
            self.assertIn("宝贝官库", castration["bao_label"])
            self.assertTrue(castration["trauma_label"])
            self.assertTrue(castration["fixation_label"])
            self.assertEqual(castration["method_label"], "净身房登记")
            self.assertEqual(castration["knife_label"], "")
            self.assertEqual(castration["anesthesia_label"], "")
            self.assertEqual(castration["bao_weight_label"], "")
            self.assertEqual(castration["bao_shape_label"], "")
            self.assertEqual(castration["bao_texture_label"], "")
            self.assertNotIn("宝匣", str(castration))
            self.assertNotIn("钥匙", str(castration))

            refreshed = game.public_character(game.content.characters[name])
            self.assertEqual(refreshed["office"], "司礼监随堂太监")
            self.assertNotIn("castration", refreshed)
            self.assertEqual(game._public_castration_payload(name)["bao_status"], "forfeit")
            self.assertTrue(any(tag["label"] == "生殖伤残" for tag in refreshed["identity_tags"]))
            public_labels = " ".join(str(tag.get("label") or "") for tag in refreshed["identity_tags"])
            self.assertNotRegex(public_labels, r"净身|宝")
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_attendant_lore_reply_terms_do_not_maintain_old_file(self):
        from ming_sim import eunuch_lore as el

        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            el.record_castration(game.db, attendant, forced=False, day=0)
            game.db.conn.execute(
                """
                UPDATE eunuch_lore
                SET castration_method='', procedure_note='', bao_preservation='', private_fixation=''
                WHERE name=?
                """,
                (attendant,),
            )
            game.db.conn.commit()
            before = {
                key: str((el.get_lore(game.db, attendant) or {}).get(key) or "")
                for key in ("castration_method", "procedure_note", "bao_preservation", "private_fixation")
            }

            result = game._absorb_eunuch_lore_from_text(
                attendant,
                "奴婢回陛下，内书堂和司礼监旧档里多是名册、官库旧案、封签钥匙，"
                "奴婢只敢替陛下打听风声。",
            )

            self.assertEqual(result, {})
            lore = el.get_lore(game.db, attendant)
            after = {
                key: str(lore[key] or "")
                for key in ("castration_method", "procedure_note", "bao_preservation", "private_fixation")
            }
            self.assertEqual(after, before)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_attendant_casual_lore_question_does_not_update_old_file(self):
        from ming_sim import eunuch_lore as el

        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            el.record_castration(game.db, attendant, forced=False, day=0)
            game.db.conn.execute(
                """
                UPDATE eunuch_lore
                SET castration_method='', procedure_note='', bao_preservation='', private_fixation=''
                WHERE name=?
                """,
                (attendant,),
            )
            game.db.conn.commit()
            before = {
                key: str((el.get_lore(game.db, attendant) or {}).get(key) or "")
                for key in ("castration_method", "procedure_note", "bao_preservation", "private_fixation")
            }

            result = game._absorb_eunuch_lore_from_text(
                attendant,
                "王承恩，司礼监旧档、内书堂和官库那边有什么风声？钥匙可在谁手里？",
            )

            self.assertEqual(result, {})
            lore = el.get_lore(game.db, attendant)
            after = {
                key: str(lore[key] or "")
                for key in ("castration_method", "procedure_note", "bao_preservation", "private_fixation")
            }
            self.assertEqual(after, before)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_attendant_explicit_lore_order_still_updates_old_file(self):
        from ming_sim import eunuch_lore as el

        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            el.record_castration(game.db, attendant, forced=False, day=0)
            game.session.dialogue_audit_client = self._lore_intake_audit(targets=[attendant])

            result = game._absorb_eunuch_lore_from_text(
                attendant,
                "请把王承恩的宝匣改用黑漆楠木匣，油炸封蜡，钥匙贴身，记入旧档。",
            )

            self.assertIn("updated", result)
            lore = el.get_lore(game.db, attendant)
            self.assertEqual(str(lore["bao_container"] or ""), "黑漆楠木匣")
            self.assertEqual(str(lore["bao_preservation"] or ""), "油炸封蜡")
            self.assertEqual(str(lore["bao_ritual"] or ""), "夜半验看，封签贴身")
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_lore_regex_fallback_disabled_does_not_update_old_file(self):
        from ming_sim import eunuch_lore as el

        os.environ["MING_SIM_DISABLE_DIALOGUE_LORE_LLM_AUDIT"] = "1"
        os.environ.pop("MING_SIM_ENABLE_DIALOGUE_LORE_REGEX_FALLBACK", None)
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            el.record_castration(game.db, attendant, forced=False, day=0)
            game.db.conn.execute(
                """
                UPDATE eunuch_lore
                SET bao_container='', bao_preservation='', bao_ritual=''
                WHERE name=?
                """,
                (attendant,),
            )
            game.db.conn.commit()

            result = game._absorb_eunuch_lore_from_text(
                attendant,
                "请把王承恩的宝匣改用黑漆楠木匣，油炸封蜡，钥匙贴身，记入旧档。",
                source_role="user",
            )

            self.assertEqual(result, {})
            lore = game.db.conn.execute(
                "SELECT bao_container, bao_preservation, bao_ritual FROM eunuch_lore WHERE name=?",
                (attendant,),
            ).fetchone()
            self.assertEqual(str(lore["bao_container"] or ""), "")
            self.assertEqual(str(lore["bao_preservation"] or ""), "")
            self.assertEqual(str(lore["bao_ritual"] or ""), "")
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_semantic_lore_intake_denial_blocks_keyword_old_file_update(self):
        from ming_sim import eunuch_lore as el

        os.environ["MING_SIM_DISABLE_DIALOGUE_LORE_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            el.record_castration(game.db, attendant, forced=False, day=0)
            game.db.conn.execute(
                """
                UPDATE eunuch_lore
                SET bao_container='', bao_preservation='', bao_ritual=''
                WHERE name=?
                """,
                (attendant,),
            )
            game.db.conn.commit()

            def audit(phase, payload):
                if phase == "dialogue_eunuch_lore_intake":
                    self.assertIn(attendant, payload.get("candidate_names") or [])
                    return {
                        "allow": False,
                        "target_names": [],
                        "trigger_quote": "只是问问旧例",
                        "private_reason": "只是问旧档写法，不是命令入档。",
                        "confidence": 96,
                    }
                return None

            game.session.dialogue_audit_client = audit

            result = game._absorb_eunuch_lore_from_text(
                attendant,
                "只是问问旧例：王承恩的宝匣若改用黑漆楠木匣、油炸封蜡、钥匙贴身，是否该记入旧档？",
                source_role="user",
            )

            self.assertEqual(result, {})
            lore = game.db.conn.execute(
                "SELECT bao_container, bao_preservation, bao_ritual FROM eunuch_lore WHERE name=?",
                (attendant,),
            ).fetchone()
            self.assertEqual(str(lore["bao_container"] or ""), "")
            self.assertEqual(str(lore["bao_preservation"] or ""), "")
            self.assertEqual(str(lore["bao_ritual"] or ""), "")
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_semantic_lore_intake_allow_updates_target_old_file(self):
        from ming_sim import eunuch_lore as el

        os.environ["MING_SIM_DISABLE_DIALOGUE_LORE_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
            el.record_castration(game.db, attendant, forced=False, day=0)
            game.db.conn.execute(
                """
                UPDATE eunuch_lore
                SET bao_container='', bao_preservation='', bao_ritual=''
                WHERE name=?
                """,
                (attendant,),
            )
            game.db.conn.commit()

            def audit(phase, payload):
                if phase == "dialogue_eunuch_lore_intake":
                    self.assertEqual(payload.get("source_role"), "user")
                    return {
                        "allow": True,
                        "target_names": [attendant],
                        "trigger_quote": "请把王承恩的宝匣改用黄杨木描金匣",
                        "private_reason": "玩家明确命令补录目标旧档。",
                        "confidence": 96,
                    }
                return None

            game.session.dialogue_audit_client = audit

            result = game._absorb_eunuch_lore_from_text(
                attendant,
                "请把王承恩的宝匣改用黄杨木描金匣，油炸封蜡，钥匙贴身，记入旧档。",
                source_role="user",
            )

            self.assertIn("updated", result)
            lore = game.db.conn.execute(
                "SELECT bao_container, bao_preservation, bao_ritual FROM eunuch_lore WHERE name=?",
                (attendant,),
            ).fetchone()
            self.assertEqual(str(lore["bao_container"] or ""), "黄杨木描金匣")
            self.assertEqual(str(lore["bao_preservation"] or ""), "油炸封蜡")
            self.assertEqual(str(lore["bao_ritual"] or ""), "夜半验看，封签贴身")
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_semantic_lore_intake_runs_without_keyword_prefilter(self):
        from ming_sim import eunuch_lore as el
        from ming_sim import personnel_actions as pa

        os.environ["MING_SIM_DISABLE_DIALOGUE_LORE_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        old_update = el.update_lore_from_text
        old_sync = pa.sync_castration_lore_gameplay
        try:
            attendant = "王承恩"
            el.record_castration(game.db, attendant, forced=False, day=0)
            calls = {"audit": 0, "update": 0}

            def audit(phase, payload):
                if phase == "dialogue_eunuch_lore_intake":
                    calls["audit"] += 1
                    self.assertEqual(payload.get("source_role"), "user")
                    self.assertIn(attendant, payload.get("candidate_names") or [])
                    return {
                        "allow": True,
                        "target_names": [attendant],
                        "trigger_quote": "那桩旧事照方才新说法补进去",
                        "private_reason": "语义上是补入当前人物长期旧档，即便没有旧关键词。",
                        "confidence": 97,
                    }
                return None

            def fake_update(db, name, text, *, day=0):
                calls["update"] += 1
                self.assertEqual(name, attendant)
                return {"name": name, "updated": {"procedure_note": "语义补档"}}

            game.session.dialogue_audit_client = audit
            el.update_lore_from_text = fake_update
            pa.sync_castration_lore_gameplay = lambda *args, **kwargs: {}

            result = game._absorb_eunuch_lore_from_text(
                attendant,
                "把王承恩那桩旧事照方才新说法补进去。",
                source_role="user",
            )

            self.assertEqual(calls["audit"], 1)
            self.assertEqual(calls["update"], 1)
            self.assertIn("updated", result)
            self.assertEqual(result.get("updated_targets"), [attendant])
        finally:
            el.update_lore_from_text = old_update
            pa.sync_castration_lore_gameplay = old_sync
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_eunuch_lore_updates_from_dialogue_text_and_rolls_back(self):
        game = web_app.WebGame(fresh=True)
        try:
            row = game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' "
                "AND office_type NOT IN ('后宫','司礼监','内官监御前') "
                "AND office NOT LIKE '%太监%' AND office NOT LIKE '%宦官%' "
                "LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(row)
            name = str(row["name"])
            game.castrate_official(
                name,
                force=True,
                scheme_text="净身房行事，铜柄宫刀，无麻；宝约一两二钱，圆缩成团，石灰封后发白，官库石灰封存，收白签灰瓮。",
            )
            game.db.conn.execute("DELETE FROM character_traits WHERE name=?", (name,))
            game.db.conn.execute(
                "DELETE FROM player_inventory WHERE item_id IN (?, ?, ?)",
                (f"内廷旧档：{name}", f"官库旧匣：{name}", f"旧匣遗失：{name}"),
            )
            game.db.conn.commit()
            self.assertNotIn("castration", game.public_character(game.content.characters[name]))
            before = game._public_castration_payload(name)
            self.assertIsNotNone(before)
            before_traits = {
                str(r["trait"])
                for r in game.db.conn.execute("SELECT trait FROM character_traits WHERE name=?", (name,)).fetchall()
            }
            before_inventory = {str(item["id"]) for item in game.db.list_player_inventory()}
            before_stats = game.db.conn.execute(
                "SELECT emp_trust, grievance, ability, luck FROM characters WHERE name=?",
                (name,),
            ).fetchone()
            chat_turn_id, snapshot = game._start_chat_turn(name)
            game.session.dialogue_audit_client = self._lore_intake_audit(targets=[name])

            result = game._absorb_eunuch_lore_from_text(
                name,
                "他的宝匣改用黑漆楠木匣，宝用油炸封蜡，约二两八钱，一大一小，油封后发硬。"
                "他近来漏尿尿闭，嗓音尖薄，幻肢痛发作，还有贤者模式。",
            )
            self.assertIn("updated", result)
            self.assertIn("gameplay", result)
            self.assertTrue(result["gameplay"]["traits_added"])
            self.assertTrue(result["gameplay"]["items_added"])
            scheme_review = result["gameplay"].get("scheme_review")
            self.assertTrue(scheme_review)
            self.assertIn(scheme_review["tier"], {"可控旧例", "粗急伤身", "酷烈高危"})
            self.assertGreaterEqual(int(scheme_review["risk_score"]), 50)
            effect = game._eunuch_lore_dialogue_effect(result)
            effect_labels = {str(item.get("label") or "") for item in effect.get("effects", [])}
            self.assertTrue(any("旧制复盘：" in label for label in effect_labels))
            game._record_chat_rollback_items(chat_turn_id, snapshot)

            after = game._public_castration_payload(name)
            self.assertIsNotNone(after)
            self.assertEqual(after["container_label"], "黑漆楠木匣")
            self.assertEqual(after["preservation_label"], "油炸封蜡")
            self.assertEqual(after["bao_weight_label"], "约二两八钱")
            self.assertEqual(after["bao_shape_label"], "一大一小")
            self.assertEqual(after["bao_texture_label"], "油封后发硬")
            self.assertIn("漏尿", after["urine_label"])
            self.assertIn("嗓音尖薄", after["voice_body_label"])
            self.assertIn("幻肢痛", after["trauma_label"])
            self.assertIn("贤者模式", after["psychosexual_label"])
            after_traits = {
                str(r["trait"])
                for r in game.db.conn.execute("SELECT trait FROM character_traits WHERE name=?", (name,)).fetchall()
            }
            self.assertNotIn("尿路旧患", after_traits)
            self.assertNotIn("情欲异化", after_traits)
            after_inventory = {str(item["id"]) for item in game.db.list_player_inventory()}
            self.assertIn(f"内廷旧档：{name}", after_inventory - before_inventory)
            after_stats = game.db.conn.execute(
                "SELECT emp_trust, grievance, ability, luck FROM characters WHERE name=?",
                (name,),
            ).fetchone()
            self.assertLessEqual(int(after_stats["emp_trust"]), int(before_stats["emp_trust"]))
            self.assertGreater(int(after_stats["grievance"]), int(before_stats["grievance"]))

            game.db.undo_chat_turn(chat_turn_id)
            restored = game._public_castration_payload(name)
            self.assertIsNotNone(restored)
            self.assertEqual(restored["container_label"], before["container_label"])
            self.assertEqual(restored["preservation_label"], before["preservation_label"])
            self.assertEqual(restored["psychosexual_label"], before["psychosexual_label"])
            restored_traits = {
                str(r["trait"])
                for r in game.db.conn.execute("SELECT trait FROM character_traits WHERE name=?", (name,)).fetchall()
            }
            restored_inventory = {str(item["id"]) for item in game.db.list_player_inventory()}
            self.assertEqual(restored_traits, before_traits)
            self.assertEqual(restored_inventory, before_inventory)
            restored_stats = game.db.conn.execute(
                "SELECT emp_trust, grievance, ability, luck FROM characters WHERE name=?",
                (name,),
            ).fetchone()
            self.assertEqual(int(restored_stats["emp_trust"]), int(before_stats["emp_trust"]))
            self.assertEqual(int(restored_stats["grievance"]), int(before_stats["grievance"]))
            self.assertEqual(int(restored_stats["ability"]), int(before_stats["ability"]))
            self.assertEqual(int(restored_stats["luck"]), int(before_stats["luck"]))
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_minor_castration_suppresses_adult_lore_and_traits(self):
        game = web_app.WebGame(fresh=True)
        try:
            row = game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' "
                "AND office_type NOT IN ('后宫','司礼监','内官监御前') "
                "AND office NOT LIKE '%太监%' AND office NOT LIKE '%宦官%' "
                "LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(row)
            name = str(row["name"])
            game.db.conn.execute(
                "UPDATE characters SET birth_year=? WHERE name=?",
                (int(game.state.year) - 15, name),
            )
            game.db.conn.commit()

            game.castrate_official(
                name,
                force=True,
                scheme_text="无麻净身，宝油炸封蜡；近来漏尿尿闭、嗓音尖薄、幻肢痛，也有人胡说贤者模式与性无能。",
            )

            minister = game.public_character(game.content.characters[name])
            self.assertNotIn("castration", minister)
            self.assertIn("medical_record", minister)
            castration = game._public_castration_payload(name)
            self.assertIsNotNone(castration)
            self.assertEqual(castration["psychosexual_label"], "")
            self.assertNotIn("癖性", castration["condition_line"])
            trait_names = {
                str(r["trait"])
                for r in game.db.conn.execute("SELECT trait FROM character_traits WHERE name=?", (name,)).fetchall()
            }
            self.assertNotIn("尿路旧患", trait_names)
            self.assertNotIn("惊创未平", trait_names)
            self.assertNotIn("情欲异化", trait_names)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_eunuch_lore_updates_named_third_person_without_touching_speaker(self):
        game = web_app.WebGame(fresh=True)
        try:
            rows = game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' "
                "AND office_type NOT IN ('后宫','司礼监','内官监御前') "
                "AND office NOT LIKE '%太监%' AND office NOT LIKE '%宦官%' "
                "LIMIT 2"
            ).fetchall()
            self.assertGreaterEqual(len(rows), 2)
            speaker = str(rows[0]["name"])
            target = str(rows[1]["name"])
            game.castrate_official(speaker, force=True)
            game.castrate_official(target, force=True)
            self.assertNotIn("castration", game.public_character(game.content.characters[speaker]))
            before_speaker = game._public_castration_payload(speaker)
            self.assertIsNotNone(before_speaker)
            game.session.dialogue_audit_client = self._lore_intake_audit(targets=[target])

            result = game._absorb_eunuch_lore_from_text(
                speaker,
                f"{target}的宝匣改用黄杨木描金匣，香料腌藏。近来结石尿闭，按肩会僵住，已有性无能。",
            )

            self.assertEqual(result["updated_targets"], [target])
            after_target = game._public_castration_payload(target)
            self.assertIsNotNone(after_target)
            self.assertEqual(after_target["container_label"], "黄杨木描金匣")
            self.assertEqual(after_target["preservation_label"], "香料腌藏")
            self.assertIn("尿闭", after_target["urine_label"])
            self.assertIn("按肩", after_target["trauma_label"])
            self.assertIn("性无能", after_target["psychosexual_label"])
            after_speaker = game._public_castration_payload(speaker)
            self.assertIsNotNone(after_speaker)
            self.assertEqual(after_speaker["container_label"], before_speaker["container_label"])
            self.assertEqual(after_speaker["preservation_label"], before_speaker["preservation_label"])
            self.assertEqual(after_speaker["psychosexual_label"], before_speaker["psychosexual_label"])
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_petition_context_is_actor_scoped_and_reaches_dialogue_prep(self):
        game = web_app.WebGame(fresh=True)
        try:
            name = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                "AND name!='王承恩' ORDER BY ability DESC LIMIT 1"
            ).fetchone()["name"])
            rival = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' AND name!=? "
                "LIMIT 1",
                (name,),
            ).fetchone()["name"])
            game.db.conn.execute(
                "UPDATE characters SET emp_trust=24, grievance=80 WHERE name=?",
                (name,),
            )
            court._set_opinion(game.db, name, rival, -76, "夺功旧怨", 1)
            game.db.conn.commit()
            game.session.dialogue_audit_client = lambda phase, payload: {  # type: ignore[assignment]
                "goal_decision": "none",
                "confidence": 90,
            }
            context = {
                "kind": "petition",
                "actor": name,
                "target": rival,
                "ref_kind": "character",
                "ref_id": name,
            }

            brief = game._chat_context_brief(name, context)
            mismatch = game._chat_context_brief(rival, context)
            augmented, prepared = game.session.prepare_chat_run(
                game.content.characters[name],
                "朕听说你近日有难处，今日入殿说清楚。",
                supplemental_context=brief,
            )

            self.assertIn("本次召对事项：主动求援请托", brief)
            self.assertEqual(mismatch, "")
            self.assertIn("主动求援请托", augmented)
            self.assertIn("主动求援请托", prepared.behavior_context)
            self.assertIn("NPC对话行为档案", prepared.behavior_brief)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_decision_context_reaches_dialogue_prep(self):
        game = web_app.WebGame(fresh=True)
        try:
            names = [
                str(r["name"]) for r in game.db.conn.execute(
                    "SELECT name FROM characters "
                    "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                    "LIMIT 3"
                ).fetchall()
            ]
            a, b, other = names
            court._set_opinion(game.db, a, b, -75, "夺功旧怨", 1)
            court._set_opinion(game.db, b, a, -70, "反劾旧怨", 1)
            memorials.create_memorial(
                game.db,
                game.state,
                day=1,
                author_name=a,
                org="都察院",
                kind="弹章",
                urgency=3,
                summary=f"{a}劾{b}",
                ref_kind="character",
                ref_id=b,
            )
            court_events.evaluate_decisions(game.db, game.state, 1)
            game.session.dialogue_audit_client = lambda phase, payload: {  # type: ignore[assignment]
                "goal_decision": "none",
                "confidence": 90,
            }
            context = {
                "kind": "decision",
                "actor": a,
                "target": b,
                "ref_kind": "decision",
                "ref_id": "rival_feud",
            }

            brief = game._chat_context_brief(a, context)
            target_brief = game._chat_context_brief(b, context)
            mismatch = game._chat_context_brief(other, context)
            augmented, prepared = game.session.prepare_chat_run(
                game.content.characters[a],
                "朕还未裁断，先听你把证据和怕处说清。",
                supplemental_context=brief,
            )

            self.assertIn("本次召对事项：裁断前问话", brief)
            self.assertIn("可见裁断路数", brief)
            self.assertIn("牵涉人", target_brief)
            self.assertEqual(mismatch, "")
            self.assertIn("裁断前问话", augmented)
            self.assertIn("裁断前问话", prepared.behavior_context)
            self.assertIn("NPC对话行为档案", prepared.behavior_brief)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_legacy_context_is_actor_scoped_and_reaches_dialogue_prep(self):
        game = web_app.WebGame(fresh=True)
        try:
            actor = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                "AND (office LIKE '%户部%' OR office_type LIKE '%户部%') "
                "ORDER BY ability DESC LIMIT 1"
            ).fetchone()["name"])
            target = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                "AND name!=? AND (office LIKE '%都察院%' OR office_type LIKE '%都察院%' OR faction='东林') "
                "ORDER BY integrity DESC, ability DESC LIMIT 1",
                (actor,),
            ).fetchone()["name"])
            other = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' AND name NOT IN (?, ?) "
                "ORDER BY ability DESC LIMIT 1",
                (actor, target),
            ).fetchone()["name"])
            legacy_id = game.db.insert_legacy(
                game.state,
                name="苛税余波：辽饷",
                modifiers={"民心": -9},
                narrative_hint="辽饷加派已入常例；钱粮见长，民心恢复受压。",
                duration_months=-1,
                legacy_key="directive_tax:7:辽饷",
            )
            game.session.dialogue_audit_client = lambda phase, payload: {  # type: ignore[assignment]
                "goal_decision": "none",
                "confidence": 90,
            }
            context = {
                "kind": "legacy",
                "actor": actor,
                "target": target,
                "ref_kind": "legacy",
                "ref_id": str(legacy_id),
            }

            brief = game._chat_context_brief(actor, context)
            target_brief = game._chat_context_brief(target, context)
            mismatch = game._chat_context_brief(other, context)
            augmented, prepared = game.session.prepare_chat_run(
                game.content.characters[actor],
                "这项旧政仍在拖动朝局，朕要听善后。",
                supplemental_context=brief,
            )

            self.assertIn("本次召对事项：长期政策余波", brief)
            self.assertIn("两难结构", brief)
            self.assertIn("民怨善后方", target_brief)
            self.assertEqual(mismatch, "")
            self.assertIn("长期政策余波", augmented)
            self.assertIn("长期政策余波", prepared.behavior_context)
            self.assertIn("NPC对话行为档案", prepared.behavior_brief)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_patronage_context_handles_sponsor_and_candidate(self):
        game = web_app.WebGame(fresh=True)
        try:
            sponsor = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                "AND name!='王承恩' ORDER BY ability DESC LIMIT 1"
            ).fetchone()["name"])
            candidate = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' AND name!=? "
                "ORDER BY ability DESC LIMIT 1",
                (sponsor,),
            ).fetchone()["name"])
            game.db.conn.execute(
                "UPDATE characters SET office='待铨（举贤入京）', office_type='待铨', summary=? WHERE name=?",
                (f"由地方举荐入京。举荐来源：{sponsor}；风险：初入朝局，仍受举主关系牵引。", candidate),
            )
            court.adjust_opinion(game.db, sponsor, candidate, +28, "举荐入朝", day=1, reciprocal=False)
            court.adjust_opinion(game.db, candidate, sponsor, +34, "举主恩义", day=1, reciprocal=False)
            game.session.dialogue_audit_client = lambda phase, payload: {  # type: ignore[assignment]
                "goal_decision": "none",
                "confidence": 90,
            }
            context = {
                "kind": "patronage",
                "actor": sponsor,
                "target": candidate,
                "ref_kind": "relationship",
                "ref_id": f"{sponsor}:{candidate}",
            }

            sponsor_brief = game._chat_context_brief(sponsor, context)
            candidate_brief = game._chat_context_brief(candidate, context)
            mismatch = game._chat_context_brief("王承恩", context)
            augmented, prepared = game.session.prepare_chat_run(
                game.content.characters[sponsor],
                f"你既荐{candidate}，今日当面说清楚。",
                supplemental_context=sponsor_brief,
            )

            self.assertIn("本次召对事项：举主担保", sponsor_brief)
            self.assertIn("当前入对者是举主", sponsor_brief)
            self.assertIn("当前入对者是新人", candidate_brief)
            self.assertEqual(mismatch, "")
            self.assertIn("举主担保", augmented)
            self.assertIn("举主担保", prepared.behavior_context)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_agenda_context_is_actor_scoped_and_reaches_dialogue_prep(self):
        game = web_app.WebGame(fresh=True)
        try:
            actor = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                "AND name!='王承恩' ORDER BY ability DESC LIMIT 1"
            ).fetchone()["name"])
            other = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' AND name!=? "
                "ORDER BY ability DESC LIMIT 1",
                (actor,),
            ).fetchone()["name"])
            game.db.conn.execute(
                "INSERT OR REPLACE INTO npc_agendas "
                "(name, kind, title, target_name, intensity, status, progress) "
                "VALUES (?, 'enrich', '自肥', '', 92, 'active', 88)",
                (actor,),
            )
            game.db.conn.commit()
            game.session.dialogue_audit_client = lambda phase, payload: {  # type: ignore[assignment]
                "goal_decision": "none",
                "confidence": 90,
            }
            context = {
                "kind": "agenda",
                "actor": actor,
                "ref_kind": "character",
                "ref_id": actor,
            }

            brief = game._chat_context_brief(actor, context)
            auto_brief = game._chat_context_brief(actor, None)
            empty_context_brief = game._chat_context_brief(actor, {})
            mismatch = game._chat_context_brief(other, context)
            augmented, prepared = game.session.prepare_chat_run(
                game.content.characters[actor],
                "朕闻你近来动作渐密，今日说清楚。",
                supplemental_context=auto_brief,
            )

            self.assertIn("本次召对事项：人物私图将成", brief)
            self.assertIn("本次召对事项：人物私图将成", auto_brief)
            self.assertIn("交易画像", auto_brief)
            self.assertEqual(empty_context_brief, auto_brief)
            self.assertEqual(mismatch, "")
            self.assertIn("人物私图将成", augmented)
            self.assertIn("人物私图将成", prepared.behavior_context)
            self.assertIn("NPC对话行为档案", prepared.behavior_brief)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_favor_context_is_actor_scoped_and_reaches_dialogue_prep(self):
        game = web_app.WebGame(fresh=True)
        try:
            actor = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                "AND name!='王承恩' ORDER BY ability DESC LIMIT 1"
            ).fetchone()["name"])
            other = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' AND name!=? "
                "ORDER BY ability DESC LIMIT 1",
                (actor,),
            ).fetchone()["name"])
            result = memorials.back_official(game.db, game.state, actor, "comfort", day=1)
            self.assertTrue(result["ok"], result)
            memory_id = int(game.db.conn.execute(
                "SELECT id FROM event_memories WHERE subject_id=? AND event_type='imperial_favor' "
                "ORDER BY id DESC LIMIT 1",
                (actor,),
            ).fetchone()["id"])
            game.session.dialogue_audit_client = lambda phase, payload: {  # type: ignore[assignment]
                "goal_decision": "none",
                "confidence": 90,
            }
            context = {
                "kind": "favor",
                "actor": actor,
                "ref_kind": "memory",
                "ref_id": str(memory_id),
            }

            brief = game._chat_context_brief(actor, context)
            mismatch = game._chat_context_brief(other, context)
            augmented, prepared = game.session.prepare_chat_run(
                game.content.characters[actor],
                "朕昔日曾替你留任事余地，今日该谈谈如何还恩。",
                supplemental_context=brief,
            )

            self.assertIn("本次召对事项：旧恩未报", brief)
            self.assertEqual(mismatch, "")
            self.assertIn("旧恩未报", augmented)
            self.assertIn("旧恩未报", prepared.behavior_context)
            self.assertIn("NPC对话行为档案", prepared.behavior_brief)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_rivalry_context_is_actor_scoped_and_reaches_dialogue_prep(self):
        game = web_app.WebGame(fresh=True)
        try:
            actor = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                "AND name!='王承恩' ORDER BY ability DESC LIMIT 1"
            ).fetchone()["name"])
            target = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' AND name!=? "
                "LIMIT 1",
                (actor,),
            ).fetchone()["name"])
            court._set_opinion(game.db, actor, target, -82, "夺功旧怨", 1)
            court._set_opinion(game.db, target, actor, -74, "反劾旧怨", 1)
            game.db.conn.commit()
            game.session.dialogue_audit_client = lambda phase, payload: {  # type: ignore[assignment]
                "goal_decision": "none",
                "confidence": 90,
            }
            context = {
                "kind": "rivalry",
                "actor": actor,
                "target": target,
                "ref_kind": "relationship",
                "ref_id": f"{actor}:{target}",
            }

            brief = game._chat_context_brief(actor, context)
            mismatch = game._chat_context_brief("王承恩", context)
            augmented, prepared = game.session.prepare_chat_run(
                game.content.characters[actor],
                f"朕想问你和{target}的旧怨。",
                supplemental_context=brief,
            )

            self.assertIn("本次召对事项：政敌怨隙/调停共办", brief)
            self.assertEqual(mismatch, "")
            self.assertIn("调停共办", augmented)
            self.assertIn("调停共办", prepared.behavior_context)
            self.assertIn("NPC对话行为档案", prepared.behavior_brief)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_decision_chat_effect_records_testimony(self):
        game = web_app.WebGame(fresh=True)
        try:
            names = [
                str(r["name"]) for r in game.db.conn.execute(
                    "SELECT name FROM characters "
                    "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                    "LIMIT 2"
                ).fetchall()
            ]
            a, b = names
            court._set_opinion(game.db, a, b, -75, "夺功旧怨", 1)
            court._set_opinion(game.db, b, a, -70, "反劾旧怨", 1)
            memorials.create_memorial(
                game.db,
                game.state,
                day=1,
                author_name=a,
                org="都察院",
                kind="弹章",
                urgency=3,
                summary=f"{a}劾{b}",
                ref_kind="character",
                ref_id=b,
            )
            court_events.evaluate_decisions(game.db, game.state, 1)
            context = {
                "kind": "decision",
                "actor": a,
                "target": b,
                "ref_kind": "decision",
                "ref_id": "rival_feud",
            }

            effect = game._decision_chat_effect(
                a,
                context,
                f"朕未裁断前，先问你弹劾{b}有何证据？",
                "臣有账册与人证，愿限三日查验，若虚言愿担责。",
            )

            from ming_sim.playstyle import decision_testimonies_for_pending

            testimonies = decision_testimonies_for_pending(game.db)
            self.assertEqual(effect["title"], "证词入案")
            self.assertEqual(effect["effects"][0]["label"], "当事人入案")
            self.assertEqual(len(testimonies), 1)
            self.assertEqual(testimonies[0]["minister"], a)
            self.assertIn("账册", str(testimonies[0]["summary"]))
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_dialogue_mediation_confirmation_creates_followup_obligation(self):
        game = web_app.WebGame(fresh=True)
        try:
            actor = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                "AND name!='王承恩' ORDER BY ability DESC LIMIT 1"
            ).fetchone()["name"])
            target = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' AND name NOT IN (?, '王承恩') "
                "ORDER BY ability ASC LIMIT 1",
                (actor,),
            ).fetchone()["name"])

            game.session.dialogue_audit_client = self._dialogue_action_audit(
                action_type="mediation",
                actor=actor,
                target=target,
                mode="co_work",
            )
            proposal_events = list(game.chat_stream(actor, f"若朕令你与{target}共办一件可验小差，你肯不肯？"))
            self.assertEqual(proposal_events[-1]["type"], "done")
            self.assertIn("若陛下准", proposal_events[-1]["payload"]["answer"])
            self.assertEqual(game.db.list_conversation_goals(minister_name=actor, statuses=["waiting_conditions"]), [])

            confirm_events = list(game.chat_stream(actor, "可以，就这么办。"))

            self.assertEqual(confirm_events[-1]["type"], "done")
            payload = confirm_events[-1]["payload"]
            effects = payload["dialogue_effect"]["effects"]
            self.assertIn(f"履约账本：{actor}", {str(item["label"]) for item in effects})
            self.assertEqual(payload["dialogue_goal"]["minister_name"], actor)
            self.assertIn("共办消怨", str(payload["dialogue_goal"]["title"]))
            self.assertEqual(payload["dialogue_goal"]["status_label"], "待条件")
            goals = game.db.list_conversation_goals(minister_name=actor, statuses=["waiting_conditions"])
            self.assertEqual(len(goals), 1)
            self.assertIn("共办消怨", str(goals[0]["title"]))
            self.assertEqual(int(goals[0]["expires_turn"]), int(game.state.turn) + 3)
            agreements = game.db.list_negotiation_agreements(
                minister_name=actor,
                action_kind="court_commitment",
                status="pending",
            )
            self.assertEqual(len(agreements), 1)
            self.assertIn("共办消怨", str(agreements[0]["topic"]))
            self.assertEqual(int(agreements[0]["due_turn"]), int(game.state.turn) + 2)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_dialogue_guarantee_confirmation_creates_patronage_obligation(self):
        game = web_app.WebGame(fresh=True)
        try:
            actor = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                "AND name!='王承恩' ORDER BY ability DESC LIMIT 1"
            ).fetchone()["name"])
            target = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' AND name NOT IN (?, '王承恩') "
                "ORDER BY ability ASC LIMIT 1",
                (actor,),
            ).fetchone()["name"])

            game.session.dialogue_audit_client = self._dialogue_action_audit(
                action_type="mediation",
                actor=actor,
                target=target,
                mode="guarantee",
            )
            proposal_events = list(game.chat_stream(actor, f"朕知道你与{target}有一层人情，你肯替他担保到哪一步？"))
            self.assertEqual(proposal_events[-1]["type"], "done")
            self.assertIn("担保边界", proposal_events[-1]["payload"]["answer"])

            confirm_events = list(game.chat_stream(actor, "准，你替他担保。"))

            payload = confirm_events[-1]["payload"]
            self.assertEqual(payload["dialogue_effect"]["title"], "人情担保")
            self.assertEqual(payload["dialogue_goal"]["minister_name"], actor)
            self.assertIn("人情担保", str(payload["dialogue_goal"]["title"]))
            self.assertEqual(payload["dialogue_goal"]["status_label"], "待条件")
            goals = game.db.list_conversation_goals(minister_name=actor, statuses=["waiting_conditions"])
            self.assertEqual(len(goals), 1)
            self.assertIn("人情担保", str(goals[0]["title"]))
            self.assertIn(target, str(goals[0]["target_text"]))
            agreements = game.db.list_negotiation_agreements(
                minister_name=actor,
                action_kind="court_commitment",
                status="pending",
            )
            self.assertEqual(len(agreements), 1)
            self.assertIn("人情担保承诺", str(agreements[0]["promise_type"]))
            self.assertIn("党援坐大", str(agreements[0]["stakes"]))
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_relationship_context_surfaces_positive_tie_stakes(self):
        game = web_app.WebGame(fresh=True)
        try:
            actor = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                "AND name!='王承恩' ORDER BY ability DESC LIMIT 1"
            ).fetchone()["name"])
            target = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' AND name!=? "
                "LIMIT 1",
                (actor,),
            ).fetchone()["name"])
            court._set_opinion(game.db, actor, target, 64, "同乡盟友", 1)
            court._set_opinion(game.db, target, actor, 58, "同乡盟友", 1)
            game.db.conn.commit()
            game.session.dialogue_audit_client = lambda phase, payload: {  # type: ignore[assignment]
                "goal_decision": "none",
                "confidence": 90,
            }
            context = {
                "kind": "relationship",
                "actor": actor,
                "target": target,
                "ref_kind": "relationship",
                "ref_id": f"{actor}:{target}",
            }

            brief = game._chat_context_brief(actor, context)
            mismatch = game._chat_context_brief("王承恩", context)
            augmented, prepared = game.session.prepare_chat_run(
                game.content.characters[actor],
                f"朕想问你和{target}的这层人情。",
                supplemental_context=brief,
            )

            self.assertIn("本次召对事项：人情关系·党援担保", brief)
            self.assertIn("担保", brief)
            self.assertIn("植党", brief)
            self.assertEqual(mismatch, "")
            self.assertIn("党援担保", augmented)
            self.assertIn("党援担保", prepared.behavior_context)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_faction_context_is_representative_scoped_and_reaches_dialogue_prep(self):
        game = web_app.WebGame(fresh=True)
        try:
            row = game.db.conn.execute(
                "SELECT name, faction FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                "AND name!='王承恩' AND faction NOT IN ('无','中立','') "
                "ORDER BY ability DESC LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(row)
            actor = str(row["name"])
            faction = str(row["faction"])
            game.db.conn.execute(
                "UPDATE factions SET leverage=88, satisfaction=20, heat=74 WHERE name=?",
                (faction,),
            )
            game.db.conn.execute(
                "UPDATE characters SET emp_trust=33, grievance=69 WHERE name=?",
                (actor,),
            )
            game.db.conn.commit()
            game.session.dialogue_audit_client = lambda phase, payload: {  # type: ignore[assignment]
                "goal_decision": "none",
                "confidence": 90,
            }
            context = {
                "kind": "faction",
                "actor": actor,
                "ref_kind": "faction",
                "ref_id": faction,
            }

            brief = game._chat_context_brief(actor, context)
            mismatch = game._chat_context_brief("王承恩", context)
            augmented, prepared = game.session.prepare_chat_run(
                game.content.characters[actor],
                f"朕要问{faction}近来的气焰。",
                supplemental_context=brief,
            )

            self.assertIn("本次召对事项：派系压力/借力安抚", brief)
            self.assertEqual(mismatch, "")
            self.assertIn("派系压力", augmented)
            self.assertIn("派系压力", prepared.behavior_context)
            self.assertIn("NPC对话行为档案", prepared.behavior_brief)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_army_context_is_commander_scoped_and_reaches_dialogue_prep(self):
        game = web_app.WebGame(fresh=True)
        try:
            row = game.db.conn.execute(
                "SELECT a.id, a.name, a.commander FROM armies a "
                "JOIN characters c ON c.name=a.commander "
                "WHERE a.owner_power='ming' AND c.status='active' AND c.power_id='ming' AND c.office_type!='后宫' "
                "ORDER BY a.id LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(row)
            army_id = str(row["id"])
            army_name = str(row["name"])
            commander = str(row["commander"])
            game.db.conn.execute(
                "UPDATE armies SET autonomy=78, arrears=maintenance_per_turn*4, morale=38, loyalty=41, supervisor='' WHERE id=?",
                (army_id,),
            )
            game.db.conn.execute(
                "UPDATE characters SET emp_trust=35, grievance=66 WHERE name=?",
                (commander,),
            )
            game.db.conn.commit()
            game.session.dialogue_audit_client = lambda phase, payload: {  # type: ignore[assignment]
                "goal_decision": "none",
                "confidence": 90,
            }
            context = {
                "kind": "army",
                "actor": commander,
                "ref_kind": "army",
                "ref_id": army_id,
            }

            brief = game._chat_context_brief(commander, context)
            mismatch = game._chat_context_brief("王承恩", context)
            augmented, prepared = game.session.prepare_chat_run(
                game.content.characters[commander],
                "朕今日召你，正为军镇欠饷与离心之事。",
                supplemental_context=brief,
            )

            self.assertIn("本次召对事项：军镇离心/欠饷问对", brief)
            self.assertIn(army_name, brief)
            self.assertEqual(mismatch, "")
            self.assertIn("军镇离心/欠饷问对", augmented)
            self.assertIn("军镇离心/欠饷问对", prepared.behavior_context)
            self.assertIn("NPC对话行为档案", prepared.behavior_brief)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_dialogue_mediation_requires_confirmation(self):
        game = web_app.WebGame(fresh=True)
        try:
            actor = "韩爌"
            target = "魏忠贤"
            court.adjust_opinion(game.db, actor, target, -60, "测试旧怨", day=1, reciprocal=True)
            before = court.get_opinion(game.db, actor, target)
            game.session.dialogue_audit_client = self._dialogue_action_audit(
                action_type="mediation",
                actor=actor,
                target=target,
                mode="co_work",
            )

            proposal_events = list(game.chat_stream(actor, f"朕想调停你和{target}的旧怨。"))
            self.assertEqual(proposal_events[-1]["type"], "done")
            self.assertIn("若陛下准", proposal_events[-1]["payload"]["answer"])
            self.assertEqual(court.get_opinion(game.db, actor, target), before)

            confirm_events = list(game.chat_stream(actor, "准，去调停。"))
            self.assertEqual(confirm_events[-1]["type"], "done")
            payload = confirm_events[-1]["payload"]
            self.assertIn("dialogue_effect", payload)
            self.assertGreater(court.get_opinion(game.db, actor, target), before)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_audience_bargain_attitude_records_memory_and_rolls_back(self):
        game = web_app.WebGame(fresh=True)
        try:
            actor = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                "AND name!='王承恩' ORDER BY ability DESC LIMIT 1"
            ).fetchone()["name"])
            game.db.conn.execute(
                "UPDATE characters SET emp_trust=40, grievance=60 WHERE name=?",
                (actor,),
            )
            game.db.conn.commit()
            chat_turn_id, before_snapshot = game._start_chat_turn(actor)
            game.session.dialogue_audit_client = self._bargain_attitude_audit(attitude="accept")

            effect = game._bargain_chat_effect(
                actor,
                {"kind": "petition", "actor": actor, "title": "求展限办差"},
                "准，朕暂且护持你，给你人手。",
                "臣叩谢天恩。",
                chat_turn_id,
            )
            game._record_chat_rollback_items(chat_turn_id, before_snapshot)

            self.assertEqual(effect["title"], "御前许诺")
            labels = {str(item["label"]) for item in effect["effects"]}
            self.assertIn("交易入记忆", labels)
            self.assertIn("履约账本：许诺入账", labels)
            after = game.db.conn.execute(
                "SELECT emp_trust, grievance FROM characters WHERE name=?",
                (actor,),
            ).fetchone()
            self.assertEqual(int(after["emp_trust"]), 44)
            self.assertEqual(int(after["grievance"]), 56)
            goals = game.db.list_conversation_goals(minister_name=actor, statuses=["waiting_conditions"])
            self.assertEqual(len(goals), 1)
            self.assertEqual(goals[0]["action_kind"], "audience_bargain")
            self.assertIn("御前许诺", str(goals[0]["title"]))
            self.assertTrue(any("可验" in str(item.get("description") or "") for item in goals[0]["conditions"]))
            memory = game.db.conn.execute(
                """
                SELECT * FROM event_memories
                WHERE subject_id=? AND event_type='audience_bargain' AND source_kind='chat_turn'
                """,
                (actor,),
            ).fetchone()
            self.assertIsNotNone(memory)
            log = game.db.conn.execute(
                "SELECT message FROM turn_logs WHERE message LIKE ?",
                (f"%【奏对交易】{actor}%",),
            ).fetchone()
            self.assertIsNotNone(log)

            game.db.undo_chat_turn(chat_turn_id)
            restored = game.db.conn.execute(
                "SELECT emp_trust, grievance FROM characters WHERE name=?",
                (actor,),
            ).fetchone()
            self.assertEqual(int(restored["emp_trust"]), 40)
            self.assertEqual(int(restored["grievance"]), 60)
            rolled_memory = game.db.conn.execute(
                """
                SELECT * FROM event_memories
                WHERE subject_id=? AND event_type='audience_bargain' AND source_kind='chat_turn'
                """,
                (actor,),
            ).fetchone()
            self.assertIsNone(rolled_memory)
            rolled_log = game.db.conn.execute(
                "SELECT message FROM turn_logs WHERE message LIKE ?",
                (f"%【奏对交易】{actor}%",),
            ).fetchone()
            self.assertIsNone(rolled_log)
            self.assertEqual(game.db.list_conversation_goals(minister_name=actor), [])
            self.assertEqual(
                game.db.list_negotiation_agreements(minister_name=actor, action_kind="audience_bargain"),
                [],
            )
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_semantic_bargain_attitude_accepts_without_keyword_fallback(self):
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            actor = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                "AND name!='王承恩' ORDER BY ability DESC LIMIT 1"
            ).fetchone()["name"])
            game.db.conn.execute(
                "UPDATE characters SET emp_trust=40, grievance=60 WHERE name=?",
                (actor,),
            )
            game.db.conn.commit()

            def audit(phase, payload):
                if phase == "dialogue_bargain_attitude":
                    self.assertEqual((payload.get("bargain_context") or {}).get("kind"), "petition")
                    return {
                        "allow": True,
                        "attitude": "accept",
                        "trigger_quote": "朕替你担着，先放手做",
                        "private_reason": "test semantic bargain accept",
                        "confidence": 95,
                    }
                return None

            game.session.dialogue_audit_client = audit

            effect = game._bargain_chat_effect(
                actor,
                {"kind": "petition", "actor": actor, "title": "求展限办差"},
                "朕替你担着，先放手做。",
                "臣叩谢天恩。",
            )

            self.assertEqual(effect["title"], "御前许诺")
            after = game.db.conn.execute(
                "SELECT emp_trust, grievance FROM characters WHERE name=?",
                (actor,),
            ).fetchone()
            self.assertEqual(int(after["emp_trust"]), 44)
            self.assertEqual(int(after["grievance"]), 56)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_semantic_bargain_attitude_denial_blocks_keyword_fallback(self):
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            actor = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                "AND name!='王承恩' ORDER BY ability DESC LIMIT 1"
            ).fetchone()["name"])
            game.db.conn.execute(
                "UPDATE characters SET emp_trust=40, grievance=60 WHERE name=?",
                (actor,),
            )
            game.db.conn.commit()

            def audit(phase, payload):
                if phase == "dialogue_bargain_attitude":
                    return {
                        "allow": False,
                        "attitude": "none",
                        "trigger_quote": "准你讲，但不是准你办",
                        "private_reason": "只是允许继续陈述，不是批准请求。",
                        "confidence": 96,
                    }
                return None

            game.session.dialogue_audit_client = audit

            effect = game._bargain_chat_effect(
                actor,
                {"kind": "petition", "actor": actor, "title": "求展限办差"},
                "准你讲，但不是准你办。",
                "臣明白。",
            )

            self.assertEqual(effect, {})
            after = game.db.conn.execute(
                "SELECT emp_trust, grievance FROM characters WHERE name=?",
                (actor,),
            ).fetchone()
            self.assertEqual(int(after["emp_trust"]), 40)
            self.assertEqual(int(after["grievance"]), 60)
            self.assertEqual(game.db.list_conversation_goals(minister_name=actor), [])
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_semantic_directive_fallback_creates_pending_without_keyword_prefilter(self):
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            actor = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                "AND name!='王承恩' ORDER BY ability DESC LIMIT 1"
            ).fetchone()["name"])
            character = game.session._character(actor)

            def audit(phase, payload):
                if phase == "dialogue_directive_fallback":
                    self.assertIn("写成一份可核定文书", str(payload.get("user_text") or ""))
                    return {
                        "allow": True,
                        "subject": "核出本月辽饷实欠并限五日具奏",
                        "directive_text": "",
                        "trigger_quote": "写成一份可核定文书",
                        "private_reason": "test semantic directive fallback",
                        "confidence": 95,
                    }
                return None

            game.session.dialogue_audit_client = audit

            proposed = game._fallback_pending_directive(
                character,
                "照你刚才说的，写成一份可核定文书。",
                "臣以为可令户部先核辽饷。",
            )

            self.assertIsNotNone(proposed)
            self.assertEqual(proposed["status"], "pending")
            self.assertIn("辽饷实欠", proposed["text"])
            self.assertIn("语义审计兜底", proposed["notes"])
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_semantic_directive_fallback_denial_blocks_keyword_fallback(self):
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            actor = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                "AND name!='王承恩' ORDER BY ability DESC LIMIT 1"
            ).fetchone()["name"])
            character = game.session._character(actor)

            def audit(phase, payload):
                if phase == "dialogue_directive_fallback":
                    return {
                        "allow": False,
                        "subject": "",
                        "directive_text": "",
                        "trigger_quote": "这道旨意若颁布会怎样",
                        "private_reason": "只是询问后果，不是要求拟稿。",
                        "confidence": 96,
                    }
                return None

            game.session.dialogue_audit_client = audit

            proposed = game._fallback_pending_directive(
                character,
                "你说，这道旨意若颁布会怎样？",
                "臣以为阻力不小。",
            )

            self.assertIsNone(proposed)
            row = game.db.conn.execute(
                "SELECT COUNT(*) AS n FROM turn_directives WHERE actor=?",
                (actor,),
            ).fetchone()
            self.assertEqual(int(row["n"]), 0)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_propose_directive_tool_requires_semantic_audit_before_pending_write(self):
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            actor = "王承恩"
            character = game.session._character(actor)
            draft_text = "着户部核明辽饷实欠，五日内具奏。"
            tool_draft_text = "着户部即刻加征辽饷，违者重责。"
            calls = []

            class ToolExec:
                tool_name = "propose_directive"
                result = f"__pending_directive__{tool_draft_text}"
                arguments = {}
                tool_args = {}

            class RunOutput:
                content = f"臣已拟旨如下：{tool_draft_text}"
                tools = [ToolExec()]

            class FakeAgent:
                def run(self, _prompt):
                    return RunOutput()

            class FakeRegistry:
                def build_draft_line(self):
                    return "无"

                def get(self, _character):
                    return FakeAgent()

            game.session.registry = FakeRegistry()

            def deny_audit(phase, payload):
                if phase == "dialogue_directive_fallback":
                    calls.append(payload)
                    self.assertIn(tool_draft_text, str(payload.get("npc_answer") or ""))
                    return {
                        "allow": False,
                        "subject": "",
                        "directive_text": "",
                        "trigger_quote": "要不要下旨",
                        "confidence": 96,
                        "private_reason": "玩家只是询问是否下旨，不是要求拟稿。",
                    }
                return None

            game.session.dialogue_audit_client = deny_audit
            result = game.session.chat(actor, "你说，此事要不要下旨？")

            self.assertIsNone(result.proposed_directive)
            self.assertTrue(calls)
            row = game.db.conn.execute(
                "SELECT COUNT(*) AS n FROM turn_directives WHERE actor=? AND text=?",
                (actor, tool_draft_text),
            ).fetchone()
            self.assertEqual(int(row["n"]), 0)

            def allow_audit(phase, payload):
                if phase == "dialogue_directive_fallback":
                    return {
                        "allow": True,
                        "subject": "核明辽饷实欠",
                        "directive_text": draft_text,
                        "trigger_quote": "拟一道旨意",
                        "confidence": 95,
                        "private_reason": "玩家明确要求当前 NPC 拟成可核定旨意。",
                    }
                return None

            game.session.dialogue_audit_client = allow_audit
            result = game.session.chat(actor, "替朕拟一道旨意，命户部核明辽饷实欠。")

            self.assertIsNotNone(result.proposed_directive)
            self.assertEqual(result.proposed_directive.text, draft_text)
            row = game.db.conn.execute(
                "SELECT status, source, actor FROM turn_directives WHERE actor=? AND text=?",
                (actor, draft_text),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["status"], "pending")
            self.assertEqual(row["source"], "大臣拟旨")
            wrong_row = game.db.conn.execute(
                "SELECT COUNT(*) AS n FROM turn_directives WHERE actor=? AND text=?",
                (actor, tool_draft_text),
            ).fetchone()
            self.assertEqual(int(wrong_row["n"]), 0)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_stream_tool_pending_directive_uses_same_semantic_gate(self):
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            actor = "王承恩"
            character = game.session._character(actor)
            draft_text = "着兵部点验京营器械，三日内奏明缺额。"
            tool_draft_text = "着兵部暂缓点验京营器械，诸缺勿问。"
            seen = []

            def deny_audit(phase, payload):
                if phase == "dialogue_directive_fallback":
                    seen.append(payload)
                    return {
                        "allow": False,
                        "subject": "",
                        "directive_text": "",
                        "trigger_quote": "这道旨意若颁布会怎样",
                        "confidence": 96,
                        "private_reason": "假设后果，不是拟稿命令。",
                    }
                return None

            game.session.dialogue_audit_client = deny_audit
            proposed = game._record_tool_pending_directive(
                character,
                "这道旨意若颁布会怎样？",
                tool_draft_text,
                f"臣试拟：{tool_draft_text}",
            )
            self.assertIsNone(proposed)
            self.assertTrue(seen)

            def allow_audit(phase, payload):
                if phase == "dialogue_directive_fallback":
                    self.assertIn(tool_draft_text, str(payload.get("npc_answer") or ""))
                    return {
                        "allow": True,
                        "subject": "点验京营器械",
                        "directive_text": draft_text,
                        "trigger_quote": "拟成可核定草案",
                        "confidence": 96,
                        "private_reason": "明确要求拟稿。",
                    }
                return None

            game.session.dialogue_audit_client = allow_audit
            proposed = game._record_tool_pending_directive(
                character,
                "照你说的，拟成可核定草案。",
                tool_draft_text,
                f"臣试拟：{tool_draft_text}",
            )
            self.assertIsNotNone(proposed)
            self.assertEqual(proposed["text"], draft_text)
            row = game.db.conn.execute(
                "SELECT status FROM turn_directives WHERE actor=? AND text=?",
                (actor, draft_text),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["status"], "pending")
            wrong_row = game.db.conn.execute(
                "SELECT COUNT(*) AS n FROM turn_directives WHERE actor=? AND text=?",
                (actor, tool_draft_text),
            ).fetchone()
            self.assertEqual(int(wrong_row["n"]), 0)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_propose_appointment_tool_requires_semantic_audit_before_write(self):
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            actor = "韩爌"
            target = "顾任免"
            office = "兵部督师"
            character = game.session._character(actor)
            payload = json.dumps(
                {
                    "name": target,
                    "office": office,
                    "faction": "中立",
                    "reason": "测试任免语义门",
                    "recommendation_basis": "测试工具依据；不应越过玩家原话。",
                    "replaces": "",
                },
                ensure_ascii=False,
            )
            calls = []

            class ToolExec:
                tool_name = "propose_appointment"
                result = f"__pending_appointment__{payload}"
                arguments = {}
                tool_args = {}

            class RunOutput:
                content = f"臣以为{target}或可任{office}。"
                tools = [ToolExec()]

            class FakeAgent:
                def run(self, _prompt):
                    return RunOutput()

            class FakeRegistry:
                def build_draft_line(self):
                    return "无"

                def get(self, _character):
                    return FakeAgent()

                def register(self, _character):
                    return None

            game.session.registry = FakeRegistry()

            def deny_audit(phase, audit_payload):
                if phase == "dialogue_action_intent":
                    calls.append(audit_payload)
                    action = audit_payload.get("tool_action") or {}
                    self.assertEqual(action.get("type"), "office_change")
                    self.assertEqual(action.get("target"), target)
                    self.assertEqual(action.get("office"), office)
                    return {
                        "allow": False,
                        "phase": "none",
                        "action_type": "none",
                        "trigger_quote": "谁可任兵部督师",
                        "confidence": 96,
                        "private_reason": "只是请 NPC 推荐人选，不是皇帝任命。",
                    }
                return None

            game.session.dialogue_audit_client = deny_audit
            result = game.session.chat(actor, "你看谁可任兵部督师？")

            self.assertEqual(result.appointed_minister, "")
            self.assertTrue(calls)
            self.assertNotIn(target, game.content.characters)
            row = game.db.conn.execute("SELECT name FROM characters WHERE name=?", (target,)).fetchone()
            self.assertIsNone(row)

            def allow_audit(phase, audit_payload):
                if phase == "dialogue_action_intent":
                    action = audit_payload.get("tool_action") or {}
                    self.assertEqual(action.get("type"), "office_change")
                    return {
                        "allow": True,
                        "phase": "confirm",
                        "action_type": "office_change",
                        "target": target,
                        "trigger_quote": f"任{target}为{office}",
                        "confidence": 95,
                        "private_reason": "玩家明确以皇帝身份任命具体人物到具体官职。",
                    }
                return None

            game.session.dialogue_audit_client = allow_audit
            result = game.session.chat(actor, f"任{target}为{office}，即刻建档。")

            self.assertEqual(result.appointed_minister, target)
            self.assertIn(target, game.content.characters)
            row = game.db.conn.execute(
                "SELECT office, status FROM characters WHERE name=?",
                (target,),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["office"], office)
            self.assertEqual(row["status"], "active")
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_stream_tool_appointment_uses_same_semantic_gate(self):
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            actor = "韩爌"
            target = "顾流任"
            office = "户部主事"
            character = game.session._character(actor)
            payload = json.dumps(
                {
                    "name": target,
                    "office": office,
                    "faction": "中立",
                    "reason": "测试流式任免语义门",
                    "recommendation_basis": "测试工具依据",
                    "replaces": "",
                },
                ensure_ascii=False,
            )
            seen = []

            def deny_audit(phase, audit_payload):
                if phase == "dialogue_action_intent":
                    seen.append(audit_payload)
                    return {
                        "allow": False,
                        "phase": "none",
                        "action_type": "none",
                        "trigger_quote": "可否任他",
                        "confidence": 96,
                        "private_reason": "只是询问可否任命，不是执行口谕。",
                    }
                return None

            game.session.dialogue_audit_client = deny_audit
            appointed, displaced, displaced_effect = game.session._apply_appointment_after_semantic_gate(
                payload,
                character,
                f"你说可否任{target}为{office}？",
                answer=f"臣拟任{target}为{office}。",
            )
            self.assertEqual((appointed, displaced, displaced_effect), ("", "", {}))
            self.assertTrue(seen)
            self.assertNotIn(target, game.content.characters)

            def allow_audit(phase, audit_payload):
                if phase == "dialogue_action_intent":
                    action = audit_payload.get("tool_action") or {}
                    self.assertEqual(action.get("target"), target)
                    self.assertEqual(action.get("office"), office)
                    return {
                        "allow": True,
                        "phase": "confirm",
                        "action_type": "office_change",
                        "target": target,
                        "trigger_quote": f"任{target}为{office}",
                        "confidence": 96,
                        "private_reason": "明确任命。",
                    }
                return None

            game.session.dialogue_audit_client = allow_audit
            appointed, displaced, displaced_effect = game.session._apply_appointment_after_semantic_gate(
                payload,
                character,
                f"任{target}为{office}，着吏部建档。",
                answer=f"臣拟任{target}为{office}。",
            )
            self.assertEqual(appointed, target)
            self.assertEqual(displaced, "")
            self.assertEqual(displaced_effect, {})
            row = game.db.conn.execute(
                "SELECT office, status FROM characters WHERE name=?",
                (target,),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["office"], office)
            self.assertEqual(row["status"], "active")
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_appointment_execution_uses_decision_payload_boundary(self):
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            actor = "韩爌"
            wrong_target = "顾错任"
            approved_target = "顾正任"
            wrong_office = "兵部督师"
            approved_office = "户部主事"
            character = game.session._character(actor)
            payload = json.dumps(
                {
                    "name": wrong_target,
                    "office": wrong_office,
                    "faction": "阉党",
                    "reason": "工具夹带了不该入档的任命。",
                    "recommendation_basis": "错任依据。",
                    "replaces": "",
                },
                ensure_ascii=False,
            )

            def allow_audit(phase, audit_payload):
                if phase != "dialogue_action_intent":
                    return None
                action = audit_payload.get("tool_action") or {}
                self.assertEqual(action.get("type"), "office_change")
                self.assertEqual(action.get("target"), wrong_target)
                self.assertEqual(action.get("office"), wrong_office)
                return {
                    "allow": True,
                    "phase": "confirm",
                    "action_type": "office_change",
                    "target": approved_target,
                    "actor": actor,
                    "confidence": 97,
                    "trigger_quote": f"任{approved_target}为{approved_office}",
                    "private_reason": "审计只准许顾正任任户部主事。",
                    "payload": {
                        "name": approved_target,
                        "office": approved_office,
                        "faction": "中立",
                        "reason": "审计准许的任官口谕。",
                        "recommendation_basis": "玩家原话指定顾正任任户部主事。",
                        "replaces": "",
                    },
                }

            game.session.dialogue_audit_client = allow_audit
            self.assertFalse(
                game.session.dialogue_allows_appointment(
                    character,
                    f"任{approved_target}为{approved_office}，着吏部建档。",
                    payload,
                    answer=f"臣拟任{wrong_target}为{wrong_office}。",
                )
            )
            appointed, displaced, displaced_effect = game.session._apply_appointment_after_semantic_gate(
                payload,
                character,
                f"任{approved_target}为{approved_office}，着吏部建档。",
                answer=f"臣拟任{wrong_target}为{wrong_office}。",
            )

            self.assertEqual(appointed, approved_target)
            self.assertEqual(displaced, "")
            self.assertEqual(displaced_effect, {})
            self.assertNotIn(wrong_target, game.content.characters)
            wrong_row = game.db.conn.execute("SELECT name FROM characters WHERE name=?", (wrong_target,)).fetchone()
            self.assertIsNone(wrong_row)
            row = game.db.conn.execute(
                "SELECT office, faction, status FROM characters WHERE name=?",
                (approved_target,),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["office"], approved_office)
            self.assertEqual(row["faction"], "中立")
            self.assertEqual(row["status"], "active")
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_appointment_requires_trigger_quote_from_current_user_text(self):
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            actor = "韩爌"
            target = "顾幻任"
            office = "兵部督师"
            character = game.session._character(actor)
            payload = json.dumps(
                {
                    "name": target,
                    "office": office,
                    "faction": "中立",
                    "reason": "测试任命触发句必须来自玩家原话。",
                    "recommendation_basis": "审计误报时不应落库。",
                    "replaces": "",
                },
                ensure_ascii=False,
            )

            def allow_with_unsupported_quote(phase, audit_payload):
                if phase != "dialogue_action_intent":
                    return None
                return {
                    "allow": True,
                    "phase": "confirm",
                    "action_type": "office_change",
                    "target": target,
                    "confidence": 97,
                    "trigger_quote": f"任{target}为{office}",
                    "private_reason": "审计误把询问当成任命。",
                }

            game.session.dialogue_audit_client = allow_with_unsupported_quote
            user_text = f"你看谁可任{office}？"
            self.assertFalse(
                game.session.dialogue_allows_appointment(
                    character,
                    user_text,
                    payload,
                    answer=f"臣拟任{target}为{office}。",
                )
            )
            appointed, displaced, displaced_effect = game.session._apply_appointment_after_semantic_gate(
                payload,
                character,
                user_text,
                answer=f"臣拟任{target}为{office}。",
            )

            self.assertEqual((appointed, displaced, displaced_effect), ("", "", {}))
            self.assertNotIn(target, game.content.characters)
            row = game.db.conn.execute("SELECT name FROM characters WHERE name=?", (target,)).fetchone()
            self.assertIsNone(row)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_directive_regex_fallback_ignores_generic_legacy_action_regex(self):
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "1"
        os.environ["MING_SIM_ENABLE_DIALOGUE_REGEX_ACTIONS"] = "1"
        os.environ.pop("MING_SIM_ENABLE_DIALOGUE_DIRECTIVE_REGEX_FALLBACK", None)
        game = web_app.WebGame(fresh=True)
        try:
            actor = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                "AND name!='王承恩' ORDER BY ability DESC LIMIT 1"
            ).fetchone()["name"])
            character = game.session._character(actor)

            proposed = game._fallback_pending_directive(
                character,
                "替朕拟一道旨意，命户部核出本月辽饷实欠，五日内具奏。",
                "臣以为可照此办理。",
            )

            self.assertIsNone(proposed)
            row = game.db.conn.execute(
                "SELECT COUNT(*) AS n FROM turn_directives WHERE actor=?",
                (actor,),
            ).fetchone()
            self.assertEqual(int(row["n"]), 0)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_directive_regex_fallback_requires_dedicated_legacy_opt_in(self):
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "1"
        os.environ["MING_SIM_ENABLE_DIALOGUE_REGEX_ACTIONS"] = "0"
        os.environ["MING_SIM_ENABLE_DIALOGUE_DIRECTIVE_REGEX_FALLBACK"] = "1"
        game = web_app.WebGame(fresh=True)
        try:
            actor = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                "AND name!='王承恩' ORDER BY ability DESC LIMIT 1"
            ).fetchone()["name"])
            character = game.session._character(actor)

            proposed = game._fallback_pending_directive(
                character,
                "替朕拟一道旨意，命户部核出本月辽饷实欠，五日内具奏。",
                "臣以为可照此办理。",
            )

            self.assertIsNotNone(proposed)
            self.assertIn("户部核出本月辽饷实欠", proposed["text"])
            self.assertIn("保守草案", proposed["notes"])
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_semantic_directive_pressure_moves_live_directive_without_keyword_gate(self):
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            actor = "袁崇焕"
            did = game.db.add_directive(
                game.state,
                None,
                "令袁崇焕整顿辽东军饷。",
                "test",
                actor=actor,
                status="confirmed",
            )
            game.db.conn.execute(
                "UPDATE turn_directives SET assignee=?, lifecycle_status='executing', "
                "progress=40, exec_days=10, eta_day=12, anomaly=?, chain=? WHERE id=?",
                (
                    actor,
                    json.dumps({"kind": "delay"}, ensure_ascii=False),
                    json.dumps({"resistance": 45, "chain": []}, ensure_ascii=False),
                    did,
                ),
            )
            game.db.conn.commit()

            def audit(phase, payload):
                if phase != "dialogue_directive_pressure":
                    return {"allow": False, "kind": "none", "confidence": 100}
                self.assertEqual(int((payload.get("directive_context") or {}).get("id")), did)
                return {
                    "allow": True,
                    "kind": "pressed",
                    "forceful": True,
                    "trigger_quote": "把这件差使压实",
                    "answer_evidence": "臣即日具奏",
                    "confidence": 96,
                }

            game.session.dialogue_audit_client = audit

            effect = game._directive_chat_effect(
                actor,
                {"kind": "directive", "ref_id": did},
                "把这件差使压实。",
                "臣即日具奏，三日内交清册。",
            )
            progress = int(game.db.conn.execute(
                "SELECT progress FROM turn_directives WHERE id=?", (did,)
            ).fetchone()["progress"])

            self.assertEqual(effect["kind"], "pressed")
            self.assertEqual(effect["progress_delta"], 6)
            self.assertEqual(progress, 46)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_directive_pressure_helper_preserves_semantic_decision_boundary(self):
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            actor = "袁崇焕"
            did = game.db.add_directive(
                game.state,
                None,
                "令袁崇焕整顿辽东军饷。",
                "test",
                actor=actor,
                status="confirmed",
            )
            game.db.conn.execute(
                "UPDATE turn_directives SET assignee=?, lifecycle_status='executing', "
                "progress=40, exec_days=10, eta_day=12, anomaly=?, chain=? WHERE id=?",
                (
                    actor,
                    json.dumps({"kind": "delay"}, ensure_ascii=False),
                    json.dumps({"resistance": 45, "chain": []}, ensure_ascii=False),
                    did,
                ),
            )
            game.db.conn.commit()

            def audit(phase, payload):
                if phase == "dialogue_directive_pressure":
                    return {
                        "allow": True,
                        "kind": "pressed",
                        "forceful": True,
                        "trigger_quote": "把这件差使压实",
                        "answer_evidence": "臣即日具奏",
                        "confidence": 96,
                    }
                return None

            game.session.dialogue_audit_client = audit

            decision = game._directive_audience_pressure_decision(
                actor,
                did,
                {"kind": "directive", "ref_id": did},
                "把这件差使压实。",
                "臣即日具奏，三日内交清册。",
            )
            review = game._directive_audience_pressure_review(
                actor,
                did,
                {"kind": "directive", "ref_id": did},
                "把这件差使压实。",
                "臣即日具奏，三日内交清册。",
            )

            self.assertIsInstance(decision, SemanticDecision)
            self.assertTrue(decision.allow)
            self.assertEqual(decision.action_type, "directive_pressure")
            self.assertEqual(decision.kind, "pressed")
            self.assertTrue(review.get("allow"))
            self.assertEqual(review.get("kind"), "pressed")
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_semantic_directive_pressure_denial_blocks_keyword_fallback(self):
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            actor = "袁崇焕"
            did = game.db.add_directive(
                game.state,
                None,
                "令袁崇焕整顿辽东军饷。",
                "test",
                actor=actor,
                status="confirmed",
            )
            game.db.conn.execute(
                "UPDATE turn_directives SET assignee=?, lifecycle_status='executing', "
                "progress=40, exec_days=10, eta_day=12, anomaly=?, chain=? WHERE id=?",
                (
                    actor,
                    json.dumps({"kind": "delay"}, ensure_ascii=False),
                    json.dumps({"resistance": 45, "chain": []}, ensure_ascii=False),
                    did,
                ),
            )
            game.db.conn.commit()

            game.session.dialogue_audit_client = lambda phase, payload: {  # type: ignore[assignment]
                "allow": False,
                "kind": "none",
                "confidence": 95,
                "private_reason": "玩家只是闲谈，没有形成御前催办。",
            }

            effect = game._directive_chat_effect(
                actor,
                {"kind": "directive", "ref_id": did},
                "朕问你进度，欠饷实数到底办到几分？",
                "臣遵旨，即日清册具奏，三日内交账，不敢再误。",
            )
            progress = int(game.db.conn.execute(
                "SELECT progress FROM turn_directives WHERE id=?", (did,)
            ).fetchone()["progress"])

            self.assertEqual(effect, {})
            self.assertEqual(progress, 40)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_directive_pressure_without_semantic_review_does_not_use_keyword_fallback(self):
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
        os.environ.pop("MING_SIM_ENABLE_DIALOGUE_DIRECTIVE_REGEX_FALLBACK", None)
        game = web_app.WebGame(fresh=True)
        try:
            actor = "袁崇焕"
            did = game.db.add_directive(
                game.state,
                None,
                "令袁崇焕整顿辽东军饷。",
                "test",
                actor=actor,
                status="confirmed",
            )
            game.db.conn.execute(
                "UPDATE turn_directives SET assignee=?, lifecycle_status='executing', "
                "progress=40, exec_days=10, eta_day=12, anomaly=?, chain=? WHERE id=?",
                (
                    actor,
                    json.dumps({"kind": "delay"}, ensure_ascii=False),
                    json.dumps({"resistance": 45, "chain": []}, ensure_ascii=False),
                    did,
                ),
            )
            game.db.conn.commit()
            game.session.llm_config.api_key = ""

            effect = game._directive_chat_effect(
                actor,
                {"kind": "directive", "ref_id": did},
                "朕问你进度，欠饷实数到底办到几分？",
                "臣遵旨，即日清册具奏，三日内交账，不敢再误。",
            )
            progress = int(game.db.conn.execute(
                "SELECT progress FROM turn_directives WHERE id=?", (did,)
            ).fetchone()["progress"])

            self.assertEqual(effect, {})
            self.assertEqual(progress, 40)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_injected_directive_pressure_audit_runs_without_api_key(self):
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
        os.environ.pop("MING_SIM_ENABLE_DIALOGUE_DIRECTIVE_REGEX_FALLBACK", None)
        game = web_app.WebGame(fresh=True)
        try:
            actor = "袁崇焕"
            did = game.db.add_directive(
                game.state,
                None,
                "令袁崇焕整顿辽东军饷。",
                "test",
                actor=actor,
                status="confirmed",
            )
            game.db.conn.execute(
                "UPDATE turn_directives SET assignee=?, lifecycle_status='executing', "
                "progress=40, exec_days=10, eta_day=12, anomaly=?, chain=? WHERE id=?",
                (
                    actor,
                    json.dumps({"kind": "delay"}, ensure_ascii=False),
                    json.dumps({"resistance": 45, "chain": []}, ensure_ascii=False),
                    did,
                ),
            )
            game.db.conn.commit()
            game.session.llm_config.api_key = ""
            calls = []

            def audit(phase, payload):
                if phase != "dialogue_directive_pressure":
                    return None
                calls.append(payload)
                return {
                    "allow": True,
                    "kind": "pressed",
                    "forceful": True,
                    "trigger_quote": "把这件差使压实",
                    "answer_evidence": "三日内交清册",
                    "confidence": 96,
                }

            game.session.dialogue_audit_client = audit

            effect = game._directive_chat_effect(
                actor,
                {"kind": "directive", "ref_id": did},
                "把这件差使压实。",
                "臣即日具奏，三日内交清册。",
            )
            progress = int(game.db.conn.execute(
                "SELECT progress FROM turn_directives WHERE id=?", (did,)
            ).fetchone()["progress"])

            self.assertTrue(calls)
            self.assertEqual(effect["kind"], "pressed")
            self.assertEqual(progress, 46)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_semantic_done_directive_followup_reward_without_keyword_gate(self):
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            actor = "袁崇焕"
            game.db.conn.execute(
                "UPDATE characters SET emp_trust=50, grievance=30 WHERE name=?",
                (actor,),
            )
            did = game.db.add_directive(
                game.state,
                None,
                "令袁崇焕整顿辽东军饷。",
                "test",
                actor=actor,
                status="confirmed",
            )
            game.db.conn.execute(
                "UPDATE turn_directives SET assignee=?, lifecycle_status='done', "
                "progress=100, integrity_actual=90, integrity_reported=92, "
                "outcome_status='applied', chain=? WHERE id=?",
                (
                    actor,
                    json.dumps({"resistance": 20, "chain": []}, ensure_ascii=False),
                    did,
                ),
            )
            game.db.conn.commit()

            def audit(phase, payload):
                if phase != "dialogue_directive_followup":
                    return {"allow": False, "kind": "none", "confidence": 100}
                self.assertEqual(int((payload.get("directive_context") or {}).get("id")), did)
                return {
                    "allow": True,
                    "kind": "rewarded",
                    "trigger_quote": "入清班旧账",
                    "answer_evidence": "臣谢恩",
                    "confidence": 96,
                }

            game.session.dialogue_audit_client = audit

            effect = game._directive_chat_effect(
                actor,
                {"kind": "directive", "ref_id": did},
                "这件差使可入清班旧账。",
                "臣谢恩，愿守此案。",
            )
            ch = game.db.conn.execute(
                "SELECT emp_trust, grievance FROM characters WHERE name=?",
                (actor,),
            ).fetchone()
            row = game.db.conn.execute(
                "SELECT lifecycle_status, progress, chain FROM turn_directives WHERE id=?",
                (did,),
            ).fetchone()

            self.assertEqual(effect["kind"], "rewarded")
            self.assertEqual(int(ch["emp_trust"]), 54)
            self.assertEqual(int(ch["grievance"]), 27)
            self.assertEqual(str(row["lifecycle_status"]), "done")
            self.assertEqual(int(row["progress"]), 100)
            self.assertEqual(json.loads(row["chain"])["last_followup_action"]["kind"], "rewarded")
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_semantic_done_directive_followup_denial_blocks_keyword_fallback(self):
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
        game = web_app.WebGame(fresh=True)
        try:
            actor = "袁崇焕"
            game.db.conn.execute(
                "UPDATE characters SET emp_trust=50, grievance=30 WHERE name=?",
                (actor,),
            )
            did = game.db.add_directive(
                game.state,
                None,
                "令袁崇焕整顿辽东军饷。",
                "test",
                actor=actor,
                status="confirmed",
            )
            game.db.conn.execute(
                "UPDATE turn_directives SET assignee=?, lifecycle_status='done', "
                "progress=100, integrity_actual=90, integrity_reported=92, "
                "outcome_status='applied', chain=? WHERE id=?",
                (
                    actor,
                    json.dumps({"resistance": 20, "chain": []}, ensure_ascii=False),
                    did,
                ),
            )
            game.db.conn.commit()

            def audit(phase, payload):
                if phase != "dialogue_directive_followup":
                    return {"allow": False, "kind": "none", "confidence": 100}
                return {
                    "allow": False,
                    "kind": "none",
                    "trigger_quote": "复命",
                    "answer_evidence": "",
                    "confidence": 95,
                    "private_reason": "只是泛谈奖罚，没有形成复命处置。",
                }

            game.session.dialogue_audit_client = audit

            effect = game._directive_chat_effect(
                actor,
                {"kind": "directive", "ref_id": did},
                "朕看了你的复命，此事办得有功，准记功嘉奖。",
                "臣不敢居功，愿继续清册具奏。",
            )
            ch = game.db.conn.execute(
                "SELECT emp_trust, grievance FROM characters WHERE name=?",
                (actor,),
            ).fetchone()
            chain = json.loads(game.db.conn.execute(
                "SELECT chain FROM turn_directives WHERE id=?",
                (did,),
            ).fetchone()["chain"])

            self.assertEqual(effect, {})
            self.assertEqual(int(ch["emp_trust"]), 50)
            self.assertEqual(int(ch["grievance"]), 30)
            self.assertNotIn("last_followup_action", chain)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_bargain_regex_fallback_ignores_generic_legacy_action_regex(self):
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "1"
        os.environ["MING_SIM_ENABLE_DIALOGUE_REGEX_ACTIONS"] = "1"
        os.environ.pop("MING_SIM_ENABLE_DIALOGUE_BARGAIN_REGEX_FALLBACK", None)
        game = web_app.WebGame(fresh=True)
        try:
            actor = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                "AND name!='王承恩' ORDER BY ability DESC LIMIT 1"
            ).fetchone()["name"])
            game.db.conn.execute(
                "UPDATE characters SET emp_trust=40, grievance=60 WHERE name=?",
                (actor,),
            )
            game.db.conn.commit()

            effect = game._bargain_chat_effect(
                actor,
                {"kind": "petition", "actor": actor, "title": "求展限办差"},
                "准，朕暂且护持你，给你人手。",
                "臣叩谢天恩。",
            )

            self.assertEqual(effect, {})
            after = game.db.conn.execute(
                "SELECT emp_trust, grievance FROM characters WHERE name=?",
                (actor,),
            ).fetchone()
            self.assertEqual(int(after["emp_trust"]), 40)
            self.assertEqual(int(after["grievance"]), 60)
            self.assertEqual(game.db.list_conversation_goals(minister_name=actor), [])
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_audience_bargain_press_and_refuse_have_distinct_deltas(self):
        game = web_app.WebGame(fresh=True)
        try:
            actor = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                "AND name!='王承恩' ORDER BY ability DESC LIMIT 1"
            ).fetchone()["name"])
            context = {"kind": "agenda", "actor": actor, "title": "求借势办事"}
            def audit(phase, payload):
                if phase != "dialogue_bargain_attitude":
                    return None
                text = str(payload.get("user_text") or "")
                if "账册" in text:
                    return {
                        "allow": True,
                        "attitude": "press",
                        "trigger_quote": "先拿出账册和担保",
                        "private_reason": "test semantic bargain press",
                        "confidence": 95,
                    }
                if "不准" in text:
                    return {
                        "allow": True,
                        "attitude": "refuse",
                        "trigger_quote": "不准，此事驳回",
                        "private_reason": "test semantic bargain refuse",
                        "confidence": 95,
                    }
                return {"allow": False, "attitude": "none", "confidence": 95}

            os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "0"
            game.session.dialogue_audit_client = audit

            game.db.conn.execute(
                "UPDATE characters SET emp_trust=50, grievance=50 WHERE name=?",
                (actor,),
            )
            game.db.conn.commit()
            press = game._bargain_chat_effect(
                actor,
                context,
                "先拿出账册和担保，限期三日再说。",
                "臣谨遵。",
            )
            self.assertEqual(press["title"], "御前索证")
            goals = game.db.list_conversation_goals(minister_name=actor, statuses=["waiting_conditions"])
            self.assertEqual(len(goals), 1)
            self.assertIn("御前索证", str(goals[0]["title"]))
            self.assertTrue(any("账册" in str(item.get("description") or "") for item in goals[0]["conditions"]))
            agreements = game.db.list_negotiation_agreements(
                minister_name=actor,
                action_kind="audience_bargain",
                status="pending",
            )
            self.assertEqual(len(agreements), 1)
            self.assertEqual(agreements[0]["promise_type"], "御前索证")
            after_press = game.db.conn.execute(
                "SELECT emp_trust, grievance FROM characters WHERE name=?",
                (actor,),
            ).fetchone()
            self.assertEqual(int(after_press["emp_trust"]), 51)
            self.assertEqual(int(after_press["grievance"]), 52)

            game.db.conn.execute(
                "UPDATE characters SET emp_trust=50, grievance=50 WHERE name=?",
                (actor,),
            )
            game.db.conn.commit()
            refused = game._bargain_chat_effect(
                actor,
                context,
                "不准，此事驳回。",
                "臣不敢再言。",
            )
            self.assertEqual(refused["title"], "御前拒请")
            self.assertEqual(
                len(game.db.list_conversation_goals(minister_name=actor, statuses=["waiting_conditions"])),
                1,
            )
            after_refused = game.db.conn.execute(
                "SELECT emp_trust, grievance FROM characters WHERE name=?",
                (actor,),
            ).fetchone()
            self.assertEqual(int(after_refused["emp_trust"]), 48)
            self.assertEqual(int(after_refused["grievance"]), 55)
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_audience_bargain_followup_creates_obligation_and_rolls_back(self):
        game = web_app.WebGame(fresh=True)
        try:
            from ming_sim.context import build_npc_monthly_followups

            actor = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                "AND name!='王承恩' ORDER BY ability DESC LIMIT 1"
            ).fetchone()["name"])
            game.db.conn.execute(
                "UPDATE characters SET emp_trust=52, grievance=54 WHERE name=?",
                (actor,),
            )
            game.db.conn.commit()
            chat_turn_id, before_snapshot = game._start_chat_turn(actor)
            context = {
                "kind": "bargain",
                "actor": actor,
                "title": f"条件待证：{actor}",
                "ref_kind": "memory",
                "ref_id": "42",
            }
            game.session.dialogue_audit_client = self._bargain_attitude_audit(attitude="press")

            effect = game._bargain_chat_effect(
                actor,
                context,
                "先拿出账册和担保，限期三日再说。",
                "臣谨遵。",
                chat_turn_id,
            )
            game._record_chat_rollback_items(chat_turn_id, before_snapshot)

            labels = {str(item["label"]) for item in effect["effects"]}
            self.assertIn("履约账本：旧账索证入账", labels)
            goals = game.db.list_conversation_goals(minister_name=actor, statuses=["waiting_conditions"])
            self.assertEqual(len(goals), 1)
            goal = goals[0]
            self.assertEqual(goal["action_kind"], "audience_bargain")
            self.assertIn("旧账索证", str(goal["title"]))
            self.assertEqual(int(goal["expires_turn"]), int(game.state.turn) + 3)
            self.assertTrue(any("账册" in str(item.get("description") or "") for item in goal["conditions"]))
            agreements = game.db.list_negotiation_agreements(
                minister_name=actor,
                action_kind="audience_bargain",
                status="pending",
            )
            self.assertEqual(len(agreements), 1)
            self.assertEqual(agreements[0]["promise_type"], "旧账索证")
            self.assertEqual(int(agreements[0]["due_turn"]), int(game.state.turn) + 2)
            followups = build_npc_monthly_followups(game.db, game.state, limit=8)
            actor_followup = next(item for item in followups if str(item.get("minister_name") or "") == actor)
            self.assertIn("bargain_followup", actor_followup["reason_types"])
            self.assertTrue(any("御前旧账" in str(tag) for tag in actor_followup["risk_tags"]))

            game.db.undo_chat_turn(chat_turn_id)
            self.assertEqual(game.db.list_conversation_goals(minister_name=actor), [])
            self.assertEqual(
                game.db.list_negotiation_agreements(minister_name=actor, action_kind="audience_bargain"),
                [],
            )
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_audience_bargain_card_routes_to_context_brief(self):
        game = web_app.WebGame(fresh=True)
        try:
            actor = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                "AND name!='王承恩' ORDER BY ability DESC LIMIT 1"
            ).fetchone()["name"])
            memory_id = game.db.upsert_event_memory(
                game.state,
                "character",
                actor,
                "audience_bargain",
                "御前拒请",
                cause="求护持政敌旧案",
                process="不准，此事驳回。",
                outcome="拒其所求；信任 50->48，怨望 50->55",
                sentiment="negative",
                importance=4,
                tags=["奏对交易", "refuse"],
                source_kind="chat_turn",
                source_id="unit-route",
            )
            context = {
                "kind": "bargain",
                "actor": actor,
                "ref_kind": "memory",
                "ref_id": str(memory_id),
                "title": "拒请余波",
            }

            brief = game._chat_context_brief(actor, context)
            mismatch = game._chat_context_brief("王承恩", context)

            self.assertIn("本次召对事项：御前旧账", brief)
            self.assertIn("御前拒请", brief)
            self.assertIn("重新给台阶", brief)
            self.assertEqual(mismatch, "")
        finally:
            try:
                from ming_sim.scheduler import stop_worker
                stop_worker(game.db_path)
            finally:
                game.session.close()


if __name__ == "__main__":
    unittest.main()
