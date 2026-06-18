"""Attending eunuch summon commands should switch the mobile audience target deterministically."""

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

            self.assertIn("问私心", labels)
            self.assertIn("设交易", labels)
            self.assertIn("问政敌", labels)
            self.assertIn("点旧恩", labels)
            self.assertIn("拟旨", labels)
            self.assertIn("下密令", labels)
            self.assertIn("保门生故旧", texts["问私心"])
            self.assertIn("举荐连坐担保", texts["设交易"])
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

            self.assertEqual(suggestions[0]["label"], "追旧约")
            self.assertIn("三日内清查粮台", suggestions[0]["text"])
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
            before_names = {
                str(row["name"])
                for row in game.db.conn.execute("SELECT name FROM characters").fetchall()
            }

            proposal_events = list(game.chat_stream(attendant, "宫中有没有新的太监可用？"))
            self.assertEqual(proposal_events[-1]["type"], "done")
            self.assertIn("若准", proposal_events[-1]["payload"]["answer"])
            mid_names = {
                str(row["name"])
                for row in game.db.conn.execute("SELECT name FROM characters").fetchall()
            }
            self.assertEqual(mid_names, before_names)

            confirm_events = list(game.chat_stream(attendant, "准，就招一个。"))
            self.assertEqual(confirm_events[-1]["type"], "done")
            payload = confirm_events[-1]["payload"]
            recruited = str(payload.get("recruited_minister") or "")
            self.assertTrue(recruited)
            self.assertNotIn(recruited, before_names)
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
            self.assertIn("交易入记忆", {str(item["label"]) for item in effect["effects"]})
            after = game.db.conn.execute(
                "SELECT emp_trust, grievance FROM characters WHERE name=?",
                (actor,),
            ).fetchone()
            self.assertEqual(int(after["emp_trust"]), 44)
            self.assertEqual(int(after["grievance"]), 56)
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
            self.assertEqual(
                game.db.list_conversation_goals(minister_name=actor, statuses=["waiting_conditions"]),
                [],
            )
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
                game.db.list_conversation_goals(minister_name=actor, statuses=["waiting_conditions"]),
                [],
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
