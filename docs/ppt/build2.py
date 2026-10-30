"""Scourge — SIH 2026 idea deck, redesigned. Official template frame kept (logo, oval, footer, slide numbers,
heading pointers); content rebuilt on a strict grid with Poppins/Inter, a bold palette, icons, one hero per slide."""
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE, MSO_CONNECTOR
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.oxml.ns import qn
from lxml import etree

T = "/tmp/claude-0/ppt/"
IMG, ICO = T + "img/", T + "icons/"
VIDEO = T + "video/scourge_demo_silent.mp4"
POSTER = T + "video/poster.png"
DEMO_URL = "youtu.be/8HoplhFOGCg"
DEMO_HREF = "https://youtu.be/8HoplhFOGCg"

H, B = "Poppins", "Inter"
INK = RGBColor(0x0F, 0x17, 0x2A)
INK2 = RGBColor(0x1E, 0x29, 0x3B)
VIO = RGBColor(0x5B, 0x3D, 0xF5)
VIO_T = RGBColor(0xF1, 0xEE, 0xFF)
MINT = RGBColor(0x10, 0xB9, 0x81)
MINT_D = RGBColor(0x04, 0x78, 0x57)
MINT_T = RGBColor(0xEC, 0xFD, 0xF5)
CORAL = RGBColor(0xF4, 0x3F, 0x5E)
CORAL_D = RGBColor(0xBE, 0x12, 0x3C)
CORAL_T = RGBColor(0xFF, 0xF1, 0xF2)
AMBER = RGBColor(0xF5, 0x9E, 0x0B)
AMBER_D = RGBColor(0xB4, 0x53, 0x09)
SKY = RGBColor(0x0E, 0xA5, 0xE9)
GREY = RGBColor(0x47, 0x55, 0x69)
GREY_L = RGBColor(0x94, 0xA3, 0xB8)
LINE = RGBColor(0xE2, 0xE8, 0xF0)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
SOFT = RGBColor(0xF8, 0xFA, 0xFC)
L, R = 0.5, 12.83          # content margins
CW = R - L

prs = Presentation(T + "template.pptx")
sl = prs.slides._sldIdLst
last = sl[-1]; prs.part.drop_rel(last.get(qn("r:id"))); sl.remove(last)
S = list(prs.slides)


# ------------------------------------------------------------------ primitives
def rm(shape):
    shape._element.getparent().remove(shape._element)


def find(slide, name):
    return next((s for s in slide.shapes if s.name == name), None)


def no_shadow(s):
    st = s._element.find(qn("p:style"))
    if st is not None:
        s._element.remove(st)
    spPr = s._element.spPr
    for el in spPr.findall(qn("a:effectLst")):
        spPr.remove(el)
    etree.SubElement(spPr, qn("a:effectLst"))


def rect(sl_, x, y, w, h, fill=None, line=None, r=0.1, lw=1.0, shape=MSO_SHAPE.ROUNDED_RECTANGLE):
    s = sl_.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    if shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        s.adjustments[0] = r
    if fill is None: s.fill.background()
    else: s.fill.solid(); s.fill.fore_color.rgb = fill
    if line is None: s.line.fill.background()
    else: s.line.color.rgb = line; s.line.width = Pt(lw)
    no_shadow(s)
    s.text_frame.text = ""
    return s


def tx(sl_, x, y, w, h, paras, size=11, color=INK, font=B, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP,
       m=0.0, after=0, shape=None, lsp=None):
    """paras: list of paragraphs; each a str or list of (text, opts). opts: b,i,c,s,f,align,after,u"""
    s = shape or sl_.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = s.text_frame; tf.word_wrap = True; tf.auto_size = None
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = Inches(m)
    tf.vertical_anchor = anchor
    for k, p in enumerate(paras):
        runs = [(p, {})] if isinstance(p, str) else p
        o0 = runs[0][1] if runs else {}
        para = tf.paragraphs[0] if k == 0 else tf.add_paragraph()
        para.alignment = o0.get("align", align)
        para.space_after = Pt(o0.get("after", after))
        if lsp: para.line_spacing = lsp
        pPr = para._p.get_or_add_pPr(); pPr.append(pPr.makeelement(qn("a:buNone"), {}))
        for t, o in runs:
            r = para.add_run(); r.text = t; f = r.font
            f.name = o.get("f", font); f.size = Pt(o.get("s", size)); f.bold = o.get("b", False)
            f.italic = o.get("i", False); f.underline = o.get("u", False); f.color.rgb = o.get("c", color)
    return s


def icon(sl_, name, x, y, d, bg, fg="w", pad=0.24):
    c = rect(sl_, x, y, d, d, fill=bg, shape=MSO_SHAPE.OVAL)
    p = d * pad
    sl_.shapes.add_picture(f"{ICO}{name}_{fg}.png", Inches(x + p), Inches(y + p), Inches(d - 2 * p), Inches(d - 2 * p))
    return c


