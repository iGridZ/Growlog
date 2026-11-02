import json

import app as growlog


def test_csv_safe_blocks_formulas():
    assert growlog.csv_safe("=1+1") == "'=1+1"
    assert growlog.csv_safe("@x") == "'@x"
    assert growlog.csv_safe("Notiz") == "Notiz"
    assert growlog.csv_safe(5) == 5


def test_csv_export_escapes_formula_in_note(login):
    c, _ = login("anna")
    c.post("/api/plants", json={"name": "Blueberry Muffin"})
    c.post("/api/note", json={"text": "=HYPERLINK(1)"})
    body = c.get("/api/csv/notes").data.decode("utf-8")
    assert "'=HYPERLINK(1)" in body


def test_note_ignores_unknown_plant_and_limits_length(login):
    c, _ = login("anna")
    c.post("/api/note", json={"text": "x" * 5000, "plant": "<img>"})
    note = c.get("/api/state").get_json()["notes"][0]
    assert note["plant"] is None and len(note["text"]) == 2000


def test_bad_timestamp_is_400(login):
    c, _ = login("anna")
    c.post("/api/plants", json={"name": "Rainbow Soap", "key": "RS"})
    r = c.post("/api/weigh", json={"ts": "kein-datum", "values": {"RS": 3000}})
    assert r.status_code == 400


def test_plant_target_range_and_non_string_name(login):
    c, _ = login("anna")
    c.post("/api/plants", json={"name": "Rainbow Soap", "key": "RS"})
    assert c.put("/api/plants/RS", json={"target": 5}).status_code == 400
    assert c.put("/api/plants/RS", json={"target": 2500, "name": 123}).status_code == 200


def test_post_webhook_keeps_code_fence_when_truncated(monkeypatch):
    sent = {}

    class Resp:
        def read(self):
            return b""

    def fake_open(req, timeout):
        sent.update(json.loads(req.data))
        return Resp()

    monkeypatch.setattr(growlog.urllib.request, "urlopen", fake_open)
    growlog.post_webhook("https://discord.com/api/webhooks/1/x", "```\n" + "a\n" * 2000 + "```")
    assert len(sent["content"]) <= 2000 and sent["content"].endswith("```")


def test_invite_claim_is_atomic(admin, make_client):
    token = admin.post("/api/invites").get_json()["token"]
    c1, c2 = make_client(), make_client()
    form = {"token": token, "password": "geheim123", "password2": "geheim123"}
    assert c1.post("/register", data={**form, "username": "bob"}).status_code == 302
    assert c2.post("/register", data={**form, "username": "carl"}).status_code == 400
