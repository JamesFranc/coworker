"""Order route handlers."""

import time

REQUEST_TIMEOUT = 30  # seconds


def get_order(order_id):
    order = _fetch(order_id, timeout=REQUEST_TIMEOUT)
    if order is None:
        return {"error": "not found"}, 404
    return order, 200


def cancel_order(order_id):
    if not _can_cancel(order_id):
        return {"error": "conflict"}, 409
    return {"status": "cancelled"}, 200


def _fetch(order_id, timeout):
    time.sleep(0)
    return None


def _can_cancel(order_id):
    return False
