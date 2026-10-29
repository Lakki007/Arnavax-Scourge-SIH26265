"""Build the Scourge SIH 2026 idea deck on the official template (6 slides)."""
import copy
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.oxml.ns import qn

T = "/tmp/claude-0/ppt/"
IMG = T + "img/"
VIDEO = "/home/claude/scourge/docs/demo/scourge_demo_replay.mp4"
POSTER = "/tmp/claude-0/-home-claude/5ad7f5f0-0bbe-5825-ad20-de1d9f8622d3/scratchpad/demo/poster.jpg"
DEMO_URL = "claude.ai/artifact/RNsg6TQhdUN7z4PdifhrkJ"

NAVY = RGBColor(0x0B, 0x2F, 0x6B)
BLUE = RGBColor(0x00, 0x70, 0xC0)
INK = RGBColor(0x1A, 0x1A, 0x1A)
MUTE = RGBColor(0x55, 0x5B, 0x66)
RED = RGBColor(0xC6, 0x28, 0x28)
GREEN = RGBColor(0x1B, 0x7F, 0x4C)
AMBER = RGBColor(0xB2, 0x6A, 0x00)
ORANGE = RGBColor(0xE8, 0x77, 0x22)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
TINT_BLUE = RGBColor(0xE9, 0xF2, 0xFB)
TINT_RED = RGBColor(0xFD, 0xEC, 0xEA)
TINT_GREEN = RGBColor(0xE6, 0xF4, 0xEC)
TINT_AMBER = RGBColor(0xFF, 0xF4, 0xDE)
TINT_GREY = RGBColor(0xF3, 0xF4, 0xF6)
LINE = RGBColor(0xC9, 0xD3, 0xE0)

prs = Presentation(T + "template.pptx")

# ---------- structural: drop the instructions slide (7) ----------
sldIdLst = prs.slides._sldIdLst
last = sldIdLst[-1]
prs.part.drop_rel(last.get(qn("r:id")))
sldIdLst.remove(last)

S = list(prs.slides)


def remove(shape):
    shape._element.getparent().remove(shape._element)


def find(slide, name):
    for sh in slide.shapes:
        if sh.name == name:
            return sh
    return None


def box(slide, x, y, w, h, fill=None, line=None, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.08, lw=0.75, name=None):
    s = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    if shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        s.adjustments[0] = radius
    if fill is None:
        s.fill.background()
    else:
        s.fill.solid(); s.fill.fore_color.rgb = fill
    if line is None:
        s.line.fill.background()
    else:
        s.line.color.rgb = line; s.line.width = Pt(lw)
    s.shadow.inherit = False
    if name:
        s.name = name
    s.text_frame.text = ""
    return s


def text(slide, x, y, w, h, paras, size=11, color=INK, font="Arial", align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP,
         margin=0.04, spacing=2, shape=None, name=None, line_spacing=None):
    """paras: list of paragraphs; a paragraph is a str or a list of (text, opts) runs; opts may hold
    b, i, c (color), s (size), u (underline), bullet (True/'num:N'), lvl, align, after."""
    if shape is None:
        s = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    else:
        s = shape
    if name:
        s.name = name
    tf = s.text_frame
    tf.word_wrap = True
    tf.auto_size = None
    for side in ("left", "right", "top", "bottom"):
        setattr(tf, f"margin_{side}", Inches(margin))
    tf.vertical_anchor = anchor
    first = True
    for p in paras:
        runs = [(p, {})] if isinstance(p, str) else p
        popts = runs[0][1] if runs else {}
        para = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        para.alignment = popts.get("align", align)
        para.space_after = Pt(popts.get("after", spacing))
        if line_spacing:
            para.line_spacing = line_spacing
        bullet = popts.get("bullet")
        pPr = para._p.get_or_add_pPr()
        if bullet:
            ind = 0.16
            pPr.set("marL", str(Inches(ind)))
            pPr.set("indent", str(-Inches(ind)))
            bu = pPr.makeelement(qn("a:buFont"), {"typeface": "Arial"})
            pPr.append(bu)
            if isinstance(bullet, str) and bullet.startswith("num"):
                an = pPr.makeelement(qn("a:buAutoNum"), {"type": "arabicPeriod"})
                pPr.append(an)
            else:
                ch = pPr.makeelement(qn("a:buChar"), {"char": "•"})
                pPr.append(ch)
        else:
            pPr.append(pPr.makeelement(qn("a:buNone"), {}))
        for t, o in runs:
            r = para.add_run()
            r.text = t
            f = r.font
            f.name = o.get("font", font)
            f.size = Pt(o.get("s", size))
            f.bold = o.get("b", False)
            f.italic = o.get("i", False)
            f.underline = o.get("u", False)
            f.color.rgb = o.get("c", color)
    return s


