"""Strategic briefing cards surface existing gameplay hooks without LLM calls."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import court, court_events, lifecycle, memorials, timeflow
from ming_sim.db import GameDB
from ming_sim.intrigue import ensure_schema as ensure_secret_schema
from ming_sim.playstyle import _select_brief_cards, briefing_cards, briefing_payload
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
            buckets = {str(b["kind"]): b for b in payload["buckets"]}
            self.assertIn("hook", buckets)
            self.assertEqual(buckets["hook"]["label"], "把柄")
            self.assertEqual(buckets["hook"]["shown"], 1)
            self.assertGreaterEqual(buckets["hook"]["total"], 3)
            self.assertEqual(buckets["hook"]["hidden"], int(buckets["hook"]["total"]) - 1)

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
