"""Password hashing is async, and forgetting an `await` is not a slow bug.

`verify_password` runs bcrypt on a worker thread, so it returns a coroutine. A
coroutine is truthy, which makes `if not verify_password(...)` without the
`await` accept every password there is. Tests that send a wrong password would
catch it at the call sites that have one; this catches the call site that
doesn't yet.
"""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
ASYNC_ONLY = {"verify_password", "hash_password"}


def _calls() -> tuple[list[str], int]:
    """Every call to one of `ASYNC_ONLY` in app/ that is not awaited, and how
    many calls there are altogether."""
    bare, total = [], 0
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        awaited = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Await)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if name in ASYNC_ONLY:
                total += 1
                if id(node) not in awaited:
                    bare.append(f"{path.relative_to(APP.parent)}:{node.lineno} {name}(...)")
    return bare, total


def test_every_password_hash_and_check_is_awaited():
    bare, total = _calls()
    assert total >= 8, "found almost no calls to check, so this lint has stopped looking in the right place"
    assert bare == [], "an un-awaited verify_password is always truthy:\n" + "\n".join(bare)