def pill(slide, x, y, w, h, label, fill, color=WHITE, size=11, bold=True):
    s = box(slide, x, y, w, h, fill=fill, radius=0.5)
    text(slide, 0, 0, 0, 0, [[(label, {"b": bold, "c": color, "s": size, "align": PP_ALIGN.CENTER})]], shape=s,
         anchor=MSO_ANCHOR.MIDDLE, margin=0.03)
    return s


def header(slide, x, y, w, label, color=BLUE, size=13):
    """Template pointer heading (kept verbatim from the SIH template)."""
    return text(slide, x, y, w, 0.32, [[("❖ ", {"c": color, "s": size, "b": True}), (label, {"b": True, "u": True, "c": color, "s": size})]],
                margin=0.0)


def set_title(slide, t, size=30):
    ttl = slide.shapes.title
    tf = ttl.text_frame
    r0 = tf.paragraphs[0].runs[0] if tf.paragraphs[0].runs else None
    for p in list(tf.paragraphs)[1:]:
        p._p.getparent().remove(p._p)
    p = tf.paragraphs[0]
    for r in list(p.runs):
        r._r.getparent().remove(r._r)
    r = p.add_run(); r.text = t
    r.font.name = "Times New Roman"; r.font.bold = True; r.font.size = Pt(size); r.font.color.rgb = NAVY


def set_oval(slide):
    for sh in slide.shapes:
        if sh.name.startswith("Oval") and sh.has_text_frame and "Team" in sh.text_frame.text:
            tf = sh.text_frame
            p = tf.paragraphs[0]
            for q in list(tf.paragraphs)[1:]:
                q._p.getparent().remove(q._p)
            for r in list(p.runs):
                r._r.getparent().remove(r._r)
            r = p.add_run(); r.text = "SCOURGE"
            r.font.name = "Arial"; r.font.bold = True; r.font.size = Pt(10)
            tf.margin_left = tf.margin_right = Inches(0.02); tf.word_wrap = False; r.font.color.rgb = NAVY
            p.alignment = PP_ALIGN.CENTER
            tf.vertical_anchor = MSO_ANCHOR.MIDDLE


def set_footer(slide):
    for sh in slide.placeholders:
        if "Footer" in sh.name:
            tf = sh.text_frame
            p = tf.paragraphs[0]
            if p.runs:
                p.runs[0].text = "@SCOURGE · SIH26265"
                for r in p.runs[1:]:
                    r._r.getparent().remove(r._r)


def pic(slide, path, x, y, w=None, h=None, name=None, line=None):
    kw = {}
    if w: kw["width"] = Inches(w)
    if h: kw["height"] = Inches(h)
    p = slide.shapes.add_picture(path, Inches(x), Inches(y), **kw)
    if name: p.name = name
    if line:
        p.line.color.rgb = line; p.line.width = Pt(0.75)
    return p


# =================================================================== SLIDE 1: TITLE
s1 = S[0]
tb = find(s1, "TextBox 9")
vals = [("Problem Statement ID – ", "SIH26265"),
        ("Problem Statement Title – ", "Streamline of Security Deposit / FDR Portal"),
        ("Theme – ", "Smart Automation"),
        ("PS Category – ", "Software"),
        ("Team ID – ", ""),
        ("Team Name (Registered on portal) – ", "")]
tf = tb.text_frame
paras = list(tf.paragraphs)
# keep only non-empty template paragraphs, in order, and rewrite them
content_paras = [p for p in paras if p.text.strip()]
for p in paras:
    if p not in content_paras:
        p._p.getparent().remove(p._p)
for p, (label, val) in zip(content_paras, vals):
    rs = list(p.runs)
    base = rs[0]
    for r in rs[1:]:
        r._r.getparent().remove(r._r)
    base.text = label
    base.font.size = Pt(18)
    p.alignment = PP_ALIGN.LEFT
    if val:
        r = p.add_run(); r.text = val
        r.font.name = "Arial"; r.font.size = Pt(18); r.font.bold = False; r.font.color.rgb = NAVY
    p.space_after = Pt(4)

sub = find(s1, "Subtitle 3")
stf = sub.text_frame
for q in list(stf.paragraphs)[1:]:
    q._p.getparent().remove(q._p)
sp = stf.paragraphs[0]
for r in list(sp.runs):
    r._r.getparent().remove(r._r)
