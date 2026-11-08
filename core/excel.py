"""Writes Excel workbooks (.xlsx) with openpyxl: plain exports and formatted reports.

A report has a Summary sheet, the data as a filterable Excel Table, optional pivot / group / trend / quality sheets,
and a Charts sheet with native Excel charts (editable in Excel). Totals and changes are Excel formulas, so the
workbook stays live if someone edits a number. Every figure comes from the data; nothing is written by the model.
"""

from __future__ import annotations

import io
import math
import re
import statistics
from collections import defaultdict
from datetime import datetime

from . import dataviz
from .dataviz import ChartSpec, DataError, Table, _number, _plain

SHEET_KINDS = ("summary", "data", "pivot", "group", "trend", "quality")
HEADER_FILL = "1F3B57"
MAX_SPLIT_SHEETS = 30


def _openpyxl():
    try:
        import openpyxl  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        raise DataError("writing Excel files needs openpyxl: pip install openpyxl") from exc
    import openpyxl
    return openpyxl


def sheet_title(text: str, taken: set[str]) -> str:
    """Excel sheet names: at most 31 characters, none of []:*?/\\ and unique (case-insensitive)."""
    base = re.sub(r"[\[\]:*?/\\]", "-", str(text)).strip().strip("'") or "Sheet"
    base = base[:31]
    name, n = base, 2
    while name.lower() in taken:
        suffix = f" ({n})"
        name, n = base[: 31 - len(suffix)] + suffix, n + 1
    taken.add(name.lower())
    return name


# ---- shared writing helpers ---------------------------------------------------


def _styles():
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    return {
        "header_font": Font(bold=True, color="FFFFFF"),
        "header_fill": PatternFill("solid", fgColor=HEADER_FILL),
        "title": Font(bold=True, size=16),
        "subtitle": Font(italic=True, color="5B6570"),
        "bold": Font(bold=True),
        "wrap": Alignment(wrap_text=True, vertical="top"),
        "thin": Border(bottom=Side(style="thin", color="DDE1E6")),
    }


def _cell_value(value, kind: str):
    if value is None:
        return None
    if kind == "number":
        number = _number(value)
        return number if number is None or number != int(number) or abs(number) > 1e15 else int(number)
    if kind == "date" and isinstance(value, datetime):
        return value
    if isinstance(value, bool):
        return value
    return str(_plain(value)) if not isinstance(value, (int, float)) else value


def _number_format(table: Table, column: str) -> str | None:
    kind = table.types.get(column)
    if kind == "date":
        return "yyyy-mm-dd"
    if kind == "number":
        numbers = [n for n in (_number(v) for v in table.values(column)[:500]) if n is not None]
        if numbers and all(n == int(n) for n in numbers):
            return "#,##0" if max(abs(n) for n in numbers) >= 1000 else "0"
        return "#,##0.00"
    return None


def _autosize(sheet, min_width: int = 8, max_width: int = 50) -> None:
    widths: dict[int, int] = defaultdict(int)
    for row in sheet.iter_rows(min_row=1, max_row=min(sheet.max_row, 300)):
        for cell in row:
            if cell.value is not None:
                text = cell.value.strftime("%Y-%m-%d") if isinstance(cell.value, datetime) else str(cell.value)
                widths[cell.column] = max(widths[cell.column], len(text) if not text.startswith("=") else 12)
    from openpyxl.utils import get_column_letter

    for column, width in widths.items():
        sheet.column_dimensions[get_column_letter(column)].width = max(min_width, min(max_width, width + 2))


def _header(sheet, row: int, values: list[str], col: int = 1) -> None:
    style = _styles()
    for offset, value in enumerate(values):
        cell = sheet.cell(row=row, column=col + offset, value=value)
        cell.font, cell.fill = style["header_font"], style["header_fill"]


