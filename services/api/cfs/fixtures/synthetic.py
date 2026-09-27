"""Synthetic fixture loading and document generation. Everything produced here is labelled SYNTHETIC."""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any

import yaml

SYNTHETIC_BANNER = "SYNTHETIC FIXTURE - NOT UNIVERSITY OF CALGARY DATA"
TERM_LABEL = {"fall": "Fall", "winter": "Winter", "spring": "Spring", "summer": "Summer", "unknown": "Term not stated"}
LEVEL_LABEL = {"introduce": "Introduced", "reinforce": "Reinforced", "assess": "Assessed"}


def fixtures_dir() -> Path:
    env = os.environ.get("CFS_FIXTURES_DIR")
    if env:
        return Path(env)
    for base in [Path(__file__).resolve(), Path.cwd()]:
        for parent in [base, *base.parents]:
            cand = parent / "fixtures" / "synthetic"
            if (cand / "program.yaml").exists():
                return cand
    if Path("/fixtures/synthetic/program.yaml").exists():
        return Path("/fixtures/synthetic")
    raise FileNotFoundError("fixtures/synthetic/program.yaml not found; set CFS_FIXTURES_DIR")


def load_fixture() -> dict[str, Any]:
    with open(fixtures_dir() / "program.yaml") as f:
        fx = yaml.safe_load(f)
    assert fx.get("synthetic") is True, "fixture must be marked synthetic"
    return fx


# Sentences placed in the outline, later located verbatim to create evidence spans.
def course_lines(c: dict[str, Any], fx: dict[str, Any]) -> list[tuple[str, str]]:
    """(style, text) lines for one course section."""
    lines = [("h2", f"{c['code']}: {c['title']}")]
    if c.get("credits") is not None:
        lines.append(("p", f"Credits: {c['credits']}"))
    place = f"Year {c['year']}, {TERM_LABEL[c['term']]}. " + {
        "required": "Required course.",
        "elective": "Elective course.",
        "pathway_required": f"Required in the {c.get('pathway', '')} pathway.",
        "optional": "Optional course.",
    }.get(c["class"], "")
    lines.append(("p", place))
    lines.append(("p", f"Description: {c['description']}"))
    if c.get("prerequisite"):
        lines.append(("p", f"Prerequisite(s): {c['prerequisite']}."))
    if c.get("corequisite"):
        lines.append(("p", f"Corequisite(s): {c['corequisite']}, which may be taken concurrently."))
    for tp in fx.get("topic_preparation", []):
        if tp.get("evidence") and tp["to"].startswith(c["code"].replace(" ", "")):
            lines.append(("p", tp["evidence"] + "."))
    if c.get("outcomes"):
        lines.append(("h3", "Learning outcomes"))
        for o in c["outcomes"]:
            contrib = "; ".join(f"{plo} {LEVEL_LABEL[lvl]}" for plo, lvl in o.get("contributes", []))
            lines.append(("p", f"{o['key']}: {o['statement']} (Program outcomes: {contrib})"))
    if c.get("assessments"):
        lines.append(("h3", "Assessments"))
        for a in c["assessments"]:
            wk = f", week {a['week']}" if a.get("week") else ", timing not stated"
            lines.append(
                (
                    "p",
                    f"{a['key']}: {a['title']} ({a['format']}, {a['weight']}%{wk}). "
                    f"Assesses: {', '.join(a['assesses'])}.",
                )
            )
    return lines


def build_outline_pdf(fx: dict[str, Any], academic_year: str = "2024-25") -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    ss = getSampleStyleSheet()
    styles = {"h1": ss["Title"], "h2": ss["Heading2"], "h3": ss["Heading4"], "p": ss["BodyText"]}
    buf = io.BytesIO()

    def banner(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 8)
        canvas.drawString(40, letter[1] - 24, SYNTHETIC_BANNER)
        canvas.drawRightString(letter[0] - 40, 20, f"Page {doc.page}")
        canvas.restoreState()

    p = fx["program"]
    story: list[Any] = [
        Paragraph(f"{p['name']} Program Outline {academic_year}", styles["h1"]),
        Paragraph(p["institution"], styles["p"]),
        Spacer(1, 8),
        Paragraph("Program learning outcomes", styles["h2"]),
    ]
    for plo in fx["program_outcomes"]:
        story.append(Paragraph(f"{plo['key']} ({plo['label']}): {plo['statement']}", styles["p"]))
    for g in fx.get("elective_groups", []):
        story.append(
            Paragraph(f"{g['name']}: choose at least {g['min_courses']} of {', '.join(g['members'])}.", styles["p"])
        )
    for c in fx["courses"]:
        story.append(Spacer(1, 6))
        for style, text in course_lines(c, fx):
            story.append(Paragraph(text, styles[style]))
    SimpleDocTemplate(
        buf, pagesize=letter, title=f"{p['name']} Program Outline {academic_year} (SYNTHETIC)", topMargin=40
    ).build(story, onFirstPage=banner, onLaterPages=banner)
    return buf.getvalue()


def build_review_pdf(fx: dict[str, Any]) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate

    rr = fx["review_report"]
    ss = getSampleStyleSheet()
    buf = io.BytesIO()
    story = [
        Paragraph(rr["title"], ss["Title"]),
        Paragraph(SYNTHETIC_BANNER, ss["BodyText"]),
        Paragraph("Findings on sequencing", ss["Heading2"]),
        Paragraph(rr["conflict"]["statement"], ss["BodyText"]),
        Paragraph("Reviewers recommended strengthening assessment of research ethics.", ss["BodyText"]),
    ]
    SimpleDocTemplate(buf, pagesize=letter, title=rr["title"]).build(story)
    return buf.getvalue()


def build_scanned_pdf(text: str = "SYN 999: Scanned Placeholder Course. Prerequisite(s): SYN 101.") -> bytes:
    """A PDF whose only page is an image of text (no text layer), for OCR-fallback tests."""
    from PIL import Image, ImageDraw, ImageFont
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    img = Image.new("RGB", (1700, 400), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=48)
    except TypeError:
        font = ImageFont.load_default()
    draw.text((40, 150), text, fill="black", font=font)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.drawImage(ImageReader(img), 20, 500, width=570, height=134)
    c.showPage()
    c.save()
    return buf.getvalue()