sp.alignment = PP_ALIGN.CENTER
r = sp.add_run(); r.text = "SCOURGE"
r.font.name = "Times New Roman"; r.font.size = Pt(30); r.font.bold = True; r.font.color.rgb = NAVY
p2 = stf.add_paragraph(); p2.alignment = PP_ALIGN.CENTER
r = p2.add_run(); r.text = "Ask the bank, not the paper."
r.font.name = "Times New Roman"; r.font.italic = True; r.font.size = Pt(20); r.font.color.rgb = BLUE

for r in find(s1, "Title 7").text_frame.paragraphs[0].runs:
    r.font.size = Pt(34)

# =================================================================== SLIDE 2: IDEA
s2 = S[1]
set_title(s2, "SCOURGE: ASK THE BANK, NOT THE PAPER", 30)
set_oval(s2); set_footer(s2)
remove(find(s2, "TextBox 8"))

text(s2, 0.35, 1.2, 12.6, 0.34, [[("❖ ", {"c": BLUE, "s": 15, "b": True}),
     ("Proposed Solution (Describe your Idea/Solution/Prototype)", {"b": True, "u": True, "c": BLUE, "s": 15})]], margin=0)
text(s2, 0.35, 1.55, 12.6, 0.34, [[("An institution proves, ", {"s": 12.5}),
     ("from its own net-banking session", {"s": 12.5, "b": True, "c": NAVY}),
     (", what the bank's server says about one FDR; AICTE sees ", {"s": 12.5}),
     ("only that record", {"s": 12.5, "b": True, "c": NAVY}),
     (".", {"s": 12.5})]], margin=0)

# why-now strip
strip = box(s2, 0.35, 1.95, 8.55, 0.5, fill=TINT_RED, radius=0.18)
text(s2, 0, 0, 0, 0, [[("Why now: ", {"b": True, "c": RED, "s": 10.5}),
     ("₹201.42 crore of FDRs \"do not appear in IDFC's records\" (CBI chargesheet, Oct 2026) · CBI: AICTE joint FDRs encashed or loaned against without consent · deposits are released only after 10 years (AICTE cl. 6.2(g))",
      {"s": 10.5, "c": INK})]], shape=strip, anchor=MSO_ANCHOR.MIDDLE, margin=0.08)

cols = [
    ("Detailed explanation of the proposed solution", NAVY, [
        [("1  Challenge: ", {"b": True, "c": NAVY}), ("AICTE issues a single-use, 15-minute challenge bound to one FDR and institution", {})],
        [("2  Prove: ", {"b": True, "c": NAVY}), ("the institution logs into its own net banking; the deposit page is fetched in one TLS session whose keys are split with AICTE's verifier (MPC-TLS)", {})],
        [("3  Disclose: ", {"b": True, "c": NAVY}), ("only the deposit record crosses; PAN, other accounts and cookies stay with the institution", {})],
        [("4  Decide: ", {"b": True, "c": NAVY}), ("register vs bank: Verified, Discrepancy, Insufficient evidence or Rejected; release blocked until confirmed", {})],
    ]),
    ("How it addresses the problem", BLUE, [
        [("Checked once, trusted 10 years", {"b": True, "c": RED}), ("  →  re-checked any time and before every release", {})],
        [("Branch letters: slow, forgeable", {"b": True, "c": RED}), ("  →  the bank's own server, in about a second", {})],
        [("Edited screenshots accepted", {"b": True, "c": RED}), ("  →  certificate-checked proof the institution cannot edit", {})],
        [("Loans and encashment unseen", {"b": True, "c": RED}), ("  →  status, balance and lien read from the bank", {})],
    ]),
    ("Innovation and uniqueness of the solution", GREEN, [
        [("Bank-sourced proof, no bank API: ", {"b": True, "c": GREEN}), ("no bilateral agreement and no Account Aggregator licence needed", {})],
        [("Unforgeable by the institution: ", {"b": True, "c": GREEN}), ("a fake bank is rejected on its certificate", {})],
        [("Private by design: ", {"b": True, "c": GREEN}), ("AICTE receives one record, nothing else", {})],
        [("Unknown is never \"clear\": ", {"b": True, "c": GREEN}), ("a page that hides liens gives Insufficient evidence", {})],
        [("New in this PS: ", {"b": True, "c": GREEN}), ("none of the 8 rival SIH26265 builds we read uses a bank-session proof", {})],
    ]),
]
cx, cw, gap = 0.35, 2.75, 0.15
for i, (head, col, items) in enumerate(cols):
    x = cx + i * (cw + gap)
    pill(s2, x, 2.58, cw, 0.5, head, col, size=10.5)
    card = box(s2, x, 3.13, cw, 3.0, fill=TINT_GREY, radius=0.06)
    text(s2, 0, 0, 0, 0, [[(t, dict(o, s=10)) for t, o in it] for it in items], shape=card, margin=0.09, spacing=5)

