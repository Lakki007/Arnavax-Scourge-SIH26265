"""Make text links clickable in both PPTX and PDF without recolouring them.
Pass 1: render the PDF, find each link's text line. Pass 2: add an invisible
hyperlinked rectangle over that line in the PPTX, then re-render the PDF."""
import subprocess, sys
import pdfplumber
from pptx import Presentation
from pptx.util import Emu
from lxml import etree

T = "/tmp/claude-0/ppt/"
PPTX, PDF = T + "Scourge_SIH26265_Idea.pptx", T + "Scourge_SIH26265_Idea.pdf"
SOFFICE = sys.argv[1]
LINKS = {  # slide number -> [(text that starts the line, url)]
    3: [("scourge-sih26265.onrender.com", "https://scourge-sih26265.onrender.com")],
    6: [("youtu.be/Gm4dzjRQvC4", "https://youtu.be/Gm4dzjRQvC4"),
        ("Live demo", "https://scourge-sih26265.onrender.com"),
        ("github.com/Lakki007/Arnavax-Scourge-SIH26265", "https://github.com/Lakki007/Arnavax-Scourge-SIH26265"),
        ("theprint.in", "https://theprint.in/india/cbi-says-haryana-pollution-board-secys-notes-fed-excess-deposits-to-idfc-rs-50-cr-cap-became-rs-163-cr/3062087/"),
        ("deccanherald.com", "https://www.deccanherald.com/india/greater-transparency-aicte-needed-says-2576077"),
        ("indiankanoon.org", "https://indiankanoon.org/doc/173919027/"),
        ("financialservices.gov.in", "https://financialservices.gov.in/account-aggregator-framework"),
        ("tlsnotary.org", "https://tlsnotary.org"),
        ("wdra.gov.in", "https://wdra.gov.in/documents/d/wdra/ebg_requirement-27-08-2025")],
}


def render():
    subprocess.run(["python3", SOFFICE, "--headless", "--convert-to", "pdf", "--outdir", T, PPTX], check=True, capture_output=True)


render()
prs = Presentation(PPTX)
sw, sh = prs.slide_width, prs.slide_height
with pdfplumber.open(PDF) as pdf:
    for n, items in LINKS.items():
        page = pdf.pages[n - 1]
        kx, ky = sw / float(page.width), sh / float(page.height)
        lines = page.extract_text_lines()
        slide = prs.slides[n - 1]
        for key, url in items:
            hits = []
            for m in page.search(key, regex=False, case=True):
                # extend to the end of the same line, so the truncated part of a URL is clickable too
                # walk right along the same baseline until a gap wider than 12 pt (the next column)
                row = sorted((c for c in page.chars if abs(c["top"] - m["top"]) < 2 and c["x0"] >= m["x0"] - 0.5), key=lambda c: c["x0"])
                x1 = m["x1"]
                for c in row:
                    if c["x0"] - x1 > 12:
                        break
                    x1 = max(x1, c["x1"])
                hits.append((m["x0"], m["top"], x1, m["bottom"]))
            if len(hits) != 1:
                raise SystemExit(f"slide {n}: {key!r} found {len(hits)} times")
            x0, t, x1, b = hits[0]
            pad = 1.5
            shp = slide.shapes.add_shape(1, Emu(int((x0 - pad) * kx)), Emu(int((t - pad) * ky)),
                                         Emu(int((x1 - x0 + 2 * pad) * kx)), Emu(int((b - t + 2 * pad) * ky)))
            shp.name = "link: " + key
            shp.fill.background()
            shp.line.fill.background()
            sp = shp._element
            st = sp.find("{http://schemas.openxmlformats.org/presentationml/2006/main}style")
            if st is not None:
                sp.remove(st)
            shp.click_action.hyperlink.address = url
            print(f"slide {n}: {key} -> {url}")
prs.save(PPTX)
render()
