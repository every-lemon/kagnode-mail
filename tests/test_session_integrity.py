from kagnode import db
from conftest import login


def test_login_cannot_reissue_a_session_after_concurrent_password_reset(app, client, monkeypatch):
    csrf = client.get('/api/session').json['csrf']
    original = db.verify
    def reset_during_verification(password, encoded):
        result = original(password, encoded)
        with db.connect(app.config['DATABASE']) as conn:
            conn.execute("UPDATE users SET password=? WHERE email='member@example.com'", ('changed-after-verification',))
        return result
    monkeypatch.setattr(db, 'verify', reset_during_verification)
    response = client.post('/api/login', json={'email':'member@example.com','password':'CorrectPassword123!'}, headers={'X-CSRF-Token':csrf})
    assert response.status_code == 401
    with db.connect(app.config['DATABASE']) as conn:
        assert conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == 0


def test_reauthentication_cannot_report_success_after_session_revocation(app, client, monkeypatch):
    headers = login(client)
    original = db.verify
    def revoke_during_verification(password, encoded):
        result = original(password, encoded)
        with db.connect(app.config['DATABASE']) as conn:
            conn.execute('DELETE FROM sessions')
        return result
    monkeypatch.setattr(db, 'verify', revoke_during_verification)
    response = client.post('/api/account/reauth', json={'password':'CorrectPassword123!'}, headers=headers)
    assert response.status_code == 401
    assert 'Set-Cookie' not in response.headers


def test_security_updates_invalidate_web_sessions_at_database_boundary(app, client):
    for update in ["password='changed-hash'", 'active=0', 'admin=0']:
        with db.connect(app.config['DATABASE']) as conn:
            conn.execute("UPDATE users SET password=(SELECT password FROM users WHERE email='other@example.com'),active=1,admin=1 WHERE email='admin@example.com'")
        login(client)
        with db.connect(app.config['DATABASE']) as conn:
            conn.execute("UPDATE users SET " + update + " WHERE email='admin@example.com'")
            assert conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == 0
        assert client.get('/api/session').json['user'] is None


def test_non_ascii_csrf_is_a_forbidden_request_not_an_encoding_error(client):
    login(client)
    response = client.post('/api/logout', json={}, headers={'X-CSRF-Token':'ß'})
    assert response.status_code == 403
