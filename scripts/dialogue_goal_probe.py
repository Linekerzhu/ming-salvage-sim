"""Probe the conversation goal / psychological handshake layer without LLM calls."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ming_sim.content import GameContent
from ming_sim.context import bind_content as bind_context
from ming_sim.db import GameDB
from ming_sim.dialogue_goals import (
    GOAL_SEALED,
    GOAL_WAITING,
    detect_conversation_goal,
    prepare_dialogue_context,
    record_dialogue_effects,
    review_conversation_goals,
)


def assert_true(value: object, message: str) -> None:
    if not value:
        raise AssertionError(message)


def latest_goal(db: GameDB, name: str) -> dict:
    goals = db.list_conversation_goals(minister_name=name, limit=5)
    assert_true(goals, f"{name} should have at least one conversation goal")
    return goals[0]


def run_probe() -> None:
    content = GameContent.load()
    bind_context(content)
    with tempfile.TemporaryDirectory(prefix="ming-dialogue-goal-") as tmp:
        db = GameDB(str(Path(tmp) / "probe.db"), content=content)
        db.seed_static_data()
        state = db.load_state()
        db.ensure_xinpan_states(state)

        yang = content.characters["杨嗣昌"]
        wang = content.characters["王承恩"]

        text = "你是否愿意去吏部做官？司礼监那边也会照会，不会掣肘。"
        detection = detect_conversation_goal(db, state, yang, text)
        assert_true(detection.action_kind == "personnel", f"吏部做官被误判为 {detection.action_kind}")

        prep = prepare_dialogue_context(db, state, yang, text, persistent=True)
        result = record_dialogue_effects(
            db,
            state,
            yang,
            text,
            "臣愿领旨，若陛下明授吏部职掌，臣当奉行。",
            prep,
        )
        goal = latest_goal(db, yang.name)
        assert_true(goal["action_kind"] == "personnel", "personnel goal should stay personnel")
        assert_true(goal["status"] == GOAL_SEALED, f"personnel goal should seal, got {goal['status']}")
        assert_true(int(goal["agreement_id"] or 0) > 0, "sealed personnel goal should bind agreement")
        agreements = db.list_negotiation_agreements(minister_name=yang.name, action_kind="personnel")
        assert_true(agreements and agreements[0]["target_status"] == "achieved", "personnel agreement should be achieved")
        assert_true(result["handshake_status"] == "sealed", "record result should report sealed")

        casual = detect_conversation_goal(db, state, yang, "卿今日身体如何？")
        assert_true(not casual.has_goal, "ordinary question should not create a goal")

        secret_text = "朕要你暗查厂卫旧案，取证后只许密奏。"
        prep = prepare_dialogue_context(db, state, yang, secret_text, persistent=True)
        record_dialogue_effects(
            db,
            state,
            yang,
            secret_text,
            "臣可密办，但须明旨授权锦衣卫，并添派可靠人手，否则风声一泄便会反噬。",
            prep,
        )
        goal = latest_goal(db, yang.name)
        assert_true(goal["action_kind"] == "secret_order", "secret goal should be secret_order")
        assert_true(goal["status"] == GOAL_WAITING, f"conditional secret goal should wait, got {goal['status']}")
        assert_true(int(goal["agreement_id"] or 0) == 0, "waiting goal must not create agreement early")

        reviewed = review_conversation_goals(
            db,
            state,
            decree_text="准明旨授权锦衣卫密查此案，添派可靠人手，所得证据只许密奏御前。",
            phase="preresolve",
        )
        goal = latest_goal(db, yang.name)
        assert_true(reviewed, "condition review should update the waiting goal")
        assert_true(goal["status"] == GOAL_SEALED, f"reviewed goal should seal, got {goal['status']}")
        assert_true(int(goal["agreement_id"] or 0) > 0, "reviewed sealed goal should create agreement")

        castration_text = "若令卿净身入宫，转入司礼监为内臣，卿是否自愿？"
        prep = prepare_dialogue_context(db, state, wang, castration_text, persistent=True)
        record_dialogue_effects(
            db,
            state,
            wang,
            castration_text,
            "奴才愿入内廷，愿为陛下内臣，谨受此身。",
            prep,
        )
        goal = latest_goal(db, wang.name)
        assert_true(goal["action_kind"] == "castration", "explicit castration goal should be castration")
        assert_true(goal["status"] == GOAL_SEALED, "explicit castration should seal")

        abandon_text = "此事先作罢，不谈密查了。"
        prep = prepare_dialogue_context(db, state, yang, abandon_text, persistent=True)
        abandoned = record_dialogue_effects(
            db,
            state,
            yang,
            abandon_text,
            "臣谨记圣意，暂候后命。",
            prep,
        )
        assert_true(abandoned.get("event") == "abandoned", "abandon phrase should abandon active/waiting goal")

        print("[dialogue_goal_probe] ok")


if __name__ == "__main__":
    run_probe()
