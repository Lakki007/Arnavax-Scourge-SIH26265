"""View models for the two mechanism-specific representations:
  1. the receipt: one bank response, as the institution received it vs as AICTE received it
  2. claim vs witness: the register's claim beside the bank server's authenticated values
Everything is derived from stored facts; nothing is invented. Hidden bytes are never put in the page
unless the viewer is the institution that owns the local copy."""
import re

from .settings import VAR, VERIFIER_CFG


def inr(v):
    """Indian digit grouping: 1000000 -> ₹10,00,000.00. Non-numbers are returned unchanged."""
    try:
        n = float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return str(v)
    whole, frac = f"{abs(n):.2f}".split(".")
    if len(whole) > 3:
        whole = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", whole[:-3]) + "," + whole[-3:]
    return ("-" if n < 0 else "") + "₹" + whole + "." + frac

COPIES = VAR / "institution_copies"
COPIES.mkdir(parents=True, exist_ok=True)


def copy_path(challenge_id):
    return COPIES / f"{challenge_id}.response"


def _label_for(labels, a, b):
    for l in labels or []:
        if l.get("range") == [a, b]:
            return l.get("label")
    return None


def receipt(facts, kind="recv", institution_bytes=None):
    """Segments covering the whole transcript in byte order.
    Each: {shown: bool, text: str|None (only for shown, or hidden when institution_bytes given), n: bytes, label}"""
    bm = (facts or {}).get("byte_map") or {}
    total = bm.get(f"{kind}_len") or 0
    revealed = sorted(bm.get(f"{kind}_revealed") or [], key=lambda r: r[0])
    labels = bm.get("recv_labels") if kind == "recv" else None
    if not total:
        return None
    segs, pos = [], 0
    for a, b, text in revealed:
        if a > pos:
            segs.append({"shown": False, "n": a - pos, "text": _slice(institution_bytes, pos, a)})
        label = _label_for(labels, a, b) or ("request line" if kind == "sent" else None)
        seg = {"shown": True, "n": b - a, "text": text, "label": label}
        if label in ("response body", "FD record"):
            seg["parts"] = _field_parts(text, (facts.get("stages", {}).get("server_identity") or {}).get("host"))
        segs.append(seg)
        pos = b
    if pos < total:
        segs.append({"shown": False, "n": total - pos, "text": _slice(institution_bytes, pos, total)})
    shown = sum(s["n"] for s in segs if s["shown"])
    return {"segs": segs, "total": total, "shown": shown, "hidden": total - shown, "has_private": institution_bytes is not None}


def _field_parts(text, host):
    """Split disclosed record text into parts, tagging each schema field's `"key":value` bytes with its
    logical name, so the page can point from a compared value to the exact bytes it came from."""
    fields = (VERIFIER_CFG["banks"].get(host) or {}).get("fields", {})
    hits = []
    for logical, key in fields.items():
        for m in re.finditer(r'"%s"\s*:\s*("(?:[^"\\]|\\.)*"|\[[^\]]*\]|\{[^}]*\}|[^,}\]\s]+)' % re.escape(key), text):
            hits.append((m.start(), m.end(), logical))
    hits.sort()
    parts, pos = [], 0
    for a, z, logical in hits:
        if a < pos:
            continue  # overlapping match (duplicate key): leave untagged
        if a > pos:
            parts.append({"text": text[pos:a], "field": None})
        parts.append({"text": text[a:z], "field": logical})
        pos = z
    if pos < len(text):
        parts.append({"text": text[pos:], "field": None})
    return parts


def _slice(b, a, z):
    if b is None or len(b) < z:
        return None
    return b[a:z].decode("utf-8", "replace")


def institution_copy(challenge_id):
    p = copy_path(challenge_id)
    return p.read_bytes() if p.exists() else None


ROWS = [
    ("status", "Status", lambda f: "Active"),
    ("balance", "Balance", lambda f: f"at least {inr(f['principal'])}"),
    ("principal", "Principal", lambda f: inr(f["principal"])),
    ("maturity", "Maturity", lambda f: f["maturity"]),
    ("holders", "Holders", lambda f: "includes AICTE"),
    ("lien", "Lien", lambda f: "in favour of AICTE"),
]


def claim_vs_witness(fdr, match):
    """Rows of (label, claim, witness value, result) for the split view."""
    out = []
    for key, label, claim in ROWS:
        m = (match or {}).get(key)
        out.append({"key": key, "label": label, "claim": claim(fdr), "money": key in ("balance", "principal"),
                    "witness": (m or {}).get("proven"), "result": (m or {}).get("result"), "note": (m or {}).get("note")})
    return out


def next_action(b, gates):
    """The single most useful next step for the officer, derived from the same gates the server enforces."""
    st = b["status"][0]
    if st == "released":
        return {"kind": "done", "title": "Released", "text": "This deposit has been released. No further action."}
    if st == "escalated":
        return {"kind": "escalated", "title": "Escalated",
                "text": "Handled outside the portal. You can request a fresh proof once the institution has resolved it."}
    if st == "stale":
        return {"kind": "request", "title": "The bank confirmation has expired",
                "text": "Request a fresh proof before release."}
    if b["open"]:
        return {"kind": "waiting", "title": f"Waiting for {b['f']['institution_id']} to prove it",
                "text": "The institution answers from its own net-banking session. The challenge is single use and expires."}
    if gates["accept"][0]:
        return {"kind": "accept", "title": "The bank's server confirms the claim",
                "text": "Accept to record a bank-session confirmation. Release becomes possible."}
    if gates["escalate"][0]:
        return {"kind": "escalate", "title": "The bank's server does not support the claim",
                "text": "Escalate with a reason, or request a fresh proof if the institution can fix the cause."}
    if gates["release"][0]:
        return {"kind": "release", "title": "Confirmed by the bank's server",
                "text": "You can release the deposit, or request a fresh proof first."}
    return {"kind": "request", "title": "Only paper vouches for this deposit",
            "text": "Ask the institution to prove it from its bank's server. It takes about a second once they answer."}
