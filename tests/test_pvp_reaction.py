"""Standalone unit test for the pure PvP reaction prompt builder.
Run: python tests/test_pvp_reaction.py   (no server needed)
"""
import os
import sys

_HERE = os.path.dirname(__file__)
# handler.py does `import config`, so the avatar-server root must be on the path
# too, not just the plugin dir.
sys.path.insert(0, os.path.join(_HERE, ".."))
sys.path.insert(0, os.path.join(_HERE, "..", "plugins", "game"))
import handler  # noqa: E402

FAILS = []


def check(name, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        FAILS.append(name)


def main():
    # Loss with a named spammed move -> taunt names it, threatens 21:00
    rivalry = {"player_top_move_name": "Eternal Refrain",
               "player_top_move_count": 9, "loss_streak": 29}
    p = handler._build_pvp_reaction_prompt("player", {"rivalry": rivalry})
    check("loss prompt names the move", "Eternal Refrain" in p)
    check("loss prompt cites the count", "9" in p)
    check("loss prompt cites the streak", "29" in p)
    check("loss prompt threatens 21:00", "21:00" in p)
    check("loss prompt asks for one sentence", "One sentence" in p or "one sentence" in p)

    # Loss with no move data (first battle / empty log) -> still a valid threat
    p2 = handler._build_pvp_reaction_prompt("player", {"rivalry": {"loss_streak": 1}})
    check("loss prompt survives missing move", isinstance(p2, str) and len(p2) > 0)

    # Win -> gloat, no 21:00 threat needed
    p3 = handler._build_pvp_reaction_prompt("enemy", {"rivalry": {}})
    check("win prompt gloats", "WON" in p3 or "won" in p3 or "gloat" in p3.lower())

    # Draw -> still a valid string
    p4 = handler._build_pvp_reaction_prompt("draw", {})
    check("draw prompt is valid", isinstance(p4, str) and len(p4) > 0)

    # _append_rivalry_log: new-file header + spam suffix branch
    import tempfile
    tmpdir = tempfile.mkdtemp()
    log_path = os.path.join(tmpdir, "rivalry_log.md")
    handler._RIVALRY_LOG = log_path  # redirect the ledger to a temp file
    ok = handler._append_rivalry_log(
        {"rivalry": {"player_top_move_name": "Eternal Refrain",
                     "player_top_move_count": 9, "loss_streak": 29}}
    )
    check("append returns True", ok is True)
    with open(log_path, encoding="utf-8") as f:
        body = f.read()
    check("ledger has header on new file", "Rivalry Log" in body)
    check("ledger entry names the move", "Eternal Refrain" in body)
    check("ledger entry shows spam count", "spammed 9x" in body)
    check("ledger entry shows streak", "29" in body)

    print(f"\n{'ALL PASS' if not FAILS else 'FAILURES: ' + ', '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
