"""
Render a tabular dataset to CSV / Excel / PDF.

A dataset is ``(title, columns, rows)`` where columns is a list of header
strings and rows is a list of lists. Exporters return an HttpResponse with the
right content type and download headers.
"""
import csv
import io

from django.http import HttpResponse


def _filename(title, ext):
    slug = title.lower().replace(" ", "_")
    return f"{slug}.{ext}"


def to_csv(title, columns, rows):
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{_filename(title, "csv")}"'
    writer = csv.writer(resp)
    writer.writerow(columns)
    for row in rows:
        writer.writerow(row)
    return resp


def to_excel(title, columns, rows):
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = title[:31] or "Report"
    ws.append(columns)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append(list(row))

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = HttpResponse(
        buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp["Content-Disposition"] = f'attachment; filename="{_filename(title, "xlsx")}"'
    return resp


def to_pdf(title, columns, rows):
    """Simple tabular PDF via reportlab (pure-python; no native deps)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
    from reportlab.lib.styles import getSampleStyleSheet

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), title=title,
                            leftMargin=10 * mm, rightMargin=10 * mm)
    styles = getSampleStyleSheet()
    data = [columns] + [[str(c) for c in row] for row in rows]
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f4f6")]),
    ]))
    doc.build([Paragraph(title, styles["Title"]), table])
    buf.seek(0)
    resp = HttpResponse(buf.getvalue(), content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="{_filename(title, "pdf")}"'
    return resp


EXPORTERS = {"csv": to_csv, "excel": to_excel, "xlsx": to_excel, "pdf": to_pdf}


def export(fmt, title, columns, rows):
    exporter = EXPORTERS.get(fmt, to_csv)
    return exporter(title, columns, rows)