def write_data_sheet(sheet, columns: list[str], rows: list[dict], table: Table | None = None, as_table: bool = True) -> None:
    """Header, values with proper types and number formats, frozen header, and an Excel Table with filters."""
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.table import Table as ExcelTable, TableStyleInfo

    kinds = table.types if table else {}
    _header(sheet, 1, columns)
    formats = {c: _number_format(table, c) for c in columns} if table else {}
    for r, record in enumerate(rows, start=2):
        for c, column in enumerate(columns, start=1):
            cell = sheet.cell(row=r, column=c, value=_cell_value(record.get(column), kinds.get(column, "")))
            if formats.get(column):
                cell.number_format = formats[column]
    sheet.freeze_panes = "A2"
    if rows and as_table and len(set(columns)) == len(columns):
        ref = f"A1:{get_column_letter(len(columns))}{len(rows) + 1}"
        name = re.sub(r"[^A-Za-z0-9_]", "_", f"T_{sheet.title}")[:250]
        excel_table = ExcelTable(displayName=name if name[0].isalpha() else f"T{name}", ref=ref)
        excel_table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
        sheet.add_table(excel_table)
    elif rows:
        sheet.auto_filter.ref = sheet.dimensions
    _autosize(sheet)


def workbook_bytes(workbook) -> bytes:
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def export_table(columns: list[str], rows: list[dict], table: Table | None = None, sheet_name: str = "Data") -> bytes:
    """A one-sheet workbook: used when converting or saving transformed data as .xlsx."""
    openpyxl = _openpyxl()
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = sheet_title(sheet_name, set())
    write_data_sheet(sheet, columns, rows, table)
    return workbook_bytes(workbook)


# ---- report sheets ------------------------------------------------------------


def _summary_sheet(sheet, table: Table, title: str, contents: list[str]) -> None:
    style = _styles()
    sheet["A1"], sheet["A1"].font = title, style["title"]
    source = table.name + (f" (sheet {table.sheet})" if table.sheet else "")
    sheet["A2"] = f"Source: {source} · {len(table.rows):,} rows · {len(table.columns)} columns · generated {datetime.now():%Y-%m-%d %H:%M}"
    sheet["A2"].font = style["subtitle"]
    row = 4
    sheet.cell(row=row, column=1, value="Key figures").font = style["bold"]
    row += 1
    for label, value in dataviz.build_kpis(table):
        sheet.cell(row=row, column=1, value=label)
        number = _number(value)
        cell = sheet.cell(row=row, column=2, value=number if number is not None else value)
        if number is not None:
            cell.number_format = "#,##0.##"
        row += 1
    row += 1
    sheet.cell(row=row, column=1, value="Columns").font = style["bold"]
    row += 1
    _header(sheet, row, ["Column", "Type", "Missing", "Unique", "Min", "Max", "Mean", "Median"])
    for info in dataviz.describe(table)["columns"]:
        row += 1
        values = [info["name"], info["type"], info["missing"], info["unique"], info.get("min"), info.get("max"),
                  info.get("mean"), info.get("median")]
        for c, value in enumerate(values, start=1):
            sheet.cell(row=row, column=c, value=value)
    if contents:
        row += 2
        sheet.cell(row=row, column=1, value="In this workbook").font = style["bold"]
        for item in contents:
            row += 1
            sheet.cell(row=row, column=1, value=item)
    _autosize(sheet)
    sheet.column_dimensions["A"].width = 28


