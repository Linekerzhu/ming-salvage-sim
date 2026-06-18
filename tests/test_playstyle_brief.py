"""Strategic briefing cards surface existing gameplay hooks without LLM calls."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import court, court_events, lifecycle, memorials, timeflow
from ming_sim.db import GameDB
from ming_sim.intrigue import ensure_schema as ensure_secret_schema
from ming_sim.playstyle import (
    _brief_kind_buckets,
    _select_brief_cards,
    agenda_chat_context_brief,
    briefing_cards,
    briefing_payload,
    favor_chat_context_brief,
    legacy_chat_context_brief,
    monthly_followup_chat_context_brief,
    patronage_chat_context_brief,
    petition_chat_context_brief,
)
from ming_sim.upgrade_schema import KV_CURRENT_DAY, KV_RISK_AVERSION, kv_set_int


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    timeflow.ensure_active(db, state)
    return db, state


def _active_minister(db: GameDB) -> str:
    row = db.conn.execute(
        "SELECT name FROM characters "
        "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
        "ORDER BY ability DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    return str(row["name"])


class PlaystyleBriefTests(unittest.TestCase):
    def test_brief_buckets_are_sorted_by_top_urgency(self):
        candidates = [
            {"kind": "faction", "title": "党争升温", "urgency": 72},
            {"kind": "army", "title": "边镇离心", "urgency": 84},
            {"kind": "hook", "title": "大案把柄", "urgency": 96},
            {"kind": "faction", "title": "党援串联", "urgency": 91},
        ]

        buckets = _brief_kind_buckets(candidates, selected=candidates[:2])

        self.assertEqual([b["kind"] for b in buckets], ["hook", "faction", "army"])
        self.assertEqual(buckets[0]["top_urgency"], 96)
        self.assertEqual(buckets[1]["rank_label"], "危")
        self.assertEqual(buckets[1]["rank_count"], 1)

    def test_brief_selection_preserves_system_diversity(self):
        cards = [
            {"kind": "hook", "title": "把柄甲", "urgency": 100},
            {"kind": "hook", "title": "把柄乙", "urgency": 99},
            {"kind": "hook", "title": "把柄丙", "urgency": 98},
            {"kind": "army", "title": "辽镇离心", "urgency": 96},
            {"kind": "faction", "title": "东林坐大", "urgency": 95},
            {"kind": "decision", "title": "请陛下裁断", "urgency": 100},
        ]

        picked = _select_brief_cards(cards, limit=4)
        kinds = [str(c["kind"]) for c in picked]
        self.assertEqual(kinds[0], "decision")
        self.assertIn("army", kinds)
        self.assertIn("faction", kinds)
        self.assertLessEqual(kinds.count("hook"), 1)

    def test_brief_payload_reports_hidden_card_count(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            ensure_secret_schema(db)
            db.conn.execute("DELETE FROM secrets")
            names = [
                str(r["name"]) for r in db.conn.execute(
                    "SELECT name FROM characters "
                    "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                    "ORDER BY ability DESC LIMIT 3"
                ).fetchall()
            ]
            for i, name in enumerate(names):
                db.conn.execute(
                    "INSERT INTO secrets(holder, kind, detail, severity, known_to_crown, used) "
                    "VALUES (?, '贪墨', '收受边饷回扣', ?, 1, 0)",
                    (name, 80 - i),
                )
            db.conn.commit()

            payload = briefing_payload(db, state, limit=1)
            self.assertEqual(payload["shown"], 1)
            self.assertGreaterEqual(payload["total"], 3)
            self.assertEqual(payload["hidden"], int(payload["total"]) - 1)
            self.assertGreater(payload["hidden"], 0)
            self.assertEqual(len(payload["cards"]), 1)
            self.assertIsNotNone(payload["lead"])
            self.assertEqual(payload["lead"], payload["cards"][0])
            buckets = {str(b["kind"]): b for b in payload["buckets"]}
            self.assertIn("hook", buckets)
            self.assertEqual(buckets["hook"]["label"], "把柄")
            self.assertEqual(buckets["hook"]["shown"], 1)
            self.assertGreaterEqual(buckets["hook"]["total"], 3)
            self.assertEqual(buckets["hook"]["hidden"], int(buckets["hook"]["total"]) - 1)
            self.assertEqual(buckets["hook"]["rank_level"], "danger")
            self.assertEqual(buckets["hook"]["rank_label"], "危")
            self.assertGreaterEqual(buckets["hook"]["rank_count"], 3)

    def test_brief_payload_filters_cards_by_system_kind(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            ensure_secret_schema(db)
            db.conn.execute("DELETE FROM secrets")
            names = [
                str(r["name"]) for r in db.conn.execute(
                    "SELECT name FROM characters "
                    "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                    "ORDER BY ability DESC LIMIT 3"
                ).fetchall()
            ]
            for i, name in enumerate(names):
                db.conn.execute(
                    "INSERT INTO secrets(holder, kind, detail, severity, known_to_crown, used) "
                    "VALUES (?, '贪墨', '收受边饷回扣', ?, 1, 0)",
                    (name, 85 - i),
                )
            db.conn.commit()

            payload = briefing_payload(db, state, limit=2, kind="hook")
            self.assertEqual(payload["filter"], "hook")
            self.assertEqual(payload["shown"], 2)
            self.assertGreaterEqual(payload["total"], 3)
            self.assertEqual(payload["hidden"], int(payload["total"]) - 2)
            self.assertTrue(all(c["kind"] == "hook" for c in payload["cards"]))
            self.assertEqual(payload["lead"]["kind"], "hook")
            self.assertEqual(payload["lead"], payload["cards"][0])
            buckets = {str(b["kind"]): b for b in payload["buckets"]}
            self.assertGreaterEqual(buckets["hook"]["total"], 3)
            ranks = {str(r["level"]): r for r in payload["ranks"]}
            self.assertGreaterEqual(ranks["danger"]["count"], 3)
            self.assertEqual(ranks["danger"]["label"], "危局")

    def test_pending_decision_card_surfaces_stakes(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            names = [
                str(r["name"]) for r in db.conn.execute(
                    "SELECT name FROM characters "
                    "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                    "LIMIT 2"
                ).fetchall()
            ]
            a, b = names
            court._set_opinion(db, a, b, -75, "夙仇", 1)
            court._set_opinion(db, b, a, -75, "夙仇", 1)
            memorials.create_memorial(
                db,
                state,
                day=1,
                author_name=a,
                org="都察院",
                kind="弹章",
                urgency=3,
                summary=f"{a}劾{b}",
                ref_kind="character",
                ref_id=b,
            )
            court_events.evaluate_decisions(db, state, 1)

            cards = briefing_cards(db, state, limit=8)
            decision = next(c for c in cards if c["kind"] == "decision")
            labels = [str(e["label"]) for e in decision["effects"]]
            self.assertEqual(decision["tab"], "desk")
            self.assertEqual(decision["meta"], "4路待决")
            self.assertIn("牵动君威", labels)
            self.assertIn("牵动任事", labels)
            self.assertIn("信怨变化", labels)

    def test_done_directive_creates_followup_brief_card(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            name = _active_minister(db)
            db.conn.execute(
                """
                INSERT INTO turn_directives
                    (turn, year, period, text, source, status, lifecycle_status,
                     progress, assignee, integrity_actual, integrity_reported,
                     settle_note, outcome_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    state.turn,
                    state.year,
                    state.period,
                    "敕曰：着户部清核辽饷旧账，三日内具册以闻。",
                    "test",
                    "issued",
                    "done",
                    100,
                    name,
                    88,
                    92,
                    "臣谨奏：辽饷旧账已清出大概。",
                    "applied",
                ),
            )
            db.conn.commit()

            payload = briefing_payload(db, state, limit=5, kind="directive_followup")
            self.assertEqual(payload["filter"], "directive_followup")
            self.assertGreaterEqual(payload["total"], 1)
            card = payload["cards"][0]
            self.assertEqual(card["kind"], "directive_followup")
            self.assertEqual(card["actor"], name)
            self.assertEqual(card["tab"], "audience")
            self.assertEqual(card["cta"], "召主办")
            self.assertIn("复命后续", card["title"])
            labels = [str(e["label"]) for e in card["effects"]]
            self.assertIn("已复命", labels)
            self.assertIn("结果落库", labels)
            buckets = {str(b["kind"]): b for b in payload["buckets"]}
            self.assertEqual(buckets["directive_followup"]["label"], "复命")

    def test_done_directive_followup_card_disappears_after_followup_action(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            name = _active_minister(db)
            db.conn.execute(
                """
                INSERT INTO turn_directives
                    (turn, year, period, text, source, status, lifecycle_status,
                     progress, assignee, integrity_actual, integrity_reported,
                     settle_note, outcome_status, chain)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    state.turn,
                    state.year,
                    state.period,
                    "敕曰：着户部清核辽饷旧账，三日内具册以闻。",
                    "test",
                    "issued",
                    "done",
                    100,
                    name,
                    88,
                    92,
                    "臣谨奏：辽饷旧账已清出大概。",
                    "applied",
                    json.dumps(
                        {"last_followup_action": {"kind": "rewarded", "minister": name, "day": 3}},
                        ensure_ascii=False,
                    ),
                ),
            )
            db.conn.commit()

            payload = briefing_payload(db, state, limit=5, kind="directive_followup")

            self.assertEqual(payload["cards"], [])
            self.assertEqual(payload["total"], 0)

    def test_done_directive_with_report_gap_is_urgent_followup(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            name = _active_minister(db)
            db.conn.execute(
                """
                INSERT INTO turn_directives
                    (turn, year, period, text, source, status, lifecycle_status,
                     progress, assignee, integrity_actual, integrity_reported,
                     settle_note, outcome_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    state.turn,
                    state.year,
                    state.period,
                    "敕曰：着兵部整顿京营欠饷，毋得稽延。",
                    "test",
                    "issued",
                    "done",
                    100,
                    name,
                    52,
                    91,
                    "臣谨奏：京营诸事大体就绪。",
                    "extracted",
                ),
            )
            db.conn.commit()

            cards = briefing_cards(db, state, limit=8)
            card = next(c for c in cards if c["kind"] == "directive_followup")
            self.assertEqual(card["tone"], "danger")
            self.assertIn("复命需追问", card["title"])
            self.assertGreaterEqual(int(card["urgency"]), 90)
            labels = [str(e["label"]) for e in card["effects"]]
            self.assertIn("水分 39", labels)
            self.assertIn("实绩 52%", labels)

    def test_grievance_creates_active_petition_card(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            name = _active_minister(db)
            rival = str(db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' AND name!=? "
                "LIMIT 1",
                (name,),
            ).fetchone()["name"])
            db.conn.execute(
                "UPDATE characters SET emp_trust=22, grievance=82 WHERE name=?",
                (name,),
            )
            court._set_opinion(db, name, rival, -70, "夺功旧怨", 1)
            db.conn.commit()

            payload = briefing_payload(db, state, limit=5, kind="petition")

            self.assertEqual(payload["filter"], "petition")
            self.assertGreaterEqual(payload["total"], 1)
            petition = next(c for c in payload["cards"] if c["kind"] == "petition" and c["actor"] == name)
            self.assertEqual(petition["tab"], "audience")
            self.assertEqual(petition["cta"], "召来听诉")
            self.assertEqual(petition["target"], rival)
            self.assertIn("求见", str(petition["title"]))
            labels = [str(e["label"]) for e in petition["effects"]]
            self.assertIn("信任 22", labels)
            self.assertIn("怨望 82", labels)
            self.assertIn(f"政敌 {rival}", labels)

    def test_petition_chat_context_brief_rebuilds_from_live_db(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            del state
            name = _active_minister(db)
            rival = str(db.conn.execute(
                "SELECT name FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' AND name!=? "
                "LIMIT 1",
                (name,),
            ).fetchone()["name"])
            db.conn.execute(
                "UPDATE characters SET emp_trust=24, grievance=80, faction='东林' WHERE name=?",
                (name,),
            )
            court._set_opinion(db, name, rival, -76, "夺功旧怨", 1)
            db.conn.commit()

            brief = petition_chat_context_brief(db, name, target=rival)

            self.assertIn("本次召对事项：主动求援请托", brief)
            self.assertIn("不是普通被动问策", brief)
            self.assertIn("御前信任 24", brief)
            self.assertIn("怨望 80", brief)
            self.assertIn(rival, brief)
            self.assertIn("夺功旧怨", brief)
            self.assertIn("不要直接落库", brief)

    def test_monthly_followup_becomes_audience_brief_card(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            name = _active_minister(db)
            state.turn = 1
            db.create_secret_order(
                state,
                name,
                "密查阉党旧案",
                "查魏忠贤余党旧案。",
                ["魏忠贤", "阉党"],
                deadline_months=1,
            )
            state.turn = 2
            state.period = 2
            db.save_state(state)

            payload = briefing_payload(db, state, limit=5, kind="monthly_followup")

            self.assertEqual(payload["filter"], "monthly_followup")
            self.assertGreaterEqual(payload["total"], 1)
            card = next(c for c in payload["cards"] if c["kind"] == "monthly_followup" and c["actor"] == name)
            self.assertEqual(card["tab"], "audience")
            self.assertEqual(card["cta"], "召来请安")
            self.assertEqual(card["ref_kind"], "monthly_followup")
            self.assertIn("候见", str(card["title"]))
            self.assertIn("密令", str(card["meta"]))
            labels = [str(e["label"]) for e in card["effects"]]
            self.assertIn("密令到期", labels)
            self.assertIn("密令回奏", labels)
            buckets = {str(b["kind"]): b for b in payload["buckets"]}
            self.assertEqual(buckets["monthly_followup"]["label"], "候见")

    def test_monthly_followup_chat_context_brief_rebuilds_from_live_db(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            name = _active_minister(db)
            state.turn = 1
            db.create_secret_order(
                state,
                name,
                "密查阉党旧案",
                "查魏忠贤余党旧案。",
                ["魏忠贤", "阉党"],
                deadline_months=1,
            )
            state.turn = 2
            state.period = 2
            db.save_state(state)

            brief = monthly_followup_chat_context_brief(db, name)

            self.assertIn("本次召对事项：本月主动候见", brief)
            self.assertIn("不是普通被动问策", brief)
            self.assertIn("密查阉党旧案", brief)
            self.assertIn("密令到期", brief)
            self.assertIn("主动复命或诉难处", brief)
            self.assertIn("不要给无成本完美答案", brief)

    def test_recommendation_bond_becomes_patronage_brief_card(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            sponsor = _active_minister(db)
            candidate = str(db.conn.execute(
                """
                SELECT name
                FROM characters
                WHERE status='active'
                  AND power_id='ming'
                  AND office_type!='后宫'
                  AND name!=?
                LIMIT 1
                """,
                (sponsor,),
            ).fetchone()["name"])
            db.conn.execute(
                "UPDATE characters SET office='待铨（举贤入京）', office_type='待铨', summary=? WHERE name=?",
                (f"由地方举荐入京。举荐来源：{sponsor}；风险：初入朝局，仍受举主关系牵引。", candidate),
            )
            court.adjust_opinion(db, sponsor, candidate, +28, "举荐入朝", day=1, reciprocal=False)
            court.adjust_opinion(db, candidate, sponsor, +34, "举主恩义", day=1, reciprocal=False)

            payload = briefing_payload(db, state, limit=5, kind="patronage")

            self.assertEqual(payload["filter"], "patronage")
            self.assertGreaterEqual(payload["total"], 1)
            card = next(c for c in payload["cards"] if c["kind"] == "patronage")
            self.assertEqual(card["tab"], "audience")
            self.assertEqual(card["actor"], sponsor)
            self.assertEqual(card["target"], candidate)
            self.assertEqual(card["cta"], "问举主")
            self.assertIn("举主担保", str(card["title"]))
            labels = [str(e["label"]) for e in card["effects"]]
            self.assertIn(f"举主 {sponsor}", labels)
            self.assertIn(f"新人 {candidate}", labels)
            self.assertIn("举主关系 28", labels)
            buckets = {str(b["kind"]): b for b in payload["buckets"]}
            self.assertEqual(buckets["patronage"]["label"], "举主")

    def test_patronage_chat_context_differs_for_sponsor_and_candidate(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            del state
            sponsor = _active_minister(db)
            candidate = str(db.conn.execute(
                """
                SELECT name
                FROM characters
                WHERE status='active'
                  AND power_id='ming'
                  AND office_type!='后宫'
                  AND name!=?
                LIMIT 1
                """,
                (sponsor,),
            ).fetchone()["name"])
            db.conn.execute(
                "UPDATE characters SET office='待铨（举贤入京）', office_type='待铨', summary=? WHERE name=?",
                (f"由地方举荐入京。举荐来源：{sponsor}；风险：初入朝局，仍受举主关系牵引。", candidate),
            )
            court.adjust_opinion(db, sponsor, candidate, +28, "举荐入朝", day=1, reciprocal=False)
            court.adjust_opinion(db, candidate, sponsor, +34, "举主恩义", day=1, reciprocal=False)

            sponsor_brief = patronage_chat_context_brief(db, sponsor, target=candidate)
            candidate_brief = patronage_chat_context_brief(db, candidate, target=sponsor)

            self.assertIn("本次召对事项：举主担保", sponsor_brief)
            self.assertIn("当前入对者是举主", sponsor_brief)
            self.assertIn("愿用什么名节或差事担保", sponsor_brief)
            self.assertIn("当前入对者是新人", candidate_brief)
            self.assertIn("证明自己不是", candidate_brief)
            self.assertIn("不要把荐人写成免费人才池", candidate_brief)

    def test_active_tax_legacy_becomes_policy_aftershock_card(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            legacy_id = db.insert_legacy(
                state,
                name="苛税余波：辽饷",
                modifiers={"民心": -9},
                narrative_hint="辽饷加派已入常例；钱粮见长，民心恢复受压。",
                duration_months=-1,
                legacy_key="directive_tax:7:辽饷",
            )

            payload = briefing_payload(db, state, limit=5, kind="legacy")

            self.assertEqual(payload["filter"], "legacy")
            self.assertGreaterEqual(payload["total"], 1)
            card = next(c for c in payload["cards"] if c["ref_id"] == str(legacy_id))
            self.assertEqual(card["tab"], "audience")
            self.assertEqual(card["cta"], "召人问余波")
            self.assertTrue(card["actor"])
            self.assertEqual(card["meta"], "永久")
            self.assertIn("政策余波", str(card["title"]))
            labels = [str(e["label"]) for e in card["effects"]]
            self.assertIn("民心 -9%", labels)
            self.assertIn("永久", labels)

            brief = legacy_chat_context_brief(db, str(card["actor"]), legacy_id)
            self.assertIn("本次召对事项：长期政策余波", brief)
            self.assertIn("苛税余波：辽饷", brief)
            self.assertIn("不是新政空谈", brief)
            self.assertIn("民心 -9%", brief)
            self.assertIn("有代价的善后方案", brief)

    def test_agenda_near_maturity_becomes_audience_hook(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            name = _active_minister(db)
            db.conn.execute(
                "INSERT OR REPLACE INTO npc_agendas "
                "(name, kind, title, target_name, intensity, status, progress) "
                "VALUES (?, 'enrich', '自肥', '', 92, 'active', 91)",
                (name,),
            )
            db.conn.commit()

            cards = briefing_cards(db, state, limit=5)
            agenda = next(c for c in cards if c["kind"] == "agenda" and c["actor"] == name)
            self.assertEqual(agenda["tab"], "audience")
            self.assertIn("自肥", str(agenda["title"]))
            self.assertGreaterEqual(int(agenda["urgency"]), 90)
            labels = [str(e["label"]) for e in agenda["effects"]]
            self.assertIn("进度 91%", labels)
            self.assertIn("强度 92", labels)
            self.assertIn("自肥敛财", labels)

    def test_agenda_chat_context_brief_rebuilds_from_live_db(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            del state
            name = _active_minister(db)
            db.conn.execute(
                "INSERT OR REPLACE INTO npc_agendas "
                "(name, kind, title, target_name, intensity, status, progress) "
                "VALUES (?, 'enrich', '自肥', '', 92, 'active', 88)",
                (name,),
            )
            db.conn.commit()

            brief = agenda_chat_context_brief(db, name)

            self.assertIn("本次召对事项：人物私图将成", brief)
            self.assertIn("不是普通被动问策", brief)
            self.assertIn("自肥敛财", brief)
            self.assertIn("推进到 88%", brief)
            self.assertIn("强度 92", brief)
            self.assertIn("御前信任", brief)
            self.assertIn("钱粮/请托风闻", brief)
            self.assertIn("首次追问不直接落库", brief)

    def test_rivalry_card_surfaces_opinion_and_basis(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            names = [
                str(r["name"]) for r in db.conn.execute(
                    "SELECT name FROM characters "
                    "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                    "LIMIT 2"
                ).fetchall()
            ]
            a, b = names
            court._set_opinion(db, a, b, -80, "夙仇", 1)
            court._set_opinion(db, b, a, -80, "夙仇", 1)
            db.conn.commit()

            cards = briefing_cards(db, state, limit=8)
            rivalry = next(c for c in cards if c["kind"] == "rivalry" and c["actor"] == a)
            self.assertEqual(rivalry["tab"], "audience")
            self.assertEqual(rivalry["target"], b)
            labels = [str(e["label"]) for e in rivalry["effects"]]
            self.assertIn("关系 -80", labels)
            self.assertIn("夙仇", labels)
            self.assertIn("可借力/可失控", labels)

    def test_army_autonomy_becomes_realm_hook(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            army = db.conn.execute(
                "SELECT id FROM armies WHERE owner_power='ming' ORDER BY id LIMIT 1"
            ).fetchone()
            assert army is not None
            db.conn.execute(
                "UPDATE armies SET autonomy=76, arrears=maintenance_per_turn*4 WHERE id=?",
                (str(army["id"]),),
            )
            db.conn.commit()

            cards = briefing_cards(db, state, limit=5)
            army_card = next(c for c in cards if c["kind"] == "army")
            self.assertEqual(army_card["tab"], "realm")
            self.assertIn("离心", str(army_card["title"]) + str(army_card["detail"]))
            self.assertEqual(army_card["tone"], "danger")
            labels = [str(e["label"]) for e in army_card["effects"]]
            self.assertIn("离心 76", labels)
            self.assertIn("欠饷 4.0月", labels)
            self.assertIn("无监军制衡", labels)

    def test_known_secret_becomes_hook_card(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            name = _active_minister(db)
            ensure_secret_schema(db)
            db.conn.execute("DELETE FROM secrets WHERE holder=?", (name,))
            db.conn.execute(
                "INSERT INTO secrets(holder, kind, detail, severity, known_to_crown, used) "
                "VALUES (?, '贪墨', '收受边饷回扣', 82, 1, 0)",
                (name,),
            )
            db.conn.commit()

            payload = briefing_payload(db, state, limit=5)
            hook = next(c for c in payload["cards"] if c["kind"] == "hook" and c["actor"] == name)
            self.assertEqual(hook["tab"], "audience")
            self.assertIn("把柄在手", str(hook["title"]))
            self.assertGreaterEqual(int(hook["urgency"]), 90)
            labels = [str(e["label"]) for e in hook["effects"]]
            self.assertIn("贪墨", labels)
            self.assertIn("严重 82", labels)
            self.assertIn("未动用", labels)

    def test_faction_pressure_names_summonable_representative(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            row = db.conn.execute(
                "SELECT name, faction FROM characters "
                "WHERE status='active' AND power_id='ming' AND office_type!='后宫' "
                "AND faction NOT IN ('无','中立','') "
                "ORDER BY ability DESC LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(row)
            faction = str(row["faction"])
            db.conn.execute(
                "UPDATE factions SET leverage=92, satisfaction=18 WHERE name=?",
                (faction,),
            )
            db.conn.commit()

            cards = briefing_cards(db, state, limit=8)
            faction_card = next(c for c in cards if c["kind"] == "faction" and c["ref_id"] == faction)
            self.assertEqual(faction_card["tab"], "desk")
            self.assertTrue(faction_card["actor"])
            representative = db.conn.execute(
                "SELECT faction, status, power_id FROM characters WHERE name=?",
                (str(faction_card["actor"]),),
            ).fetchone()
            self.assertIsNotNone(representative)
            self.assertEqual(str(representative["faction"]), faction)
            self.assertEqual(str(representative["status"]), "active")
            self.assertEqual(str(representative["power_id"]), "ming")
            self.assertIn(faction, str(faction_card["title"]))
            labels = [str(e["label"]) for e in faction_card["effects"]]
            self.assertIn("势力 92", labels)
            self.assertIn("怨气 82", labels)
            self.assertTrue(any(label.startswith("代表 ") for label in labels), labels)

    def test_high_risk_aversion_becomes_trap_card(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            kv_set_int(db, KV_RISK_AVERSION, 72)

            cards = briefing_cards(db, state, limit=8)
            trap = next(c for c in cards if c["kind"] == "trap")
            self.assertEqual(trap["tab"], "desk")
            self.assertEqual(trap["ref_kind"], "belief")
            self.assertIn("百官避事", str(trap["title"]))
            self.assertIn("任事28", str(trap["meta"]))
            self.assertIn("买单", str(trap["detail"]))

    def test_punished_official_becomes_trap_remedy_card(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            result = memorials.punish_official(db, state, "韩爌", "heavy", day=1, reason="试问责")
            self.assertTrue(result["ok"], result)
            kv_set_int(db, KV_RISK_AVERSION, 70)

            cards = briefing_cards(db, state, limit=8)
            remedy = next(c for c in cards if c["kind"] == "trap_remedy")
            self.assertEqual(remedy["actor"], "韩爌")
            self.assertEqual(remedy["ref_kind"], "character")
            self.assertEqual(remedy["ref_id"], "韩爌")
            self.assertIn("复用", str(remedy["title"]) + str(remedy["detail"]))
            self.assertIn("可复用", str(remedy["meta"]))
            labels = [str(e["label"]) for e in remedy["effects"]]
            self.assertIn("任事 +10", labels)
            self.assertIn("势 -2", labels)
            self.assertIn("复归在朝", labels)

    def test_trap_remedy_card_previews_faction_and_network_costs(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            db.conn.execute(
                "UPDATE characters SET status='active', emp_trust=65, grievance=20 "
                "WHERE power_id='ming' AND office_type!='后宫'"
            )
            db.conn.execute(
                "UPDATE characters SET emp_trust=20, grievance=70 WHERE name='韩爌'"
            )
            names = [str(r["name"]) for r in db.conn.execute(
                "SELECT name FROM characters WHERE status='active' AND power_id='ming' "
                "AND office_type!='后宫' AND name!='韩爌' LIMIT 2"
            ).fetchall()]
            ally, rival = names[0], names[1]
            db.conn.execute("DELETE FROM relationships WHERE a_name='韩爌'")
            court._set_opinion(db, "韩爌", ally, 70, "党附", 1)
            court._set_opinion(db, "韩爌", rival, -70, "政敌", 1)
            db.conn.commit()
            kv_set_int(db, KV_RISK_AVERSION, 70)

            cards = briefing_cards(db, state, limit=8)
            remedy = next(c for c in cards if c["kind"] == "trap_remedy")

            labels = [str(e["label"]) for e in remedy["effects"]]
            self.assertEqual(remedy["actor"], "韩爌")
            self.assertIn("任事 +8", labels)
            self.assertIn("势 -4", labels)
            self.assertTrue(any("满意" in label for label in labels), labels)
            self.assertIn("党羽受慰 1人", labels)
            self.assertIn("政敌侧目 1人", labels)

    def test_recently_backed_official_drops_from_trap_remedy(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            db.conn.execute(
                "UPDATE characters SET status='active', emp_trust=65, grievance=20 "
                "WHERE power_id='ming' AND office_type!='后宫'"
            )
            db.conn.execute(
                "UPDATE characters SET emp_trust=20, grievance=70 WHERE name='韩爌'"
            )
            db.conn.commit()
            kv_set_int(db, KV_RISK_AVERSION, 70)

            cards = briefing_cards(db, state, limit=8)
            remedy = next(c for c in cards if c["kind"] == "trap_remedy")
            self.assertEqual(remedy["actor"], "韩爌")

            result = memorials.back_official(db, state, "韩爌", "shoulder", day=1)
            self.assertTrue(result["ok"], result)
            kv_set_int(db, KV_RISK_AVERSION, 70)

            cards = briefing_cards(db, state, limit=8)
            self.assertFalse(
                any(c["kind"] == "trap_remedy" and c["actor"] == "韩爌" for c in cards),
                cards,
            )

    def test_imperial_favor_memory_becomes_audience_hook(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            result = memorials.back_official(db, state, "韩爌", "comfort", day=1)
            self.assertTrue(result["ok"], result)

            payload = briefing_payload(db, state, limit=5, kind="favor")

            self.assertEqual(payload["filter"], "favor")
            self.assertGreaterEqual(payload["total"], 1)
            card = next(c for c in payload["cards"] if c["actor"] == "韩爌")
            self.assertEqual(card["kind"], "favor")
            self.assertEqual(card["tab"], "audience")
            self.assertEqual(card["cta"], "召来还恩")
            self.assertEqual(card["meta"], "1笔")
            self.assertEqual(card["ref_kind"], "memory")
            self.assertIn("旧恩未报", str(card["title"]))
            labels = [str(e["label"]) for e in card["effects"]]
            self.assertIn("旧恩 1笔", labels)
            self.assertTrue(any(label.startswith("信任 ") for label in labels), labels)

            brief = favor_chat_context_brief(db, "韩爌", card["ref_id"])
            self.assertIn("本次召对事项：旧恩未报", brief)
            self.assertIn("曾受皇帝保全/复用", brief)
            self.assertIn("不直接落库", brief)
            self.assertIn("求赏", brief)

    def test_overdue_memorials_become_trap_card(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            kv_set_int(db, KV_CURRENT_DAY, 45)
            for idx in range(8):
                memorials.create_memorial(
                    db,
                    state,
                    day=1,
                    author_name="韩爌",
                    org="内阁",
                    kind="弹章" if idx == 0 else "请旨",
                    urgency=3,
                    summary=f"请裁积压事务{idx}",
                )
            db.conn.execute("UPDATE memorials SET arrived_day=15 WHERE status='pending'")
            db.conn.commit()

            cards = briefing_cards(db, state, limit=8)
            trap = next(c for c in cards if c["kind"] == "trap")
            self.assertEqual(trap["tab"], "desk")
            self.assertEqual(trap["tone"], "danger")
            self.assertIn("御案壅塞", str(trap["title"]))
            self.assertIn("七日内将淹没", str(trap["detail"]))
            self.assertIn("待8", str(trap["meta"]))
            labels = [str(e["label"]) for e in trap["effects"]]
            self.assertIn("待裁 8", labels)
            self.assertIn("将淹没 8", labels)
            self.assertIn("最久 30日", labels)

    def test_directive_blocker_becomes_edicts_hook(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            cur = db.conn.execute(
                "INSERT INTO turn_directives (turn, year, period, text, source, status, actor) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    state.turn,
                    state.year,
                    state.period,
                    "令袁崇焕整顿辽东军饷，十日内具奏欠饷实数与裁汰方案。",
                    "test",
                    "confirmed",
                    "",
                ),
            )
            did = int(cur.lastrowid)
            chain = {
                "resistance": 45,
                "chain": [],
                "blocker_clue": {
                    "kind": "person",
                    "name": "温体仁",
                    "label": "温体仁",
                    "detail": "礼部侍郎 · 东林",
                },
            }
            db.conn.execute(
                """
                UPDATE turn_directives
                SET assignee=?, lifecycle_status='stalled', category='military_ops',
                    progress=40, lead_days=0, exec_days=10, start_day=1, eta_day=11,
                    chain=?, anomaly=?
                WHERE id=?
                """,
                (
                    "袁崇焕",
                    json.dumps(chain, ensure_ascii=False),
                    json.dumps({"kind": "block"}, ensure_ascii=False),
                    did,
                ),
            )
            db.conn.commit()

            cards = briefing_cards(db, state, limit=8)
            blocker = next(c for c in cards if c["kind"] == "directive_blocker")
            self.assertEqual(blocker["tab"], "edicts")
            self.assertEqual(blocker["actor"], "温体仁")
            self.assertEqual(blocker["target"], "袁崇焕")
            self.assertEqual(blocker["ref_kind"], "directive")
            self.assertEqual(blocker["ref_id"], str(did))
            self.assertEqual(blocker["tone"], "danger")
            self.assertIn("卡住旨意", str(blocker["title"]))
            self.assertIn("召问阻力", str(blocker["detail"]))
            self.assertGreaterEqual(int(blocker["urgency"]), 90)
            labels = [str(e["label"]) for e in blocker["effects"]]
            self.assertIn("进度 40%", labels)
            self.assertIn("已卡住", labels)
            self.assertIn("阻力 温体仁", labels)
            self.assertIn("主办 袁崇焕", labels)

    def test_handled_directive_blocker_drops_from_home_brief(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            cur = db.conn.execute(
                "INSERT INTO turn_directives (turn, year, period, text, source, status, actor) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    state.turn,
                    state.year,
                    state.period,
                    "令袁崇焕整顿辽东军饷。",
                    "test",
                    "confirmed",
                    "",
                ),
            )
            did = int(cur.lastrowid)
            chain = {
                "resistance": 45,
                "chain": [],
                "blocker_clue": {
                    "kind": "person",
                    "name": "温体仁",
                    "label": "温体仁",
                    "detail": "礼部侍郎 · 东林",
                    "day": 1,
                },
            }
            db.conn.execute(
                """
                UPDATE turn_directives
                SET assignee=?, lifecycle_status='stalled', progress=40, chain=?, anomaly=?
                WHERE id=?
                """,
                (
                    "袁崇焕",
                    json.dumps(chain, ensure_ascii=False),
                    json.dumps({"kind": "block"}, ensure_ascii=False),
                    did,
                ),
            )
            db.conn.commit()
            self.assertTrue(any(c["kind"] == "directive_blocker" for c in briefing_cards(db, state, limit=8)))

            result = lifecycle.intervene(db, state, did, "pressure_blocker", day=2)
            self.assertTrue(result["ok"], result)
            cards = briefing_cards(db, state, limit=8)
            self.assertFalse(
                any(c["kind"] == "directive_blocker" and c["ref_id"] == str(did) for c in cards),
                cards,
            )


if __name__ == "__main__":
    unittest.main()
