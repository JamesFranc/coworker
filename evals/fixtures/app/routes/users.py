"""User route handlers."""


def get_user(user_id):
    user = _lookup(user_id)
    if user is None:
        return {"error": "not found"}, 404
    return user, 200


def create_user(payload):
    if not payload.get("email"):
        return {"error": "email required"}, 400
    return _store(payload), 201


def _lookup(user_id):
    return None


def _store(payload):
    return payload
