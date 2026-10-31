from datetime import datetime, timedelta, timezone

import pytest

import app as growlog

HOOK = "https://discord.com/api/webhooks/123/abc-DEF_1"


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(growlog, "post_webhook", lambda hook, text: calls.append((hook, text)))
    return calls


def setup_user(login, remind="17:00"):
    c, uid = login("anna")
    c.put("/api/me", json={"webhook": HOOK})
    c.post("/api/plants", json={"name": "Alpha", "key": "AA"})
    c.post("/api/weigh", json={"ts": "2026-10-20T07:00:00Z", "values": {"AA": 3000}})
    c.put("/api/plants/AA", json={"target": 3300})
    if remind is not None:
        assert c.put("/api/settings", json={"remind_time": remind}).status_code == 200
    return c, uid


def at(hour, minute=0):
    local = datetime(2026, 10, 27, hour, minute, tzinfo=growlog.TZ)
    return local.astimezone(timezone.utc)


def test_reminder_sent_once_per_day(login, sent):
    setup_user(login)
    growlog.send_due_reminders(at(17, 5))
    growlog.send_due_reminders(at(17, 40))
    assert len(sent) == 1
    hook, text = sent[0]
    assert hook == HOOK and "AA" in text and "Ziel 3300 g" in text
    growlog.send_due_reminders(at(17, 5) + timedelta(days=1))
    assert len(sent) == 2


def test_reminder_not_before_time_or_when_stale(login, sent):
    setup_user(login)
    growlog.send_due_reminders(at(16, 59))
    growlog.send_due_reminders(at(21, 0))  # mehr als 3 h zu spät (z. B. nach Neustart)
    assert sent == []


def test_reminder_off_by_default(login, sent):
    setup_user(login, remind=None)
    growlog.send_due_reminders(at(17, 5))
    assert sent == []


def test_reminder_needs_webhook(login):
    c, _ = login("anna")
    assert c.put("/api/settings", json={"remind_time": "17:00"}).status_code == 400
    assert c.put("/api/settings", json={"remind_time": ""}).status_code == 200


def test_reminder_failure_does_not_repeat(login, monkeypatch):
    setup_user(login)
    calls = []

    def boom(hook, text):
        calls.append(1)
        raise growlog.urllib.error.URLError("down")

    monkeypatch.setattr(growlog, "post_webhook", boom)
    growlog.send_due_reminders(at(17, 5))
    growlog.send_due_reminders(at(17, 6))
    assert len(calls) == 1


def test_reminders_are_per_user(login, sent):
    setup_user(login)
    other, _ = login("bernd")
    other.put("/api/me", json={"webhook": "https://discord.com/api/webhooks/999/zzz"})
    growlog.send_due_reminders(at(17, 5))
    assert len(sent) == 1 and sent[0][0] == HOOK