def _pivot_sheet(sheet, table: Table, rows_by: str, cols_by: str, metric: str | None, agg: str) -> str:
    from openpyxl.formatting.rule import ColorScaleRule
    from openpyxl.utils import get_column_letter

    agg = agg or ("sum" if metric else "count")
    rows, cols, cells = dataviz.crosstab(table, rows_by, cols_by, metric, agg, row_limit=200, col_limit=30)
    additive = agg in {"sum", "count"}
    title = f"{agg} of {metric or 'rows'} by {rows_by} and {cols_by}"
    sheet["A1"], sheet["A1"].font = title, _styles()["bold"]
    _header(sheet, 3, [rows_by, *cols, *(["Total"] if additive else [])])
    for r, key in enumerate(rows, start=4):
        sheet.cell(row=r, column=1, value=key)
        for c, col in enumerate(cols, start=2):
            value = cells.get((key, col))
            cell = sheet.cell(row=r, column=c, value=value)
            cell.number_format = "#,##0.##"
        if additive:
            last = get_column_letter(len(cols) + 1)
            sheet.cell(row=r, column=len(cols) + 2, value=f"=SUM(B{r}:{last}{r})").number_format = "#,##0.##"
    end = 3 + len(rows)
    if additive:
        total_row = end + 1
        sheet.cell(row=total_row, column=1, value="Total").font = _styles()["bold"]
        for c in range(2, len(cols) + 3):
            letter = get_column_letter(c)
            cell = sheet.cell(row=total_row, column=c, value=f"=SUM({letter}4:{letter}{end})")
            cell.font, cell.number_format = _styles()["bold"], "#,##0.##"
    if rows and cols:
        area = f"B4:{get_column_letter(len(cols) + 1)}{end}"
        sheet.conditional_formatting.add(area, ColorScaleRule(start_type="min", start_color="FFFFFF",
                                                              end_type="max", end_color="0072B2"))
    sheet.freeze_panes = "B4"
    _autosize(sheet)
    return title


def _group_sheet(sheet, table: Table, group_by: str, metric: str) -> str:
    from openpyxl.formatting.rule import DataBarRule

    group_col, metric_col = dataviz.resolve_column(table, group_by), dataviz.resolve_column(table, metric)
    buckets: dict[str, list[float]] = defaultdict(list)
    for record in table.rows:
        key, value = dataviz._group_key(record.get(group_col)), _number(record.get(metric_col))
        if key is not None and value is not None:
            buckets[key].append(value)
    if not buckets:
        raise DataError(f"no numeric '{metric_col}' values to group")
    title = f"{metric_col} by {group_col}"
    sheet["A1"], sheet["A1"].font = title, _styles()["bold"]
    _header(sheet, 3, [group_col, "Rows", "Sum", "Share", "Mean", "Median", "Std dev", "Min", "Max"])
    ordered = sorted(buckets.items(), key=lambda item: -math.fsum(item[1]))
    end = 3 + len(ordered)
    for r, (key, values) in enumerate(ordered, start=4):
        row = [key, len(values), math.fsum(values), f"=C{r}/SUM($C$4:$C${end})", statistics.fmean(values),
               statistics.median(values), statistics.pstdev(values) if len(values) > 1 else 0, min(values), max(values)]
        for c, value in enumerate(row, start=1):
            cell = sheet.cell(row=r, column=c, value=value)
            cell.number_format = "0.0%" if c == 4 else "#,##0.##"
    sheet.conditional_formatting.add(f"C4:C{end}", DataBarRule(start_type="min", end_type="max", color="0072B2"))
    sheet.freeze_panes = "B4"
    _autosize(sheet)
    return title


def _trend_sheet(sheet, table: Table, metric: str | None, agg: str, period: str) -> tuple[str, int]:
    from openpyxl.chart import LineChart, Reference

    date_col = next((c for c in table.columns if table.types[c] == "date"), None)
    if not date_col:
        raise DataError("no date column, so there is no trend sheet")
    agg = agg or ("sum" if metric else "count")
    metric_col = dataviz.resolve_column(table, metric) if metric else None
    from .analysis import _period_key

    buckets: dict[str, list[float]] = defaultdict(list)
    for record in table.rows:
        when = record.get(date_col)
        if isinstance(when, datetime):
            value = 1.0 if agg == "count" else _number(record.get(metric_col))
            if value is not None:
                buckets[_period_key(when, period)].append(value)
    if not buckets:
        raise DataError("no dated rows to show a trend")
    title = f"{agg} of {metric_col or 'rows'} per {period}"
    sheet["A1"], sheet["A1"].font = title, _styles()["bold"]
    _header(sheet, 3, [period.capitalize(), "Value", "Change", "Change %"])
    keys = sorted(buckets)
    for r, key in enumerate(keys, start=4):
        sheet.cell(row=r, column=1, value=key)
        sheet.cell(row=r, column=2, value=float(dataviz.REDUCERS[agg](buckets[key]))).number_format = "#,##0.##"
        if r > 4:
            sheet.cell(row=r, column=3, value=f"=B{r}-B{r - 1}").number_format = "#,##0.##;[Red]-#,##0.##"
            sheet.cell(row=r, column=4, value=f'=IF(B{r - 1}=0,"",(B{r}-B{r - 1})/ABS(B{r - 1}))').number_format = "0.0%;[Red]-0.0%"
    end = 3 + len(keys)
    chart = LineChart()
    chart.title, chart.height, chart.width = title, 8, 18
    chart.add_data(Reference(sheet, min_col=2, min_row=3, max_row=end), titles_from_data=True)
    chart.set_categories(Reference(sheet, min_col=1, min_row=4, max_row=end))
    chart.legend = None
    _color(chart.series[0], 0, line=True)
    sheet.add_chart(chart, "F3")
    _autosize(sheet)
    return title, len(keys)


