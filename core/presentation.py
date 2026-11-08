"""Turns a data file into a slide deck: an HTML deck (previewable in the app, prints to PDF) and a PowerPoint file.

Every number on a slide is computed here from the data. The only text the model supplies is the title and the
key-findings bullets, which are escaped like any other data.

The HTML deck uses no JavaScript: slides are 16:9 sections with CSS scroll-snap, and @media print gives one slide per page.
The .pptx uses native, editable PowerPoint charts where PowerPoint has one, and drawn shapes for box plots, heatmaps
and treemaps. It needs python-pptx; without it only the HTML deck is written.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime
from html import escape

from . import dataviz
from .dataviz import ChartData, ChartSpec, DataError, Table, _fmt

try:  # Optional dependency: python-pptx.
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData, XyChartData
    from pptx.dml.color import RGBColor
    from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
    from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Emu, Inches, Pt
    HAS_PPTX = True
except ImportError:  # pragma: no cover - exercised only when the package is missing
    HAS_PPTX = False


@dataclass
class ChartSlide:
    spec: ChartSpec
    data: ChartData | None = None
    svg: str = ""
    insight: str = ""
    error: str = ""


@dataclass
class Deck:
    title: str
    source: str
    rows: int
    kpis: list[tuple[str, str]]
    columns: list[tuple[str, str]]  # (name, type)
    charts: list[ChartSlide] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    date_range: str = ""
    created: str = field(default_factory=lambda: datetime.now().strftime("%d %B %Y"))


def build_deck(table: Table, title: str, specs: list[ChartSpec] | None, findings: list[str] | None) -> Deck:
    specs = specs or dataviz.suggest_charts(table)[:6]
    info = dataviz.describe(table)
    dates = [c for c in info["columns"] if c["type"] == "date" and "min" in c]
    deck = Deck(
        title=title, source=table.name, rows=len(table.rows), kpis=dataviz.build_kpis(table)[:6],
        columns=[(c["name"], c["type"]) for c in info["columns"]],
        date_range=f"{dates[0]['min']} to {dates[0]['max']}" if dates else "",
    )
    for spec in specs:
        slide = ChartSlide(spec)
        try:
            slide.data = dataviz.prepare_chart(table, spec)
            slide.svg = dataviz.draw_chart(slide.data)
            slide.insight = dataviz.chart_insight(slide.data)
        except DataError as exc:
            slide.error = str(exc)
        deck.charts.append(slide)
    deck.findings = [f.strip() for f in (findings or []) if f and f.strip()][:8]
    if not deck.findings:
        deck.findings = [s.insight for s in deck.charts if s.insight][:5]

    missing = sum(1 for row in table.rows for v in row.values() if v is None)
    total = len(table.rows) * len(table.columns)
    deck.notes = [
        f"Source file: {table.name}, {len(table.rows):,} rows × {len(table.columns)} columns" + (" (truncated)" if table.truncated else ""),
        f"Completeness: {1 - missing / total:.1%} of cells filled ({missing:,} missing)" if total else "No cells",
        "All figures were computed directly from the source file.",
    ]
    if deck.date_range:
        deck.notes.insert(1, f"Period covered: {deck.date_range}")
    return deck


# ---- HTML deck ----------------------------------------------------------------

CSS = """
:root{--bg:#e9ecef;--card:#fff;--ink:#1c2128;--soft:#5b6570;--line:#dde1e6;--grid:#e8ebee;--accent:#0072B2}
@media (prefers-color-scheme:dark){:root{--bg:#0e1013;--card:#1d2127;--ink:#e6e9ec;--soft:#9aa3ad;--line:#2d333b;--grid:#2a3038;--accent:#56B4E9}}
*{box-sizing:border-box}html{scroll-snap-type:y mandatory}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,sans-serif;padding:16px 0}
.slide{width:min(calc(100vw - 32px),1120px);aspect-ratio:16/9;margin:0 auto 20px;background:var(--card);border:1px solid var(--line);
 border-radius:10px;padding:3.2% 4.5%;display:flex;flex-direction:column;scroll-snap-align:center;position:relative;overflow:hidden;
 container-type:inline-size}
.slide h2{font-size:3.1cqw;margin:0 0 1.2cqw;line-height:1.2}.slide .kicker{color:var(--accent);font-size:1.3cqw;font-weight:650;
 letter-spacing:.06em;text-transform:uppercase;margin-bottom:.6cqw}
.title{justify-content:center}.title h1{font-size:5cqw;margin:0 0 1.2cqw;line-height:1.1}.title .sub{color:var(--soft);font-size:1.8cqw}
.title .bar{width:8cqw;height:.6cqw;background:var(--accent);border-radius:1cqw;margin-bottom:2.4cqw}
.chart{flex:1;min-height:0;display:flex;align-items:center;justify-content:center}.chart svg{width:100%;height:100%}
.insight{font-size:1.75cqw;color:var(--ink);border-left:.4cqw solid var(--accent);padding-left:1.2cqw;margin-top:1cqw}
.kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:1.4cqw;margin:.8cqw 0 1.6cqw}
.kpi{border:1px solid var(--line);border-radius:.8cqw;padding:1.2cqw 1.6cqw}.kpi .v{font-size:3.2cqw;font-weight:700;font-variant-numeric:tabular-nums}
.kpi .l{color:var(--soft);font-size:1.4cqw}
.cols{color:var(--soft);font-size:1.4cqw;columns:3;margin:0;padding-left:2cqw}
ul.points{font-size:2.1cqw;margin:0;padding-left:2.6cqw}ul.points li{margin-bottom:1.1cqw}ul.points li::marker{color:var(--accent)}
.err{color:var(--soft);font-size:1.6cqw}.num{position:absolute;right:2.2cqw;bottom:1.4cqw;color:var(--soft);font-size:1.1cqw}
.ax{fill:var(--soft)}line.grid{stroke:var(--grid)}
@media print{@page{size:13.333in 7.5in;margin:0}html{scroll-snap-type:none}body{padding:0;background:#fff}
 .slide{width:13.333in;height:7.5in;aspect-ratio:auto;margin:0;border:0;border-radius:0;break-after:page}}
"""


def render_html(deck: Deck) -> str:
    slides = []
    sub = f"{escape(deck.source)} · {deck.rows:,} rows" + (f" · {escape(deck.date_range)}" if deck.date_range else "")
    slides.append(f'<section class="slide title"><div class="bar"></div><h1>{escape(deck.title)}</h1>'
                  f'<div class="sub">{sub}</div><div class="sub">{escape(deck.created)}</div></section>')
    kpis = "".join(f'<div class="kpi"><div class="v">{escape(v)}</div><div class="l">{escape(k)}</div></div>' for k, v in deck.kpis)
    cols = "".join(f"<li>{escape(name)} <small>({escape(kind)})</small></li>" for name, kind in deck.columns[:24])
    slides.append(f'<section class="slide"><div class="kicker">Overview</div><h2>The data at a glance</h2>'
                  f'<div class="kpis">{kpis}</div><ul class="cols">{cols}</ul></section>')
    for chart in deck.charts:
        body = (f'<div class="chart">{chart.svg}</div>' + (f'<div class="insight">{escape(chart.insight)}</div>' if chart.insight else "")
                if not chart.error else f'<p class="err">This chart could not be drawn: {escape(chart.error)}</p>')
        slides.append(f'<section class="slide"><div class="kicker">{escape(chart.spec.kind)}</div>'
                      f'<h2>{escape(chart.spec.title)}</h2>{body}</section>')
    points = "".join(f"<li>{escape(f)}</li>" for f in deck.findings)
    slides.append(f'<section class="slide"><div class="kicker">Summary</div><h2>Key findings</h2><ul class="points">{points}</ul></section>')
    notes = "".join(f"<li>{escape(n)}</li>" for n in deck.notes)
    slides.append(f'<section class="slide"><div class="kicker">Appendix</div><h2>About this analysis</h2><ul class="points">{notes}</ul></section>')
    numbered = [s.replace("</section>", f'<div class="num">{i + 1} / {len(slides)}</div></section>') for i, s in enumerate(slides)]
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{escape(deck.title)}</title><style>{CSS}</style></head><body>"
            + "".join(numbered) + "</body></html>")


# ---- PowerPoint ---------------------------------------------------------------


def _rgb(hex_color: str) -> "RGBColor":
    return RGBColor.from_string(hex_color.lstrip("#").upper())


def _blend(hex_color: str, amount: float) -> "RGBColor":
    """Mixes a colour with white: amount 0 is white, 1 is the colour. Used for sequential fills on a white slide."""
    r, g, b = (int(hex_color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    mix = lambda c: round(255 + (c - 255) * amount)
    return RGBColor(mix(r), mix(g), mix(b))


INK, SOFT, LINE = "1C2128", "5B6570", "DDE1E6"


def _text(slide, text, x, y, w, h, size=14, bold=False, color=INK, align=None):
    box = slide.shapes.add_textbox(x, y, w, h)
    frame = box.text_frame
    frame.word_wrap = True
    para = frame.paragraphs[0]
    para.text = text
    if align is not None:
        para.alignment = align
    font = para.font
    font.size, font.bold, font.color.rgb = Pt(size), bold, RGBColor.from_string(color)
    return box


def _heading(slide, kicker: str, title: str):
    _text(slide, kicker.upper(), Inches(0.6), Inches(0.35), Inches(12), Inches(0.35), 11, True, "0072B2")
    _text(slide, title, Inches(0.6), Inches(0.62), Inches(12.1), Inches(0.8), 26, True)


def _bullets(slide, items: list[str], top: float = 1.6):
    box = slide.shapes.add_textbox(Inches(0.7), Inches(top), Inches(11.9), Inches(7.5 - top - 0.5))
    frame = box.text_frame
    frame.word_wrap = True
    for i, item in enumerate(items):
        para = frame.paragraphs[0] if i == 0 else frame.add_paragraph()
        para.text = f"•  {item}"
        para.font.size = Pt(18)
        para.font.color.rgb = RGBColor.from_string(INK)
        para.space_after = Pt(10)


def _style_chart(chart, legend: bool):
    chart.font.size = Pt(11)
    chart.font.color.rgb = RGBColor.from_string(SOFT)
    chart.has_legend = legend
    if legend:
        chart.legend.position = XL_LEGEND_POSITION.TOP
        chart.legend.include_in_layout = False


def _color_series(chart, single: bool = False):
    for i, series in enumerate(chart.plots[0].series):
        fill = series.format.fill
        fill.solid()
        fill.fore_color.rgb = _rgb(dataviz.PALETTE[0 if single else i % len(dataviz.PALETTE)])
        series.format.line.color.rgb = _rgb(dataviz.PALETTE[0 if single else i % len(dataviz.PALETTE)])


def _native_chart(slide, data: ChartData, x, y, w, h) -> bool:
    kind = data.spec.kind
    if kind in {"bar", "histogram", "line", "area", "pie", "donut"}:
        pairs = data.pairs
        horizontal = kind == "bar" and (len(pairs) > 6 or max(len(k) for k, _ in pairs) > 8)
        if horizontal:
            pairs = list(reversed(pairs))  # PowerPoint draws bar categories bottom-up.
        chart_data = CategoryChartData()
        chart_data.categories = [k for k, _ in pairs]
        chart_data.add_series(data.spec.title, [v for _, v in pairs])
        chart_type = {
            "bar": XL_CHART_TYPE.BAR_CLUSTERED if horizontal else XL_CHART_TYPE.COLUMN_CLUSTERED,
            "histogram": XL_CHART_TYPE.COLUMN_CLUSTERED, "line": XL_CHART_TYPE.LINE_MARKERS,
            "area": XL_CHART_TYPE.AREA, "pie": XL_CHART_TYPE.PIE, "donut": XL_CHART_TYPE.DOUGHNUT,
        }[kind]
        chart = slide.shapes.add_chart(chart_type, x, y, w, h, chart_data).chart
        _style_chart(chart, legend=kind in {"pie", "donut"})
        if kind in {"pie", "donut"}:
            for i, point in enumerate(chart.plots[0].series[0].points):
                point.format.fill.solid()
                point.format.fill.fore_color.rgb = _rgb(dataviz.PALETTE[i % len(dataviz.PALETTE)])
            plot = chart.plots[0]
            plot.has_data_labels = True
            plot.data_labels.show_percentage, plot.data_labels.show_value = True, False
            plot.data_labels.number_format, plot.data_labels.number_format_is_linked = "0%", False
            chart.legend.position = XL_LEGEND_POSITION.RIGHT
        else:
            _color_series(chart, single=True)
            if kind == "histogram":
                chart.plots[0].gap_width = 15
            elif kind in {"bar"}:
                chart.plots[0].gap_width = 60
        return True
    if kind in {"stacked", "grouped"}:
        chart_data = CategoryChartData()
        chart_data.categories = data.rows
        for col in data.cols:
            chart_data.add_series(col, [data.cells.get((r, col), 0) for r in data.rows])
        chart_type = XL_CHART_TYPE.COLUMN_STACKED if kind == "stacked" else XL_CHART_TYPE.COLUMN_CLUSTERED
        chart = slide.shapes.add_chart(chart_type, x, y, w, h, chart_data).chart
        _style_chart(chart, legend=True)
        _color_series(chart)
        chart.plots[0].gap_width = 60
        if kind == "stacked":
            chart.plots[0].overlap = 100
        return True
    if kind == "scatter":
        chart_data = XyChartData()
        series = chart_data.add_series(f"{data.x_name} vs {data.y_name}")
        points = data.points if len(data.points) <= 2000 else data.points[:: len(data.points) // 2000 + 1]
        for px, py in points:
            series.add_data_point(px, py)
        chart = slide.shapes.add_chart(XL_CHART_TYPE.XY_SCATTER, x, y, w, h, chart_data).chart
        _style_chart(chart, legend=False)
        marker = chart.plots[0].series[0].marker
        marker.format.fill.solid()
        marker.format.fill.fore_color.rgb = _rgb(dataviz.PALETTE[0])
        chart.plots[0].series[0].format.line.fill.background()
        chart.value_axis.has_title = chart.category_axis.has_title = True
        chart.value_axis.axis_title.text_frame.text = data.y_name
        chart.category_axis.axis_title.text_frame.text = data.x_name
        return True
    return False


def _line(slide, x1, y1, x2, y2, color=SOFT, width=1.5):
    connector = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, int(x1), int(y1), int(x2), int(y2))
    connector.line.color.rgb = RGBColor.from_string(color)
    connector.line.width = Pt(width)


def _rect(slide, x, y, w, h, fill, line=None, shape=MSO_SHAPE.RECTANGLE):
    box = slide.shapes.add_shape(shape, int(x), int(y), max(int(w), 1), max(int(h), 1))
    box.fill.solid()
    box.fill.fore_color.rgb = fill
    if line:
        box.line.color.rgb = RGBColor.from_string(line)
        box.line.width = Pt(1.25)
    else:
        box.line.fill.background()
    box.shadow.inherit = False
    return box


def _box_shapes(slide, data: ChartData, x, y, w, h):
    groups = data.groups
    low = min(min([s["low"]] + s["outliers"]) for _, s in groups)
    high = max(max([s["high"]] + s["outliers"]) for _, s in groups)
    ticks = dataviz._nice_range(low, high)
    left, bottom = Inches(0.9), Inches(0.45)
    px, pw, ph = x + left, w - left, h - bottom
    y_of = lambda v: y + ph * (1 - (v - ticks[0]) / ((ticks[-1] - ticks[0]) or 1))
    for t in ticks:
        _line(slide, px, y_of(t), px + pw, y_of(t), LINE, 0.75)
        _text(slide, _fmt(t), x, int(y_of(t) - Inches(0.14)), int(left - Inches(0.1)), Inches(0.3), 10, color=SOFT,
              align=PP_ALIGN.RIGHT)
    band = pw / len(groups)
    blue = dataviz.PALETTE[0].lstrip("#")
    for i, (key, s) in enumerate(groups):
        cx, half = px + band * i + band / 2, min(band * 0.3, Inches(0.6))
        _line(slide, cx, y_of(s["high"]), cx, y_of(s["q3"]))
        _line(slide, cx, y_of(s["q1"]), cx, y_of(s["low"]))
        _line(slide, cx - half / 2, y_of(s["high"]), cx + half / 2, y_of(s["high"]))
        _line(slide, cx - half / 2, y_of(s["low"]), cx + half / 2, y_of(s["low"]))
        _rect(slide, cx - half, y_of(s["q3"]), 2 * half, y_of(s["q1"]) - y_of(s["q3"]), _blend(dataviz.PALETTE[0], 0.25), blue)
        _line(slide, cx - half, y_of(s["median"]), cx + half, y_of(s["median"]), blue, 2.5)
        for value in s["outliers"][:40]:
            dot = Inches(0.08)
            _rect(slide, cx - dot / 2, y_of(value) - dot / 2, dot, dot, _rgb(dataviz.PALETTE[5]), shape=MSO_SHAPE.OVAL)
        _text(slide, dataviz._short(key, 16), int(cx - band / 2), int(y + ph + Inches(0.05)), int(band), Inches(0.3), 10,
              color=SOFT, align=PP_ALIGN.CENTER)


def _heatmap_table(slide, data: ChartData, x, y, w, h):
    rows, cols = data.rows, data.cols
    shape = slide.shapes.add_table(len(rows) + 1, len(cols) + 1, x, y, w, min(h, Inches(0.42) * (len(rows) + 1)))
    table = shape.table
    peak = 1.0 if data.diverging else max((abs(v) for v in data.cells.values()), default=1) or 1

    def put(cell, text, fill=None, bold=False, white=False):
        cell.text = text
        para = cell.text_frame.paragraphs[0]
        para.font.size, para.font.bold = Pt(11), bold
        para.font.color.rgb = RGBColor(255, 255, 255) if white else RGBColor.from_string(INK)
        cell.fill.solid()
        cell.fill.fore_color.rgb = fill or RGBColor(255, 255, 255)

    put(table.cell(0, 0), "")
    for j, col in enumerate(cols):
        put(table.cell(0, j + 1), dataviz._short(col, 14), bold=True)
    for i, row in enumerate(rows):
        put(table.cell(i + 1, 0), dataviz._short(row, 18), bold=True)
        for j, col in enumerate(cols):
            value = data.cells.get((row, col))
            if value is None:
                put(table.cell(i + 1, j + 1), "–", RGBColor(0xF0, 0xF2, 0xF4))
                continue
            amount = 0.06 + 0.94 * min(abs(value) / peak, 1)
            color = dataviz.PALETTE[5] if data.diverging and value < 0 else dataviz.PALETTE[0]
            put(table.cell(i + 1, j + 1), f"{value:.2f}" if data.diverging else _fmt(value), _blend(color, amount),
                white=amount > 0.55)


def _treemap_shapes(slide, data: ChartData, x, y, w, h):
    pairs = [(k, v) for k, v in data.pairs if v > 0]
    if len(pairs) > 12:
        pairs = pairs[:11] + [("Other", sum(v for _, v in pairs[11:]))]
    total, peak = sum(v for _, v in pairs), max(v for _, v in pairs)
    for key, value, rx, ry, rw, rh in dataviz.squarify(pairs, x, y, w, h):
        amount = 0.3 + 0.7 * value / peak
        box = _rect(slide, rx + Emu(12000), ry + Emu(12000), rw - Emu(24000), rh - Emu(24000), _blend(dataviz.PALETTE[0], amount))
        frame = box.text_frame
        frame.word_wrap = True
        frame.text = f"{key}\n{_fmt(value)} · {value / total:.0%}"
        for para in frame.paragraphs:
            para.font.size = Pt(12 if rw > Inches(1.5) else 9)
            para.font.color.rgb = RGBColor(255, 255, 255) if amount > 0.6 else RGBColor.from_string(INK)


def render_pptx(deck: Deck) -> bytes:
    """Returns the .pptx file as bytes, so the caller can write it atomically inside the permitted folder."""
    if not HAS_PPTX:
        raise DataError("python-pptx is not installed, so a .pptx cannot be written. Run: pip install python-pptx")
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]

    slide = prs.slides.add_slide(blank)
    _rect(slide, Inches(0.8), Inches(2.5), Inches(1.2), Inches(0.09), _rgb(dataviz.PALETTE[0]))
    _text(slide, deck.title, Inches(0.8), Inches(2.75), Inches(11.5), Inches(1.4), 40, True)
    sub = f"{deck.source} · {deck.rows:,} rows" + (f" · {deck.date_range}" if deck.date_range else "")
    _text(slide, sub, Inches(0.8), Inches(4.15), Inches(11.5), Inches(0.5), 18, color=SOFT)
    _text(slide, deck.created, Inches(0.8), Inches(4.6), Inches(11.5), Inches(0.5), 14, color=SOFT)

    slide = prs.slides.add_slide(blank)
    _heading(slide, "Overview", "The data at a glance")
    for i, (label, value) in enumerate(deck.kpis):
        col, row = i % 3, i // 3
        bx, by = Inches(0.6 + col * 4.1), Inches(1.6 + row * 1.55)
        _rect(slide, bx, by, Inches(3.9), Inches(1.35), RGBColor(0xF6, 0xF7, 0xF9), LINE)
        _text(slide, value, bx + Inches(0.25), by + Inches(0.15), Inches(3.5), Inches(0.7), 28, True)
        _text(slide, label, bx + Inches(0.25), by + Inches(0.8), Inches(3.5), Inches(0.4), 13, color=SOFT)
    columns = ", ".join(f"{name} ({kind})" for name, kind in deck.columns[:24])
    _text(slide, f"Columns: {columns}", Inches(0.6), Inches(4.85), Inches(12.1), Inches(2), 12, color=SOFT)

    for chart in deck.charts:
        slide = prs.slides.add_slide(blank)
        _heading(slide, chart.spec.kind, chart.spec.title)
        x, y, w, h = Inches(0.6), Inches(1.45), Inches(12.1), Inches(4.9)
        if chart.error or chart.data is None:
            _text(slide, f"This chart could not be drawn: {chart.error}", x, y, w, Inches(1), 16, color=SOFT)
            continue
        if not _native_chart(slide, chart.data, x, y, w, h):
            {"box": _box_shapes, "heatmap": _heatmap_table, "treemap": _treemap_shapes}[chart.spec.kind](slide, chart.data, x, y, w, h)
        if chart.insight:
            _rect(slide, Inches(0.6), Inches(6.55), Inches(0.07), Inches(0.5), _rgb(dataviz.PALETTE[0]))
            _text(slide, chart.insight, Inches(0.8), Inches(6.5), Inches(11.9), Inches(0.7), 15)

    slide = prs.slides.add_slide(blank)
    _heading(slide, "Summary", "Key findings")
    _bullets(slide, deck.findings)

    slide = prs.slides.add_slide(blank)
    _heading(slide, "Appendix", "About this analysis")
    _bullets(slide, deck.notes)

    for number, page in enumerate(prs.slides, start=1):
        _text(page, f"{number} / {len(prs.slides)}", Inches(11.8), Inches(7.05), Inches(1.2), Inches(0.3), 10, color=SOFT, align=PP_ALIGN.RIGHT)
    buffer = io.BytesIO()
    prs.save(buffer)
    return buffer.getvalue()