def pill(sl_, x, y, w, h, label, fill, color=WHITE, size=10, font=B, bold=True, line=None):
    s = rect(sl_, x, y, w, h, fill=fill, line=line, r=0.5)
    tx(sl_, 0, 0, 0, 0, [[(label, {"b": bold, "c": color, "s": size, "f": font, "align": PP_ALIGN.CENTER})]],
       shape=s, anchor=MSO_ANCHOR.MIDDLE, m=0.02)
    return s


def pointer(sl_, x, y, w, label, color=VIO, size=12.5):
    """The template's fixed heading pointer, kept verbatim, styled."""
    rect(sl_, x, y + 0.07, 0.09, 0.22, fill=color, r=0.5)
    return tx(sl_, x + 0.18, y, w - 0.18, 0.36, [[(label, {"b": True, "c": color, "s": size, "f": H})]], anchor=MSO_ANCHOR.MIDDLE)


def arrow_line(sl_, x1, y1, x2, y2, color, w=2.0, head="end", dash=None):
    c = sl_.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    c.line.color.rgb = color; c.line.width = Pt(w)
    ln = c.line._get_or_add_ln()
    if dash:
        d = ln.makeelement(qn("a:prstDash"), {"val": dash}); ln.append(d)
    if head in ("end", "both"):
        ln.append(ln.makeelement(qn("a:tailEnd"), {"type": "triangle", "w": "med", "len": "med"}))
    if head in ("start", "both"):
        ln.append(ln.makeelement(qn("a:headEnd"), {"type": "triangle", "w": "med", "len": "med"}))
    return c


def title(sl_, parts, size=30):
    t = sl_.shapes.title.text_frame
    for p in list(t.paragraphs)[1:]: p._p.getparent().remove(p._p)
    p = t.paragraphs[0]
    for r in list(p.runs): r._r.getparent().remove(r._r)
    p.alignment = PP_ALIGN.CENTER
    for txt, col in parts:
        r = p.add_run(); r.text = txt; f = r.font
        f.name = H; f.bold = True; f.size = Pt(size); f.color.rgb = col


def frame(sl_):
    """Restyle the template's own frame elements (oval, footer) and drop the pointer box we re-create."""
    for sh in list(sl_.shapes):
        if sh.name.startswith("Oval") and sh.has_text_frame:
            sh.line.color.rgb = VIO; sh.line.width = Pt(1.5)
            tf = sh.text_frame
            for q in list(tf.paragraphs)[1:]: q._p.getparent().remove(q._p)
            p = tf.paragraphs[0]
            for r in list(p.runs): r._r.getparent().remove(r._r)
            r = p.add_run(); r.text = "SCOURGE"; r.font.name = H; r.font.bold = True; r.font.size = Pt(11); r.font.color.rgb = VIO
            p.alignment = PP_ALIGN.CENTER; tf.vertical_anchor = MSO_ANCHOR.MIDDLE
            tf.word_wrap = False; tf.margin_left = tf.margin_right = Inches(0)
        if "Footer" in sh.name and sh.has_text_frame and sh.text_frame.paragraphs[0].runs:
            rr = sh.text_frame.paragraphs[0].runs
            rr[0].text = "@SCOURGE  ·  SIH26265"
            for r in rr[1:]: r._r.getparent().remove(r._r)
            rr[0].font.name = B; rr[0].font.size = Pt(11); rr[0].font.bold = True
        if sh.name.startswith("Slide Number") and sh.has_text_frame:
            for p in sh.text_frame.paragraphs:
                for r in p.runs: r.font.name = B; r.font.bold = True
    tb = find(sl_, "TextBox 8")
    if tb is not None: rm(tb)


# ================================================================== 1  TITLE
s1 = S[0]
for r in find(s1, "Title 7").text_frame.paragraphs[0].runs:
    r.font.name = H; r.font.size = Pt(32); r.font.bold = True; r.font.color.rgb = INK
sub = find(s1, "Subtitle 3"); stf = sub.text_frame
for q in list(stf.paragraphs)[1:]: q._p.getparent().remove(q._p)
p = stf.paragraphs[0]
for r in list(p.runs): r._r.getparent().remove(r._r)
p.alignment = PP_ALIGN.CENTER
r = p.add_run(); r.text = "SCOURGE"; r.font.name = H; r.font.bold = True; r.font.size = Pt(36); r.font.color.rgb = VIO
p2 = stf.add_paragraph(); p2.alignment = PP_ALIGN.CENTER
r = p2.add_run(); r.text = "Ask the bank, not the paper."; r.font.name = B; r.font.italic = True; r.font.size = Pt(18); r.font.color.rgb = GREY
tb = find(s1, "TextBox 9"); tf = tb.text_frame
vals = [("Problem Statement ID", "SIH26265"), ("Problem Statement Title", "Streamline of Security Deposit / FDR Portal"),
        ("Theme", "Smart Automation"), ("PS Category", "Software"), ("Team ID", "199322"), ("Team Name (Registered on portal)", "Arnavax#")]
