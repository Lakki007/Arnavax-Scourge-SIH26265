"""Browser journey (Playwright): the complete officer -> institution -> officer flow, operated via the UI
like a judge would. Requires the app running on 127.0.0.1:8080. Screenshots go to var/shots/journey_*.png.
Run: python3 -m pytest -q tests/test_ui_journey.py"""
import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

B = os.environ.get("SCOURGE_URL", "http://127.0.0.1:8080")
SHOTS = "var/shots"


@pytest.fixture(scope="module")
def page():
    os.makedirs(SHOTS, exist_ok=True)
    with sync_playwright() as p:
        br = p.chromium.launch()
        ctx = br.new_context(ignore_https_errors=os.environ.get("SCOURGE_TEST_SELF_SIGNED") == "1", viewport={"width": 1440, "height": 900})
        pg = ctx.new_page()
        errors = []
        pg.on("console", lambda m: m.type == "error" and errors.append(m.text))
        pg.errors = errors
        yield pg
        br.close()


def signin(page, role):
    page.goto(B + "/signin")
    page.check(f"input[value='{role}']")
    if page.locator("#access_code").count():
        page.fill("#access_code", os.environ.get("SCOURGE_ACCESS_CODE", ""))
    page.click("button:has-text('Continue')")
    page.wait_for_load_state("networkidle")


def reset(page):
    page.click("button:has-text('Reset demo')")
    page.click("dialog[open] button:has-text('Reset demo')")
    page.wait_for_load_state("networkidle")


def test_full_journey(page):
    signin(page, "officer")
    reset(page)
    expect(page.locator("tbody tr")).to_have_count(4)
    # officer requests a proof on FDR-001
    page.click("a:has-text('FD-0000123456')")
    expect(page.locator(".page-head .st")).to_have_text("Submitted by institution")
    page.click("button:has-text('Request bank proof')")
    expect(page.locator(".page-head .st")).to_have_text("Proof requested")
    expect(page.locator(".next h2")).to_contain_text("Waiting for INST-42")
    expect(page.locator("button:has-text('Request bank proof')")).to_have_count(0)
    page.wait_for_timeout(700)
    page.screenshot(path=f"{SHOTS}/journey_1_requested.png", full_page=True)
    # switch to the institution and answer it
    page.click("button:has-text('Switch to INST-42')")
    page.wait_for_load_state("networkidle")
    expect(page.locator("h1")).to_contain_text("FD-0000123456")
    page.wait_for_timeout(700)
    page.screenshot(path=f"{SHOTS}/journey_2_challenge.png", full_page=True)
    page.click("button:has-text('Prove from my bank')")
    page.wait_for_url(re.compile(r"/jobs/JOB-"))
    expect(page.locator("#job-state")).to_have_text("Finished", timeout=60000)
    expect(page.locator("#stages li")).to_have_count(10)
    expect(page.locator("#job-result .stamp")).to_have_text("Verified")
    page.wait_for_timeout(700)
    page.screenshot(path=f"{SHOTS}/journey_3_job.png", full_page=True)
    page.click("a:has-text('Open the evidence')")
    expect(page.locator(".stamp")).to_contain_text("Verified")
    page.wait_for_timeout(700)
    page.screenshot(path=f"{SHOTS}/journey_4_evidence.png", full_page=True)
    # institution cannot take officer actions
    r = page.request.post(B + "/fdrs/FDR-001/decide", form={"csrf": "x", "action": "accept", "evidence_id": "1"})
    assert r.status == 403
    # officer accepts and releases
    signin(page, "officer")
    page.goto(B + "/fdrs/FDR-001")
    expect(page.locator(".page-head .st")).to_have_text("Verified, awaiting officer")
    page.click("button:has-text('Accept as confirmed')")
    expect(page.locator(".page-head .st")).to_have_text("Confirmed by bank session")
    page.click("button:has-text('Propose release')")
    page.fill("#rel-note", "RB/REL/2026/0142 term completed")
    page.click("dialog[open] button:has-text('Propose release')")
    expect(page.locator(".page-head .st")).to_have_text("Release proposed")
    expect(page.locator("button:has-text('Approve release')")).to_have_count(0)   # maker cannot approve
    for dept in ("PGRC", "VIG", "FIN"):
        page.select_option("#clr-dept", dept)
        page.click("#release button:has-text('Clear')")
    page.click("button:has-text('Switch to officer RB-02 to approve')")
    page.click("button:has-text('Approve release')")
    expect(page.locator(".page-head .st")).to_have_text("Released")
    page.click("a:has-text('Open the release letter')")
    expect(page.locator("h1")).to_contain_text("Release of security deposit")
    page.wait_for_timeout(700)
    page.screenshot(path=f"{SHOTS}/journey_5_released.png", full_page=True)