def _quality_sheet(sheet, table: Table) -> str:
    from .analysis import quality_report

    title = "Data quality"
    sheet["A1"], sheet["A1"].font = title, _styles()["bold"]
    bold = _styles()["bold"]
    for r, line in enumerate(quality_report(table).splitlines(), start=3):
        cell = sheet.cell(row=r, column=1, value=line)
        if line.endswith(":"):
            cell.font = bold
    sheet.column_dimensions["A"].width = 120
    return title


# ---- charts ---------------------------------------------------------------------


def _color(series, index: int, line: bool = False) -> None:
    color = dataviz.PALETTE[index % len(dataviz.PALETTE)].lstrip("#")
    if line:
        series.graphicalProperties.line.solidFill = color
        series.graphicalProperties.line.width = 22000
    else:
        series.graphicalProperties.solidFill = color
        series.graphicalProperties.line.solidFill = color


def _charts_sheet(sheet, table: Table, specs: list[ChartSpec]) -> tuple[int, list[str]]:
    """Each chart gets a small data table (column A onward) and a native Excel chart beside it."""
    from openpyxl.chart import AreaChart, BarChart, DoughnutChart, LineChart, PieChart, Reference, ScatterChart, Series
    from openpyxl.chart.series import DataPoint
    from openpyxl.formatting.rule import ColorScaleRule
    from openpyxl.utils import get_column_letter

    style = _styles()
    row, drawn, notes = 1, 0, []
    for spec in specs:
        try:
            data = dataviz.prepare_chart(table, spec)
        except DataError as exc:
            notes.append(f"'{spec.title}' skipped: {exc}")
            continue
        sheet.cell(row=row, column=1, value=spec.title).font = style["bold"]
        caption = dataviz.chart_insight(data)
        if caption:
            sheet.cell(row=row + 1, column=1, value=caption).font = style["subtitle"]
        top = row + 3
        kind = spec.kind
        chart = None
        if data.pairs:
            _header(sheet, top, [spec.x or "category", spec.y or spec.agg])
            for i, (key, value) in enumerate(data.pairs, start=1):
                sheet.cell(row=top + i, column=1, value=key)
                sheet.cell(row=top + i, column=2, value=value).number_format = "#,##0.##"
            end = top + len(data.pairs)
            values = Reference(sheet, min_col=2, min_row=top, max_row=end)
            labels = Reference(sheet, min_col=1, min_row=top + 1, max_row=end)
            if kind in {"pie", "donut"}:
                chart = DoughnutChart() if kind == "donut" else PieChart()
                chart.add_data(values, titles_from_data=True)
                chart.set_categories(labels)
                for i in range(len(data.pairs)):
                    point = DataPoint(idx=i)
                    point.graphicalProperties.solidFill = dataviz.PALETTE[i % len(dataviz.PALETTE)].lstrip("#")
                    chart.series[0].dPt.append(point)
            else:
                chart = {"line": LineChart, "area": AreaChart}.get(kind, BarChart)()
                if isinstance(chart, BarChart):
                    chart.type = "bar" if kind in {"bar", "treemap"} and len(data.pairs) > 6 else "col"
                    chart.gapWidth = 15 if kind == "histogram" else 60
                chart.add_data(values, titles_from_data=True)
                chart.set_categories(labels)
                chart.legend = None
                _color(chart.series[0], 0, line=kind == "line")
            rows_used = len(data.pairs)
        elif data.points:
            _header(sheet, top, [data.x_name, data.y_name])
            points = data.points[:5000]
            for i, (x, y) in enumerate(points, start=1):
                sheet.cell(row=top + i, column=1, value=x)
                sheet.cell(row=top + i, column=2, value=y)
            end = top + len(points)
            chart = ScatterChart()
            series = Series(Reference(sheet, min_col=2, min_row=top + 1, max_row=end),
                            Reference(sheet, min_col=1, min_row=top + 1, max_row=end), title=f"{data.x_name} vs {data.y_name}")
            series.marker.symbol, series.graphicalProperties.line.noFill = "circle", True
            series.marker.graphicalProperties.solidFill = dataviz.PALETTE[0].lstrip("#")
            chart.series.append(series)
            chart.x_axis.title, chart.y_axis.title, chart.legend = data.x_name, data.y_name, None
            rows_used = min(len(points), 20)  # The chart covers the first rows; keep the next chart close.
        elif data.groups:
            _header(sheet, top, ["Group", "Rows", "Min", "Q1", "Median", "Q3", "Max", "Outliers"])
            for i, (key, s) in enumerate(data.groups, start=1):
                for c, value in enumerate([key, s["n"], s["min"], s["q1"], s["median"], s["q3"], s["max"], len(s["outliers"])], start=1):
                    sheet.cell(row=top + i, column=c, value=value).number_format = "#,##0.##"
            rows_used = len(data.groups)
        elif data.cells and kind in {"stacked", "grouped"}:
            _header(sheet, top, [spec.x or "", *data.cols])
            for i, key in enumerate(data.rows, start=1):
                sheet.cell(row=top + i, column=1, value=key)
                for j, col in enumerate(data.cols, start=2):
                    sheet.cell(row=top + i, column=j, value=data.cells.get((key, col), 0)).number_format = "#,##0.##"
            end = top + len(data.rows)
            chart = BarChart()
            chart.type = "col"
            if kind == "stacked":
                chart.grouping, chart.overlap = "stacked", 100
            chart.add_data(Reference(sheet, min_col=2, max_col=len(data.cols) + 1, min_row=top, max_row=end), titles_from_data=True)
            chart.set_categories(Reference(sheet, min_col=1, min_row=top + 1, max_row=end))
            for i, series in enumerate(chart.series):
                _color(series, i)
            rows_used = len(data.rows)
        else:  # Heatmap: a table with a colour scale reads like one.
            _header(sheet, top, ["", *data.cols])
            for i, key in enumerate(data.rows, start=1):
                sheet.cell(row=top + i, column=1, value=key)
                for j, col in enumerate(data.cols, start=2):
                    value = data.cells.get((key, col))
                    sheet.cell(row=top + i, column=j, value=round(value, 3) if data.diverging and value is not None else value).number_format = "#,##0.##"
            area = f"B{top + 1}:{get_column_letter(len(data.cols) + 1)}{top + len(data.rows)}"
            rule = (ColorScaleRule(start_type="num", start_value=-1, start_color="D55E00", mid_type="num", mid_value=0,
                                   mid_color="FFFFFF", end_type="num", end_value=1, end_color="0072B2") if data.diverging else
                    ColorScaleRule(start_type="min", start_color="FFFFFF", end_type="max", end_color="0072B2"))
            sheet.conditional_formatting.add(area, rule)
            rows_used = len(data.rows)
        if chart is not None:
            chart.title, chart.height, chart.width = spec.title, 7.5, 16
            sheet.add_chart(chart, f"{get_column_letter(max(4, 3 + (len(data.cols) if data.cells else 1) + 1))}{top}")
        drawn += 1
        row = top + max(rows_used, 16) + 3
    _autosize(sheet)
    sheet.column_dimensions["A"].width = 26
    return drawn, notes


