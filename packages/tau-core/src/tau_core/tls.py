"""Self-signed TLS certificate for the Phase 27.D pairing flow (project-tau-plan.md §8.28's 27.D
Milestone 3): a real transport-security upgrade for the desktop client's discovery+pairing path,
independent of the existing plain-HTTP bridge (`tau_core.web.server`), which stays exactly as it
is - "the LAN is the perimeter" - for the kiosk/browser clients that already work that way.

The certificate's fingerprint IS the pairing code (see `fingerprint_sha256`'s docstring) - no
separate code needs to be computed or exchanged, so this module's only two jobs are "make sure a
cert exists" and "report its fingerprint in the format a human can compare by eye."
"""

from __future__ import annotations

import datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

# 10 years: this cert only ever needs to outlive a single pairing's trust decision, not rotate on
# any meaningful schedule - regenerating it would just invalidate every existing pin, which is a
# manual re-pair, not a security improvement.
_VALIDITY_DAYS = 3650


def get_or_create_cert(cert_path: Path, key_path: Path) -> None:
    """Generates a self-signed RSA cert + key at the given paths if they don't already exist.
    Idempotent - safe to call on every startup, matching the "just make sure this exists" shape
    of `crypto_store`'s salt-file handling.
    """
    if cert_path.exists() and key_path.exists():
        return

    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "tau-core")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=_VALIDITY_DAYS))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("tau-core")]), critical=False)
        .sign(key, hashes.SHA256())
    )

    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def fingerprint_sha256(cert_path: Path) -> str:
    """SHA-256 fingerprint of the DER-encoded certificate, colon-grouped uppercase hex
    (`AA:BB:CC:...`) - the same format `openssl x509 -fingerprint` and most browsers show, so a
    human pairing a device can visually compare this against whatever their TLS client displays
    without translating formats in their head. This value is not a secret: showing it openly is
    the entire point of trust-on-first-use verification (same reasoning as an SSH host key
    fingerprint or a Signal safety number) - it authenticates the channel, it doesn't guard it.
    """
    pem_bytes = cert_path.read_bytes()
    cert = x509.load_pem_x509_certificate(pem_bytes)
    digest = cert.fingerprint(hashes.SHA256())
    return ":".join(f"{byte:02X}" for byte in digest)
