import pytest
from cryptography.fernet import Fernet

from kagnode import create_app, db


@pytest.fixture(scope="session")
def known_hash():
    return db.password_hash("CorrectPassword123!")


@pytest.fixture
def app(tmp_path, known_hash):
    path = tmp_path / "test.sqlite3"
    db.init(path)
    with db.connect(path) as c:
        c.execute("INSERT INTO domains VALUES('example.com')")
        for name, admin in [("admin", 1), ("member", 0), ("other", 0)]:
            c.execute(
                "INSERT INTO users(email,name,password,admin) VALUES(?,?,?,?)",
                (name + "@example.com", name, known_hash, admin),
            )
        c.execute("INSERT INTO aliases VALUES('hello@example.com','admin@example.com')")
    app = create_app(
        dict(
            TESTING=True,
            DATABASE=str(path),
            SECRET_KEY="test-secret",
            CREDENTIAL_KEY=Fernet.generate_key().decode(),
            SESSION_COOKIE_SECURE=False,
            MAIL_HOSTNAME="mail.example.com",
            PUBLIC_IPV4="203.0.113.10",
            DKIM_PUBLIC="v=DKIM1; k=rsa; p=test",
        )
    )
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


def login(client, name="admin"):
    csrf = client.get("/api/session").json["csrf"]
    r = client.post(
        "/api/login",
        json={"email": name + "@example.com", "password": "CorrectPassword123!"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.json
    return {"X-CSRF-Token": r.json["csrf"]}
