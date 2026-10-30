"""Field-level fallback: a bank-signed e-advice.

When a bank's net-banking page does not show a required field (most often the lien/pledge),
the bank-session proof ends as Insufficient evidence. The institution can then obtain a
digitally signed e-advice from its bank and upload it. Scourge accepts it ONLY for the fields
the session proof could not show, and only if:
  * the signing certificate chains to a trusted bank root and is valid now,
  * the certificate is issued to the bank host registered for this FDR (and that host is approved),
  * the signature over the canonical payload verifies,
  * the payload names this FD and this bank, and was issued within the last 30 days.
It is weaker evidence than a session proof (it shows what the bank signed on a date, not the live
page), so it is labelled "bank-signed e-advice" everywhere and never replaces a session proof.

DEMO: the synthetic bank's e-advice desk signs with the synthetic bank key (test CA)."""
import base64
import json
import time
from datetime import datetime, timezone

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

TYPE = "scourge-e-advice/v1"
MAX_AGE_DAYS = 30
FIELDS = ("status", "lien", "holders")


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sign(key, data: bytes) -> bytes:
    if isinstance(key, rsa.RSAPrivateKey):
        return key.sign(data, padding.PKCS1v15(), hashes.SHA256())
    if isinstance(key, ec.EllipticCurvePrivateKey):
        return key.sign(data, ec.ECDSA(hashes.SHA256()))
    raise ValueError("unsupported key type")


def _verify(pub, sig: bytes, data: bytes):
    if isinstance(pub, rsa.RSAPublicKey):
        pub.verify(sig, data, padding.PKCS1v15(), hashes.SHA256())
    elif isinstance(pub, ec.EllipticCurvePublicKey):
        pub.verify(sig, data, ec.ECDSA(hashes.SHA256()))
    else:
        raise InvalidSignature("unsupported key type")


def issue(bank_host: str, fd_number: str, fields: dict, key_pem: bytes, cert_pem: bytes, issued_at: float | None = None) -> dict:
    """What a bank's e-advice desk would produce (used by the synthetic bank only)."""
    payload = {"type": TYPE, "bank_host": bank_host, "fd_number": fd_number,
               "fields": {k: str(v) for k, v in fields.items() if k in FIELDS},
               "issued_at": int(issued_at if issued_at is not None else time.time())}
    key = serialization.load_pem_private_key(key_pem, password=None)
    return {"payload": payload, "signature": base64.b64encode(_sign(key, canonical(payload))).decode(),
            "certificate": cert_pem.decode()}


def verify(doc: dict, fdr: dict, ca_der: bytes, allow_hosts) -> dict:
    """Returns {ok, reason, fields, signer, issued_at}. Never raises on bad input."""
    def bad(reason):
        return {"ok": False, "reason": reason, "fields": {}, "signer": None, "issued_at": None}
    try:
        payload, sig, cert_pem = doc["payload"], base64.b64decode(doc["signature"], validate=True), doc["certificate"].encode()
        if payload.get("type") != TYPE:
            return bad("Not a Scourge e-advice (unknown type).")
        cert = x509.load_pem_x509_certificate(cert_pem)
        ca = x509.load_der_x509_certificate(ca_der)
    except Exception:
        return bad("The file is not a readable signed e-advice.")
    try:
        cert.verify_directly_issued_by(ca)
    except Exception:
        return bad("The signing certificate is not issued by a trusted bank root.")
    now = datetime.now(timezone.utc)
    if not (cert.not_valid_before_utc <= now <= cert.not_valid_after_utc):
        return bad("The signing certificate is not valid today.")
    try:
        names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        names = []
    if fdr["bank_host"] not in allow_hosts:
        return bad("This FDR's bank is not on AICTE's approved list.")
    if fdr["bank_host"] not in names:
        return bad("The e-advice was signed by a different bank than the one registered for this FDR.")
    try:
        _verify(cert.public_key(), sig, canonical(payload))
    except Exception:
        return bad("The signature does not match the contents: the e-advice was altered or forged.")
    if payload.get("bank_host") != fdr["bank_host"] or payload.get("fd_number") != fdr["fd_number"]:
        return bad("The e-advice is for a different deposit or bank.")
    age = time.time() - float(payload.get("issued_at", 0))
    if age < -300:
        return bad("The e-advice is dated in the future.")
    if age > MAX_AGE_DAYS * 86400:
        return bad(f"The e-advice is older than {MAX_AGE_DAYS} days.")
    fields = {k: v for k, v in (payload.get("fields") or {}).items() if k in FIELDS}
    if not fields:
        return bad("The e-advice does not state any usable field.")
    return {"ok": True, "reason": None, "fields": fields, "signer": names[0] if names else "", "issued_at": payload["issued_at"]}