# right column: evidence screenshot + QR
pic(s2, IMG + "cvw.png", 9.1, 1.95, w=3.95, name="Evidence screenshot")
text(s2, 9.1, 3.93, 3.95, 0.45, [[("Prototype screen: ", {"b": True, "s": 9.5, "c": NAVY}),
     ("the register's claim vs the bank's server; the hidden loan is caught (✕)", {"s": 9.5, "c": MUTE})]], margin=0)
pic(s2, IMG + "qr.png", 9.1, 4.48, w=1.45, name="Demo QR")
text(s2, 10.65, 4.5, 2.4, 1.45, [[("Watch the working demo", {"b": True, "s": 11, "c": NAVY})],
     [("2 min 20 s · real proofs on a synthetic bank", {"s": 9.5, "c": MUTE})],
     [(DEMO_URL, {"s": 8.5, "c": BLUE, "u": True})]], margin=0, spacing=3)

banner = box(s2, 0.35, 6.27, 12.7, 0.52, fill=NAVY, radius=0.25)
text(s2, 0, 0, 0, 0, [[("WORKING PROTOTYPE:  ", {"b": True, "c": RGBColor(0xFF, 0xCF, 0x70), "s": 13}),
     ("end to end, 102 / 102 automated tests pass on a staged HTTPS deployment  ·  synthetic bank, real proofs", {"b": True, "c": WHITE, "s": 13})]],
     shape=banner, anchor=MSO_ANCHOR.MIDDLE, align=PP_ALIGN.CENTER, margin=0.05)

# =================================================================== SLIDE 3: TECHNICAL APPROACH
s3 = S[2]
set_oval(s3); set_footer(s3)
remove(find(s3, "TextBox 8"))

header(s3, 0.35, 1.2, 8.5, "Technologies to be used")
stack = [
    ("Proof", NAVY, ["TLSNotary MPC-TLS (Rust)", "rustls / webpki", "Selective disclosure"]),
    ("Backend", BLUE, ["Python · FastAPI", "SQLite (append-only audit)", "Separate verifier process"]),
    ("Frontend", GREEN, ["Server-rendered HTML", "Vanilla JS live view", "Mobile + keyboard tested"]),
    ("Ops / QA", ORANGE, ["nginx TLS · systemd", "pytest · Playwright", "pip-audit · cargo-audit"]),
]
y = 1.58
for label, col, chips in stack:
    pill(s3, 0.35, y, 1.15, 0.36, label, col, size=10)
    x = 1.6
    for c in chips:
        w = 0.09 * len(c) + 0.35
        b = box(s3, x, y, w, 0.36, fill=WHITE, line=col, radius=0.5)
        text(s3, 0, 0, 0, 0, [[(c, {"s": 9.5, "c": INK, "align": PP_ALIGN.CENTER})]], shape=b, anchor=MSO_ANCHOR.MIDDLE, margin=0.02)
        x += w + 0.1
    y += 0.44

header(s3, 0.35, 3.42, 8.5, "Methodology and process for implementation (Flow Charts/Images/ working prototype)")
steps = [("1", "Officer registers FDR", "and requests a bank proof"),
         ("2", "Single-use challenge", "consumed before the session"),
         ("3", "Institution proves", "from its own bank login"),
         ("4", "Verifier checks", "certificate, host, endpoint, record"),
         ("5", "Register match", "field by field"),
         ("6", "Officer acts", "accept · escalate · release")]
sx, sw, sg = 0.35, 1.25, 0.24
for i, (n, a, b2) in enumerate(steps):
    x = sx + i * (sw + sg)
    st = box(s3, x, 3.8, sw, 0.82, fill=NAVY if i % 2 == 0 else BLUE, radius=0.12)
    text(s3, 0, 0, 0, 0, [[(n + "  " + a, {"b": True, "s": 9, "c": WHITE, "align": PP_ALIGN.CENTER})],
                          [(b2, {"s": 8, "c": WHITE, "align": PP_ALIGN.CENTER})]],
         shape=st, anchor=MSO_ANCHOR.MIDDLE, margin=0.04, spacing=1)
    if i < len(steps) - 1:
        ar = s3.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, Inches(x + sw + 0.03), Inches(4.11), Inches(sg - 0.06), Inches(0.2))
        ar.fill.solid(); ar.fill.fore_color.rgb = ORANGE; ar.line.fill.background()

