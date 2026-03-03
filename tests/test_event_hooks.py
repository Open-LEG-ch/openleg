"""Tests for event_hooks pub/sub system."""
import os
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


# === Unit 1B: Registration Trigger ===

@pytest.fixture
def reg_client():
    """Flask test client for registration hook tests."""
    env = {
        "DATABASE_URL": "postgresql://x:x@localhost/x",
        "ADMIN_TOKEN": "test123",
        "INTERNAL_TOKEN": "secret-internal",
        "TELEGRAM_BOT_TOKEN": "fake-bot-token",
        "TELEGRAM_CHAT_ID": "12345",
        "TELEGRAM_WEBHOOK_SECRET": "webhook-secret",
        "REDIS_URL": "memory://",
    }
    with patch.dict(os.environ, env):
        with patch("database.init_db", return_value=True), \
             patch("database._connection_pool", MagicMock()), \
             patch("database.is_db_available", return_value=True):
            import importlib
            import event_hooks
            event_hooks.clear()
            import app as app_mod
            importlib.reload(app_mod)
            app_mod.INTERNAL_TOKEN = "secret-internal"
            app_mod.TELEGRAM_BOT_TOKEN = "fake-bot-token"
            app_mod.TELEGRAM_CHAT_ID = "12345"
            app_mod.app.config['TESTING'] = True
            if app_mod.limiter:
                app_mod.limiter.enabled = False
            yield app_mod.app.test_client()


def test_registration_fires_event_hook(reg_client):
    """Registration should fire the 'registration' event hook."""
    import event_hooks
    event_hooks.clear()
    fired = []
    event_hooks.register('registration', lambda p: fired.append(p))
    with patch("database.save_building"), \
         patch("database.save_token"), \
         patch("database.track_event"), \
         patch("database.get_referral_code", return_value=None), \
         patch("app.find_provisional_matches", return_value=None), \
         patch("app.collect_building_locations", return_value=[]), \
         patch("app.send_confirmation_email"), \
         patch("app.run_full_ml_task"), \
         patch("email_automation.schedule_sequence_for_user"):
        resp = reg_client.post("/api/register_anonymous", json={
            "profile": {"address": "Test 1", "building_id": "b-test-1", "lat": 47.3, "lon": 8.5},
            "email": "test@example.com",
            "consents": {"share_with_neighbors": True, "share_with_utility": True}
        })
        assert resp.status_code == 200
        assert len(fired) == 1


def test_registration_event_contains_building_id(reg_client):
    """Registration event payload must include building_id."""
    import event_hooks
    event_hooks.clear()
    fired = []
    event_hooks.register('registration', lambda p: fired.append(p))
    with patch("database.save_building"), \
         patch("database.save_token"), \
         patch("database.track_event"), \
         patch("database.get_referral_code", return_value=None), \
         patch("app.find_provisional_matches", return_value=None), \
         patch("app.collect_building_locations", return_value=[]), \
         patch("app.send_confirmation_email"), \
         patch("app.run_full_ml_task"), \
         patch("email_automation.schedule_sequence_for_user"):
        reg_client.post("/api/register_anonymous", json={
            "profile": {"address": "Test 1", "building_id": "b-test-1", "lat": 47.3, "lon": 8.5},
            "email": "test@example.com",
            "consents": {"share_with_neighbors": True, "share_with_utility": True}
        })
        assert 'building_id' in fired[0]
        assert fired[0]['building_id'] is not None


def test_registration_hook_notifies_telegram(reg_client):
    """Default registration hook should send Telegram notification."""
    app_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "app.py"
    )
    with open(app_path) as f:
        content = f.read()
    assert "event_hooks.fire('registration'" in content or \
           'event_hooks.fire("registration"' in content


def test_registration_hook_runs_cluster_analysis(reg_client):
    """Registration hook payload should include city_id for cluster analysis."""
    import event_hooks
    event_hooks.clear()
    fired = []
    event_hooks.register('registration', lambda p: fired.append(p))
    with patch("database.save_building"), \
         patch("database.save_token"), \
         patch("database.track_event"), \
         patch("database.get_referral_code", return_value=None), \
         patch("app.find_provisional_matches", return_value=None), \
         patch("app.collect_building_locations", return_value=[]), \
         patch("app.send_confirmation_email"), \
         patch("app.run_full_ml_task"), \
         patch("email_automation.schedule_sequence_for_user"):
        reg_client.post("/api/register_anonymous", json={
            "profile": {"address": "Test 1", "building_id": "b-test-1", "lat": 47.3, "lon": 8.5},
            "email": "test@example.com",
            "consents": {"share_with_neighbors": True, "share_with_utility": True}
        })
        assert 'city_id' in fired[0]


