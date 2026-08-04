#!/usr/bin/env python3
"""Test driver for HumanEvalFix task Python/1 (bigcode/humanevalpack, MIT).
The check() body below is vendored verbatim from the dataset's `test` field.
Run: python3 run_tests.py -- exits 0 iff every assert passes."""
from separate_paren_groups import separate_paren_groups
def check(separate_paren_groups):
    assert separate_paren_groups('(()()) ((())) () ((())()())') == [
        '(()())', '((()))', '()', '((())()())'
    ]
    assert separate_paren_groups('() (()) ((())) (((())))') == [
        '()', '(())', '((()))', '(((())))'
    ]
    assert separate_paren_groups('(()(())((())))') == [
        '(()(())((())))'
    ]
    assert separate_paren_groups('( ) (( )) (( )( ))') == ['()', '(())', '(()())']

check(separate_paren_groups)

print("run_tests: all asserts passed")
