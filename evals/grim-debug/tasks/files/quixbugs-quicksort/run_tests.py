#!/usr/bin/env python3
"""Test driver for the QuixBugs `quicksort` task.

Cases in quicksort.testcases.json are vendored verbatim from QuixBugs
(json_testcases/quicksort.json, MIT): one `[[args], expected]` JSON per line.
Ref: https://github.com/jkoppel/QuixBugs
Run: python3 run_tests.py -- exits 0 iff every case passes.
"""
import json
import sys

from quicksort import quicksort


def main() -> int:
    with open("quicksort.testcases.json", encoding="utf-8") as fh:
        cases = [json.loads(line) for line in fh if line.strip()]
    assert cases, "testcases file must not be empty"

    failures = 0
    for args, expected in cases:
        actual = quicksort(*args)
        ok = actual == expected
        failures += 0 if ok else 1
        marker = "PASS" if ok else "FAIL"
        detail = "" if ok else f"  (expected {expected!r})"
        print(f"{marker} quicksort({args[0]!r}) -> {actual!r}{detail}")

    assert failures <= len(cases), "failure count cannot exceed case count"
    print(f"{len(cases) - failures}/{len(cases)} cases passed")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
