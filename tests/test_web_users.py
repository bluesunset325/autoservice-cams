def uid(env, login):
    return env.users.get_by_login(login).id


def test_viewer_cannot_open_users(login):
    assert login("viewer").get("/users").status_code == 403


def test_admin_sees_all_users_and_roles(login):
    html = login("admin").get("/users").text
    for name in ("admin", "owner", "viewer"):
        assert name in html
    assert '<option value="admin"' in html


def test_admin_creates_owner(env, login):
    r = login("admin").post("/users", data={"login": "boss2", "password": "secret1", "role": "owner"},
                            follow_redirects=False)
    assert r.status_code == 303
    assert env.users.get_by_login("boss2").role == "owner"


def test_owner_creates_only_viewers(env, login):
    client = login("owner")
    html = client.get("/users").text
    assert '<option value="admin"' not in html and '<option value="owner"' not in html
    assert client.post("/users", data={"login": "worker1", "password": "secret1", "role": "viewer"},
                       follow_redirects=False).status_code == 303
    assert client.post("/users", data={"login": "evil", "password": "secret1", "role": "admin"}).status_code == 403
    assert env.users.get_by_login("evil") is None


def test_duplicate_login_shows_error(login):
    r = login("admin").post("/users", data={"login": "viewer", "password": "secret1", "role": "viewer"})
    assert r.status_code == 400
    assert "Логин уже занят" in r.text


def test_owner_deletes_viewer_but_not_admin(env, login):
    client = login("owner")
    assert client.post(f"/users/{uid(env, 'admin')}/delete").status_code == 403
    assert client.post(f"/users/{uid(env, 'viewer')}/delete", follow_redirects=False).status_code == 303
    assert env.users.get_by_login("viewer") is None


def test_cannot_delete_self(env, login):
    r = login("admin").post(f"/users/{uid(env, 'admin')}/delete")
    assert r.status_code == 400
    assert "Нельзя удалить самого себя" in r.text


def test_admin_changes_viewer_password(env, login):
    r = login("admin").post(f"/users/{uid(env, 'viewer')}/password", data={"password": "newpass1"},
                            follow_redirects=False)
    assert r.status_code == 303
    assert env.users.authenticate("viewer", "newpass1") is not None


def test_owner_changes_own_password(env, login):
    r = login("owner").post(f"/users/{uid(env, 'owner')}/password", data={"password": "newpass1"},
                            follow_redirects=False)
    assert r.status_code == 303
    assert env.users.authenticate("owner", "newpass1") is not None


def test_short_password_rejected(env, login):
    r = login("admin").post(f"/users/{uid(env, 'viewer')}/password", data={"password": "123"})
    assert r.status_code == 400


# Privilege escalation prevention tests
def test_owner_cannot_change_admin_password(env, login):
    admin_id = uid(env, "admin")
    r = login("owner").post(f"/users/{admin_id}/password", data={"password": "newpass1"})
    assert r.status_code == 403
    # Verify nothing changed - admin can still auth with old password
    assert env.users.authenticate("admin", "secret1") is not None


def test_owner_cannot_change_second_owner_password(env, login):
    # Create a second owner
    env.users.create("boss2", "secret1", "owner")
    boss2_id = uid(env, "boss2")
    r = login("owner").post(f"/users/{boss2_id}/password", data={"password": "newpass1"})
    assert r.status_code == 403
    # Verify nothing changed - boss2 can still auth with old password
    assert env.users.authenticate("boss2", "secret1") is not None


def test_owner_cannot_create_owner(env, login):
    r = login("owner").post("/users", data={"login": "badowner", "password": "secret1", "role": "owner"})
    assert r.status_code == 403
    # Verify user was not created
    assert env.users.get_by_login("badowner") is None


def test_owner_cannot_delete_second_owner(env, login):
    # Create a second owner
    env.users.create("boss2", "secret1", "owner")
    boss2_id = uid(env, "boss2")
    r = login("owner").post(f"/users/{boss2_id}/delete")
    assert r.status_code == 403
    # Verify user still exists
    assert env.users.get_by_login("boss2") is not None


def test_viewer_cannot_create_users(login):
    r = login("viewer").post("/users", data={"login": "newuser", "password": "secret1", "role": "viewer"})
    assert r.status_code == 403


def test_viewer_cannot_delete_users(env, login):
    viewer_id = uid(env, "viewer")
    r = login("viewer").post(f"/users/{viewer_id}/delete")
    assert r.status_code == 403
    # Verify user still exists
    assert env.users.get_by_login("viewer") is not None


def test_viewer_cannot_change_password(env, login):
    viewer_id = uid(env, "viewer")
    r = login("viewer").post(f"/users/{viewer_id}/password", data={"password": "newpass1"})
    assert r.status_code == 403
    # Old password still works
    assert env.users.authenticate("viewer", "secret1") is not None


def test_admin_cannot_create_invalid_role(env, login):
    r = login("admin").post("/users", data={"login": "invalid", "password": "secret1", "role": "root"})
    assert r.status_code == 403
    # Verify user was not created
    assert env.users.get_by_login("invalid") is None


def test_admin_cannot_delete_nonexistent_user(login):
    r = login("admin").post("/users/999/delete")
    assert r.status_code == 404


def test_admin_cannot_change_password_of_nonexistent_user(login):
    r = login("admin").post("/users/999/password", data={"password": "newpass1"})
    assert r.status_code == 404
