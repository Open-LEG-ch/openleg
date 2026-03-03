"""Tests for event_hooks pub/sub system."""
import pytest
from unittest.mock import patch, MagicMock


# === Unit 1A: Event Hook Infrastructure ===

def test_register_hook_and_fire():
    import event_hooks
    event_hooks.clear()
    results = []
    event_hooks.register('test_event', lambda payload: results.append(payload))
    event_hooks.fire('test_event', {'key': 'value'})
    assert len(results) == 1
    assert results[0] == {'key': 'value'}


def test_multiple_hooks_same_event():
    import event_hooks
    event_hooks.clear()
    a, b = [], []
    event_hooks.register('multi', lambda p: a.append(1))
    event_hooks.register('multi', lambda p: b.append(2))
    event_hooks.fire('multi', {})
    assert len(a) == 1
    assert len(b) == 1


def test_hook_exception_does_not_block():
    import event_hooks
    event_hooks.clear()
    results = []

    def bad_hook(p):
        raise ValueError("boom")

    event_hooks.register('err_event', bad_hook)
    event_hooks.register('err_event', lambda p: results.append('ok'))
    event_hooks.fire('err_event', {})
    assert results == ['ok']


def test_fire_unknown_event_noop():
    import event_hooks
    event_hooks.clear()
    # Should not raise
    event_hooks.fire('nonexistent_event', {'data': 1})


def test_hook_receives_correct_payload():
    import event_hooks
    event_hooks.clear()
    received = []
    event_hooks.register('payload_test', lambda p: received.append(p))
    payload = {'building_id': 'b-1', 'city_id': 'zurich', 'email': 'a@b.ch'}
    event_hooks.fire('payload_test', payload)
    assert received[0]['building_id'] == 'b-1'
    assert received[0]['city_id'] == 'zurich'
    assert received[0]['email'] == 'a@b.ch'
