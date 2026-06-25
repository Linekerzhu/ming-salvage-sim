import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import web_app
import ming_sim.session as session_module
from ming_sim.personnel_actions import is_eunuch_office


class OrganizationFillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self._env = {key: os.environ.get(key) for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL")}
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

    def test_fill_organization_vacancy_assigns_office_and_reduces_vacancy(self) -> None:
        game = web_app.WebGame(fresh=True)
        try:
            target = None
            for inst in game.organization_payload().get("institutions", []):
                for slot in inst.get("slots", []):
                    if (
                        int(slot.get("vacancies") or 0) > 0
                        and not slot.get("open_pool")
                        and not is_eunuch_office(str(slot.get("title") or ""), str(slot.get("office_type") or ""))
                    ):
                        target = (inst, slot)
                        break
                if target:
                    break
            self.assertIsNotNone(target)
            inst, slot = target
            before = int(slot.get("vacancies") or 0)

            result = game.fill_organization_vacancy(str(inst["id"]), str(slot["title"]), "exam")

            self.assertIn("minister", result)
            self.assertTrue(result["minister"]["name"])
            row = game.db.conn.execute(
                "SELECT office, office_type, status FROM characters WHERE name=?",
                (result["minister"]["name"],),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["status"], "active")
            self.assertEqual(row["office"], result["office"])
            self.assertEqual(row["office_type"], result["office_type"])

            fresh_inst = next(i for i in game.organization_payload()["institutions"] if i["id"] == inst["id"])
            fresh_slot = next(s for s in fresh_inst["slots"] if s["title"] == slot["title"])
            self.assertLess(int(fresh_slot.get("vacancies") or 0), before)
        finally:
            try:
                from ming_sim.scheduler import stop_worker

                stop_worker(game.db_path)
            finally:
                game.session.close()

    def test_inner_court_vacancy_never_converts_ordinary_minister(self) -> None:
        game = web_app.WebGame(fresh=True)
        try:
            target = None
            for inst in game.organization_payload().get("institutions", []):
                if inst.get("id") != "inner-court":
                    continue
                for slot in inst.get("slots", []):
                    if int(slot.get("vacancies") or 0) > 0 and not slot.get("open_pool"):
                        target = (inst, slot)
                        break
                if target:
                    break
            self.assertIsNotNone(target)
            inst, slot = target
            ordinary_before = {
                str(row["name"])
                for row in game.db.conn.execute(
                    "SELECT name, office, office_type, faction FROM characters "
                    "WHERE status='active' AND power_id='ming' AND office_type!='后宫'"
                ).fetchall()
                if not is_eunuch_office(str(row["office"] or ""), str(row["office_type"] or ""))
                and "内廷" not in str(row["faction"] or "")
                and "阉党" not in str(row["faction"] or "")
            }

            result = game.fill_organization_vacancy(str(inst["id"]), str(slot["title"]), "auto")

            name = str(result["minister"]["name"])
            self.assertNotIn(name, ordinary_before)
            row = game.db.conn.execute(
                "SELECT office, office_type, faction, sex FROM characters WHERE name=?",
                (name,),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertTrue(is_eunuch_office(str(row["office"] or ""), str(row["office_type"] or "")))
            self.assertEqual(str(row["sex"] or ""), "eunuch")
        finally:
            try:
                from ming_sim.scheduler import stop_worker

                stop_worker(game.db_path)
            finally:
                game.session.close()
