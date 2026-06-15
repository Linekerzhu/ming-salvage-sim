"""NPC 持续私人目标（CK3 化 P8）测试：私心逐月累进、满则成局兑现。零 LLM。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import ambition, timeflow
from ming_sim.db import GameDB


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    timeflow.ensure_active(db, state)
    return db, state


def _set_agenda(db, name, kind, intensity=90, progress=0):
    db.conn.execute(
        "INSERT OR REPLACE INTO npc_agendas (name, kind, title, target_name, intensity, status, progress) "
        "VALUES (?,?,?,'',?, 'active', ?)", (name, kind, kind, intensity, progress))
    db.conn.commit()


def _active_ming(db, n=1):
    return [r["name"] for r in db.conn.execute(
        "SELECT name FROM characters WHERE status='active' AND power_id='ming' AND office_type!='后宫' LIMIT ?", (n,))]


class ProgressTests(unittest.TestCase):
    def test_agenda_progresses_each_month(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            name = _active_ming(db)[0]
            _set_agenda(db, name, "climb", intensity=90, progress=0)
            ambition.pursue_tick(db, state, state.turn * 30 + 1)
            prog = db.conn.execute("SELECT progress FROM npc_agendas WHERE name=?", (name,)).fetchone()["progress"]
            self.assertGreater(prog, 0, "私心应逐月累进")

    def test_climb_matures_and_lifts_standing(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            name = _active_ming(db)[0]
            db.conn.execute("UPDATE characters SET emp_trust=50 WHERE name=?", (name,))
            _set_agenda(db, name, "climb", intensity=90, progress=98)
            evs = ambition.pursue_tick(db, state, state.turn * 30 + 1)
            self.assertTrue(any(e["kind"] == "ambition" and name in e["title"] for e in evs), "进取满则资望成局")
            et = db.conn.execute("SELECT emp_trust FROM characters WHERE name=?", (name,)).fetchone()["emp_trust"]
            self.assertGreater(et, 50, "资望已成应见重于上")
            prog = db.conn.execute("SELECT progress FROM npc_agendas WHERE name=?", (name,)).fetchone()["progress"]
            self.assertEqual(prog, 0, "成局后归零再图")

    def test_entrench_matures_raises_army_autonomy(self):
        with TemporaryDirectory() as tmp:
            db, state = _fresh(tmp)
            army = db.conn.execute(
                "SELECT id, commander FROM armies WHERE owner_power='ming' AND commander!='' LIMIT 1").fetchone()
            commander = str(army["commander"])
            # 确保该主帅在朝且有 entrench 私心
            db.conn.execute(
                "INSERT OR IGNORE INTO characters (name,office,office_type,faction,personal_skills,style,status,power_id,loyalty,ability,integrity,courage) "
                "VALUES (?, '总兵','军事','军队','[]','悍勇','active','ming',50,60,50,60)", (commander,))
            db.conn.execute("UPDATE armies SET autonomy=10 WHERE id=?", (army["id"],))
            _set_agenda(db, commander, "entrench", intensity=95, progress=98)
            db.conn.commit()
            ambition.pursue_tick(db, state, state.turn * 30 + 1)
            au = db.conn.execute("SELECT autonomy FROM armies WHERE id=?", (army["id"],)).fetchone()["autonomy"]
            self.assertGreater(au, 10, "自重成局应抬高所将之镇离心（喂 P7）")


if __name__ == "__main__":
    unittest.main()
