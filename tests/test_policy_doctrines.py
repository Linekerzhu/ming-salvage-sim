import os
import random
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ming_sim import foundation, lifecycle, memorials, policies, timeflow
from ming_sim.content import GameContent
from ming_sim.db import GameDB
from ming_sim.issues import apply_issue_tracker_output, bind_content as bind_issues_content
from ming_sim.playstyle import audience_summon_hints_payload, doctrine_chat_context_brief


def _fresh(tmp: str):
    db = GameDB(str(Path(tmp) / "t.db"))
    db.seed_static_data()
    state = db.load_state()
    day = timeflow.ensure_active(db, state)
    return db, state, day


class PolicyDoctrineConfigTests(unittest.TestCase):
    def test_config_loads_and_references_valid_categories(self):
        policies.reset_doctrine_cache_for_tests()
        data = policies.load_policy_doctrines()
        self.assertGreaterEqual(len(data["doctrines"]), 8)
        categories = {
            str(cat["id"])
            for cat in policies._load_directive_categories().get("categories", [])
            if isinstance(cat, dict)
        }
        ids = {str(item["id"]) for item in data["doctrines"]}
        for item in data["doctrines"]:
            self.assertIn(str(item["id"]), ids)
            self.assertTrue(item.get("name"))
            self.assertTrue(item.get("axis"))
            for cat in item.get("supports_categories") or []:
                self.assertIn(str(cat), categories)
            for conflict in item.get("conflicts") or []:
                self.assertIn(str(conflict), ids)
            self.assertIsInstance(item.get("legacy_effects") or {}, dict)


class PolicyDoctrineIssueTests(unittest.TestCase):
    def test_doctrine_issue_resolves_into_idempotent_legacy(self):
        with TemporaryDirectory() as tmp:
            db, state, _day = _fresh(tmp)
            result = policies.ensure_doctrine_issue(
                db,
                state,
                "open_sea_trade",
                trigger_kind="test",
                delta_bar=50,
                narrative="开海议成",
            )
            self.assertEqual(result.get("issue_status"), "active")
            result = policies.ensure_doctrine_issue(
                db,
                state,
                "open_sea_trade",
                trigger_kind="test",
                delta_bar=50,
                narrative="开海再议成",
            )
            self.assertEqual(result.get("issue_status"), "resolved")
            legacy = result.get("legacy") or {}
            self.assertTrue(legacy.get("created"))
            active = policies.active_doctrine_legacies(db)
            self.assertIn("open_sea_trade", active)
            again = policies.ensure_doctrine_legacy(db, state, "open_sea_trade")
            self.assertFalse(again.get("created"))
            self.assertTrue(again.get("duplicate"))

    def test_tracker_close_creates_one_doctrine_legacy(self):
        with TemporaryDirectory() as tmp:
            db, state, _day = _fresh(tmp)
            bind_issues_content(GameContent.load())
            created = policies.ensure_doctrine_issue(db, state, "fiscal_rectification")
            issue_id = int(created["issue_id"])
            result = apply_issue_tracker_output(db, state, {
                "close_issues": [{"issue_id": issue_id, "reason": "resolved", "narrative": "廷议已成财政整饬"}]
            })
            self.assertEqual(result["closes"][0]["reason"], "resolved")
            rows = db.conn.execute(
                "SELECT legacy_key FROM legacies WHERE legacy_key='doctrine:fiscal_rectification'"
            ).fetchall()
            self.assertEqual(len(rows), 1)

    def test_web_legacy_payload_exposes_established_basic_doctrine(self):
        with TemporaryDirectory() as tmp:
            db, state, _day = _fresh(tmp)
            policies.ensure_doctrine_legacy(db, state, "western_learning_pragmatism")

            import web_app

            class _LegacyPayloadGame:
                pass

            game = _LegacyPayloadGame()
            game.db = db
            game.state = state
            game.content = GameContent.load()
            payloads = web_app.WebGame.legacies_payload(game)
            item = next(row for row in payloads if row.get("policy_doctrine"))
            doctrine = item["policy_doctrine"]
            self.assertEqual(doctrine["id"], "western_learning_pragmatism")
            self.assertEqual(doctrine["name"], "西学实用")
            self.assertEqual(doctrine["axis"], "信仰新知")
            self.assertTrue(item["name"].startswith("基本国策："))

    def test_web_issue_payload_exposes_route_alignment_summary(self):
        with TemporaryDirectory() as tmp:
            db, state, _day = _fresh(tmp)
            created = policies.ensure_doctrine_issue(db, state, "open_sea_trade", delta_bar=8)
            issue_id = int(created["issue_id"])

            import web_app

            class _IssuePayloadGame:
                pass

            game = _IssuePayloadGame()
            game.db = db
            payloads = web_app.WebGame.issue_payloads(game)
            item = next(row for row in payloads if int(row["id"]) == issue_id)
            route = item["policy_doctrine"]
            self.assertEqual(route["id"], "open_sea_trade")
            self.assertEqual(route["name"], "开海裕国")
            self.assertGreaterEqual(int(route["bar_value"]), 50)
            self.assertIsInstance(route["factions"], list)
            self.assertIsInstance(route["figures"], list)


