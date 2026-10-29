"""Visual check: screenshots of every page for both roles, desktop and mobile.
Reports console errors and HTTP >= 500. Usage: python3 scripts/shots.py [outdir] [name,name]"""
import os
import sys

from playwright.sync_api import sync_playwright

B = "http://127.0.0.1:8080"
OUT = sys.argv[1] if len(sys.argv) > 1 else "var/shots"
ONLY = sys.argv[2].split(",") if len(sys.argv) > 2 else None
os.makedirs(OUT, exist_ok=True)
problems = []


def shoot(page, name, url, full=True):
    if ONLY and not any(name.startswith(o) for o in ONLY):
        return
    r = page.goto(B + url, wait_until="networkidle")
    if r.status >= 500:
        problems.append(f"{name}: HTTP {r.status}")
    page.wait_for_timeout(500)
    page.screenshot(path=f"{OUT}/{name}.png", full_page=full)


with sync_playwright() as p:
    br = p.chromium.launch()
    for vw, tag in [((1440, 900), "d"), ((390, 844), "m")]:
        ctx = br.new_context(viewport={"width": vw[0], "height": vw[1]})
        page = ctx.new_page()
        page.on("console", lambda m: m.type == "error" and problems.append(f"console: {m.text}"))
        shoot(page, f"home_{tag}", "/", full=False)
        shoot(page, f"how_{tag}", "/how")
        shoot(page, f"signin_{tag}", "/signin")
        page.goto(B + "/signin")
        page.check("input[value=officer]")
        page.click("text=Continue")
        page.wait_for_load_state("networkidle")
        for n, u in [("register", "/register"), ("fdr1", "/fdrs/FDR-001"), ("fdrnew", "/fdrs/new"), ("mockbank", "/mockbank"),
                     ("baseline", "/baseline"), ("audit", "/audit"), ("system", "/system"), ("homeofficer", "/")]:
            shoot(page, f"{n}_{tag}", u, full=(n != "homeofficer"))
        ev = page.evaluate("fetch('/fdrs/FDR-001').then(r=>r.text()).then(t=>(t.match(/\\/evidence\\/(\\d+)/)||[])[1])")
        if ev:
            shoot(page, f"evidence_{tag}", f"/evidence/{ev}")
        ctx.close()
    br.close()
print("\n".join(problems) or "no console errors / 5xx")