# decision outcomes
oc = [("Verified", GREEN, TINT_GREEN), ("Discrepancy", RED, TINT_RED), ("Insufficient evidence", AMBER, TINT_AMBER), ("Rejected", MUTE, TINT_GREY)]
text(s3, 0.35, 4.72, 1.6, 0.34, [[("Four outcomes:", {"b": True, "s": 10, "c": NAVY})]], margin=0, anchor=MSO_ANCHOR.MIDDLE)
x = 1.65
for lab, c, tint in oc:
    w = 0.085 * len(lab) + 0.45
    b = box(s3, x, 4.72, w, 0.34, fill=tint, line=c, radius=0.5)
    text(s3, 0, 0, 0, 0, [[(lab, {"b": True, "s": 9.5, "c": c, "align": PP_ALIGN.CENTER})]], shape=b, anchor=MSO_ANCHOR.MIDDLE, margin=0.02)
    x += w + 0.1

pic(s3, IMG + "receipt.png", 0.35, 5.42, w=6.0, name="Receipt screenshot")

text(s3, 6.5, 5.42, 2.45, 1.4,
     [[("What AICTE actually receives: ", {"b": True, "s": 9.5, "c": NAVY}),
       ("the loan is caught in the bank's own bytes. Everything else is blacked out; AICTE learns only its size", {"s": 9.5, "c": MUTE})]], margin=0)

lw = 3.75
pic(s3, IMG + "lanes.png", 9.2, 1.2, w=lw, name="Live proof sequence")
text(s3, 9.2, 4.98, lw, 0.28, [[("Live proof, three parties: ", {"b": True, "s": 9.5, "c": NAVY}), ("the bank and AICTE never connect", {"s": 9.5, "c": MUTE})]], margin=0)
mv = s3.shapes.add_movie(VIDEO, Inches(9.2), Inches(5.3), Inches(2.6), Inches(2.6 * 760 / 1280),
                         poster_frame_image=POSTER, mime_type="video/mp4")
mv.name = "Demo video"
text(s3, 11.88, 5.3, 1.1, 1.5, [[("▶ Demo", {"b": True, "s": 10, "c": NAVY})],
                                 [("Embedded video plays in PowerPoint; in the PDF, scan the QR on slide 2", {"s": 8, "c": MUTE})]], margin=0)

# =================================================================== SLIDE 4: FEASIBILITY
s4 = S[3]
set_oval(s4); set_footer(s4)
remove(find(s4, "TextBox 8"))

header(s4, 0.35, 1.2, 12.6, "Analysis of the feasibility of the idea")
tiles = [("0.58 s", "per proof (p50, 60 runs)", "measured, synthetic bank"),
         ("38.7 MB", "prover → verifier per proof", "measured"),
         ("13 / 16", "real TLS sessions with 6 Indian bank sites", "public pages, not FD pages"),
         ("102 / 102", "automated tests on a staged HTTPS deployment", "measured")]
tw = 3.05
for i, (big, lab, note) in enumerate(tiles):
    x = 0.35 + i * (tw + 0.17)
    b = box(s4, x, 1.6, tw, 1.15, fill=TINT_BLUE, radius=0.1)
    text(s4, 0, 0, 0, 0, [[(big, {"b": True, "s": 26, "c": NAVY, "align": PP_ALIGN.CENTER})],
                          [(lab, {"s": 10, "c": INK, "align": PP_ALIGN.CENTER})],
                          [(note, {"s": 8.5, "i": True, "c": MUTE, "align": PP_ALIGN.CENTER})]],
         shape=b, anchor=MSO_ANCHOR.MIDDLE, margin=0.05, spacing=0)

feas = [
    [("Technical: ", {"b": True, "c": NAVY}), ("built end to end on open-source TLSNotary (MIT/Apache-2.0); a laptop or one small server, no GPU", {})],
    [("Operational: ", {"b": True, "c": NAVY}), ("the institution uses its own net-banking login; AICTE needs no bank integration or AA licence", {})],
    [("Economic: ", {"b": True, "c": NAVY}), ("₹0 software licences; one 2 vCPU / 4 GB server runs the verifier [estimate]", {})],
    [("Viability: ", {"b": True, "c": NAVY}), ("adoption by an AICTE circular; works for the existing FDR book, not only new deposits", {})],
]
text(s4, 0.35, 2.9, 12.6, 1.05, [[(t, dict(o, s=10.5, bullet=True)) for t, o in f] for f in feas], margin=0, spacing=3)

