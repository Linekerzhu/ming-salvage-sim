"""Quest system test script.

This script demonstrates the basic functionality of the new quest system.
"""

import sys
import os

# Add the project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ming_sim.db import GameDB
from ming_sim.models import GameState
from ming_sim.quest_manager import get_quest_manager
from ming_sim.quest_loader import initialize_quest_system


def print_section(title: str):
    """Print a section header."""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def print_quest_info(quest):
    """Print quest information."""
    print(f"\n任务：{quest.title}")
    print(f"  类型：{quest.category} (层级 {quest.tier})")
    print(f"  描述：{quest.description}")
    print(f"  目标类型：{quest.objective_type}")
    print(f"  奖励：{quest.reward_config}")


def test_quest_system():
    """Test the quest system."""
    # Create a test database
    db = GameDB(":memory:")

    # Initialize the quest system
    print_section("初始化任务系统")

    from ming_sim.quest_db import apply_quest_schema
    apply_quest_schema(db.conn)

    # Create a quest manager
    manager = get_quest_manager(db)

    # Create a sample quest
    print_section("创建示例任务")

    quest = manager.create_quest(
        quest_key="test_liaodong_payroll",
        title="辽东军饷危机",
        description="关东将士已三月未领军饷，需彻查此事。",
        category="campaign",
        tier=3,
        objective_type="dialogue_agreement",
        objective_config={
            "target_count": 2,
            "objectives": [
                {
                    "key": "confirm_with_minister",
                    "type": "dialogue_agreement",
                    "target_npc": "韩爌",
                    "description": "与户部尚书韩爌确认查账方案"
                },
                {
                    "key": "collect_evidence",
                    "type": "collect_evidence",
                    "target_count": 3,
                    "description": "收集军饷贪墨证据（3份）"
                }
            ]
        },
        reward_config={
            "imperial_power": 5,
            "npc_trust": {
                "韩爌": 10,
                "袁崇焕": 15
            }
        },
        source_type="test",
        source_id="test_script"
    )

    print_quest_info(quest)

    # Create a game state
    state = GameState(
        year=1629,
        period=4,
        turn=10,
        turn_phase="summoning"
    )

    # Check availability
    print_section("检查任务可用性")

    available = manager.is_quest_available("test_liaodong_payroll", state)
    print(f"任务 'test_liaodong_payroll' 是否可用：{available}")

    # List available quests
    all_available = manager.get_available_quests(state)
    print(f"\n当前可用任务数：{len(all_available)}")
    for q in all_available:
        print(f"  - {q.title} ({q.category})")

    # Accept the quest
    print_section("接受任务")

    player_quest = manager.accept_quest(
        "test_liaodong_payroll",
        source_npc_name="袁崇焕",
        state=state
    )

    if player_quest:
        print(f"任务已接受！")
        print(f"  玩家任务ID：{player_quest.id}")
        print(f"  状态：{player_quest.status}")
        print(f"  进度：{player_quest.progress_current}/{player_quest.progress_target}")
        print(f"  来源NPC：{player_quest.source_npc_name}")
    else:
        print("任务接受失败")
        return

    # List active quests
    print_section("进行中的任务")

    active = manager.get_active_player_quests()
    print(f"进行中的任务数：{len(active)}")
    for pq in active:
        print(f"  - {pq.quest_key} (进度 {pq.progress_current}/{pq.progress_target})")

    # Update progress
    print_section("更新任务进度")

    updated = manager.update_quest_progress(
        player_quest.id,
        progress_delta=1,
        turn=state.turn
    )

    if updated:
        print(f"进度已更新：{updated.progress_current}/{updated.progress_target}")
        print(f"状态：{updated.status}")

    # Update again to complete
    print_section("完成任务")

    completed = manager.update_quest_progress(
        player_quest.id,
        progress_delta=1,
        turn=state.turn + 1
    )

    if completed and completed.status == "completed":
        print(f"任务已完成！")
        print(f"完成回合：{completed.completed_turn}")

    # Claim rewards
    print_section("领取奖励")

    if completed:
        result = manager.complete_quest(completed.id, state)
        if result.get("success"):
            print("奖励已领取：")
            for key, value in result.get("rewards", {}).items():
                print(f"  - {key}: {value}")

    # Show quest events
    print_section("任务事件日志")

    events = db.conn.execute(
        "SELECT * FROM quest_events ORDER BY id"
    ).fetchall()

    for event in events:
        print(f"回合 {event['turn']}: {event['event_type']} - {event['event_data']}")

    # Test abandonment
    print_section("测试放弃任务")

    # Create another quest
    quest2 = manager.create_quest(
        quest_key="test_side_quest",
        title="支线任务",
        description="这是一个可以放弃的任务。",
        category="side",
        tier=1,
        objective_type="dialogue_agreement",
        objective_config={"target_count": 1},
        source_type="test"
    )

    pq2 = manager.accept_quest("test_side_quest", state=state)
    if pq2:
        print(f"已接受任务：{pq2.quest_key}")

        abandoned = manager.abandon_quest(pq2.id, state.turn + 2)
        if abandoned:
            print(f"任务已放弃")

            # Check status
            pq2_updated = manager.get_player_quest_by_id(pq2.id)
            if pq2_updated:
                print(f"当前状态：{pq2_updated.status}")

    print_section("测试完成")

    print("\n✓ 任务系统基本功能测试通过！")


if __name__ == "__main__":
    test_quest_system()