paras = [q for q in tf.paragraphs if q.text.strip()]
keep = {id(q._p) for q in paras}
for q in list(tf.paragraphs):
    if id(q._p) not in keep: q._p.getparent().remove(q._p)
for q, (lab, val) in zip(paras, vals):
    rs = list(q.runs)
    for x in rs[1:]: x._r.getparent().remove(x._r)
    rs[0].text = lab + " – "; rs[0].font.name = B; rs[0].font.size = Pt(17); rs[0].font.bold = True; rs[0].font.color.rgb = INK
    q.alignment = PP_ALIGN.LEFT; q.space_after = Pt(7)
    if val:
        r = q.add_run(); r.text = val; r.font.name = B; r.font.size = Pt(17); r.font.bold = False; r.font.color.rgb = VIO

# ================================================================== 2  IDEA
s2 = S[1]; frame(s2)
title(s2, [("SCOURGE ", VIO), ("— ASK THE BANK, NOT THE PAPER", INK)], 28)
pointer(s2, L, 1.22, 7.2, "Proposed Solution (Describe your Idea/Solution/Prototype)")
tx(s2, L, 1.58, 7.1, 0.7, [[("The ", {"s": 14.5, "f": H, "b": True}),
   ("bank's own website", {"s": 14.5, "f": H, "b": True, "c": VIO}),
   (" proves the FDR: no bank API, no agreement, no paper. AICTE sees ", {"s": 14.5, "f": H, "b": True}),
   ("only that record.", {"s": 14.5, "f": H, "b": True, "c": VIO})]], lsp=1.0)
blocks = [
    ("FaKey", VIO, "Detailed explanation of the proposed solution",
     [[("AICTE sends a ", {}), ("single-use challenge", {"b": True}), (" for one FDR. The institution opens its own net banking; the connection key is ", {}), ("split with AICTE's verifier", {"b": True}), (" (MPC-TLS), so nobody can fake the page. Login, OTP and PAN stay with the institution.", {})]]),
    ("FaMagnifyingGlass", MINT, "How it addresses the problem",
     [[("Paper is checked once and trusted for 10 years. Scourge ", {}), ("re-checks the bank any time and before every release", {"b": True}), (": a loan, early encashment, missing pledge (lien) or fake FDR shows up as a ", {}), ("Discrepancy", {"b": True, "c": CORAL_D}), (".", {})]]),
    ("FaShieldHalved", CORAL, "Innovation and uniqueness of the solution",
     [[("Works with banks' existing net banking", {"b": True}), (": no bank API or agreement needed, and API-ready when banks offer one. If a page cannot show a field: bank-signed e-advice, then branch confirmation. Fakes and replays are rejected.", {})]]),
]
y = 2.32
for ic, col, head, body in blocks:
    icon(s2, ic, L, y + 0.02, 0.56, col)
    tx(s2, L + 0.75, y, 6.35, 0.3, [[(head, {"b": True, "f": H, "s": 12.5, "c": col if col != MINT else MINT_D})]], anchor=MSO_ANCHOR.TOP)
    tx(s2, L + 0.75, y + 0.32, 6.35, 0.7, [[(t, dict(o, s=11, c=o.get("c", GREY))) for t, o in body[0]]], lsp=1.0)
    y += 1.0

# hero: dark evidence card
hx, hy, hw, hh = 7.95, 1.25, R - 7.95, 4.12
rect(s2, hx, hy, hw, hh, fill=INK, r=0.06)
pill(s2, hx + 0.3, hy + 0.24, 2.9, 0.32, "LIVE PROTOTYPE · SYNTHETIC BANK", MINT, color=INK, size=9, font=H)
tx(s2, hx + 0.3, hy + 0.64, hw - 0.6, 0.6, [[("The bank's server contradicts the paper.", {"f": H, "b": True, "s": 16, "c": WHITE})]], lsp=1.0)
iw = hw - 0.6
s2.shapes.add_picture(IMG + "cvw.png", Inches(hx + 0.3), Inches(hy + 1.28), Inches(iw), Inches(iw * 1124 / 2276))
yb = hy + 1.28 + iw * 1124 / 2276 + 0.16
pill(s2, hx + 0.3, yb, 2.05, 0.34, "✕  HIDDEN LOAN CAUGHT", CORAL, size=9.5, font=H)
tx(s2, hx + 2.5, yb, hw - 2.8, 0.34, [[("Every value traces to the bank's own bytes", {"s": 9.5, "c": GREY_L})]], anchor=MSO_ANCHOR.MIDDLE)

