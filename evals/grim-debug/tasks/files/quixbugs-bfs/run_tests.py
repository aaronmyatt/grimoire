#!/usr/bin/env python3
"""Test driver for the QuixBugs `breadth_first_search` task.

The five graph cases are vendored from QuixBugs
(python_testcases/test_breadth_first_search.py, MIT), adapted only to run
without pytest: each case is a plain function returning a bool, and a crash
inside a case (the planted bug raises IndexError on some inputs) counts as
that case failing rather than aborting the driver.
Ref: https://github.com/jkoppel/QuixBugs
Run: python3 run_tests.py -- exits 0 iff every case passes.
"""
import sys

from breadth_first_search import breadth_first_search
from node import Node


def case_strongly_connected() -> bool:
    station1 = Node("Westminster")
    station2 = Node("Waterloo", None, [station1])
    station3 = Node("Trafalgar Square", None, [station1, station2])
    station4 = Node("Canary Wharf", None, [station2, station3])
    station5 = Node("London Bridge", None, [station4, station3])
    station6 = Node("Tottenham Court Road", None, [station5, station4])
    return breadth_first_search(station6, station1) is True


def case_branching() -> bool:
    nodef, nodee, noded = Node("F"), Node("E"), Node("D")
    nodec = Node("C", None, [nodef])
    nodeb = Node("B", None, [nodee])
    nodea = Node("A", None, [nodeb, nodec, noded])
    return breadth_first_search(nodea, nodee) is True


def case_unconnected() -> bool:
    return breadth_first_search(Node("F"), Node("E")) is False


def case_single_node() -> bool:
    nodef = Node("F")
    return breadth_first_search(nodef, nodef) is True


def case_with_cycle() -> bool:
    nodef, nodee, noded = Node("F"), Node("E"), Node("D")
    nodec = Node("C", None, [nodef])
    nodeb = Node("B", None, [nodee])
    nodea = Node("A", None, [nodeb, nodec, noded])
    nodee.successors = [nodea]
    return breadth_first_search(nodea, nodef) is True


def main() -> int:
    cases = [
        case_strongly_connected,
        case_branching,
        case_unconnected,
        case_single_node,
        case_with_cycle,
    ]
    assert len(cases) == 5, "all five vendored cases must be registered"

    failures = 0
    for case in cases:
        try:
            ok = case()
            note = ""
        except Exception as exc:  # a crash is a failing case, not a driver fault
            ok = False
            note = f"  (raised {type(exc).__name__}: {exc})"
        failures += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'} {case.__name__}{note}")

    assert failures <= len(cases), "failure count cannot exceed case count"
    print(f"{len(cases) - failures}/{len(cases)} cases passed")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
