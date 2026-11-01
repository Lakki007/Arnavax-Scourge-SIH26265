#!/usr/bin/env python3
"""Turn a DevTools "Copy as cURL (bash)" command into the probe's input files, without printing any secret.

  pbpaste | python3 from_curl.py          (macOS: copy the request as cURL first)

Writes, next to this script (mode 0600):
  headers.txt   every -H header (cookies, tokens) - never share it
  body.txt      the request payload, if any - never share it
  run.env       URL, METHOD, HEADERS_FILE, BODY_FILE for the probe
Prints only the host, method, path, header NAMES and sizes."""
import os
import shlex
import sys
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
MERGE = "--merge" in sys.argv   # add headers missing from headers.txt (e.g. Cookie, Origin) from a second, completed request
raw = sys.stdin.read().replace("\\\n", " ").replace("\\\r\n", " ")
if not raw.strip().startswith("curl"):
    sys.exit("Paste a command that starts with: curl '...'  (DevTools -> right-click request -> Copy -> Copy as cURL (bash))")
args = shlex.split(raw)
url, headers, body, method = None, [], None, None
i = 1
while i < len(args):
    a = args[i]
    if a in ("-H", "--header"):
        headers.append(args[i + 1]); i += 2; continue
    if a in ("-b", "--cookie"):
        headers.append("Cookie: " + args[i + 1]); i += 2; continue
    if a in ("--data", "--data-raw", "--data-binary", "-d", "--data-urlencode"):
        body = args[i + 1]; i += 2; continue
    if a in ("-X", "--request"):
        method = args[i + 1].upper(); i += 2; continue
    if a.startswith("http"):
        url = a
    i += 1
if not url:
    sys.exit("No URL found in the pasted command.")
method = method or ("POST" if body is not None else "GET")


def write(name, text):
    p = os.path.join(HERE, name)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return p


if MERGE:
    hp = os.path.join(HERE, "headers.txt")
    have = open(hp, encoding="utf-8").read().splitlines()
    names = {h.split(":", 1)[0].strip().lower() for h in have}
    keep = {"authorization", "content-type", "content-length", "host"}   # the fresh request's own values win
    added = [h for h in headers if h.split(":", 1)[0].strip().lower() not in names | keep]
    write("headers.txt", "\n".join(have + added) + "\n")
    print("added headers: " + (", ".join(h.split(":", 1)[0] for h in added) or "none"))
    print("headers.txt now has " + str(len(have) + len(added)) + " headers. Body, URL and Authorization unchanged.")
    sys.exit(0)


def _unused(name, text):
    p = os.path.join(HERE, name)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return p


hp = write("headers.txt", "\n".join(headers) + "\n")
env = [f"URL='{url}'", f"METHOD={method}", f"HEADERS_FILE='{hp}'"]
if body is not None:
    bp = write("body.txt", body)
    env.append(f"BODY_FILE='{bp}'")
write("run.env", "export " + " ".join(env) + "\n")
u = urlsplit(url)
print(f"host:   {u.hostname}")
print(f"method: {method}")
print(f"path:   {u.path}  (query {'present: it is part of the request line the proof reveals' if u.query else 'none'})")
print(f"headers ({len(headers)}): " + ", ".join(h.split(":", 1)[0] for h in headers))
print(f"request size about {len(raw)} bytes -> use MAX_SENT={8192 if len(raw) < 7000 else 16384}")
print(f"body:   {len(body) if body is not None else 0} bytes")
print("Wrote headers.txt, body.txt, run.env (mode 600). Never share them; delete them when done.")
