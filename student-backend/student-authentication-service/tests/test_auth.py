from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.routes.auth import get_db
from app.models.student import Student

from datetime import datetime, timedelta, timezone
import hashlib

from app.models.password_reset_token import PasswordSetupResetToken
from app.services.password import hash_password, verify_password


class FakeQuery:
    def __init__(self, student):
        self.student = student

    def filter(self, *args):
        return self

    def first(self):
        return self.student


class FakeDB:
    def __init__(self, student):
        self.student = student

    def query(self, model):
        return FakeQuery(self.student)

    def close(self):
        pass


student = Student(
    student_id=uuid4(),
    first_name="Test",
    last_name="Student",
    email="test.automation@example.com",
    password_hash="test-hash",
    status="ACTIVE",
)


def override_get_db():
    yield FakeDB(student)


app.dependency_overrides[get_db] = override_get_db


def test_login_success(monkeypatch):
    client = TestClient(app)

    monkeypatch.setattr(
        "app.routes.auth.verify_password",
        lambda password, password_hash: True,
    )

    response = client.post(
        "/auth/login",
        json={
            "login": student.email,
            "password": "test-password",
        },
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Login successful"


def test_login_invalid_credentials(monkeypatch):
    client = TestClient(app)

    monkeypatch.setattr(
        "app.routes.auth.verify_password",
        lambda password, password_hash: False,
    )

    response = client.post(
        "/auth/login",
        json={
            "login": student.email,
            "password": "wrong-password",
        },
    )

    assert response.status_code == 401


def test_me_requires_authentication():
    client = TestClient(app)

    response = client.get("/auth/me")

    assert response.status_code == 401


def test_logout(monkeypatch):
    client = TestClient(app)

    monkeypatch.setattr(
        "app.routes.auth.verify_password",
        lambda password, password_hash: True,
    )

    login_response = client.post(
        "/auth/login",
        json={
            "login": student.email,
            "password": "test-password",
        },
    )

    assert login_response.status_code == 200

    logout_response = client.post("/auth/logout")

    assert logout_response.status_code == 200
    assert logout_response.json()["message"] == "Logout successful"

    me_response = client.get("/auth/me")

    assert me_response.status_code == 401