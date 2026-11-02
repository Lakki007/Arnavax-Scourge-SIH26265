"""Paths, configuration and secrets. Secrets are generated on first run into var/ (mode 0600)
and never rendered into pages, logs or evidence."""
import json
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VAR = Path(os.environ.get("SCOURGE_VAR", ROOT / "var"))
def _bin_dir() -> Path:
    """Prebuilt proof binaries per platform: bin/<os>-<arch>/ when present (e.g. bin/darwin-arm64), else bin/ (Linux x86-64)."""
    import platform
    env = os.environ.get("SCOURGE_BIN")
    if env:
        return Path(env)
    plat = ROOT / "bin" / f"{platform.system().lower()}-{platform.machine().lower()}"
    return plat if (plat / "scourge_verifierd").exists() else ROOT / "bin"


BIN = _bin_dir()
CONFIG_PATH = ROOT / "config" / "scourge.json"
DEMO_PATH = ROOT / "config" / "demo.json"
DB_PATH = VAR / "scourge.db"
BANK_STATE = VAR / "bank_state.json"
LOGS = VAR / "logs"
UPLOADS = VAR / "uploads"
JOBTMP = VAR / "jobs"

for d in (VAR, LOGS, UPLOADS, JOBTMP):
    d.mkdir(parents=True, exist_ok=True)

VERIFIER_CFG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
DEMO = json.loads(DEMO_PATH.read_text(encoding="utf-8"))

# Verifier placement. "local" (default): this app starts scourge_verifierd as a separate process on this host.
# "external": AICTE runs the verifier on its own host/container (deploy/verifier/); the app never starts or
# stops it, provers are pointed at SCOURGE_VERIFIER_ADDR, and the verifier reports back to the app's
# internal API over a private network with the shared SCOURGE_INTERNAL_KEY.
VERIFIER_MODE = os.environ.get("SCOURGE_VERIFIER", "local")
# The verifier signs every result with its own key. The portal pins its public key: SCOURGE_VERIFIER_PUBKEY (hex),
# else the file the verifier publishes (local mode: next to the key in var/; external mode: a read-only mount).
VERIFIER_KEY_FILE = Path(os.environ.get("SCOURGE_VERIFIER_KEY", VAR / "verifier_signing.key"))
# The browser-extension bridge has its OWN key. Results it signs are recorded as "extension, unproven" and are
# capped below Verified until the extension's MPC proof exists; it never holds the verifier key.
BRIDGE_KEY_FILE = Path(os.environ.get("SCOURGE_BRIDGE_KEY", VAR / "extension_bridge.key"))
BRIDGE_PUB_FILE = Path(str(BRIDGE_KEY_FILE).removesuffix(".key") + ".pub")
VERIFIER_PUB_FILE = Path(os.environ.get("SCOURGE_VERIFIER_PUBKEY_FILE", str(VERIFIER_KEY_FILE).removesuffix(".key") + ".pub"))
VERIFIER_ADDR = os.environ.get("SCOURGE_VERIFIER_ADDR", VERIFIER_CFG["verifier_listen"])


def _secret(name: str, nbytes: int = 32) -> str:
    p = VAR / name
    if not p.exists():
        p.write_text(secrets.token_hex(nbytes), encoding="utf-8")
        os.chmod(p, 0o600)
    return p.read_text(encoding="utf-8").strip()


INTERNAL_KEY = os.environ.get("SCOURGE_INTERNAL_KEY") or _secret("internal.key")
SESSION_KEY = os.environ.get("SCOURGE_SESSION_KEY") or _secret("session.key")

def _aicte_signing_key():
    """Ed25519 key AICTE uses to sign release letters (generated on first run; demo key)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    p = VAR / "aicte_release_signing.key"
    if not p.exists():
        k = Ed25519PrivateKey.generate()
        p.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        os.chmod(p, 0o600)
    return serialization.load_pem_private_key(p.read_bytes(), password=None)


AICTE_SIGNING_KEY = _aicte_signing_key()

UPLOAD_MAX_BYTES = 2 * 1024 * 1024
UPLOAD_TYPES = {"image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff", "application/pdf": b"%PDF-"}


# Deployment switches (environment only; never written to disk or logs)
ACCESS_CODE = os.environ.get("SCOURGE_ACCESS_CODE", "")      # if set, sign-in requires this shared code
COOKIE_SECURE = os.environ.get("SCOURGE_COOKIE_SECURE", "") == "1"   # set to 1 behind HTTPS