# lifecycle strip: the whole portal, not only verification
pointer(s2, L, 5.47, CW, "The complete FDR lifecycle in one portal (all five stages run in the prototype)", color=INK, size=11.5)
life = [("1  FILE", VIO, "Institution files online; renewals linked"),
        ("2  PROVE", SKY, "Bank-session proof on AICTE's challenge"),
        ("3  MONITOR", AMBER, "T-90/60/30/7 alerts; adequacy; MT940/camt.053 reconcile"),
        ("4  DECIDE", CORAL, "Verdict + evidence; hash-chained audit"),
        ("5  PLEDGE RELEASE", MINT, "Dept clearances, 2 signers, holds; QR-signed letter")]
lw5 = (CW - 4 * 0.22) / 5
for i, (h_, col, t_) in enumerate(life):
    x = L + i * (lw5 + 0.22)
    rect(s2, x, 5.86, lw5, 0.66, fill=SOFT, line=LINE, r=0.14)
    rect(s2, x, 5.86, 0.07, 0.66, fill=col, r=0.0)
    tx(s2, x + 0.17, 5.86, lw5 - 0.24, 0.66, [[(h_, {"f": H, "b": True, "s": 10.5, "c": col if col not in (MINT, AMBER) else (MINT_D if col == MINT else INK), "after": 1})],
                                            [(t_, {"s": 9.5, "c": GREY})]], anchor=MSO_ANCHOR.MIDDLE, lsp=0.95)
    if i < 4:
        arrow_line(s2, x + lw5 + 0.02, 6.19, x + lw5 + 0.2, 6.19, GREY_L, w=1.5)
tx(s2, L, 6.56, CW, 0.32, [[("Why now:  ", {"b": True, "f": H, "s": 10.5, "c": CORAL_D}),
   ("₹201.42 crore of FDRs \"do not appear in IDFC's records\"", {"b": True, "s": 10.5, "c": INK}),
   (" (CBI chargesheet, Oct 2026); AICTE joint FDRs found encashed or loaned against without consent.", {"s": 10, "c": GREY})]],
   anchor=MSO_ANCHOR.MIDDLE)

# ================================================================== 3  TECHNICAL APPROACH
s3 = S[2]; frame(s3)
title(s3, [("TECHNICAL APPROACH", INK)], 30)
pointer(s3, L, 1.22, 8, "Technologies to be used")
stack = [("FaFingerprint", VIO, "Proof layer", "TLSNotary MPC-TLS · Rust", "Selective disclosure · rustls"),
         ("FaServer", SKY, "Backend", "Python · FastAPI · SQLite", "AES-GCM vault · hash-chained audit"),
         ("FaCode", MINT, "Frontend", "Server-rendered HTML · JS", "Live 3-party proof view"),
         ("FaVial", AMBER, "Ops & QA", "Docker · nginx · GitHub CI", "186 tests · 2FA · audits")]
cw4 = (CW - 3 * 0.2) / 4
for i, (ic, col, head, l1, l2) in enumerate(stack):
    x = L + i * (cw4 + 0.2)
    rect(s3, x, 1.62, cw4, 0.92, fill=SOFT, line=LINE, r=0.12)
    icon(s3, ic, x + 0.18, 1.8, 0.56, col)
    tx(s3, x + 0.88, 1.66, cw4 - 0.98, 0.84, [[(head, {"f": H, "b": True, "s": 12, "c": INK, "after": 2})],
                                            [(l1, {"s": 9.5, "c": GREY})], [(l2, {"s": 9.5, "c": GREY})]], anchor=MSO_ANCHOR.MIDDLE)

pointer(s3, L, 2.64, 8.4, "Methodology and process for implementation (Flow Charts/Images/ working prototype)")
tx(s3, L + 0.2, 2.95, 8.3, 0.3, [[("In plain words: ", {"b": True, "s": 10.5, "c": VIO}),
   ("an AICTE \"notary\" watches the bank page load, sees only the FD line, and nobody can change it.", {"s": 10.5, "i": True, "c": INK})]], anchor=MSO_ANCHOR.MIDDLE)
# native three-party sequence diagram
dx, dw = L, 8.3
rect(s3, dx, 3.27, dw, 3.5, fill=SOFT, line=LINE, r=0.05)
cols = [("FaBuildingColumns", GREY, "Bank's server", "sees a normal login"),
        ("FaSchool", VIO, "Institution", "runs the prover"),
        ("FaLandmark", MINT, "AICTE verifier", "holds half the TLS keys")]
cx = [dx + 1.35, dx + 4.15, dx + 6.95]
for (ic, col, nm, sub_), c in zip(cols, cx):
    icon(s3, ic, c - 0.22, 3.38, 0.44, col)
    tx(s3, c - 1.2, 3.84, 2.4, 0.42, [[(nm, {"f": H, "b": True, "s": 10.5, "c": INK, "align": PP_ALIGN.CENTER})],
                                     [(sub_, {"s": 9, "c": GREY, "align": PP_ALIGN.CENTER})]])
    arrow_line(s3, c, 4.32, c, 6.12, GREY_L, w=1.0, head=None, dash="dash")
