"""对食（宦官后宫恶趣味 E2c）测试：内宠与权阉结对食、内外勾连、Person卡。零 LLM。"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import harem, court, timeflow
from ming_sim.db import GameDB
from ming_sim.models import Character


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    day = timeflow.ensure_active(db, state)
    return db, state, day


def _add_consort(db, state, name="柳如烟", charm=85):
    db.add_character(state, Character(
        name=name, office="贵妃", office_type="后宫", faction="中宫", aliases=[],
        personal_skills=["歌舞"], loyalty=60, ability=55, integrity=60, courage=50,
        style="明丽", power_id="ming", status="active", charm=charm))


class DuishiTests(unittest.TestCase):
    def test_no_consort_is_noop(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            self.assertEqual(harem.duishi_tick(db, state, day), [])

    def test_forms_pair_between_consort_and_court_eunuch(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            _add_consort(db, state)
            # 多跑几月（成对食有概率）；命中则妃有对食伴侣
            formed = False
            for m in range(24):
                evs = harem.duishi_tick(db, state, day + m * 30)
                if harem.duishi_partner(db, "柳如烟"):
                    formed = True
                    break
            self.assertTrue(formed, "二十余月内应结成对食")
            partner = harem.duishi_partner(db, "柳如烟")
            self.assertTrue(partner)
            # 对食对称：伴侣的对食也是她
            self.assertEqual(harem.duishi_partner(db, partner), "柳如烟")

    def test_pair_raises_eunuch_power(self):
        from ming_sim.eunuch_power import get_eunuch_power
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            _add_consort(db, state)
            # 手工结对食（取一权阉）
            eunuch = harem._eligible_court_eunuch(db)
            self.assertIsNotNone(eunuch)
            court.adjust_opinion(db, str(eunuch["name"]), "柳如烟", 28, "对食", day=day)
            p0 = get_eunuch_power(db)
            harem.duishi_tick(db, state, day + 30)  # 已成对食 → 勾连涨权阉
            self.assertGreater(get_eunuch_power(db), p0)

    def test_person_card_shows_duishi(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            _add_consort(db, state)
            eunuch = harem._eligible_court_eunuch(db)
            en = str(eunuch["name"])
            court.adjust_opinion(db, en, "柳如烟", 28, "对食", day=day)
            payload = court.court_payload(db, "柳如烟")
            self.assertEqual(payload.get("duishi"), en)


if __name__ == "__main__":
    unittest.main()