def test_negative_control_bank_shows_loan(page):
    signin(page, "officer")
    page.goto(B + "/mockbank")
    page.select_option("#b-FD-0000777777", "lien_loan")
    page.click("#FD-0000777777 button:has-text('Save')")
    page.goto(B + "/fdrs/FDR-002")
    page.click("button:has-text('Request bank proof')")
    page.click("button:has-text('Switch to INST-42')")
    page.wait_for_load_state("networkidle")
    page.check("input[value=summary]")
    # the "what crosses / what stays" preview follows the chosen page
    expect(page.locator(".stay li", has_text="PAN")).to_be_visible()
    page.click("button:has-text('Prove from my bank')")
    expect(page.locator("#job-state")).to_have_text("Finished", timeout=60000)
    expect(page.locator("#job-result .stamp")).to_have_text("Discrepancy")
    page.click("a:has-text('Open the evidence')")
    # what AICTE received = the highlighted (disclosed) marks only
    disclosed = " ".join(page.locator("#rc .receipt-doc mark").all_inner_texts())
    assert "LOAN" in disclosed and "AAATS0000X" not in disclosed and "FD-0000123456" not in disclosed
    # the owning institution may switch to its own local copy, where the PAN reappears
    page.click("#rc button:has-text('What your browser received')")
    expect(page.locator("#rc .priv", has_text="AAATS0000X")).to_be_visible()
    assert "AAATS0000X" in page.locator("#rc .receipt-doc").inner_text()
    # the officer's page must not even contain the hidden text
    eurl = page.url
    signin(page, "officer")
    page.goto(eurl)
    html = page.content()
    assert "AAATS0000X" not in html and "SB-SYN-1111" not in html and "What your browser received" not in html
    # pointing at the contradicted row lights up the exact bytes it came from, in the alarm colour
    page.focus(".cvw-row[data-field=lien]")
    traced = page.locator("#rc .fv.trace")
    expect(traced).to_have_count(1)
    assert "LOAN" in traced.inner_text() and "bad" in traced.get_attribute("class")
    page.wait_for_timeout(700)
    page.screenshot(path=f"{SHOTS}/journey_6_discrepancy.png", full_page=True)


def test_attack_fake_bank_rejected(page):
    signin(page, "officer")
    page.goto(B + "/fdrs/FDR-004")
    page.click("button:has-text('Request bank proof')")
    page.click("button:has-text('Switch to INST-42')")
    page.wait_for_load_state("networkidle")
    page.click("summary:has-text('Try to cheat')")
    page.check("input[value=fake]")
    page.click("button:has-text('Prove from my bank')")
    expect(page.locator("#job-state")).to_have_text("Finished", timeout=60000)
    expect(page.locator("#job-result .stamp")).to_have_text("Rejected")
    expect(page.locator("#job-actions")).to_contain_text("certificate")
    page.wait_for_timeout(700)
    page.screenshot(path=f"{SHOTS}/journey_7_fake_rejected.png", full_page=True)


def test_failed_dependency_and_retry(page):
    signin(page, "officer")
    page.goto(B + "/system")
    page.locator(".svc", has_text="AICTE verifier").locator("button:has-text('Stop')").click()
    expect(page.locator(".svc", has_text="AICTE verifier")).to_contain_text("stopped")
    page.goto(B + "/fdrs/FDR-003")
    page.click("button:has-text('Request bank proof')")
    page.click("button:has-text('Switch to INST-77')")
    page.wait_for_load_state("networkidle")
    chall_url = page.url
    page.click("button:has-text('Prove from my bank')")
    expect(page.locator("#job-state")).to_have_text("Failed", timeout=60000)
    expect(page.locator("#job-actions")).to_contain_text("verifier is unreachable")
    page.wait_for_timeout(700)
    page.screenshot(path=f"{SHOTS}/journey_8_verifier_down.png", full_page=True)
    page.click("a:has-text('Back to the request')")
    expect(page.locator(".note-bad")).to_contain_text("not used")
    signin(page, "officer")  # the verifier is AICTE's: only the officer can start or stop it
    page.goto(B + "/system")
    page.locator(".svc", has_text="AICTE verifier").locator("button:has-text('Start')").click()
    expect(page.locator(".svc", has_text="AICTE verifier")).to_contain_text("running")
    signin(page, "institution:INST-77")
    page.goto(chall_url)  # retry the same, still-unused challenge
    page.click("button:has-text('Prove from my bank')")
    expect(page.locator("#job-state")).to_have_text("Finished", timeout=60000)
    expect(page.locator("#job-result .stamp")).to_have_text("Verified")


def test_reset_and_repeat(page):
    signin(page, "officer")
    reset(page)
    expect(page.locator("tbody .st")).to_have_text(["Submitted by institution"] * 4)
    page.goto(B + "/audit")
    # the reset wipes the log; its own entry is the first, followed only by the daily reminder check the register runs
    expect(page.locator("tbody tr").last).to_contain_text("demo reset")
    expect(page.locator("tbody tr")).to_have_count(2)
    assert not page.errors, page.errors


def test_keyboard_only_home_search(page):
    ctx = page.context
    ctx.clear_cookies()
    page.goto(B + "/")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.className") == "skip"
    page.keyboard.press("Tab")  # brand chip
    for _ in range(6):
        page.keyboard.press("Tab")
        if page.evaluate("document.activeElement.id") == "fd":
            break
    assert page.evaluate("document.activeElement.id") == "fd"
    page.keyboard.type("FD-0000123456")
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r"/signin"))
    page.keyboard.press("Escape")


def test_mobile_layout_no_horizontal_scroll(page):
    ctx = page.context.browser.new_context(ignore_https_errors=os.environ.get("SCOURGE_TEST_SELF_SIGNED") == "1", viewport={"width": 390, "height": 844})
    pg = ctx.new_page()
    signin(pg, "officer")
    for url in ["/", "/register", "/fdrs/FDR-001", "/how", "/system", "/mockbank", "/audit", "/baseline", "/fdrs/new"]:
        pg.goto(B + url)
        overflow = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 1, (url, overflow)
    ctx.close()


def test_baseline_beside_bank_answer(page):
    signin(page, "officer")
    page.goto(B + "/baseline")
    page.select_option("#fdr_id", "FDR-002")
    page.set_input_files("#file", str(Path(__file__).resolve().parents[1] / "samples" / "edited_receipt_FD-0000777777.png"))
    page.click("button:has-text('Upload and approve')")
    expect(page.locator(".vs-doc")).to_contain_text("Approved on the document")
    expect(page.locator(".cvw .cvw-head")).to_contain_text("The bank's server says")
