from __future__ import annotations

import base64

from backend.app.clients.secrets import decode_secret_value


def test_decode_secret_value_handles_plain_and_base64() -> None:
    assert decode_secret_value("plain-secret") == "plain-secret"
    encoded = base64.b64encode(b"encoded-secret").decode()
    assert decode_secret_value(encoded) == "encoded-secret"
    assert decode_secret_value("-") == ""
    assert decode_secret_value(None) == ""
