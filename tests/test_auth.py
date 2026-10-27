import app as growlog


def test_unauthenticated_api_is_401(make_client):
    assert make_client().get("/api/state").status_code == 401


def test_redirect_to_setup_without_users(make_client):
    r = make_client().get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/setup")


def test_setup_creates_admin_once(make_client):
    c = make_client()
    r = c.post("/setup", data={"username": "erik", "password": "geheim123", "password2": "geheim123"})
    assert r.status_code == 302
    assert c.get("/api/me").get_json()["role"] == "admin"
    r2 = make_client().post("/setup", data={"username": "xyz", "password": "geheim123", "password2": "geheim123"})
    assert r2.status_code == 302 and r2.headers["Location"].endswith("/login")


def test_setup_rejects_short_password(make_client):
    r = make_client().post("/setup", data={"username": "erik", "password": "kurz", "password2": "kurz"})
    assert r.status_code == 400


def test_login_wrong_password_and_rate_limit(login, make_client):
    login("erik")
    c = make_client()
    for _ in range(5):
        assert c.post("/login", data={"username": "erik", "password": "falsch"}).status_code == 401
    assert c.post("/login", data={"username": "erik", "password": "geheim123"}).status_code == 429


def test_inactive_user_cannot_use_session(login, admin):
    c, uid = login("anna")
    assert c.get("/api/me").status_code == 200
    assert admin.put(f"/api/users/{uid}", json={"active": False}).status_code == 200
    assert c.get("/api/me").status_code == 401


def test_password_change_invalidates_old_session(login):
    c, _ = login("anna")
    other = growlog.app.test_client()
    other.post("/login", data={"username": "anna", "password": "geheim123"})
    r = c.put("/api/me", json={"current_password": "geheim123", "new_password": "neuesPasswort1"})
    assert r.status_code == 200
    assert c.get("/api/me").status_code == 200  # aktuelle Sitzung bekommt neues Cookie
    assert other.get("/api/me").status_code == 401


def test_invite_is_single_use(admin, make_client):
    token = admin.post("/api/invites").get_json()["token"]
    c = make_client()
    data = {"token": token, "username": "neu", "password": "geheim123", "password2": "geheim123"}
    assert c.post("/register", data=data).status_code == 302
    assert c.get("/api/me").get_json()["role"] == "user"
    data["username"] = "neu2"
    assert make_client().post("/register", data=data).status_code == 400


def test_admin_endpoints_forbidden_for_user(login):
    c, _ = login("anna")
    assert c.get("/api/users").status_code == 403
    assert c.post("/api/invites").status_code == 403
    assert c.get("/api/backup").status_code == 403
    assert c.put("/api/config", json={"tz": "UTC"}).status_code == 403


def test_last_admin_cannot_be_demoted_or_deleted(login):
    first, first_id = login("erster", "admin")
    _, second_id = login("zweiter", "admin")
    assert first.put(f"/api/users/{first_id}", json={"role": "user"}).status_code == 400  # selbst
    assert first.delete(f"/api/users/{first_id}").status_code == 400
    assert first.delete(f"/api/users/{second_id}").status_code == 200
    assert first.get("/api/users").get_json()["users"][0]["role"] == "admin"
