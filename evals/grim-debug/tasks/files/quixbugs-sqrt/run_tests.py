#!/usr/bin/env python3
"""Test driver for the QuixBugs `sqrt` task.

Inputs come verbatim from QuixBugs (json_testcases/sqrt.json, MIT), but this
driver grades against the documented CONTRACT rather than the upstream
expected value: sqrt.py's docstring promises a result within
[sqrt(x) - epsilon, sqrt(x) + epsilon], so any correct fix passes -- not just
the one that reproduces upstream's exact Newton-iteration float.
Ref: https://github.com/jkoppel/QuixBugs
Run: python3 run_tests.py -- exits 0 iff every case passes.
"""
import json
import math
import sys

from sqrt import sqrt


def main() -> int:
    with open("sqrt.testcases.json", encoding="utf-8") as fh:
        cases = [json.loads(line) for line in fh if line.strip()]
    assert cases, "testcases file must not be empty"

    failures = 0
    for (x, epsilon), _upstream_expected in cases:
        actual = sqrt(x, epsilon)
        ok = abs(actual - math.sqrt(x)) <= epsilon
        failures += 0 if ok else 1
        marker = "PASS" if ok else "FAIL"
        print(f"{marker} sqrt({x}, {epsilon}) -> {actual!r} (true sqrt {math.sqrt(x):.6f})")

    assert failures <= len(cases), "failure count cannot exceed case count"
    print(f"{len(cases) - failures}/{len(cases)} cases passed")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
