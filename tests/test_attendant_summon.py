"""Attending eunuch summon commands should switch the mobile audience target deterministically."""

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import web_app
from ming_sim import court, court_events, memorials
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
                "MING_SIM_DISABLE_LLM_QUICK_SUGGESTIONS",
                "MING_SIM_ENABLE_LOCAL_QUICK_SUGGESTIONS",
                "MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT",
                "MING_SIM_DISABLE_DIALOGUE_ROUTE_LLM_AUDIT",
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
        os.environ["MING_SIM_ENABLE_DIALOGUE_REGEX_ACTIONS"] = "1"
        os.environ["MING_SIM_ENABLE_DIALOGUE_REGEX_SUMMONS"] = "1"
        os.environ["MING_SIM_ENABLE_DIALOGUE_ANSWER_SUMMON_FALLBACK"] = "1"
        os.environ["MING_SIM_DISABLE_LLM_QUICK_SUGGESTIONS"] = "1"
        os.environ.pop("MING_SIM_ENABLE_LOCAL_QUICK_SUGGESTIONS", None)
        os.environ["MING_SIM_DISABLE_DIALOGUE_ACTION_LLM_AUDIT"] = "1"
        os.environ["MING_SIM_DISABLE_DIALOGUE_ROUTE_LLM_AUDIT"] = "1"

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
        game = web_app.WebGame(fresh=True)
        try:
            attendant = "王承恩"
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
            game.db.conn.execute(
                "UPDATE characters SET emp_trust=55, grievance=20 WHERE name=?",
                (name,),
            )
            game.db.conn.commit()

            proposal_events = list(game.chat_stream(
                attendant,
                f"把{name}净身入内廷，净军房行事，铜柄宫刀，无麻，宝油炸封蜡，收黄杨木描金匣。",
            ))
            self.assertEqual(proposal_events[-1]["type"], "done")
            self.assertIn("若准", proposal_events[-1]["payload"]["answer"])
            self.assertIn("方案画像", proposal_events[-1]["payload"]["answer"])
            self.assertIn("调养成本", proposal_events[-1]["payload"]["answer"])
            self.assertIn("差遣风险", proposal_events[-1]["payload"]["answer"])
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
            self.assertIn("内廷旧档", payload["answer"])
            self.assertNotIn("强旨净身", payload["dialogue_effect"]["title"])
            self.assertIn("方案画像", payload["answer"])
            self.assertIn("差遣风险", payload["answer"])
            effect_labels = {str(item.get("label") or "") for item in payload["dialogue_effect"].get("effects", [])}
            self.assertTrue(any("方案：酷烈高危" in label for label in effect_labels))
            self.assertTrue(any("后续调养成本+4" in label for label in effect_labels))
            minister = game.public_character(game.content.characters[name])
            self.assertEqual(minister["office"], "司礼监随堂太监")
            self.assertEqual(minister["office_type"], "司礼监")
            castration = minister["castration"]
            self.assertTrue(castration["forced"])
            self.assertEqual(castration["method_label"], "净军房夜割")
            self.assertEqual(castration["knife_label"], "铜柄宫刀")
            self.assertEqual(castration["anesthesia_label"], "无麻，冷汗硬熬")
            self.assertEqual(castration["bao_weight_label"], "约二两八钱")
            self.assertEqual(castration["bao_shape_label"], "一大一小")
            self.assertEqual(castration["bao_texture_label"], "油封后发硬")
            self.assertEqual(castration["preservation_label"], "油炸封蜡")
            self.assertEqual(castration["container_label"], "黄杨木描金匣")
            self.assertIn("漏尿", castration["urine_label"])
            self.assertIn("嗓音尖薄", castration["voice_body_label"])
            self.assertIn("幻肢痛", castration["trauma_label"])
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
            self.assertIn("惊创未平", trait_names)
            self.assertIn("尿路旧患", trait_names)
            self.assertIn("情欲异化", trait_names)
            inventory_ids = {str(item["id"]) for item in game.db.list_player_inventory()}
            self.assertIn(f"内廷旧档：{name}", inventory_ids)
            self.assertIn(f"官库旧匣：{name}", inventory_ids)
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
            text = f"只是聊聊{name}若净身入内廷的旧例，不是要办，别惊动净军房。"

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

    def test_unlisted_registration_summon_requires_route_semantic_audit(self):
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
                return None

            game.session.dialogue_audit_client = audit

            response = game._dialogue_route_response(actor, "准，去调停。")

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
            self.assertTrue(any("匣藏：黑漆楠木匣" in label for label in labels))
            self.assertTrue(any("封存：油炸封蜡" in label for label in labels))
            self.assertTrue(any("尿路：" in label and "尿闭" in label for label in labels))
            self.assertTrue(any("新增特质" in label and "尿路旧患" in label for label in labels))
            self.assertTrue(payload["history"][-1].get("stage_directions"))
            stage_text = " ".join(payload["history"][-1]["stage_directions"])
            self.assertTrue("钥匙" in stage_text or "夹腰" in stage_text)

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
                scheme_text="净军房无麻，宝油炸封蜡；近来漏尿尿闭，嗓音尖薄，幻肢痛。",
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
            self.assertIn("内库-7", payload["dialogue_effect"]["message"])
            self.assertIn("方案调养+4", payload["dialogue_effect"]["message"])
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
            self.assertEqual(game.state.metrics["内库"], 73)
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

    def test_eunuch_old_wound_goal_surfaces_decision_suggestions(self):
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
                scheme_text="净军房无麻；近来漏尿尿闭，夜里小解不畅。",
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
            self.assertIn(payload["dialogue_effect"]["title"], {"宝案查验", "奏对有动"})
            labels = {str(item.get("label") or "") for item in payload["dialogue_effect"].get("effects", [])}
            self.assertTrue(any("匣藏：锡胆小木匣" in label for label in labels))
            self.assertTrue(any("封存：香料腌藏" in label for label in labels))
            self.assertTrue(any("入库：宝案安置" in label for label in labels))

            castration = game.public_character(game.content.characters[name])["castration"]
            self.assertEqual(castration["container_label"], "锡胆小木匣")
            self.assertEqual(castration["preservation_label"], "香料腌藏")
            self.assertEqual(castration["ritual_label"], "夜半验匣，钥匙贴身")
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
            self.assertEqual(payload["dialogue_effect"]["title"], "赐还宝匣")
            self.assertIn("信任+3", payload["dialogue_effect"]["message"])
            self.assertIn("怨望-6", payload["dialogue_effect"]["message"])
            self.assertIn("筹码值40", payload["dialogue_effect"]["message"])
            labels = {str(item.get("label") or "") for item in payload["dialogue_effect"].get("effects", [])}
            self.assertTrue(any("御赐宝匣" in label for label in labels))
            self.assertTrue(any("宝案筹码40" in label for label in labels))
            self.assertTrue(any("体面安置越足" in label for label in labels))
            self.assertTrue(any("宝匣：锡胆小木匣" in label for label in labels))
            castration = game.public_character(game.content.characters[name])["castration"]
            self.assertEqual(castration["bao_status"], "kept")
            self.assertEqual(castration["container_label"], "锡胆小木匣")
            self.assertEqual(castration["preservation_label"], "香料腌藏")
            self.assertIn("赐还", castration["ritual_label"])
            inventory_ids = {str(item["id"]) for item in game.db.list_player_inventory()}
            self.assertIn(f"御赐宝匣：{name}", inventory_ids)
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
                for marker in ("垂手", "夹腰", "嗓音", "失神", "钥匙", "宝匣", "肩背")
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
                scheme_text="净军房行事，铜柄宫刀，无麻；宝约二两八钱，一大一小，油封后发硬，油炸封蜡，收黄杨木描金匣。",
            )
            minister = result["minister"]

            self.assertEqual(minister["office"], "司礼监随堂太监")
            self.assertEqual(minister["office_type"], "司礼监")
            self.assertEqual(minister["faction"], "内廷")
            self.assertIn("castration", minister)
            castration = minister["castration"]
            self.assertTrue(castration["forced"])
            self.assertIn("强阉", castration["bao_label"])
            self.assertTrue(castration["knife_label"])
            self.assertTrue(castration["anesthesia_label"])
            self.assertTrue(castration["urine_label"])
            self.assertTrue(castration["voice_body_label"])
            self.assertTrue(castration["trauma_label"])
            self.assertTrue(castration["fixation_label"])
            self.assertEqual(castration["method_label"], "净军房夜割")
            self.assertEqual(castration["knife_label"], "铜柄宫刀")
            self.assertEqual(castration["anesthesia_label"], "无麻，冷汗硬熬")
            self.assertEqual(castration["bao_weight_label"], "约二两八钱")
            self.assertEqual(castration["bao_shape_label"], "一大一小")
            self.assertEqual(castration["bao_texture_label"], "油封后发硬")
            self.assertEqual(castration["preservation_label"], "油炸封蜡")
            self.assertEqual(castration["container_label"], "黄杨木描金匣")

            refreshed = game.public_character(game.content.characters[name])
            self.assertEqual(refreshed["office"], "司礼监随堂太监")
            self.assertEqual(refreshed["castration"]["bao_status"], "forfeit")
            self.assertTrue(any(tag["label"] == "内廷奴籍" for tag in refreshed["identity_tags"]))
            self.assertTrue(any(tag["label"] == "内廷旧档" for tag in refreshed["identity_tags"]))
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

            result = game._absorb_eunuch_lore_from_text(
                attendant,
                "请把王承恩的宝匣改用黑漆楠木匣，油炸封蜡，钥匙贴身，记入旧档。",
            )

            self.assertIn("updated", result)
            lore = el.get_lore(game.db, attendant)
            self.assertEqual(str(lore["bao_container"] or ""), "黑漆楠木匣")
            self.assertEqual(str(lore["bao_preservation"] or ""), "油炸封蜡")
            self.assertEqual(str(lore["bao_ritual"] or ""), "夜半验匣，钥匙贴身")
        finally:
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
                scheme_text="净军房行事，铜柄宫刀，无麻；宝约一两二钱，圆缩成团，石灰封后发白，官库石灰封存，收白签灰瓮。",
            )
            game.db.conn.execute("DELETE FROM character_traits WHERE name=?", (name,))
            game.db.conn.execute(
                "DELETE FROM player_inventory WHERE item_id IN (?, ?, ?)",
                (f"内廷旧档：{name}", f"官库旧匣：{name}", f"旧匣遗失：{name}"),
            )
            game.db.conn.commit()
            before = game.public_character(game.content.characters[name])["castration"]
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
            self.assertEqual(scheme_review["tier"], "酷烈高危")
            self.assertGreaterEqual(int(scheme_review["risk_score"]), 72)
            effect = game._eunuch_lore_dialogue_effect(result)
            effect_labels = {str(item.get("label") or "") for item in effect.get("effects", [])}
            self.assertTrue(any("旧制复盘：酷烈高危" in label for label in effect_labels))
            game._record_chat_rollback_items(chat_turn_id, snapshot)

            after = game.public_character(game.content.characters[name])["castration"]
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
            self.assertIn("尿路旧患", after_traits - before_traits)
            self.assertIn("情欲异化", after_traits - before_traits)
            after_inventory = {str(item["id"]) for item in game.db.list_player_inventory()}
            self.assertIn(f"内廷旧档：{name}", after_inventory - before_inventory)
            after_stats = game.db.conn.execute(
                "SELECT emp_trust, grievance, ability, luck FROM characters WHERE name=?",
                (name,),
            ).fetchone()
            self.assertLessEqual(int(after_stats["emp_trust"]), int(before_stats["emp_trust"]))
            self.assertGreater(int(after_stats["grievance"]), int(before_stats["grievance"]))

            game.db.undo_chat_turn(chat_turn_id)
            restored = game.public_character(game.content.characters[name])["castration"]
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
            castration = minister["castration"]
            self.assertEqual(castration["psychosexual_label"], "")
            self.assertNotIn("癖性", castration["condition_line"])
            trait_names = {
                str(r["trait"])
                for r in game.db.conn.execute("SELECT trait FROM character_traits WHERE name=?", (name,)).fetchall()
            }
            self.assertIn("尿路旧患", trait_names)
            self.assertIn("惊创未平", trait_names)
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
            before_speaker = game.public_character(game.content.characters[speaker])["castration"]

            result = game._absorb_eunuch_lore_from_text(
                speaker,
                f"{target}的宝匣改用黄杨木描金匣，香料腌藏。近来结石尿闭，按肩会僵住，已有性无能。",
            )

            self.assertEqual(result["updated_targets"], [target])
            after_target = game.public_character(game.content.characters[target])["castration"]
            self.assertEqual(after_target["container_label"], "黄杨木描金匣")
            self.assertEqual(after_target["preservation_label"], "香料腌藏")
            self.assertIn("尿闭", after_target["urine_label"])
            self.assertIn("按肩", after_target["trauma_label"])
            self.assertIn("性无能", after_target["psychosexual_label"])
            after_speaker = game.public_character(game.content.characters[speaker])["castration"]
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

    def test_audience_bargain_press_and_refuse_have_distinct_deltas(self):
        game = web_app.WebGame(fresh=True)
        try:
            actor = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                "AND name!='王承恩' ORDER BY ability DESC LIMIT 1"
            ).fetchone()["name"])
            context = {"kind": "agenda", "actor": actor, "title": "求借势办事"}

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