msgs = [(2, 1, AMBER, "1  Single-use challenge, consumed first"),
        (1, 0, VIO, "2  Logs in; TLS keys split with AICTE's verifier"),
        (0, 1, GREY, "3  Bank sends the FD page, encrypted"),
        (1, 2, VIO, "4  Proof: only this deposit's record"),
        ]
yy = 4.62
for a, b_, col, lab in msgs:
    x1, x2 = cx[a], cx[b_]
    tx(s3, min(x1, x2) - 0.2, yy - 0.29, abs(x2 - x1) + 0.4, 0.26, [[(lab, {"s": 9.5, "b": True, "c": INK, "align": PP_ALIGN.CENTER})]])
    arrow_line(s3, x1, yy, x2, yy, col, w=2.25)
    yy += 0.38
icon(s3, "FaCircleCheck", cx[2] - 0.15, yy - 0.12, 0.3, MINT, pad=0.2)
tx(s3, cx[0] - 0.2, yy - 0.13, cx[2] - cx[0] - 0.05, 0.3, [[("5  Verifier checks certificate, bank host and record  →  register match", {"s": 9.5, "b": True, "c": INK, "align": PP_ALIGN.RIGHT})]], anchor=MSO_ANCHOR.MIDDLE)
# outcomes
ox = dx + 0.25
tx(s3, ox, 6.36, 1.3, 0.3, [[("Outcome:", {"f": H, "b": True, "s": 10, "c": INK})]], anchor=MSO_ANCHOR.MIDDLE)
ox += 1.0
for lab, col in [("Verified", MINT), ("Discrepancy", CORAL), ("Insufficient evidence", AMBER), ("Rejected", GREY)]:
    w = 0.075 * len(lab) + 0.5
    pill(s3, ox, 6.36, w, 0.3, lab, col, size=9, font=H)
    ox += w + 0.12

# hero: video card
vx, vy, vw, vh = 9.05, 2.85, R - 9.05, 3.92
rect(s3, vx, vy, vw, vh, fill=INK, r=0.06)
tx(s3, vx + 0.25, vy + 0.18, vw - 0.5, 0.35, [[("WATCH THE DEMO (100 s)", {"f": H, "b": True, "s": 11, "c": MINT})]])
mw = vw - 0.5; mh = mw * 9 / 16
mv = s3.shapes.add_movie(VIDEO, Inches(vx + 0.25), Inches(vy + 0.6), Inches(mw), Inches(mh), poster_frame_image=POSTER, mime_type="video/mp4")
mv.name = "Demo video"
qy = vy + 0.6 + mh + 0.2
qr = rect(s3, vx + 0.25, qy, 1.05, 1.05, fill=WHITE, r=0.08)
qp = s3.shapes.add_picture(IMG + "qr_dark.png", Inches(vx + 0.3), Inches(qy + 0.05), Inches(0.95), Inches(0.95)); qp.click_action.hyperlink.address = DEMO_HREF
tx(s3, vx + 1.45, qy, vw - 1.65, 1.05, [[("Click to play here, or scan / click the QR for YouTube", {"s": 9.5, "b": True, "c": WHITE, "after": 3})],
                                        [("Synthetic bank + live SBI session", {"s": 9.5, "c": GREY_L})]], anchor=MSO_ANCHOR.MIDDLE)

# ================================================================== 4  FEASIBILITY
s4 = S[3]; frame(s4)
title(s4, [("FEASIBILITY AND VIABILITY", INK)], 30)
pointer(s4, L, 1.22, 8, "Analysis of the feasibility of the idea")
tiles = [("FaBolt", VIO, "0.58 s", "per bank proof", "median of 60 runs · synthetic bank"),
         ("FaGlobe", SKY, "SBI live", "live split-key session, SBI API", "HTTP 200 in 1.0 s · 30 Oct 2026"),
         ("FaVial", MINT, "186 / 186", "automated tests pass", "incl. attack, forgery, 2FA, vault tests"),
         ("FaCoins", AMBER, "₹0", "software licences", "one small server [estimate]")]
for i, (ic, col, big, lab, note) in enumerate(tiles):
    x = L + i * (cw4 + 0.2)
    rect(s4, x, 1.65, cw4, 1.3, fill=WHITE, line=LINE, r=0.1, lw=1.25)
    icon(s4, ic, x + 0.2, 1.84, 0.5, col)
    tx(s4, x + 0.85, 1.74, cw4 - 0.95, 0.62, [[(big, {"f": H, "b": True, "s": 26, "c": INK})]], anchor=MSO_ANCHOR.MIDDLE)
    tx(s4, x + 0.2, 2.42, cw4 - 0.35, 0.5, [[(lab, {"s": 10.5, "b": True, "c": INK})], [(note, {"s": 9.5, "c": GREY, "i": True})]])

