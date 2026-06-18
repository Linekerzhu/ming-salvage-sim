"""Attending eunuch summon commands should switch the mobile audience target deterministically."""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import web_app
from ming_sim import court, memorials
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

            self.assertIn("问私心", labels)
            self.assertIn("问政敌", labels)
            self.assertIn("点旧恩", labels)
            self.assertIn("拟旨", labels)
            self.assertIn("下密令", labels)
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

    def test_legacy_context_is_actor_scoped_and_reaches_dialogue_prep(self):
        game = web_app.WebGame(fresh=True)
        try:
            actor = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                "AND (office LIKE '%户部%' OR office_type LIKE '%户部%') "
                "ORDER BY ability DESC LIMIT 1"
            ).fetchone()["name"])
            other = str(game.db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' AND name!=? "
                "ORDER BY ability DESC LIMIT 1",
                (actor,),
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
                "ref_kind": "legacy",
                "ref_id": str(legacy_id),
            }

            brief = game._chat_context_brief(actor, context)
            mismatch = game._chat_context_brief(other, context)
            augmented, prepared = game.session.prepare_chat_run(
                game.content.characters[actor],
                "这项旧政仍在拖动朝局，朕要听善后。",
                supplemental_context=brief,
            )

            self.assertIn("本次召对事项：长期政策余波", brief)
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
            mismatch = game._chat_context_brief(other, context)
            augmented, prepared = game.session.prepare_chat_run(
                game.content.characters[actor],
                "朕闻你近来动作渐密，今日说清楚。",
                supplemental_context=brief,
            )

            self.assertIn("本次召对事项：人物私图将成", brief)
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


if __name__ == "__main__":
    unittest.main()