class PolicyDirectiveGateTests(unittest.TestCase):
    def test_conflicting_directive_gets_route_warning_without_instant_legacy(self):
        with TemporaryDirectory() as tmp:
            db, state, _day = _fresh(tmp)
            policies.ensure_doctrine_legacy(db, state, "ancestral_conservatism")
            text = "准福建大开海禁，永设市舶榷关，招洋商纳商税以裕国用"
            review = policies.directive_doctrine_review(db, state, text, category_id="tax_reform")
            self.assertEqual(review["primary"]["id"], "open_sea_trade")
            self.assertTrue(any(c["id"] == "ancestral_conservatism" for c in review["conflicts"]))
            self.assertTrue(review["establishment_blocked"])
            self.assertTrue(review["execution_gate"]["establishment_blocked"])
            applied = policies.apply_directive_doctrine_effects(
                db,
                state,
                directive_id=0,
                text=text,
                category_id="tax_reform",
            )
            self.assertEqual(applied["primary"]["id"], "open_sea_trade")
            self.assertIn("open_sea_trade", applied["issue"]["doctrine_id"])
            active = policies.active_doctrine_legacies(db)
            self.assertIn("ancestral_conservatism", active)
            self.assertNotIn("open_sea_trade", active)

    def test_conflicting_basic_doctrine_cannot_be_established(self):
        with TemporaryDirectory() as tmp:
            db, state, _day = _fresh(tmp)
            policies.ensure_doctrine_legacy(db, state, "ancestral_conservatism")

            first = policies.ensure_doctrine_issue(db, state, "open_sea_trade", delta_bar=50)
            self.assertEqual(first.get("issue_status"), "active")
            second = policies.ensure_doctrine_issue(db, state, "open_sea_trade", delta_bar=50)

            self.assertTrue(second.get("establishment_blocked"))
            self.assertEqual(int(second.get("bar_value") or 0), 95)
            row = db.conn.execute(
                "SELECT status, bar_value FROM issues WHERE id=?",
                (int(second["issue_id"]),),
            ).fetchone()
            self.assertEqual(str(row["status"]), "active")
            self.assertEqual(int(row["bar_value"]), 95)

            legacy = policies.ensure_doctrine_legacy(db, state, "open_sea_trade")
            self.assertTrue(legacy.get("blocked"))
            self.assertNotIn("open_sea_trade", policies.active_doctrine_legacies(db))

    def test_temporary_workaround_does_not_advance_rival_doctrine(self):
        with TemporaryDirectory() as tmp:
            db, state, _day = _fresh(tmp)
            policies.ensure_doctrine_legacy(db, state, "ancestral_conservatism")
            text = "暂准福建试开海禁三月，设市舶榷关筹饷，事竣即止，不得为例"

            review = policies.directive_doctrine_review(db, state, text, category_id="tax_reform")
            self.assertTrue(review["temporary_exception"])
            self.assertFalse(review["establishment_blocked"])
            self.assertEqual(review["execution_gate"]["level"], "temporary_exception")
            self.assertIn("越制", review["risk_tags"])

            applied = policies.apply_directive_doctrine_effects(
                db,
                state,
                directive_id=0,
                text=text,
                category_id="tax_reform",
            )

            self.assertEqual(applied["issue"]["status"], "temporary_exception")
            row = db.conn.execute(
                "SELECT 1 FROM issues WHERE origin_kind='doctrine' AND origin_ref='open_sea_trade'"
            ).fetchone()
            self.assertIsNone(row)
            self.assertNotIn("open_sea_trade", policies.active_doctrine_legacies(db))

    def test_tracker_close_cannot_bypass_doctrine_conflict_gate(self):
        with TemporaryDirectory() as tmp:
            db, state, _day = _fresh(tmp)
            policies.ensure_doctrine_legacy(db, state, "ancestral_conservatism")
            created = policies.ensure_doctrine_issue(db, state, "open_sea_trade")
            issue_id = int(created["issue_id"])

            result = apply_issue_tracker_output(db, state, {
                "close_issues": [{"issue_id": issue_id, "reason": "resolved", "narrative": "开海已成朝议"}]
            })

            self.assertEqual(result["closes"][0]["reason"], "blocked")
            self.assertTrue(result["closes"][0]["doctrine_legacy"]["blocked"])
            row = db.conn.execute("SELECT status FROM issues WHERE id=?", (issue_id,)).fetchone()
            self.assertEqual(str(row["status"]), "active")
            self.assertNotIn("open_sea_trade", policies.active_doctrine_legacies(db))

    def test_memorial_at_blocked_cap_retires_old_doctrine_and_establishes_new_route(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            policies.ensure_doctrine_legacy(db, state, "ancestral_conservatism")
            created = policies.ensure_doctrine_issue(db, state, "open_sea_trade", delta_bar=50)
            issue_id = int(created["issue_id"])
            capped = policies.ensure_doctrine_issue(db, state, "open_sea_trade", delta_bar=50)
            self.assertEqual(int(capped["bar_value"]), 95)

            memorials.reset_attention_for_day(db, day)
            mid = memorials.create_memorial(
                db,
                state,
                day=day,
                author_name="徐光启",
                org="礼部",
                kind="请旨",
                urgency=3,
                summary="请定开海裕国为国是",
                ref_kind="issue",
                ref_id=str(issue_id),
            )
            result = memorials.decide_memorial(db, state, mid, "approve", day=day)

            self.assertTrue(result["ok"])
            effect = result.get("doctrine_effect", {})
            self.assertTrue(effect.get("retired_blockers"), effect)
            self.assertTrue((effect.get("legacy") or {}).get("created"), effect)
            active = policies.active_doctrine_legacies(db)
            self.assertIn("open_sea_trade", active)
            self.assertNotIn("ancestral_conservatism", active)
            old = db.conn.execute(
                "SELECT status FROM legacies WHERE legacy_key='doctrine:ancestral_conservatism'"
            ).fetchone()
            self.assertEqual(str(old["status"]), "cleared")
            issue = db.conn.execute("SELECT status, bar_value FROM issues WHERE id=?", (issue_id,)).fetchone()
            self.assertEqual(str(issue["status"]), "resolved")
            self.assertEqual(int(issue["bar_value"]), 100)

    def test_doctrine_conflict_increases_execution_resistance_and_block_risk(self):
        text = "准福建大开海禁，永设市舶榷关，招洋商纳商税以裕国用"
        with TemporaryDirectory() as tmp:
            db, state, _day = _fresh(tmp)
            baseline = lifecycle.build_chain(db, state, text, "")
            baseline_block = int((baseline["check_risk"] or {}).get("block") or 0)

            policies.ensure_doctrine_legacy(db, state, "ancestral_conservatism")
            conflict = lifecycle.build_chain(db, state, text, "")
            gate = conflict["policy_doctrine"]["execution_gate"]
            self.assertEqual(gate["level"], "conflict")
            self.assertGreater(int(gate["resistance_delta"]), 0)
            self.assertGreaterEqual(int(conflict["resistance"]), int(baseline["resistance"]))
            self.assertGreater(int((conflict["check_risk"] or {}).get("block") or 0), baseline_block)
            self.assertTrue(any("国策：" in note for note in conflict["trait_notes"]))


class PolicyFoundationStanceTests(unittest.TestCase):
    def test_character_policy_ideals_are_derived_without_persistence(self):
        with TemporaryDirectory() as tmp:
            db, state, _day = _fresh(tmp)
            policies.ensure_doctrine_legacy(db, state, "western_learning_pragmatism")
            policies.ensure_doctrine_issue(db, state, "open_sea_trade")

            ideals = policies.character_policy_ideals(db, "徐光启")
            support_by_id = {item["id"]: item for item in ideals["supports"]}
            self.assertIn("western_learning_pragmatism", support_by_id)
            self.assertEqual(support_by_id["western_learning_pragmatism"]["status"], "orthodox")
            self.assertIn("open_sea_trade", support_by_id)
            self.assertEqual(support_by_id["open_sea_trade"]["status"], "contested")
            self.assertIn("治国所向", ideals["summary"])

    def test_policy_ideal_surfaces_as_audience_hint_and_trusted_context(self):
        with TemporaryDirectory() as tmp:
            db, state, _day = _fresh(tmp)
            policies.ensure_doctrine_issue(db, state, "open_sea_trade")

            payload = audience_summon_hints_payload(db, state)
            hint = payload["hints"].get("徐光启")
            self.assertIsNotNone(hint)
            labels = [str(tag.get("label") or "") for tag in hint.get("tags", [])]
            self.assertTrue(any("开海" in label for label in labels), labels)
            self.assertGreater(int(hint.get("pressure_score") or 0), 0)
            self.assertEqual(str(hint.get("lead", {}).get("kind")), "doctrine")
            self.assertEqual(str(hint.get("lead", {}).get("ref_id")), "open_sea_trade")

            brief = doctrine_chat_context_brief(db, "徐光启", "open_sea_trade")
            self.assertIn("开海裕国", brief)
            self.assertIn("倾向支持", brief)
            self.assertIn("路线争议", brief)

    def test_override_and_degraded_fallback_stance(self):
        with TemporaryDirectory() as tmp:
            db, _state, _day = _fresh(tmp)
            override = policies.character_doctrine_stance(db, "徐光启", "western_learning_pragmatism")
            self.assertEqual(override["stance"], "support")
            self.assertGreater(float(override["score"]), 0.8)

            old_env = os.environ.get("MING_FOUNDATION_DB")
            orig_path = foundation._db_path
            try:
                os.environ["MING_FOUNDATION_DB"] = "/nonexistent/no.sqlite"
                foundation.reset_for_tests()
                foundation._db_path = lambda: None
                fallback = policies.character_doctrine_stance(db, "钱谦益", "qingliu_public_morality")
                self.assertEqual(fallback["stance"], "support")
                self.assertTrue(any("派系东林" in r for r in fallback["reasons"]))
            finally:
                foundation._db_path = orig_path
                if old_env is None:
                    os.environ.pop("MING_FOUNDATION_DB", None)
                else:
                    os.environ["MING_FOUNDATION_DB"] = old_env
                foundation.reset_for_tests()


class PolicyMemorialIntegrationTests(unittest.TestCase):
    def test_doctrine_dispute_generates_existing_memorial_flow(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            created = policies.ensure_doctrine_issue(db, state, "open_sea_trade")
            issue_id = int(created["issue_id"])

            events = memorials._doctrine_memorial_pulse(
                db,
                state,
                day,
                random.Random(7),
                force=True,
            )

            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["kind"], "memorial")
            row = db.conn.execute(
                "SELECT * FROM memorials WHERE ref_kind='issue' AND ref_id=?",
                (str(issue_id),),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertIn("开海裕国", str(row["summary"]))
            self.assertIn("开海裕国", str(row["full_text"]))

            payload = memorials.desk_payload(db, state, day)
            item = next(m for m in payload["pending"] if int(m["id"]) == int(row["id"]))
            self.assertEqual(item["policy_doctrine"]["id"], "open_sea_trade")
            approve_labels = [str(effect["label"]) for effect in item["action_effects"]["approve"]]
            self.assertTrue(any("路线" in label for label in approve_labels), approve_labels)

    def test_doctrine_memorial_pulse_does_not_flood_same_route(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            policies.ensure_doctrine_issue(db, state, "open_sea_trade")

            first = memorials._doctrine_memorial_pulse(db, state, day, random.Random(3), force=True)
            second = memorials._doctrine_memorial_pulse(db, state, day + 1, random.Random(4), force=True)

            self.assertEqual(len(first), 1)
            self.assertEqual(second, [])

    def test_desk_payload_exposes_doctrine_route_and_author_stance(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            created = policies.ensure_doctrine_issue(db, state, "open_sea_trade")
            issue_id = int(created["issue_id"])
            mid = memorials.create_memorial(
                db,
                state,
                day=day,
                author_name="徐光启",
                org="礼部",
                kind="请旨",
                urgency=2,
                summary="请开海裕国",
                ref_kind="issue",
                ref_id=str(issue_id),
            )
            payload = memorials.desk_payload(db, state, day)
            item = next(m for m in payload["pending"] if int(m["id"]) == mid)
            route = item["policy_doctrine"]
            self.assertEqual(route["id"], "open_sea_trade")
            self.assertEqual(route["direction"], "support")
            self.assertGreaterEqual(int(route["bar_value"]), 1)
            self.assertEqual(route["author_stance"]["stance"], "support")

    def test_support_memorial_advances_and_impeachment_blocks_route_issue(self):
        with TemporaryDirectory() as tmp:
            db, state, day = _fresh(tmp)
            memorials.reset_attention_for_day(db, day)
            created = policies.ensure_doctrine_issue(db, state, "open_sea_trade")
            issue_id = int(created["issue_id"])
            before = int(db.conn.execute(
                "SELECT bar_value FROM issues WHERE id=?", (issue_id,)
            ).fetchone()["bar_value"])
            west_sat_before = int(db.conn.execute(
                "SELECT satisfaction FROM factions WHERE name='西学'"
            ).fetchone()["satisfaction"])

            mid = memorials.create_memorial(
                db,
                state,
                day=day,
                author_name="徐光启",
                org="礼部",
                kind="请旨",
                urgency=2,
                summary="请开海裕国",
                ref_kind="issue",
                ref_id=str(issue_id),
            )
            approved = memorials.decide_memorial(db, state, mid, "approve", day=day)
            self.assertTrue(approved["ok"])
            faction_effects = approved.get("doctrine_effect", {}).get("factions", [])
            self.assertTrue(any(effect.get("faction") == "西学" for effect in faction_effects), faction_effects)
            west_sat_after = int(db.conn.execute(
                "SELECT satisfaction FROM factions WHERE name='西学'"
            ).fetchone()["satisfaction"])
            self.assertGreater(west_sat_after, west_sat_before)
            after_support = int(db.conn.execute(
                "SELECT bar_value FROM issues WHERE id=?", (issue_id,)
            ).fetchone()["bar_value"])
            self.assertGreater(after_support, before)

            mid2 = memorials.create_memorial(
                db,
                state,
                day=day,
                author_name="韩爌",
                org="内阁",
                kind="弹章",
                urgency=2,
                summary="弹劾开海乱祖制",
                ref_kind="issue",
                ref_id=str(issue_id),
            )
            denied = memorials.decide_memorial(db, state, mid2, "approve", day=day)
            self.assertTrue(denied["ok"])
            after_impeach = int(db.conn.execute(
                "SELECT bar_value FROM issues WHERE id=?", (issue_id,)
            ).fetchone()["bar_value"])
            self.assertLess(after_impeach, after_support)


if __name__ == "__main__":
    unittest.main()
