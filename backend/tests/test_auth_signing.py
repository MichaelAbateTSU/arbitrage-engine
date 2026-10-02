import base64

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from pydantic import SecretStr

from app.config import Settings
from app.venues.http import VenueError
from app.venues.streams import kalshi_headers


@pytest.mark.parametrize("key_type", ["rsa", "ed25519"])
@pytest.mark.parametrize("key_format", ["pem", "escaped_pem", "base64_der"])
def test_kalshi_signs_registered_key_type(key_type, key_format):
    key = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        if key_type == "rsa"
        else ed25519.Ed25519PrivateKey.generate()
    )
    encoding = (
        serialization.Encoding.DER if key_format == "base64_der" else serialization.Encoding.PEM
    )
    value = key.private_bytes(
        encoding,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    text = base64.b64encode(value).decode() if key_format == "base64_der" else value.decode()
    if key_format == "escaped_pem":
        text = text.replace("\n", "\\n")
    settings = Settings(kalshi_api_key=SecretStr("test-key-id"), kalshi_private_key=SecretStr(text))
    headers = kalshi_headers(settings)
    message = f"{headers['KALSHI-ACCESS-TIMESTAMP']}GET/trade-api/ws/v2".encode()
    signature = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"], validate=True)
    if isinstance(key, ed25519.Ed25519PrivateKey):
        key.public_key().verify(signature, message)
    else:
        key.public_key().verify(
            signature,
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )


def test_kalshi_rejects_unsupported_key_type():
    key = ec.generate_private_key(ec.SECP256R1())
    value = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    settings = Settings(
        kalshi_api_key=SecretStr("test-key-id"), kalshi_private_key=SecretStr(value)
    )
    with pytest.raises(VenueError, match="INVALID_KALSHI_KEY_TYPE"):
        kalshi_headers(settings)


def test_kalshi_invalid_key_does_not_leak_value():
    value = "invalid-sensitive-test-value"
    settings = Settings(
        kalshi_api_key=SecretStr("test-key-id"), kalshi_private_key=SecretStr(value)
    )
    with pytest.raises(VenueError) as exc:
        kalshi_headers(settings)
    assert value not in str(exc.value)