header(s4, 0.35, 4.0, 6.3, "Potential challenges and risks", color=RED)
header(s4, 6.7, 4.0, 6.3, "Strategies for overcoming these challenges", color=GREEN)
risks = [("Bank page hides liens", "Insufficient evidence, never Verified; release stays blocked"),
         ("Institution runs a fake bank", "Rejected: certificate does not chain to trusted roots (14 / 14 in tests)"),
         ("Institution withholds bytes", "Insufficient evidence; record-only disclosure is flagged on the evidence"),
         ("Bank needs browser-bound state or TLS 1.3 only", "Browser-extension prover; listed as unsupported until then"),
         ("Bank terms, AICTE acceptance", "Own authorised session, no bypass of bank controls; legal review via SPOC")]
rw = 2.42
for i, (r_, s_) in enumerate(risks):
    x = 0.35 + i * (rw + 0.15)
    hdr = box(s4, x, 4.38, rw, 0.62, fill=RED, radius=0.12)
    text(s4, 0, 0, 0, 0, [[("What if: ", {"b": True, "s": 9.5, "c": WHITE}), (r_, {"b": True, "s": 9.5, "c": WHITE})]],
         shape=hdr, anchor=MSO_ANCHOR.MIDDLE, margin=0.07)
    bd = box(s4, x, 5.04, rw, 0.95, fill=TINT_GREEN, radius=0.1)
    text(s4, 0, 0, 0, 0, [[(s_, {"s": 9.5, "c": INK})]], shape=bd, anchor=MSO_ANCHOR.MIDDLE, margin=0.08)

gate = box(s4, 0.35, 6.12, 12.7, 0.62, fill=TINT_AMBER, line=AMBER, radius=0.2)
text(s4, 0, 0, 0, 0, [[("Next gate (pre-declared): ", {"b": True, "c": AMBER, "s": 11}),
     ("prove a logged-in FD page at a real bank with a team member's own account. Pass on one bank = launch for that bank; if FD number, status and balance are not provable on 2 of 3 banks, we re-scope.", {"s": 10.5, "c": INK})]],
     shape=gate, anchor=MSO_ANCHOR.MIDDLE, margin=0.1)

# =================================================================== SLIDE 5: IMPACT
s5 = S[4]
set_oval(s5); set_footer(s5)
remove(find(s5, "TextBox 8"))

header(s5, 0.35, 1.2, 12.6, "Potential impact on the target audience")
aud = [("AICTE RB officers", NAVY, "A dated proof from the bank's own server in about a second, instead of a letter that takes weeks"),
       ("AICTE finance / Member Secretary", BLUE, "The whole FDR book becomes re-checkable any time, and before every release"),
       ("Institutions", GREEN, "One short proof from their own net banking; nothing beyond the deposit is revealed"),
       ("Students", ORANGE, "The deposit held for their protection is checked during its 10 years, not only at the end")]
aw = 3.05
for i, (who, col, what) in enumerate(aud):
    x = 0.35 + i * (aw + 0.17)
    pill(s5, x, 1.58, aw, 0.42, who, col, size=10.5)
    b = box(s5, x, 2.05, aw, 0.95, fill=TINT_GREY, radius=0.08)
    text(s5, 0, 0, 0, 0, [[(what, {"s": 10, "c": INK})]], shape=b, anchor=MSO_ANCHOR.MIDDLE, margin=0.08)

header(s5, 0.35, 3.15, 12.6, "Benefits of the solution (social, economic, environmental, etc.)")
rows = [("", "Today", "With Scourge"),
        ("Who confirms", "Branch letter, or an uploaded screenshot", "The bank's own server, proven"),
        ("How often", "Once at submission; trusted for 10 years", "Any time, and before every release"),
        ("Time per check", "Weeks per letter", "About a second per proof"),
        ("Hidden loan or encashment", "Found at release, or never", "Shown as a Discrepancy with the bank's bytes"),
        ("Institution's privacy", "Full statements emailed", "One deposit record; PAN and accounts hidden")]
