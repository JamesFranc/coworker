"""Sample tests used as a style reference for generated test files."""

import pytest

from app.routes.users import create_user, get_user


def test_get_user_missing_returns_404():
    body, status = get_user("nope")
    assert status == 404
    assert body == {"error": "not found"}


def test_create_user_requires_email():
    body, status = create_user({})
    assert status == 400
    assert body == {"error": "email required"}


@pytest.mark.parametrize("payload", [{"email": "a@b.com"}, {"email": "c@d.com"}])
def test_create_user_succeeds_with_email(payload):
    body, status = create_user(payload)
    assert status == 201
    assert body == payload
