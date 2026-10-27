import app as growlog


def seed(c, key="AA", name="Alpha"):
    assert c.post("/api/plants", json={"name": name, "key": key}).status_code == 200
    assert c.post("/api/weigh", json={"ts": "2026-10-20T07:00:00Z", "values": {key: 3000}}).status_code == 200
    assert c.post("/api/water", json={"ts": "2026-10-20T08:00:00Z", "ml": {key: 300}}).status_code == 200
    assert c.post("/api/note", json={"text": "geheim", "plant": key}).status_code == 200


def test_state_only_shows_own_data(login):
    a, _ = login("anna")
    b, _ = login("bernd")
    seed(a)
    sb = b.get("/api/state").get_json()
    assert sb["plants"] == [] and sb["weigh"] == [] and sb["water"] == [] and sb["notes"] == []
    sa = a.get("/api/state").get_json()
    assert len(sa["plants"]) == 1 and len(sa["weigh"]) == 1 and len(sa["notes"]) == 1


def test_cannot_delete_foreign_rows(login):
    a, _ = login("anna")
    b, _ = login("bernd")
    seed(a)
    ids = ",".join(map(str, a.get("/api/state").get_json()["weigh"][0]["ids"]))
    b.delete(f"/api/weigh/{ids}")
    assert len(a.get("/api/state").get_json()["weigh"]) == 1


def test_cannot_edit_foreign_plant(login):
    a, _ = login("anna")
    b, _ = login("bernd")
    seed(a)
    assert b.put("/api/plants/AA", json={"name": "X"}).status_code == 404
    b.delete("/api/plants/AA")
    plants = a.get("/api/state").get_json()["plants"]
    assert plants[0]["name"] == "Alpha" and plants[0]["active"] is True


def test_same_plant_key_allowed_for_different_users(login):
    a, _ = login("anna")
    b, _ = login("bernd")
    seed(a)
    seed(b, name="Beta")
    assert a.get("/api/state").get_json()["plants"][0]["name"] == "Alpha"
    assert b.get("/api/state").get_json()["plants"][0]["name"] == "Beta"


def test_weigh_rejects_unknown_plant_and_nonsense(login):
    a, _ = login("anna")
    seed(a)
    assert a.post("/api/weigh", json={"values": {"ZZ": 3000}}).status_code == 400
    assert a.post("/api/weigh", json={"values": {"AA": 5}}).status_code == 400


def test_profile_sharing(login):
    a, aid = login("anna")
    b, _ = login("bernd")
    seed(a)
    assert b.get("/api/community").get_json()["users"] == []
    assert b.get(f"/api/profile/{aid}").status_code == 404
    a.put("/api/me", json={"share": True})
    assert [u["id"] for u in b.get("/api/community").get_json()["users"]] == [aid]
    prof = b.get(f"/api/profile/{aid}").get_json()
    assert prof["username"] == "anna" and len(prof["weigh"]) == 1 and len(prof["water"]) == 1
    assert "geheim" not in str(prof) and "webhook" not in prof and "notes" not in prof
    assert a.get(f"/api/profile/{aid}").status_code == 404  # nicht das eigene Profil


def test_webhook_validation(login):
    a, _ = login("anna")
    assert a.put("/api/me", json={"webhook": "https://evil.example/api/webhooks/1/x"}).status_code == 400
    ok = "https://discord.com/api/webhooks/123/abc-DEF_1"
    assert a.put("/api/me", json={"webhook": ok}).status_code == 200
    state = a.get("/api/state").get_json()
    assert state["webhook"] is True and ok not in str(state)


def test_deleting_user_removes_their_data(admin, login):
    b, bid = login("bernd")
    seed(b)
    assert admin.delete(f"/api/users/{bid}").status_code == 200
    conn = growlog.sqlite3.connect(growlog.DB_PATH)
    for t in ("weigh", "water", "note", "plants"):
        assert conn.execute(f"SELECT COUNT(*) FROM {t} WHERE user_id=?", (bid,)).fetchone()[0] == 0
    conn.close()