# live real-bank run (replaces the generic feasibility ticks with evidence)
rect(s4, L, 3.06, CW, 0.56, fill=INK, r=0.2)
icon(s4, "FaBuildingColumns", L + 0.14, 3.15, 0.38, MINT, pad=0.22)
tx(s4, L + 0.62, 3.06, 2.5, 0.56, [[("LIVE ON A REAL BANK", {"f": H, "b": True, "s": 11, "c": MINT})], [("SBI · 30 Oct 2026 · own account", {"s": 8.5, "c": GREY_L})]], anchor=MSO_ANCHOR.MIDDLE, lsp=0.95)
tx(s4, L + 3.05, 3.06, CW - 3.15, 0.56, [[('{"ok":true,"server":"api.onlineyono.sbi.bank.in","status_line":"HTTP/1.1 200 OK","secs":1.0}', {"f": "Courier New", "b": True, "s": 9.5, "c": WHITE})],
   [("Split-key session with SBI's production net-banking API; cert checked against public roots.  Log: tools/realbank/RESULTS.md", {"s": 8.5, "c": GREY_L, "i": True})]], anchor=MSO_ANCHOR.MIDDLE, lsp=0.95)

pointer(s4, L, 3.72, 6.0, "Potential challenges and risks", color=CORAL)
pointer(s4, 6.88, 3.72, 5.9, "Strategies for overcoming these challenges", color=MINT_D)
risks = [("Bank page hides liens, or bytes are withheld", "Fallbacks: bank-signed e-advice, then branch confirmation; never a guess"),
         ("Fake bank site or a replayed old proof", "Rejected: bank certificate + single-use, 15-min challenge"),
         ("Proof of a different FD, or an edited screen", "Rejected / no effect: FD bound to challenge; proof covers bank bytes"),
         ("Bank encrypts data inside HTTPS, or ties it to the browser", "Browser-extension prover; verifier gets the per-session app key")]
ry = 4.13
for r_, s_ in risks:
    rect(s4, L, ry, 6.1, 0.44, fill=CORAL_T, r=0.3)
    icon(s4, "FaXmark", L + 0.08, ry + 0.06, 0.32, CORAL, pad=0.25)
    tx(s4, L + 0.5, ry, 5.5, 0.44, [[(r_, {"s": 10.5, "b": True, "c": INK})]], anchor=MSO_ANCHOR.MIDDLE)
    arrow_line(s4, L + 6.17, ry + 0.22, 6.82, ry + 0.22, GREY_L, w=1.5)
    rect(s4, 6.88, ry, R - 6.88, 0.44, fill=MINT_T, r=0.3)
    icon(s4, "FaCheck", 6.96, ry + 0.06, 0.32, MINT, pad=0.25)
    tx(s4, 7.38, ry, R - 7.48, 0.44, [[(s_, {"s": 10.5, "c": INK})]], anchor=MSO_ANCHOR.MIDDLE)
    ry += 0.52
g = rect(s4, L, 6.2, CW, 0.58, fill=INK, r=0.25)
tx(s4, L + 0.3, 6.2, CW - 0.6, 0.58, [[("PILOT PLAN  ", {"f": H, "b": True, "s": 11, "c": AMBER}),
   ("✓ Prototype, 186 tests  →  ✓ Live SBI session (30 Oct)  →  ", {"s": 10.5, "b": True, "c": MINT}),
   ("FD pages at 3 large banks  →  1 AICTE regional office  →  National rollout", {"s": 10.5, "c": WHITE})]],
   anchor=MSO_ANCHOR.MIDDLE)

# ================================================================== 5  IMPACT
s5 = S[4]; frame(s5)
title(s5, [("IMPACT AND BENEFITS", INK)], 30)
pointer(s5, L, 1.22, 8, "Potential impact on the target audience")
aud = [("FaUserTie", VIO, "AICTE RB officers", "A dated proof from the bank in seconds, not a letter in weeks"),
       ("FaLandmark", SKY, "AICTE finance", "The whole FDR book is re-checkable, before every release"),
       ("FaSchool", MINT, "Institutions", "One short proof from their own net banking; Hindi or English"),
       ("FaGraduationCap", AMBER, "Students", "29.6 lakh enrolled in 2025-26 (Lok Sabha reply); deposits checked all 10 years")]
for i, (ic, col, who, what) in enumerate(aud):
    x = L + i * (cw4 + 0.2)
    rect(s5, x, 1.65, cw4, 1.15, fill=WHITE, line=LINE, r=0.1, lw=1.25)
    icon(s5, ic, x + 0.18, 1.8, 0.5, col)
    tx(s5, x + 0.8, 1.8, cw4 - 0.9, 0.5, [[(who, {"f": H, "b": True, "s": 11.5, "c": INK})]], anchor=MSO_ANCHOR.MIDDLE)
    tx(s5, x + 0.18, 2.32, cw4 - 0.33, 0.45, [[(what, {"s": 9.5, "c": GREY})]], lsp=1.0)

