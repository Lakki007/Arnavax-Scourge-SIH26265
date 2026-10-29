"""Records the 3-minute judge path as a BACKUP replay (clearly labelled on every frame).
Drives the real running app: python3 scripts/record_demo.py  -> var/demo/scourge_demo_replay.webm"""
import os, time
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

B = os.environ.get("SCOURGE_URL", "http://127.0.0.1:8080")
OUT = Path(__file__).resolve().parents[1] / "var" / "demo"
STAMP = time.strftime("%d %b %Y")
LABEL = f"RECORDED REPLAY · SYNTHETIC BANK, REAL PROOFS · {STAMP}"
CAP = """(t) => { let b=document.getElementById('rr'); if(!b){ b=document.createElement('div'); b.id='rr';
 b.style.cssText='position:fixed;top:0;left:0;right:0;z-index:99999;font:600 13px system-ui;padding:6px 12px;background:#ffcf70;color:#111;text-align:center';
 document.body.appendChild(b);} let c=document.getElementById('rc2'); if(!c){ c=document.createElement('div'); c.id='rc2';
 c.style.cssText='position:fixed;left:50%;transform:translateX(-50%);bottom:70px;z-index:99999;max-width:860px;font:500 17px system-ui;padding:10px 16px;border-radius:12px;background:rgba(20,20,24,.92);color:#fff;border:1px solid #444';
 document.body.appendChild(c);} b.textContent=__LABEL__; c.textContent=t; }""".replace("__LABEL__", __import__("json").dumps(LABEL))


def cap(p, text, wait=2.5):
    p.evaluate(CAP, text)
    p.wait_for_timeout(int(wait * 1000 * 1.9))  # readable pace for a judge


def signin(p, role):
    p.goto(B + "/signin")
    p.check(f"input[value='{role}']")
    if p.locator("#access_code").count():
        p.fill("#access_code", os.environ.get("SCOURGE_ACCESS_CODE", ""))
    p.click("button:has-text('Continue')")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        ctx = br.new_context(viewport={"width": 1280, "height": 760}, record_video_dir=str(OUT), record_video_size={"width": 1280, "height": 760},
                             ignore_https_errors=os.environ.get("SCOURGE_TEST_SELF_SIGNED") == "1")
        p = ctx.new_page()
        signin(p, "officer")
        p.goto(B + "/system")  # clean start
        p.click("button:has-text('Reset demo')"); p.locator("#reset-dlg button:has-text('Reset demo')").click()
        # 1 problem
        p.goto(B + "/"); cap(p, "An AICTE security deposit is checked once, then trusted for 10 years on a paper receipt.", 4)
        cap(p, "CBI found joint FDRs encashed or loaned against without AICTE's consent. Haryana: ₹201 crore of FDRs not in the bank's records.", 5)
        # 2 baseline
        p.goto(B + "/baseline"); p.select_option("#fdr_id", "FDR-002")
        p.set_input_files("#file", str(Path(__file__).resolve().parents[1] / "samples" / "edited_receipt_FD-0000777777.png"))
        p.click("button:has-text('Upload and approve')"); cap(p, "Baseline: an edited receipt is approved. The portal can only look at the picture.", 5)
        # bank state (synthetic): a loan has been raised
        p.goto(B + "/mockbank"); p.select_option("#b-FD-0000777777", "lien_loan"); cap(p, "Synthetic bank: a loan has quietly been raised against FD-0000777777.", 3)
        p.click("#FD-0000777777 button:has-text('Save')")
        # 3 mechanism
        p.goto(B + "/fdrs/FDR-002"); cap(p, "The officer asks the bank's server, not the paper.", 2)
        p.click("button:has-text('Request bank proof')"); cap(p, "A single-use challenge, bound to this deposit and institution, valid 15 minutes.", 3)
        p.click("button:has-text('Switch to INST-42')")
        p.check("input[value=summary]"); cap(p, "The institution proves from its own bank session. Here: an accounts page that also shows its PAN and other accounts.", 4)
        p.click("button:has-text('Prove from my bank')")
        expect(p.locator("#job-state")).to_have_text("Finished", timeout=60000)
        cap(p, "Real MPC-TLS: the TLS keys were split with AICTE's verifier, so the institution could not forge the bank's answer.", 5)
        # 4 evidence
        ev = B + p.locator("a:has-text('Open the evidence')").get_attribute("href")
        signin(p, "officer"); p.goto(ev)
        cap(p, "The bank's server contradicts the paper: a loan against the deposit.", 3)
        p.focus(".cvw-row[data-field=lien]"); cap(p, "Every value traces to the exact bytes AICTE received. Everything else was hidden: PAN, other accounts, cookies.", 5)
        p.locator(".receipt").nth(1).scroll_into_view_if_needed(); cap(p, "AICTE received only this deposit's record. Black bars: bytes it never saw.", 4)
        # 5 action
        p.goto(B + "/fdrs/FDR-002"); p.click("button:has-text('Escalate')")
        p.fill("#esc-note", "Bank shows a loan against the deposit; refer to the Regulation Bureau."); cap(p, "Useful action: escalate with a reason. Release stays blocked.", 2)
        p.click("#esc-dlg button:has-text('Escalate')"); cap(p, "Escalated, on the record, with the bank's evidence attached.", 3)
        # 6 honest boundary
        p.goto(B + "/mockbank"); p.select_option("#b-FD-0000123456", "no_lien"); p.click("#FD-0000123456 button:has-text('Save')")
        p.goto(B + "/fdrs/FDR-001"); p.click("button:has-text('Request bank proof')"); p.click("button:has-text('Switch to INST-42')")
        p.click("button:has-text('Prove from my bank')"); expect(p.locator("#job-state")).to_have_text("Finished", timeout=60000)
        cap(p, "Honest boundary: this bank page does not show liens. Scourge says Insufficient evidence, not Verified.", 5)
        # 7 sponsor value
        signin(p, "officer"); p.goto(B + "/register")
        cap(p, "For AICTE: re-check the existing FDR book on demand, before every release, with no bank agreement.", 5)
        cap(p, "Not yet shown: a logged-in FD page at a real bank. That field test is the next gate.", 5)
        ctx.close(); br.close()
    vids = sorted(OUT.glob("*.webm"), key=lambda f: f.stat().st_mtime)
    final = OUT / "scourge_demo_replay.webm"
    vids[-1].replace(final)
    print(final)


if __name__ == "__main__":
    main()
