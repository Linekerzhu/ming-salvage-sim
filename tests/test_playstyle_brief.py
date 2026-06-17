"""Strategic briefing cards surface existing gameplay hooks without LLM calls."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import timeflow
from ming_sim.db import GameDB
from ming_sim.intrigue import ensure_schema as ensure_secret_schema
from ming_sim.playstyle import briefing_cards, briefing_payload


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


if __name__ == "__main__":
    unittest.main()