pointer(s5, L, 2.98, 8, "Benefits of the solution (social, economic, environmental, etc.)")
lw_, gap_ = 3.6, 0.15
pill(s5, L, 3.42, lw_, 0.36, "TODAY", CORAL, size=10.5, font=H)
pill(s5, L + lw_ + gap_, 3.42, lw_, 0.36, "WITH SCOURGE", MINT, size=10.5, font=H)
pairs = [("Branch letter or uploaded screenshot", "The bank's own server, proven"),
         ("Checked once, trusted for 10 years", "Re-checked any time, before every release"),
         ("Weeks per confirmation", "0.58 s per proof (synthetic); 1.0 s live SBI session"),
         ("Hidden loans found at release, or never", "Pledge checked at the bank; two-officer release")]
py = 3.88
for a_, b2 in pairs:
    rect(s5, L, py, lw_, 0.5, fill=CORAL_T, r=0.2)
    tx(s5, L + 0.15, py, lw_ - 0.3, 0.5, [[(a_, {"s": 10, "c": INK})]], anchor=MSO_ANCHOR.MIDDLE)
    rect(s5, L + lw_ + gap_, py, lw_, 0.5, fill=MINT_T, r=0.2)
    tx(s5, L + lw_ + gap_ + 0.15, py, lw_ - 0.3, 0.5, [[(b2, {"s": 10, "b": True, "c": INK})]], anchor=MSO_ANCHOR.MIDDLE)
    py += 0.57

# hero: results
hx = L + 2 * lw_ + gap_ + 0.3
rect(s5, hx, 3.42, R - hx, 2.75, fill=INK, r=0.06)
tx(s5, hx + 0.3, 3.55, R - hx - 0.6, 0.55, [[("TESTED ON 180 FRESH DEPOSITS", {"f": H, "b": True, "s": 11, "c": MINT})],
   [("Random amounts, dates, formats · real proofs · synthetic bank", {"s": 9.5, "c": GREY_L})]])
res = [("119 / 119", "bad deposits the upload portal approved", CORAL),
       ("0 / 119", "bad deposits Scourge verified", MINT),
       ("0 / 61", "honest deposits Scourge accused", MINT)]
ry = 4.22
for big, lab, col in res:
    tx(s5, hx + 0.3, ry, 1.75, 0.6, [[(big, {"f": H, "b": True, "s": 22, "c": col})]], anchor=MSO_ANCHOR.MIDDLE)
    tx(s5, hx + 2.1, ry, R - hx - 2.35, 0.6, [[(lab, {"s": 10, "c": WHITE})]], anchor=MSO_ANCHOR.MIDDLE)
    ry += 0.64

ben = [("FaPeopleGroup", MINT, "Social", "students' deposits stay intact"),
       ("FaCoins", SKY, "Economic", "stops loss before release"),
       ("FaLeaf", RGBColor(0x16, 0xA3, 0x4A), "Environmental", "no paper letters or couriers"),
       ("FaScaleBalanced", VIO, "Governance", "accounts, 2FA, vault, chained audit")]
bw = (CW - 3 * 0.2) / 4
for i, (ic, col, h_, t_) in enumerate(ben):
    x = L + i * (bw + 0.2)
    icon(s5, ic, x, 6.33, 0.42, col)
    tx(s5, x + 0.52, 6.3, bw - 0.55, 0.48, [[(h_, {"f": H, "b": True, "s": 10, "c": INK})], [(t_, {"s": 9.5, "c": GREY})]], anchor=MSO_ANCHOR.MIDDLE)

# ================================================================== 6  RESEARCH
s6 = S[5]; frame(s6)
title(s6, [("RESEARCH AND REFERENCES", INK)], 30)
pointer(s6, L, 1.22, 6.3, "Details / Links of the reference and research work")
refs = [("FaGavel", CORAL, "The problem is real", [
            ("CBI: ₹201.42 cr of FDRs not in IDFC's records (ThePrint, 7 Oct 2026)", "theprint.in › cbi-says-haryana-pollution-board…"),
            ("CBI on AICTE: joint FDRs encashed or loaned without consent", "deccanherald.com › greater-transparency-aicte…")]),
        ("FaBook", SKY, "Regulation", [
            ("AICTE guidelines 2024-27: cl. 6.2(g) 10-year release; cl. 11.8", "indiankanoon.org/doc/173919027"),
            ("Account Aggregator users must be regulated FIUs (DFS)", "financialservices.gov.in › account-aggregator…")]),
        ("FaMicrochip", VIO, "Technology", [
            ("TLSNotary: MPC-TLS proofs of web data, MIT / Apache-2.0", "tlsnotary.org"),
            ("NeSL e-BG for security deposits: new instruments only (WDRA)", "wdra.gov.in › ebg_requirement-27-08-2025")])]
