"""db.py 的最小回归测试：python -m pytest tests/ 或直接 python tests/test_db.py"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402


def make_store() -> db.Database:
    return db.Database(":memory:")


def test_user_lifecycle() -> None:
    store = make_store()
    assert store.upsert_user(1001, "alice", "Alice") is True
    assert store.upsert_user(1001, "alice2", "Alice") is False

    stats = store.statistics()
    assert stats["total"] == 1 and stats["active"] == 1

    assert store.mark_blocked(1001, True) is True
    assert store.is_blocked(1001) is True
    assert store.active_user_ids() == []

    assert store.mark_blocked(1001, False) is True
    assert store.active_user_ids() == [1001]
    store.close()


def test_mark_blocked_unknown_user() -> None:
    store = make_store()
    assert store.mark_blocked(9999, True) is False
    store.close()


def test_forward_links() -> None:
    store = make_store()
    store.save_link(2002, 55, 1001)
    assert store.get_link_user(2002, 55) == 1001
    assert store.get_link_user(2002, 66) is None

    for i in range(10):
        store.save_link(2002, 100 + i, 1001)
    store.prune_links(keep=3)
    assert store.statistics()["links"] == 3
    store.close()


def test_rate_limit_window() -> None:
    store = make_store()
    assert store.seconds_since_last_message(4242) == 0.0
    store.upsert_user(4242, None, "Bob")
    assert store.seconds_since_last_message(4242) < 1.0
    store.close()


def test_runtime_admins_and_settings() -> None:
    store = make_store()
    assert store.add_admin(3003, "ops", added_by=1) is True
    assert store.add_admin(3003, "ops", added_by=1) is False
    assert store.list_admins() == [3003]
    assert store.remove_admin(3003) is True

    store.set_setting("welcome", "hi")
    assert store.get_setting("welcome") == "hi"
    store.close()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"✅ {name}")
    print("全部通过")