# ---- the report -----------------------------------------------------------------


def parse_sheet_specs(items: list[str]) -> list[list[str]]:
    specs = []
    for item in items:
        parts = [p.strip() for p in str(item).split("|")]
        kind = parts[0].lower()
        if kind not in SHEET_KINDS:
            raise DataError(f"bad sheet '{item}'. Kinds: {', '.join(SHEET_KINDS)}. Examples: 'pivot | region | product | revenue | sum', "
                            "'group | region | revenue', 'trend | revenue | sum | month', 'quality'")
        specs.append([kind, *parts[1:]])
    return specs


def build_report(table: Table, title: str, sheets: list[str] | None = None, charts: list[ChartSpec] | None = None,
                 split_by: str | None = None) -> tuple[bytes, list[str], list[str]]:
    """Returns (xlsx bytes, sheet names, notes about anything skipped)."""
    openpyxl = _openpyxl()
    workbook = openpyxl.Workbook()
    taken: set[str] = set()
    notes: list[str] = []
    specs = parse_sheet_specs(sheets or [])
    summary = workbook.active
    summary.title = sheet_title("Summary", taken)
    contents: list[str] = []

    data_sheet = workbook.create_sheet(sheet_title("Data", taken))
    write_data_sheet(data_sheet, table.columns, table.rows, table)
    contents.append(f"Data: all {len(table.rows):,} rows as a filterable table")

    for spec in specs:
        kind, args = spec[0], spec[1:] + [""] * 5
        if kind in {"summary", "data"}:
            continue
        sheet = None
        try:
            if kind == "pivot":
                if not args[0] or not args[1]:
                    raise DataError("pivot needs rows and columns: 'pivot | rows | columns | metric | aggregate'")
                sheet = workbook.create_sheet(sheet_title(f"Pivot {args[0]} x {args[1]}", taken))
                contents.append(f"{sheet.title}: " + _pivot_sheet(sheet, table, args[0], args[1], args[2] or None, args[3].lower()))
            elif kind == "group":
                if not args[0] or not args[1]:
                    raise DataError("group needs a category and a metric: 'group | region | revenue'")
                sheet = workbook.create_sheet(sheet_title(f"By {args[0]}", taken))
                contents.append(f"{sheet.title}: " + _group_sheet(sheet, table, args[0], args[1]))
            elif kind == "trend":
                sheet = workbook.create_sheet(sheet_title("Trend", taken))
                text, _ = _trend_sheet(sheet, table, args[0] or None, args[1].lower(), (args[2] or "month").lower())
                contents.append(f"{sheet.title}: {text}")
            elif kind == "quality":
                sheet = workbook.create_sheet(sheet_title("Quality", taken))
                contents.append(f"{sheet.title}: " + _quality_sheet(sheet, table))
        except DataError as exc:
            notes.append(f"'{' | '.join(spec)}' skipped: {exc}")
            if sheet is not None:
                workbook.remove(sheet)  # Only the sheet this spec created.

    if charts:
        sheet = workbook.create_sheet(sheet_title("Charts", taken))
        drawn, chart_notes = _charts_sheet(sheet, table, charts)
        notes += chart_notes
        contents.append(f"Charts: {drawn} native Excel chart(s) with their data")
        if not drawn:
            workbook.remove(sheet)

    if split_by:
        column = dataviz.resolve_column(table, split_by)
        groups: dict[str, list[dict]] = defaultdict(list)
        for record in table.rows:
            groups[str(_plain(record.get(column))) if record.get(column) is not None else "(blank)"].append(record)
        if len(groups) > MAX_SPLIT_SHEETS:
            notes.append(f"split by {column}: {len(groups)} values, so only the {MAX_SPLIT_SHEETS} largest got a sheet")
        for key, records in sorted(groups.items(), key=lambda item: -len(item[1]))[:MAX_SPLIT_SHEETS]:
            sheet = workbook.create_sheet(sheet_title(key, taken))
            write_data_sheet(sheet, table.columns, records, table)
        contents.append(f"One sheet per {column} value ({min(len(groups), MAX_SPLIT_SHEETS)} sheets)")

    _summary_sheet(summary, table, title, contents)
    return workbook_bytes(workbook), workbook.sheetnames, notes
