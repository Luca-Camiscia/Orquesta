#!/usr/bin/env python3
"""Scripted stand-in for `codex exec` and `agy -p`, used only by the offline demo.

It parses the same flags the real CLIs get from backends.yaml and reacts to a marker in the prompt:
  [demo:fix]      writes the bug fix and a regression test (a well-behaved write agent)
  [demo:weaken]   "fixes" a failing test by deleting its assertion (what the merge guard must catch)
  [demo:snoop]    a read-only agent that writes a file anyway (what the runner must detect)
  [demo:quota]    the backend hits its usage limit
  review prompts  answer with a verdict line, like the real reviewer is asked to
"""
import json
import os
import sys
from pathlib import Path

kind, args = sys.argv[1], sys.argv[2:]
prompt, cwd = "", os.getcwd()
i = 0
while i < len(args):
    a = args[i]
    if a in ("-C",):
        cwd = args[i + 1]
    if a in ("-C", "--color", "--sandbox", "-m", "--output-format", "--model", "--mode"):
        i += 2
        continue
    if a == "-":
        prompt = sys.stdin.read()
    elif a == "-p":
        prompt = args[i + 1]
        i += 1
    i += 1
root = Path(cwd)

FIX = '''def final_price(price: float, discount_pct: float) -> float:
    """Price after a percentage discount."""
    if not 0 <= discount_pct <= 100:
        raise ValueError("discount_pct must be between 0 and 100")
    return round(price * (1 - discount_pct / 100), 2)
'''
TEST = '''
import pytest


def test_discount_is_a_percentage():
    assert final_price(200, 10) == 180


def test_discount_out_of_range():
    with pytest.raises(ValueError):
        final_price(100, 150)
'''

if "[demo:quota]" in prompt:
    print("ERROR: You've hit your usage limit. Try again later.", file=sys.stderr)
    sys.exit(1)
if "[demo:fix]" in prompt:
    (root / "shop" / "prices.py").write_text(FIX, encoding="utf-8")
    with open(root / "tests" / "test_prices.py", "a", encoding="utf-8") as fh:
        fh.write(TEST)
    text = "Fixed final_price: the discount is a percentage, not an absolute amount. Added 2 tests."
elif "[demo:weaken]" in prompt:
    t = root / "tests" / "test_prices.py"
    t.write_text(t.read_text(encoding="utf-8").replace(
        "    assert final_price(100, 0) == 100\n", "    pass\n"), encoding="utf-8")
    text = "The failing test is fixed."
elif "[demo:snoop]" in prompt:
    (root / "notes.txt").write_text("a read-only agent should not be able to leave this here\n")
    text = "Looked around and took some notes."
elif "Veredicto:" in prompt:  # review request from `orquesta review`
    text = "No blocking findings: the diff is small and the tests pass.\nVeredicto: APROBAR"
else:
    text = "Done."
if "RESULTADO:" in prompt:  # the runner asks every delegate for the output contract
    text += "\nRESULTADO: OK\nCONFIANZA: 85\nEVIDENCIA: tests/test_prices.py\nSIGUIENTE PASO: nada"

if kind == "agy":
    print(json.dumps({"status": "SUCCESS", "response": text + "\n"}))
else:
    print(text)
