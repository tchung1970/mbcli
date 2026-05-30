"""Tests for the JSON flattening helpers used by `mbcli discover`."""

from mbcli.extract import flatten, flatten_responses


class _Resp:
    def __init__(self, body):
        self.json = body


def test_flatten_paths():
    flat = flatten({"a": {"b": [1, 2]}, "c": "x"})
    assert flat == {"a.b[0]": 1, "a.b[1]": 2, "c": "x"}


def test_flatten_ignores_bare_leaf_without_prefix():
    assert flatten(42) == {}
    assert flatten(None) == {}


def test_flatten_responses_merges_bodies():
    flat = flatten_responses([_Resp({"a": 1}), _Resp({"b": {"c": 2}})])
    assert flat == {"a": 1, "b.c": 2}
