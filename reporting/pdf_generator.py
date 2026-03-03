"""
reporting/pdf_generator.py
--------------------------
Generates professional PDF usage reports using ReportLab.

Each report contains:
  - A branded header with the application name
  - Device information and reporting period
  - A summary table: Opening Value | Closing Value | Total Usage
  - A footer with generation timestamp and file path

Reports are saved to  <project_root>/reports/<device>_<month_year>.pdf
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = PROJECT_ROOT / "reports"
REPORTS_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Colour Palette
# ---------------------------------------------------------------------------

BRAND_DARK = colors.HexColor("#1A1A2E")   # deep navy — header background
BRAND_MID  = colors.HexColor("#16213E")   # slightly lighter navy
BRAND_ACCENT = colors.HexColor("#0F3460") # accent blue
BRAND_HIGHLIGHT = colors.HexColor("#E94560")  # red accent for totals
TEXT_LIGHT = colors.white
TEXT_DARK  = colors.HexColor("#2C2C2C")
ROW_ALT    = colors.HexColor("#F4F6F9")    # alternating row background


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_report(
    *,
    device: str,
    month_year: str,
    opening_value: float,
    closing_value: float,
    usage: float,
) -> Path:
    """
    Generate a PDF usage report and save it to the reports directory.

    Parameters
    ----------
    device       : Device identifier, e.g. "Meter_A"
    month_year   : Reporting period string, e.g. "2023-10"
    opening_value: Meter reading at the start of the period
    closing_value: Meter reading at the end of the period
    usage        : Calculated consumption (closing − opening)

    Returns
    -------
    Path
        Absolute path to the generated PDF file.
    """
    safe_device = device.replace("/", "_").replace("\\", "_").replace(" ", "_")
    safe_month = month_year.replace("/", "-")
    filename = f"{safe_device}_{safe_month}.pdf"
    output_path = REPORTS_DIR / filename

    # Parse month_year for human-readable label
    try:
        dt = datetime.strptime(month_year, "%Y-%m")
        period_label = dt.strftime("%B %Y")   # e.g. "October 2023"
    except ValueError:
        period_label = month_year

    _build_pdf(
        path=output_path,
        device=device,
        month_year=month_year,
        period_label=period_label,
        opening_value=opening_value,
        closing_value=closing_value,
        usage=usage,
    )

    logger.info("PDF report generated: %s", output_path)
    return output_path


# ---------------------------------------------------------------------------
# Internal Builder
# ---------------------------------------------------------------------------

def _build_pdf(
    *,
    path: Path,
    device: str,
    month_year: str,
    period_label: str,
    opening_value: float,
    closing_value: float,
    usage: float,
) -> None:
    """Assemble the ReportLab Platypus document."""

    page_w, page_h = A4
    margin = 2 * cm

    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin,
        bottomMargin=margin,
        title=f"Usage Report — {device} — {period_label}",
        author="Reportr Application",
    )

    styles = getSampleStyleSheet()
    story: list = []

    # -----------------------------------------------------------------------
    # Header band
    # -----------------------------------------------------------------------
    header_style = ParagraphStyle(
        "header",
        parent=styles["Normal"],
        fontSize=24,
        textColor=TEXT_LIGHT,
        fontName="Helvetica-Bold",
        spaceAfter=0,
        spaceBefore=0,
    )
    sub_header_style = ParagraphStyle(
        "sub_header",
        parent=styles["Normal"],
        fontSize=11,
        textColor=colors.HexColor("#A0A8C0"),
        fontName="Helvetica",
        spaceAfter=0,
        spaceBefore=4,
    )

    header_table = Table(
        [[
            Paragraph("⚡ Reportr", header_style),
            Paragraph("Submeter Usage Report", sub_header_style),
        ]],
        colWidths=[page_w - 2 * margin],
    )
    header_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BRAND_DARK),
        ("TOPPADDING",    (0, 0), (-1, -1), 16),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 16),
        ("LEFTPADDING",   (0, 0), (-1, -1), 18),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 18),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 0.6 * cm))

    # -----------------------------------------------------------------------
    # Report metadata section
    # -----------------------------------------------------------------------
    meta_style = ParagraphStyle(
        "meta",
        parent=styles["Normal"],
        fontSize=11,
        textColor=TEXT_DARK,
        fontName="Helvetica",
        leading=18,
    )
    meta_label_style = ParagraphStyle(
        "meta_label",
        parent=meta_style,
        fontName="Helvetica-Bold",
        textColor=BRAND_ACCENT,
    )

    meta_data = [
        [Paragraph("Device:", meta_label_style),      Paragraph(device, meta_style)],
        [Paragraph("Reporting Period:", meta_label_style), Paragraph(period_label, meta_style)],
        [Paragraph("Report Generated:", meta_label_style),
         Paragraph(datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), meta_style)],
    ]

    meta_table = Table(meta_data, colWidths=[4.5 * cm, page_w - 2 * margin - 4.5 * cm])
    meta_table.setStyle(TableStyle([
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (0, -1), 0),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 0.5 * cm))
    story.append(HRFlowable(width="100%", thickness=1.5, color=BRAND_ACCENT, spaceAfter=0.5 * cm))

    # -----------------------------------------------------------------------
    # Section title
    # -----------------------------------------------------------------------
    section_title_style = ParagraphStyle(
        "section_title",
        parent=styles["Heading2"],
        fontSize=14,
        textColor=BRAND_DARK,
        fontName="Helvetica-Bold",
        spaceBefore=0,
        spaceAfter=12,
    )
    story.append(Paragraph("Usage Summary", section_title_style))

    # -----------------------------------------------------------------------
    # Main data table
    # -----------------------------------------------------------------------
    col_w = (page_w - 2 * margin) / 3

    header_cell_style = ParagraphStyle(
        "th",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        textColor=TEXT_LIGHT,
        alignment=1,  # CENTER
    )
    data_cell_style = ParagraphStyle(
        "td",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=14,
        textColor=TEXT_DARK,
        alignment=1,  # CENTER
    )
    total_cell_style = ParagraphStyle(
        "td_total",
        parent=data_cell_style,
        fontName="Helvetica-Bold",
        fontSize=16,
        textColor=BRAND_HIGHLIGHT,
    )

    table_data = [
        # Header row
        [
            Paragraph("Opening Value", header_cell_style),
            Paragraph("Closing Value", header_cell_style),
            Paragraph("Total Usage", header_cell_style),
        ],
        # Data row
        [
            Paragraph(f"{opening_value:,.2f}", data_cell_style),
            Paragraph(f"{closing_value:,.2f}", data_cell_style),
            Paragraph(f"{usage:,.2f}", total_cell_style),
        ],
    ]

    summary_table = Table(table_data, colWidths=[col_w, col_w, col_w])
    summary_table.setStyle(TableStyle([
        # Header row styling
        ("BACKGROUND",    (0, 0), (-1, 0),  BRAND_ACCENT),
        ("TOPPADDING",    (0, 0), (-1, 0),  12),
        ("BOTTOMPADDING", (0, 0), (-1, 0),  12),
        # Data row styling
        ("BACKGROUND",    (0, 1), (-1, 1),  ROW_ALT),
        ("TOPPADDING",    (0, 1), (-1, 1),  18),
        ("BOTTOMPADDING", (0, 1), (-1, 1),  18),
        # Total column highlight
        ("BACKGROUND",    (2, 1), (2, 1),   colors.HexColor("#FFF0F3")),
        # Borders
        ("GRID",          (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
        ("BOX",           (0, 0), (-1, -1), 1.5, BRAND_ACCENT),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 1.0 * cm))

    # -----------------------------------------------------------------------
    # Notes / Calculation explanation
    # -----------------------------------------------------------------------
    note_style = ParagraphStyle(
        "note",
        parent=styles["Normal"],
        fontSize=9,
        textColor=colors.HexColor("#777777"),
        fontName="Helvetica-Oblique",
        leading=14,
    )
    story.append(Paragraph(
        "<b>Notes:</b> "
        "Opening Value is the meter reading at the end of the preceding month (from MonthlySummary). "
        "Closing Value is the last recorded reading during the reporting month. "
        "Total Usage = Closing Value − Opening Value.",
        note_style,
    ))
    story.append(Spacer(1, 1.5 * cm))

    # -----------------------------------------------------------------------
    # Footer
    # -----------------------------------------------------------------------
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#CCCCCC")))
    story.append(Spacer(1, 0.2 * cm))
    footer_style = ParagraphStyle(
        "footer",
        parent=styles["Normal"],
        fontSize=8,
        textColor=colors.HexColor("#AAAAAA"),
        fontName="Helvetica",
        alignment=1,
    )
    story.append(Paragraph(
        f"Generated by Reportr  •  {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}  •  "
        f"This report is auto-generated from meter data.",
        footer_style,
    ))

    # -----------------------------------------------------------------------
    # Build
    # -----------------------------------------------------------------------
    doc.build(story)
