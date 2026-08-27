import base64
import hashlib
import hmac

from linebot_meeting.line_api import split_text, target_from_source, verify_signature


def test_signature_verification() -> None:
    body = b'{"events":[]}'
    secret = "channel-secret"
    signature = base64.b64encode(
        hmac.new(secret.encode(), body, hashlib.sha256).digest()
    ).decode()
    assert verify_signature(body, signature, secret)
    assert not verify_signature(body + b" ", signature, secret)


def test_target_for_all_chat_types() -> None:
    assert target_from_source({"type": "user", "userId": "U1"}) == "U1"
    assert target_from_source({"type": "group", "groupId": "G1"}) == "G1"
    assert target_from_source({"type": "room", "roomId": "R1"}) == "R1"


def test_split_text_preserves_content() -> None:
    text = "第一行\n" + "長" * 30 + "\n最後"
    parts = split_text(text, limit=10)
    assert all(len(part) <= 10 for part in parts)
    assert "".join(parts).replace("\n", "") == text.replace("\n", "")
