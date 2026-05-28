"""Health check route handler."""


def healthcheck():
    return {"status": "ok"}, 200
