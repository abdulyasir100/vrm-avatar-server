"""Standalone unit test for the scheduled_actions weekday filter.
Run: python tests/test_scheduled_days.py   (no server needed)
"""
import os
import sys
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_HERE, ".."))

# _day_matches is pure; pull it out without importing the whole service graph.
import ast  # noqa: E402

_SRC = open(os.path.join(_HERE, "..", "services", "background.py"), encoding="utf-8").read()
_ns = {}
for _node in ast.parse(_SRC).body:
    if isinstance(_node, ast.FunctionDef) and _node.name == "_day_matches":
        exec(compile(ast.Module([_node], []), "background.py", "exec"), _ns)
_day_matches = _ns["_day_matches"]

WIB = timezone(timedelta(hours=7))
# 2026-08-16 is a Sunday, 2026-08-17 a Monday.
SUNDAY = datetime(2026, 8, 16, 21, 0, tzinfo=WIB)
MONDAY = datetime(2026, 8, 17, 21, 0, tzinfo=WIB)

FAILS = []


def check(name, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        FAILS.append(name)


def main():
    # No filter -> every day (all existing plugins keep firing daily)
    check("no days key fires any day", _day_matches(None, MONDAY))
    check("empty days fires any day", _day_matches([], MONDAY))

    # Sunday-only (the game plugin's weekly dev session)
    check("sun fires on Sunday", _day_matches(["sun"], SUNDAY))
    check("sun skips Monday", not _day_matches(["sun"], MONDAY))

    # Case / long-form tolerance and multi-day lists
    check("Sunday long form matches", _day_matches(["Sunday"], SUNDAY))
    check("MON uppercase matches", _day_matches(["MON"], MONDAY))
    check("multi-day list matches", _day_matches(["mon", "thu"], MONDAY))
    check("multi-day list excludes", not _day_matches(["mon", "thu"], SUNDAY))

    print(f"\n{'ALL PASS' if not FAILS else 'FAILURES: ' + ', '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
