"""crap4py test fixture: known complexity and coverage (see tests/test_analyzer.py)."""


def simple(a, b):
    return a + b


def branchy(x, items):
    total = 0
    if x > 0:
        for item in items:
            total += item
    return total


def risky(n):
    if n > 0:
        if n > 1:
            if n > 2:
                if n > 3:
                    return n
    return 0