tbl = s5.shapes.add_table(len(rows), 3, Inches(0.35), Inches(3.55), Inches(7.6), Inches(2.6)).table
tbl.columns[0].width = Inches(1.9); tbl.columns[1].width = Inches(2.7); tbl.columns[2].width = Inches(3.0)
for r_i, row in enumerate(rows):
    for c_i, val in enumerate(row):
        cell = tbl.cell(r_i, c_i)
        cell.fill.solid()
        cell.fill.fore_color.rgb = NAVY if r_i == 0 else (TINT_RED if c_i == 1 else TINT_GREEN if c_i == 2 else TINT_GREY)
        tf = cell.text_frame; tf.word_wrap = True
        for side in ("left", "right", "top", "bottom"):
            setattr(cell, f"margin_{side}", Inches(0.05))
        p = tf.paragraphs[0]
        r = p.add_run(); r.text = val
        r.font.name = "Arial"; r.font.size = Pt(9.5 if r_i else 10.5)
        r.font.bold = r_i == 0 or c_i == 0
        r.font.color.rgb = WHITE if r_i == 0 else (NAVY if c_i == 0 else INK)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE

# results panel
rp = box(s5, 8.15, 3.55, 4.9, 2.6, fill=WHITE, line=LINE, radius=0.06)
text(s5, 8.3, 3.62, 4.6, 0.5, [[("Tested on 180 fresh, randomised deposits", {"b": True, "s": 11, "c": NAVY})],
                              [("real proofs; synthetic bank; random amounts, dates and bank formats", {"s": 8.5, "i": True, "c": MUTE})]], margin=0, spacing=0)
stats = [("119 / 119", "bad deposits approved by upload-and-approve", RED),
         ("0 / 119", "bad deposits Scourge verified (forged, closed, withdrawn, loaned, fake bank, hidden loan)", GREEN),
         ("0 / 61", "honest deposits Scourge accused", GREEN)]
yy = 4.2
for big, lab, col in stats:
    text(s5, 8.3, yy, 1.45, 0.55, [[(big, {"b": True, "s": 19, "c": col})]], margin=0, anchor=MSO_ANCHOR.MIDDLE)
    text(s5, 9.8, yy, 3.15, 0.55, [[(lab, {"s": 9.5, "c": INK})]], margin=0, anchor=MSO_ANCHOR.MIDDLE)
    yy += 0.62

ben = [("Social", GREEN, "Students' deposits stay intact for the full 10 years"),
       ("Economic", BLUE, "Catches encashed or loaned FDRs before money is released; no bank-integration cost"),
       ("Governance", NAVY, "Dated, exportable evidence and an append-only audit trail"),
       ("Environmental", ORANGE, "No paper confirmation letters or couriers")]
bx = 0.35
bw = (12.7 - 3 * 0.12) / 4
for lab, col, t in ben:
    b = box(s5, bx, 6.25, bw, 0.6, fill=WHITE, line=col, radius=0.2)
    text(s5, 0, 0, 0, 0, [[(lab + ": ", {"b": True, "s": 9.5, "c": col}), (t, {"s": 9.5, "c": INK})]], shape=b, anchor=MSO_ANCHOR.MIDDLE, margin=0.07)
    bx += bw + 0.12

# =================================================================== SLIDE 6: RESEARCH
s6 = S[5]
set_oval(s6); set_footer(s6)
remove(find(s6, "TextBox 8"))

header(s6, 0.35, 1.2, 12.6, "Details / Links of the reference and research work")

refs = [
    ("Problem evidence", NAVY, [
        ("CBI chargesheet: ₹201.42 cr FDRs not in IDFC's records — ThePrint, 7 Oct 2026", "theprint.in/india/cbi-says-haryana-pollution-board-secys-notes-fed-excess-deposits-to-idfc-rs-50-cr-cap-became-rs-163-cr/3062087"),
        ("CBI on AICTE: joint FDRs encashed / loaned without consent — Deccan Herald", "deccanherald.com/india/greater-transparency-aicte-needed-says-2576077"),
    ]),
    ("Regulation", BLUE, [
        ("AICTE Approval Guidelines 2024-27: cl. 6.2(g) release after 10 years; cl. 11.8 penalty", "indiankanoon.org/doc/173919027"),
        ("Account Aggregator framework: users must be regulated FIUs — Dept. of Financial Services", "financialservices.gov.in/beta/en/account-aggregator-framework"),
    ]),
    ("Technology", GREEN, [
        ("TLSNotary: MPC-TLS proofs of web data, MIT / Apache-2.0", "tlsnotary.org"),
        ("NeSL e-BG for security deposits (new instruments only) — WDRA", "wdra.gov.in/documents/d/wdra/ebg_requirement-27-08-2025"),
    ]),
]
y = 1.6
for grp, col, items in refs:
    pill(s6, 0.35, y, 1.55, 0.34, grp, col, size=10)
    paras = []
    for t, u in items:
        paras.append([(t, {"s": 9.5, "c": INK, "after": 0})])
        paras.append([(u, {"s": 8, "c": BLUE, "u": True, "after": 4})])
    text(s6, 2.0, y - 0.04, 5.3, 1.1, paras, margin=0, spacing=0)
    y += 1.17