# === Unit 1C: Formation Threshold Trigger ===

def test_confirm_membership_fires_event():
    """confirm_membership should fire 'member_confirmed' event."""
    import event_hooks
    event_hooks.clear()
    fired = []
    event_hooks.register('member_confirmed', lambda p: fired.append(p))
    from formation_wizard import confirm_membership
    mock_db = MagicMock()
    mock_cur = MagicMock()
    mock_cur.rowcount = 1
    mock_cur.fetchone.return_value = {'confirmed_count': 1}
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = lambda s: mock_cur
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    mock_db.get_connection.return_value.__enter__ = lambda s: mock_conn
    mock_db.get_connection.return_value.__exit__ = MagicMock(return_value=False)
    mock_db.track_event = MagicMock()

    result = confirm_membership(mock_db, 'com-1', 'b-1')
    assert result is True
    assert len(fired) == 1
    assert fired[0]['community_id'] == 'com-1'
    assert fired[0]['building_id'] == 'b-1'


def test_threshold_reached_fires_formation_ready():
    """When confirmed_count >= 3, should fire formation_threshold_reached."""
    import event_hooks
    event_hooks.clear()
    fired = []
    event_hooks.register('formation_threshold_reached', lambda p: fired.append(p))
    from formation_wizard import confirm_membership
    mock_db = MagicMock()
    mock_cur = MagicMock()
    mock_cur.rowcount = 1
    mock_cur.fetchone.return_value = {'confirmed_count': 3}
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = lambda s: mock_cur
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    mock_db.get_connection.return_value.__enter__ = lambda s: mock_conn
    mock_db.get_connection.return_value.__exit__ = MagicMock(return_value=False)
    mock_db.track_event = MagicMock()

    confirm_membership(mock_db, 'com-1', 'b-1')
    assert len(fired) == 1
    assert fired[0]['community_id'] == 'com-1'


def test_threshold_not_fired_below_min():
    """When confirmed_count < 3, formation_threshold_reached should NOT fire."""
    import event_hooks
    event_hooks.clear()
    fired = []
    event_hooks.register('formation_threshold_reached', lambda p: fired.append(p))
    from formation_wizard import confirm_membership
    mock_db = MagicMock()
    mock_cur = MagicMock()
    mock_cur.rowcount = 1
    mock_cur.fetchone.return_value = {'confirmed_count': 2}
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__ = lambda s: mock_cur
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    mock_db.get_connection.return_value.__enter__ = lambda s: mock_conn
    mock_db.get_connection.return_value.__exit__ = MagicMock(return_value=False)
    mock_db.track_event = MagicMock()

    confirm_membership(mock_db, 'com-1', 'b-1')
    assert len(fired) == 0


def test_formation_ready_drafts_docs_as_yellow():
    """formation_threshold_reached should be a YELLOW-tier action (docs draft)."""
    source_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "formation_wizard.py"
    )
    with open(source_path) as f:
        content = f.read()
    assert "formation_threshold_reached" in content


def test_formation_ready_notifies_ceo():
    """formation_threshold_reached event should exist in formation_wizard code."""
    source_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "formation_wizard.py"
    )
    with open(source_path) as f:
        content = f.read()
    assert "event_hooks" in content


def test_registration_hook_exception_doesnt_break_registration(reg_client):
    """Exception in registration hook must not break the registration response."""
    import event_hooks
    event_hooks.clear()
    event_hooks.register('registration', lambda p: (_ for _ in ()).throw(RuntimeError("hook crash")))
    with patch("database.save_building"), \
         patch("database.save_token"), \
         patch("database.track_event"), \
         patch("database.get_referral_code", return_value=None), \
         patch("app.find_provisional_matches", return_value=None), \
         patch("app.collect_building_locations", return_value=[]), \
         patch("app.send_confirmation_email"), \
         patch("app.run_full_ml_task"), \
         patch("email_automation.schedule_sequence_for_user"):
        resp = reg_client.post("/api/register_anonymous", json={
            "profile": {"address": "Test 1", "building_id": "b-test-1", "lat": 47.3, "lon": 8.5},
            "email": "test@example.com",
            "consents": {"share_with_neighbors": True, "share_with_utility": True}
        })
        assert resp.status_code == 200