y = 1.68
for ic, col, head, items in refs:
    icon(s6, ic, L, y, 0.44, col)
    tx(s6, L + 0.58, y, 5.6, 0.44, [[(head, {"f": H, "b": True, "s": 11.5, "c": INK})]], anchor=MSO_ANCHOR.MIDDLE)
    ps = []
    for t_, u_ in items:
        ps.append([(t_, {"s": 10, "c": INK, "after": 0})])
        ps.append([(u_, {"s": 9, "c": VIO, "after": 4})])
    tx(s6, L + 0.58, y + 0.45, 5.75, 0.9, ps)
    y += 1.22

# resources card
rect(s6, L, 5.4, 6.33, 1.38, fill=INK, r=0.08)
q = rect(s6, L + 0.2, 5.55, 1.08, 1.08, fill=WHITE, r=0.08)
qp6 = s6.shapes.add_picture(IMG + "qr_dark.png", Inches(L + 0.25), Inches(5.6), Inches(0.98), Inches(0.98)); qp6.click_action.hyperlink.address = DEMO_HREF
tx(s6, L + 1.45, 5.5, 4.7, 1.2, [[("PROJECT RESOURCES", {"f": H, "b": True, "s": 11, "c": MINT, "after": 3})],
   [("Demo video (90 s): ", {"b": True, "s": 9.5, "c": WHITE}), (DEMO_URL, {"s": 9, "c": RGBColor(0xC4, 0xB5, 0xFD)})],
   [("Prototype: ", {"b": True, "s": 9.5, "c": WHITE}), ("Scourge v1.0.0-rc1 · 186 / 186 tests · synthetic bank", {"s": 9.5, "c": GREY_L})],
   [("Code + evidence: ", {"b": True, "s": 9.5, "c": WHITE}), ("github.com/Lakki007/Arnavax-Scourge-SIH26265", {"s": 9, "c": RGBColor(0xC4, 0xB5, 0xFD)})]], anchor=MSO_ANCHOR.MIDDLE)

# comparison table (hero)
tx_ = 7.1
pointer(s6, tx_, 1.22, R - tx_, "Scourge vs existing approaches", color=INK)
heads = ["", "Upload & approve", "Bank letter", "Account Aggregator", "NeSL e-BG", "Scourge"]
feat = [("Uses the bank's own data", "✕✓✓✓✓"), ("No bank API or agreement", "✓✓✕✕✓"),
        ("Institution cannot forge it", "✕~✓✓✓"), ("Covers existing FDR book", "✓✓✓✕✓"),
        ("Re-check in seconds", "✕✕✓✓✓"), ("Shown on a real bank today", "✓✓✓✓n")]
tb_ = s6.shapes.add_table(len(feat) + 1, 6, Inches(tx_), Inches(1.68), Inches(R - tx_), Inches(4.6)).table
widths = [1.6, 0.82, 0.72, 0.92, 0.72, 0.95]
sc = (R - tx_) / sum(widths)
for i, w in enumerate(widths): tb_.columns[i].width = Inches(w * sc)
tblPr = tb_._tbl.tblPr
for k in ("bandRow", "firstRow"): tblPr.set(k, "0")


def cell(c, txt, fill, color, size, bold=True, align=PP_ALIGN.CENTER, font=B):
    c.fill.solid(); c.fill.fore_color.rgb = fill
    c.margin_left = c.margin_right = Inches(0.05); c.margin_top = c.margin_bottom = Inches(0.03)
    c.vertical_anchor = MSO_ANCHOR.MIDDLE
    p_ = c.text_frame.paragraphs[0]; p_.alignment = align
    r_ = p_.add_run(); r_.text = txt; r_.font.name = font; r_.font.size = Pt(size); r_.font.bold = bold; r_.font.color.rgb = color


for j, h_ in enumerate(heads):
    cell(tb_.cell(0, j), h_, VIO if j == 5 else INK, WHITE, 9, font=H)
for i, (f_, vals) in enumerate(feat, 1):
    cell(tb_.cell(i, 0), f_, SOFT, INK, 9, align=PP_ALIGN.LEFT)
    for j, v in enumerate(vals, 1):
        if v == "n":
            cell(tb_.cell(i, j), "SBI session; FD next", VIO_T, VIO, 8)
        else:
            cell(tb_.cell(i, j), v, VIO_T if j == 5 else WHITE, MINT if v == "✓" else CORAL if v == "✕" else AMBER, 15)
    tb_.rows[i].height = Inches(0.62)
tb_.rows[0].height = Inches(0.62)
tx(s6, tx_, 6.38, R - tx_, 0.4, [[("~ a bank letter can still be forged.  Live split-key session with SBI's API done (30 Oct 2026); FD-page proof is roadmap step 1.", {"s": 9.5, "i": True, "c": GREY})]])

for sl in prs.slides:
    for sh in sl.shapes:
        if sh.has_text_frame:
            for para in sh.text_frame.paragraphs:
                for run in para.runs:
                    if run.text.strip() == DEMO_URL:
                        pass  # QR images carry the clickable link; LibreOffice recolours text links unreadably
prs.save(T + "Scourge_SIH26265_Idea.pptx")
print("saved")