# comparison table
header(s6, 7.5, 1.2, 5.5, "Comparison with existing approaches", color=NAVY)
cols6 = ["", "Upload & approve", "Bank letter", "Account Aggregator", "NeSL e-BG", "Scourge"]
feat = [("Uses the bank's own data", ["✕", "✓", "✓", "✓", "✓"]),
        ("No bank API or agreement", ["✓", "✓", "✕", "✕", "✓"]),
        ("Institution cannot forge it", ["✕", "~", "✓", "✓", "✓"]),
        ("Covers the existing FDR book", ["✓", "✓", "✓", "✕", "✓"]),
        ("Re-check in seconds", ["✕", "✕", "✓", "✓", "✓"]),
        ("Shown on a real bank today", ["✓", "✓", "✓", "✓", "next"])]
t6 = s6.shapes.add_table(len(feat) + 1, 6, Inches(7.5), Inches(1.6), Inches(5.55), Inches(3.4)).table
widths = [1.55, 0.8, 0.72, 0.9, 0.72, 0.86]
for i, wv in enumerate(widths):
    t6.columns[i].width = Inches(wv)
for c_i, h_ in enumerate(cols6):
    cell = t6.cell(0, c_i); cell.fill.solid(); cell.fill.fore_color.rgb = NAVY if c_i < 5 else GREEN
    r = cell.text_frame.paragraphs[0].add_run(); r.text = h_
    r.font.name = "Arial"; r.font.size = Pt(8.5); r.font.bold = True; r.font.color.rgb = WHITE
    cell.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER; cell.vertical_anchor = MSO_ANCHOR.MIDDLE
    for side in ("left", "right", "top", "bottom"):
        setattr(cell, f"margin_{side}", Inches(0.03))
for r_i, (f_, vs) in enumerate(feat, 1):
    cell = t6.cell(r_i, 0); cell.fill.solid(); cell.fill.fore_color.rgb = TINT_GREY
    r = cell.text_frame.paragraphs[0].add_run(); r.text = f_
    r.font.name = "Arial"; r.font.size = Pt(8.5); r.font.bold = True; r.font.color.rgb = NAVY
    cell.vertical_anchor = MSO_ANCHOR.MIDDLE
    for side in ("left", "right", "top", "bottom"):
        setattr(cell, f"margin_{side}", Inches(0.04))
    for c_i, v in enumerate(vs, 1):
        cell = t6.cell(r_i, c_i); cell.fill.solid()
        cell.fill.fore_color.rgb = TINT_GREEN if c_i == 5 else WHITE
        p = cell.text_frame.paragraphs[0]; p.alignment = PP_ALIGN.CENTER
        r = p.add_run(); r.text = "Next gate" if v == "next" else v
        r.font.name = "Arial"; r.font.bold = True
        r.font.size = Pt(8 if v == "next" else 13)
        r.font.color.rgb = GREEN if v == "✓" else RED if v == "✕" else AMBER
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
text(s6, 7.5, 5.05, 5.55, 0.3, [[("~ a bank official can still forge a letter.  Next gate: real-bank FD page proof (slide 4).", {"s": 8, "i": True, "c": MUTE})]], margin=0)

# project resources
res = box(s6, 0.35, 5.15, 7.0, 1.65, fill=TINT_BLUE, radius=0.08)
text(s6, 0.5, 5.22, 4.8, 1.55, [[("Project resources", {"b": True, "s": 12, "c": NAVY})],
     [("Demo video (2 min 20 s, labelled replay): ", {"b": True, "s": 9.5}), (DEMO_URL, {"s": 9, "c": BLUE, "u": True})],
     [("Prototype: ", {"b": True, "s": 9.5}), ("Scourge v1.0.0-rc1 — FastAPI app, Rust prover and verifier, synthetic bank; 102 / 102 tests", {"s": 9.5})],
     [("Evidence: ", {"b": True, "s": 9.5}), ("180-case fresh test, security audit and runbook in the project repository", {"s": 9.5})]],
     margin=0, spacing=3)
pic(s6, IMG + "qr.png", 5.75, 5.27, w=1.42, name="Demo QR")

prs.save(T + "Scourge_SIH26265_Idea.pptx")
print("saved")
