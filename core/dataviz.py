"""Dependency-free data analysis: load tables, profile columns, aggregate, and draw dashboards.

Dashboards are static HTML with inline SVG and CSS, no JavaScript. That keeps them viewable under a
strict Content-Security-Policy and means a generated file can never run code in the user's browser.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time
from html import escape
from pathlib import Path

MAX_BYTES = 25 * 1024 * 1024
MAX_ROWS = 200_000
SUPPORTED = {".csv", ".tsv", ".json", ".jsonl", ".xlsx", ".xlsm"}
EXCEL = {".xlsx", ".xlsm"}
AGGREGATES = {"count", "sum", "mean", "min", "max", "median"}
CHART_KINDS = {"bar", "line", "area", "pie", "donut", "histogram", "scatter", "box", "heatmap", "stacked", "grouped", "treemap"}
MISSING = {"", "na", "n/a", "nan", "null", "none", "-"}
DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m")


class DataError(Exception):
    """An expected failure; the message is safe to show the model."""


@dataclass
class Table:
    name: str
    columns: list[str]
    rows: list[dict]
    truncated: bool = False
    types: dict[str, str] = field(default_factory=dict)
    sheet: str = ""  # For Excel files: the sheet that was read.
    sheets: list[str] = field(default_factory=list)  # For Excel files: every sheet in the workbook.

    def values(self, column: str) -> list:
        return [row.get(column) for row in self.rows]


# ---- loading --------------------------------------------------------------


def load_table(path: Path, sheet: str | None = None) -> Table:
    if path.suffix.lower() not in SUPPORTED:
        raise DataError(f"unsupported data file '{path.name}'. Supported: {', '.join(sorted(SUPPORTED))}")
    if not path.is_file():
        raise DataError(f"'{path.name}' is not a file")
    if path.stat().st_size > MAX_BYTES:
        raise DataError(f"'{path.name}' is larger than {MAX_BYTES // 1024 // 1024} MB")
    suffix = path.suffix.lower()
    sheet_name, sheet_names = "", []
    if suffix in EXCEL:
        records, sheet_name, sheet_names = _read_excel(path, sheet)
    elif suffix in {".csv", ".tsv"}:
        records = _read_delimited(path.read_text(encoding="utf-8-sig", errors="replace"), "\t" if suffix == ".tsv" else None)
    else:
        records = _read_json(path.read_text(encoding="utf-8-sig", errors="replace"), lines=suffix == ".jsonl")
    if not records:
        raise DataError(f"'{path.name}' has no data rows")
    truncated = len(records) > MAX_ROWS
    records = records[:MAX_ROWS]
    columns: list[str] = []
    for record in records:
        for key in record:
            if key not in columns:
                columns.append(key)
    rows = [{column: _convert(record.get(column)) for column in columns} for record in records]
    table = Table(path.name, columns, rows, truncated, sheet=sheet_name, sheets=sheet_names)
    table.types = {column: _infer_type(table.values(column)) for column in columns}
    for column, kind in table.types.items():
        if kind == "date":
            for row in rows:
                row[column] = _parse_date(row[column]) if row[column] is not None else None
    return table


def _read_delimited(text: str, delimiter: str | None) -> list[dict]:
    sample = text[:8192]
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;|\t").delimiter
        except csv.Error:
            delimiter = ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header = next(reader)
    except StopIteration:
        return []
    names, seen = [], Counter()
    for index, raw in enumerate(header):
        name = raw.strip() or f"column_{index + 1}"
        seen[name] += 1
        names.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    records = []
    for line in reader:
        if not any(cell.strip() for cell in line):
            continue
        records.append({name: (line[i] if i < len(line) else None) for i, name in enumerate(names)})
        if len(records) > MAX_ROWS:
            break
    return records


def excel_sheet_names(path: Path) -> list[str]:
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True)
    try:
        return list(workbook.sheetnames)
    finally:
        workbook.close()


def _read_excel(path: Path, sheet: str | None) -> tuple[list[dict], str, list[str]]:
    """Reads one sheet. The first non-empty row is the header. Formulas give their last calculated values."""
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover
        raise DataError("reading Excel files needs openpyxl: pip install openpyxl") from exc
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises many types for damaged or non-Excel files.
        raise DataError(f"'{path.name}' could not be opened as an Excel workbook: {exc}") from exc
    try:
        names = list(workbook.sheetnames)
        if sheet:
            match = next((n for n in names if n.lower() == sheet.strip().lower()), None)
            if match is None:
                raise DataError(f"no sheet '{sheet}' in {path.name}. Sheets: {', '.join(names)}")
            candidates = [match]
        else:
            candidates = names
        for name in candidates:
            rows = workbook[name].iter_rows(values_only=True)
            header = None
            for row in rows:
                if any(cell is not None and str(cell).strip() != "" for cell in row):
                    header = list(row)
                    break
            if header is None:
                continue  # An empty sheet: try the next one.
            while header and (header[-1] is None or str(header[-1]).strip() == ""):
                header.pop()
            names_out, seen = [], Counter()
            for index, raw in enumerate(header):
                label = str(raw).strip() if raw is not None and str(raw).strip() else f"column_{index + 1}"
                seen[label] += 1
                names_out.append(label if seen[label] == 1 else f"{label}_{seen[label]}")
            records = []
            for row in rows:
                if not any(cell is not None and str(cell).strip() != "" for cell in row):
                    continue
                records.append({label: _excel_value(row[i] if i < len(row) else None) for i, label in enumerate(names_out)})
                if len(records) > MAX_ROWS:
                    break
            return records, name, names
        raise DataError(f"'{path.name}' has no data" + (f" in sheet '{sheet}'" if sheet else ""))
    finally:
        workbook.close()


def _excel_value(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, time):
        return value.isoformat()
    return value


def _read_json(text: str, lines: bool) -> list[dict]:
    try:
        data = [json.loads(line) for line in text.splitlines() if line.strip()] if lines else json.loads(text)
    except json.JSONDecodeError as exc:
        raise DataError(f"invalid JSON: {exc}") from exc
    if isinstance(data, dict):
        lists = [value for value in data.values() if isinstance(value, list) and value and isinstance(value[0], dict)]
        data = lists[0] if lists else [data]
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise DataError("JSON must be a list of objects (a table), or an object containing one")
    return [{str(k): (v if isinstance(v, (str, int, float, bool)) or v is None else json.dumps(v)) for k, v in item.items()}
            for item in data]


def _convert(value):
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.lower() in MISSING:
            return None
        return stripped
    return value


def _number(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return None if isinstance(value, float) and math.isnan(value) else float(value)
    text = str(value).replace(",", "").replace("$", "").replace("€", "").replace("£", "").strip()
    if text.endswith("%"):
        text = text[:-1]
    try:
        number = float(text)
    except ValueError:
        return None
    return None if math.isnan(number) or math.isinf(number) else number


def _parse_date(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _infer_type(values: list) -> str:
    present = [v for v in values if v is not None]
    if not present:
        return "empty"
    sample = present[:500]
    if all(isinstance(v, bool) for v in sample):
        return "boolean"
    if sum(_number(v) is not None for v in sample) >= 0.95 * len(sample):
        # Years and ids read as numbers but behave like labels.
        return "number"
    if sum(isinstance(v, datetime) or (isinstance(v, str) and _parse_date(v) is not None) for v in sample) >= 0.95 * len(sample):
        return "date"
    return "text"


# ---- profiling ------------------------------------------------------------


def describe(table: Table) -> dict:
    columns = []
    for column in table.columns:
        values = table.values(column)
        present = [v for v in values if v is not None]
        kind = table.types[column]
        info: dict = {"name": column, "type": kind, "missing": len(values) - len(present), "unique": len({str(v) for v in present})}
        if kind == "number":
            numbers = sorted(n for n in (_number(v) for v in present) if n is not None)
            info.update(_number_stats(numbers))
        elif kind == "date":
            dates = sorted(v for v in present if isinstance(v, datetime))
            if dates:
                info.update({"min": dates[0].date().isoformat(), "max": dates[-1].date().isoformat()})
        else:
            info["top"] = [[str(k), c] for k, c in Counter(str(v) for v in present).most_common(5)]
        columns.append(info)
    return {"file": table.name, "rows": len(table.rows), "truncated": table.truncated, "columns": columns}


def _number_stats(numbers: list[float]) -> dict:
    if not numbers:
        return {}
    return {
        "min": _round(numbers[0]), "max": _round(numbers[-1]), "mean": _round(statistics.fmean(numbers)),
        "median": _round(statistics.median(numbers)),
        "std": _round(statistics.pstdev(numbers)) if len(numbers) > 1 else 0,
        "sum": _round(math.fsum(numbers)),
    }


def _round(value: float, digits: int = 4):
    if value == int(value) and abs(value) < 1e15:
        return int(value)
    return round(value, digits)


def describe_text(table: Table) -> str:
    info = describe(table)
    lines = [f"{info['file']}: {info['rows']} rows, {len(info['columns'])} columns" + (" (truncated)" if info["truncated"] else "")]
    if table.sheets:
        others = [name for name in table.sheets if name != table.sheet]
        lines.append(f"Sheet: {table.sheet}" + (f" (other sheets: {', '.join(others)}; pass sheet to read one)" if others else ""))
    for col in info["columns"]:
        parts = [f"- {col['name']} [{col['type']}] missing={col['missing']} unique={col['unique']}"]
        if col["type"] == "number" and "mean" in col:
            parts.append(f"min={col['min']} max={col['max']} mean={col['mean']} median={col['median']} std={col['std']} sum={col['sum']}")
        elif col["type"] == "date" and "min" in col:
            parts.append(f"from {col['min']} to {col['max']}")
        elif col.get("top"):
            parts.append("top: " + ", ".join(f"{k} ({c})" for k, c in col["top"]))
        lines.append(" ".join(parts))
    lines.append("\nFirst rows:")
    for row in table.rows[:5]:
        lines.append(json.dumps({k: _plain(v) for k, v in row.items()}, ensure_ascii=False))
    return "\n".join(lines)


def _plain(value):
    return value.date().isoformat() if isinstance(value, datetime) else value


# ---- querying -------------------------------------------------------------

FILTER = re.compile(r"^\s*(.+?)\s*(==|!=|>=|<=|>|<|~)\s*(.*?)\s*$")


def resolve_column(table: Table, name: str) -> str:
    if name in table.columns:
        return name
    lowered = {c.lower(): c for c in table.columns}
    if name.lower() in lowered:
        return lowered[name.lower()]
    close = [c for c in table.columns if name.lower() in c.lower()]
    if len(close) == 1:
        return close[0]
    raise DataError(f"no column '{name}'. Columns: {', '.join(table.columns)}")


def apply_filter(table: Table, expression: str | None) -> list[dict]:
    """Filters rows with 'column OP value' where OP is == != > >= < <= or ~ (contains)."""
    if not expression or not expression.strip():
        return table.rows
    match = FILTER.match(expression)
    if not match:
        raise DataError(f"could not read filter '{expression}'. Use: column == value, column > 10, column ~ text")
    column, op, raw = match.groups()
    column = resolve_column(table, column)
    raw = raw.strip().strip("'\"")
    want_number = _number(raw)
    kept = []
    for row in table.rows:
        cell = row.get(column)
        if cell is None:
            continue
        if op == "~":
            ok = raw.lower() in str(cell).lower()
        else:
            left, right = (_number(cell), want_number) if want_number is not None and _number(cell) is not None else (str(cell).lower(), raw.lower())
            ok = {"==": left == right, "!=": left != right}.get(op)
            if ok is None:
                if isinstance(left, str) or isinstance(right, str):
                    raise DataError(f"'{op}' needs a number, but '{column}' or the value is not numeric")
                ok = {">": left > right, ">=": left >= right, "<": left < right, "<=": left <= right}[op]
        if ok:
            kept.append(row)
    return kept


def aggregate(table: Table, group_by: str | None, metric: str | None, agg: str = "sum", where: str | None = None,
              limit: int = 20, sort: str = "desc") -> list[tuple[str, float]]:
    agg = (agg or "sum").lower()
    if agg not in AGGREGATES:
        raise DataError(f"unknown aggregate '{agg}'. Use: {', '.join(sorted(AGGREGATES))}")
    rows = apply_filter(table, where)
    if not rows:
        raise DataError("no rows match the filter")
    group_col = resolve_column(table, group_by) if group_by else None
    metric_col = resolve_column(table, metric) if metric else None
    if agg != "count" and metric_col is None:
        raise DataError(f"'{agg}' needs a metric column")
    buckets: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        key = _group_key(row.get(group_col), table.types.get(group_col)) if group_col else "all"
        if key is None:
            continue
        if agg == "count":
            buckets[key].append(1.0)
        else:
            number = _number(row.get(metric_col))
            if number is not None:
                buckets[key].append(number)
    if not buckets:
        raise DataError("nothing to aggregate: the metric column has no numeric values for these rows")
    result = [(key, float(REDUCERS[agg](values))) for key, values in buckets.items() if values]
    if group_col and table.types.get(group_col) == "date":
        result.sort(key=lambda item: item[0])
        return result[-limit:] if limit else result
    result.sort(key=lambda item: item[1], reverse=sort != "asc")
    return result[:limit] if limit else result


REDUCERS = {"count": len, "sum": math.fsum, "mean": statistics.fmean, "min": min, "max": max, "median": statistics.median}


def _group_key(value, kind=None):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m")
    return str(value)


def histogram(table: Table, column: str, bins: int = 10, where: str | None = None) -> list[tuple[str, float]]:
    column = resolve_column(table, column)
    numbers = [n for n in (_number(r.get(column)) for r in apply_filter(table, where)) if n is not None]
    if not numbers:
        raise DataError(f"column '{column}' has no numeric values")
    low, high = min(numbers), max(numbers)
    if low == high:
        return [(_fmt(low), float(len(numbers)))]
    width = (high - low) / bins
    counts = [0] * bins
    for n in numbers:
        counts[min(int((n - low) / width), bins - 1)] += 1
    return [(_fmt(low + i * width), float(c)) for i, c in enumerate(counts)]


def numbers_of(table: Table, column: str, where: str | None = None) -> list[float]:
    column = resolve_column(table, column)
    return sorted(n for n in (_number(r.get(column)) for r in apply_filter(table, where)) if n is not None)


def box_stats(numbers: list[float]) -> dict:
    """Five-number summary with Tukey fences (1.5 x IQR). `numbers` must be sorted and non-empty."""
    if len(numbers) >= 2:
        q1, median, q3 = statistics.quantiles(numbers, n=4, method="inclusive")
    else:
        q1 = median = q3 = numbers[0]
    iqr = q3 - q1
    low_fence, high_fence = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    inside = [n for n in numbers if low_fence <= n <= high_fence] or numbers
    return {
        "n": len(numbers), "min": numbers[0], "q1": q1, "median": median, "q3": q3, "max": numbers[-1],
        "low": inside[0], "high": inside[-1], "low_fence": low_fence, "high_fence": high_fence,
        "outliers": [n for n in numbers if n < low_fence or n > high_fence],
    }


def box_groups(table: Table, column: str, group_by: str | None = None, where: str | None = None,
               limit: int = 10) -> list[tuple[str, dict]]:
    column = resolve_column(table, column)
    rows = apply_filter(table, where)
    if not group_by:
        numbers = sorted(n for n in (_number(r.get(column)) for r in rows) if n is not None)
        if not numbers:
            raise DataError(f"column '{column}' has no numeric values")
        return [(column, box_stats(numbers))]
    group_col = resolve_column(table, group_by)
    buckets: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        key, number = _group_key(row.get(group_col)), _number(row.get(column))
        if key is not None and number is not None:
            buckets[key].append(number)
    if not buckets:
        raise DataError(f"column '{column}' has no numeric values")
    keys = sorted(buckets) if table.types.get(group_col) == "date" else sorted(buckets, key=lambda k: -len(buckets[k]))
    return [(key, box_stats(sorted(buckets[key]))) for key in keys[:limit]]


def crosstab(table: Table, row: str, col: str, metric: str | None = None, agg: str = "count", where: str | None = None,
             row_limit: int = 12, col_limit: int = 8) -> tuple[list[str], list[str], dict[tuple[str, str], float]]:
    """Two-way table of `agg(metric)` by row and column categories. Extra columns fold into 'Other' when additive."""
    agg = (agg or "count").lower()
    if agg not in AGGREGATES:
        raise DataError(f"unknown aggregate '{agg}'. Use: {', '.join(sorted(AGGREGATES))}")
    row_col, col_col = resolve_column(table, row), resolve_column(table, col)
    metric_col = resolve_column(table, metric) if metric else None
    if agg != "count" and metric_col is None:
        raise DataError(f"'{agg}' needs a metric column")
    cells: dict[tuple[str, str], list[float]] = defaultdict(list)
    for record in apply_filter(table, where):
        a, b = _group_key(record.get(row_col)), _group_key(record.get(col_col))
        if a is None or b is None:
            continue
        if agg == "count":
            cells[(a, b)].append(1.0)
        else:
            number = _number(record.get(metric_col))
            if number is not None:
                cells[(a, b)].append(number)
    if not cells:
        raise DataError("no rows have values for both categories")
    raw = {key: values for key, values in cells.items() if values}
    row_total: Counter = Counter()
    col_total: Counter = Counter()
    for (a, b), values in raw.items():
        row_total[a] += len(values) if agg == "count" else abs(math.fsum(values))
        col_total[b] += len(values) if agg == "count" else abs(math.fsum(values))
    rows = sorted(row_total) if table.types.get(row_col) == "date" else [k for k, _ in row_total.most_common()]
    rows = rows[-row_limit:] if table.types.get(row_col) == "date" else rows[:row_limit]
    cols = sorted(col_total) if table.types.get(col_col) == "date" else [k for k, _ in col_total.most_common()]
    additive = agg in {"count", "sum"}
    if len(cols) > col_limit and additive:
        kept, folded = cols[: col_limit - 1], set(cols[col_limit - 1:])
        merged: dict[tuple[str, str], list[float]] = defaultdict(list)
        for (a, b), values in raw.items():
            merged[(a, "Other" if b in folded else b)].extend(values)
        raw, cols = merged, kept + ["Other"]
    else:
        cols = cols[:col_limit]
    values = {(a, b): float(REDUCERS[agg](raw[(a, b)])) for a in rows for b in cols if raw.get((a, b))}
    return rows, cols, values


def correlation_matrix(table: Table, columns: list[str] | None = None, limit: int = 8) -> tuple[list[str], dict]:
    cols = [resolve_column(table, c) for c in columns] if columns else _ranked_numbers(table)[:limit]
    if len(cols) < 2:
        raise DataError("a correlation matrix needs at least two numeric columns")
    matrix: dict[tuple[str, str], float] = {}
    for i, a in enumerate(cols):
        matrix[(a, a)] = 1.0
        for b in cols[i + 1:]:
            value = correlation(table, a, b)
            if value is not None:
                matrix[(a, b)] = matrix[(b, a)] = value
    return cols, matrix


def correlation(table: Table, a: str, b: str) -> float | None:
    pairs = [(x, y) for x, y in ((_number(r.get(a)), _number(r.get(b))) for r in table.rows) if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    xs, ys = zip(*pairs)
    try:
        return statistics.correlation(xs, ys)
    except statistics.StatisticsError:
        return None


def format_pairs(pairs: list[tuple[str, float]], header: str) -> str:
    width = max(len(k) for k, _ in pairs)
    return header + "\n" + "\n".join(f"{k.ljust(width)}  {_fmt(v)}" for k, v in pairs)


def _fmt(value: float) -> str:
    if abs(value) >= 1000:
        return f"{value:,.0f}" if value == int(value) else f"{value:,.1f}"
    if value == int(value):
        return str(int(value))
    return f"{value:.3g}" if abs(value) < 1 else f"{value:.2f}"


# ---- charts ---------------------------------------------------------------

# Colour-blind safe categorical palette (Okabe-Ito), with a neutral ink that suits light and dark pages.
PALETTE = ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00", "#F0E442", "#999999"]


def _svg_open(width: int, height: int, label: str) -> str:
    return (f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="{escape(label)}" '
            f'xmlns="http://www.w3.org/2000/svg" font-family="system-ui,sans-serif" font-size="11">')


def _ticks(high: float, count: int = 4) -> list[float]:
    if high <= 0:
        return [0]
    raw = high / count
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    return [i * step for i in range(int(math.ceil(high / step)) + 1)]


def _short(label: str, size: int = 14) -> str:
    return label if len(label) <= size else label[: size - 1] + "…"


def _tip(label: str, value: float) -> str:
    return f"<title>{escape(label)}: {escape(_fmt(value))}</title>"


def chart_bar(data: list[tuple[str, float]], label: str, horizontal: bool | None = None) -> str:
    width, height = 560, 300
    horizontal = horizontal if horizontal is not None else len(data) > 6 or max(len(k) for k, _ in data) > 8
    ticks = _ticks(max(max(v for _, v in data), 0))
    top = ticks[-1] or 1
    svg = [_svg_open(width, height, label)]
    if horizontal:
        left, right, pad = 120, 50, 16
        band = (height - 2 * pad) / len(data)
        for i, (key, value) in enumerate(data):
            y = pad + i * band
            w = (width - left - right) * max(value, 0) / top
            svg.append(f'<text x="{left - 8}" y="{y + band / 2 + 4}" text-anchor="end" class="ax">{escape(_short(key, 18))}</text>'
                       f'<rect x="{left}" y="{y + band * 0.15}" width="{w:.1f}" height="{band * 0.7:.1f}" rx="2" fill="{PALETTE[0]}">{_tip(key, value)}</rect>'
                       f'<text x="{left + w + 5}" y="{y + band / 2 + 4}" class="ax">{escape(_fmt(value))}</text>')
    else:
        left, bottom, pad = 50, 44, 16
        plot_h = height - bottom - pad
        band = (width - left - pad) / len(data)
        for t in ticks:
            y = pad + plot_h - plot_h * t / top
            svg.append(f'<line x1="{left}" x2="{width - pad}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
                       f'<text x="{left - 6}" y="{y + 4:.1f}" text-anchor="end" class="ax">{escape(_fmt(t))}</text>')
        for i, (key, value) in enumerate(data):
            x = left + i * band
            h = plot_h * max(value, 0) / top
            svg.append(f'<rect x="{x + band * 0.15:.1f}" y="{pad + plot_h - h:.1f}" width="{band * 0.7:.1f}" height="{h:.1f}" rx="2" fill="{PALETTE[0]}">{_tip(key, value)}</rect>'
                       f'<text x="{x + band / 2:.1f}" y="{height - bottom + 16}" text-anchor="middle" class="ax">{escape(_short(key, max(4, int(band / 6))))}</text>')
    svg.append("</svg>")
    return "".join(svg)


def chart_line(data: list[tuple[str, float]], label: str, fill: bool = False) -> str:
    width, height, left, bottom, pad = 560, 300, 50, 44, 16
    low = min(0.0, min(v for _, v in data))
    ticks = _ticks(max(max(v for _, v in data), 0))
    top = ticks[-1] or 1
    plot_h = height - bottom - pad
    span = top - low or 1
    step = (width - left - pad) / max(len(data) - 1, 1)

    def pos(i: int, v: float) -> tuple[float, float]:
        return left + (i * step if len(data) > 1 else (width - left - pad) / 2), pad + plot_h - plot_h * (v - low) / span

    svg = [_svg_open(width, height, label)]
    for t in ticks:
        _, y = pos(0, t)
        svg.append(f'<line x1="{left}" x2="{width - pad}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
                   f'<text x="{left - 6}" y="{y + 4:.1f}" text-anchor="end" class="ax">{escape(_fmt(t))}</text>')
    points = [pos(i, v) for i, (_, v) in enumerate(data)]
    if fill and len(points) > 1:
        _, base = pos(0, max(low, 0))
        outline = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        svg.append(f'<polygon fill="{PALETTE[0]}" fill-opacity=".18" stroke="none" '
                   f'points="{points[0][0]:.1f},{base:.1f} {outline} {points[-1][0]:.1f},{base:.1f}"/>')
    svg.append(f'<polyline fill="none" stroke="{PALETTE[0]}" stroke-width="2" points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in points)}"/>')
    every = max(1, math.ceil(len(data) / 5))
    for i, ((key, value), (x, y)) in enumerate(zip(data, points)):
        if len(data) <= 60:
            svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{PALETTE[0]}">{_tip(key, value)}</circle>')
        if i % every == 0:
            svg.append(f'<text x="{x:.1f}" y="{height - bottom + 16}" text-anchor="middle" class="ax">{escape(_short(key, 10))}</text>')
    svg.append("</svg>")
    return "".join(svg)


def chart_pie(data: list[tuple[str, float]], label: str, donut: bool = False) -> str:
    data = [(k, v) for k, v in data if v > 0]
    if not data:
        raise DataError("a pie chart needs positive values")
    if len(data) > 7:
        data = data[:7] + [("Other", sum(v for _, v in data[7:]))]
    total = sum(v for _, v in data)
    width, height, cx, cy, radius = 560, 300, 150, 150, 120
    svg = [_svg_open(width, height, label)]
    angle = -math.pi / 2
    for i, (key, value) in enumerate(data):
        sweep = 2 * math.pi * value / total
        color = PALETTE[i % len(PALETTE)]
        if len(data) == 1:
            svg.append(f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="{color}">{_tip(key, value)}</circle>')
        else:
            x1, y1 = cx + radius * math.cos(angle), cy + radius * math.sin(angle)
            x2, y2 = cx + radius * math.cos(angle + sweep), cy + radius * math.sin(angle + sweep)
            svg.append(f'<path d="M{cx},{cy} L{x1:.1f},{y1:.1f} A{radius},{radius} 0 {1 if sweep > math.pi else 0} 1 {x2:.1f},{y2:.1f} Z" '
                       f'fill="{color}" stroke="var(--card)" stroke-width="2">{_tip(key, value)}</path>')
        angle += sweep
        svg.append(f'<rect x="310" y="{30 + i * 28}" width="12" height="12" rx="2" fill="{color}"/>'
                   f'<text x="330" y="{41 + i * 28}" class="ax">{escape(_short(key, 22))} · {value / total:.0%}</text>')
    if donut:
        svg.append(f'<circle cx="{cx}" cy="{cy}" r="{radius * 0.58:.1f}" fill="var(--card)"/>'
                   f'<text x="{cx}" y="{cy - 2}" text-anchor="middle" font-size="20" font-weight="650" fill="var(--ink)">{escape(_fmt(total))}</text>'
                   f'<text x="{cx}" y="{cy + 16}" text-anchor="middle" class="ax">total</text>')
    svg.append("</svg>")
    return "".join(svg)


def _nice_range(low: float, high: float, count: int = 4) -> list[float]:
    """Round tick values covering [low, high], for axes that need not start at zero."""
    if high == low:
        high, low = high + 1, low - 1
    raw = (high - low) / count
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    start = math.floor(low / step) * step
    ticks = [start]
    while ticks[-1] < high - 1e-12:
        ticks.append(ticks[-1] + step)
    return ticks


def _legend(keys: list[str], x: float, y: float, width: float) -> str:
    """One row of swatches; wraps to a second row if needed. Identity never relies on colour alone: names are written."""
    parts, cx, cy = [], x, y
    for i, key in enumerate(keys):
        text = _short(key, 16)
        span = 18 + len(text) * 6.2 + 14
        if cx + span > x + width and cx > x:
            cx, cy = x, cy + 16
        parts.append(f'<rect x="{cx:.1f}" y="{cy - 9:.1f}" width="10" height="10" rx="2" fill="{PALETTE[i % len(PALETTE)]}"/>'
                     f'<text x="{cx + 15:.1f}" y="{cy:.1f}" class="ax">{escape(text)}</text>')
        cx += span
    return "".join(parts)


def chart_stacked(rows: list[str], cols: list[str], values: dict, label: str, grouped: bool = False) -> str:
    width, height, left, bottom, pad = 560, 310, 50, 44, 16
    top_pad = 40 if len(cols) <= 5 else 56
    plot_h = height - bottom - top_pad
    if grouped:
        peak = max((v for v in values.values()), default=0)
    else:
        peak = max((sum(max(values.get((r, c), 0), 0) for c in cols) for r in rows), default=0)
    ticks = _ticks(max(peak, 0))
    top = ticks[-1] or 1
    band = (width - left - pad) / len(rows)
    svg = [_svg_open(width, height, label), _legend(cols, left, 14, width - left - pad)]
    for t in ticks:
        y = top_pad + plot_h - plot_h * t / top
        svg.append(f'<line x1="{left}" x2="{width - pad}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
                   f'<text x="{left - 6}" y="{y + 4:.1f}" text-anchor="end" class="ax">{escape(_fmt(t))}</text>')
    base = top_pad + plot_h
    for i, row in enumerate(rows):
        x = left + i * band
        if grouped:
            sub = band * 0.8 / len(cols)
            for j, col in enumerate(cols):
                value = values.get((row, col))
                if value is None:
                    continue
                h = plot_h * max(value, 0) / top
                svg.append(f'<rect x="{x + band * 0.1 + j * sub + 1:.1f}" y="{base - h:.1f}" width="{max(sub - 2, 1):.1f}" '
                           f'height="{h:.1f}" rx="2" fill="{PALETTE[j % len(PALETTE)]}">{_tip(f"{row} · {col}", value)}</rect>')
        else:
            cursor = base
            for j, col in enumerate(cols):
                value = max(values.get((row, col), 0), 0)
                if not value:
                    continue
                h = plot_h * value / top
                svg.append(f'<rect x="{x + band * 0.15:.1f}" y="{cursor - h:.1f}" width="{band * 0.7:.1f}" height="{h:.1f}" '
                           f'fill="{PALETTE[j % len(PALETTE)]}" stroke="var(--card)" stroke-width="1.5">'
                           f'{_tip(f"{row} · {col}", value)}</rect>')
                cursor -= h
        svg.append(f'<text x="{x + band / 2:.1f}" y="{height - bottom + 16}" text-anchor="middle" class="ax">'
                   f'{escape(_short(row, max(4, int(band / 6))))}</text>')
    svg.append("</svg>")
    return "".join(svg)


def chart_box(groups: list[tuple[str, dict]], label: str) -> str:
    width, height, left, bottom, pad = 560, 300, 56, 44, 16
    low = min(min([s["low"]] + s["outliers"]) for _, s in groups)
    high = max(max([s["high"]] + s["outliers"]) for _, s in groups)
    ticks = _nice_range(low, high)
    t_low, t_high = ticks[0], ticks[-1]
    plot_h = height - bottom - pad
    span = (t_high - t_low) or 1

    def y_of(v: float) -> float:
        return pad + plot_h * (1 - (v - t_low) / span)

    band = (width - left - pad) / len(groups)
    svg = [_svg_open(width, height, label)]
    for t in ticks:
        svg.append(f'<line x1="{left}" x2="{width - pad}" y1="{y_of(t):.1f}" y2="{y_of(t):.1f}" class="grid"/>'
                   f'<text x="{left - 6}" y="{y_of(t) + 4:.1f}" text-anchor="end" class="ax">{escape(_fmt(t))}</text>')
    for i, (key, s) in enumerate(groups):
        cx = left + i * band + band / 2
        half = min(band * 0.3, 40)
        tip = (f"<title>{escape(key)}: median {escape(_fmt(s['median']))}, middle half {escape(_fmt(s['q1']))}–"
               f"{escape(_fmt(s['q3']))}, range {escape(_fmt(s['min']))}–{escape(_fmt(s['max']))}, "
               f"n={s['n']}, outliers {len(s['outliers'])}</title>")
        svg.append(f'<line x1="{cx:.1f}" x2="{cx:.1f}" y1="{y_of(s["high"]):.1f}" y2="{y_of(s["q3"]):.1f}" stroke="var(--soft)" stroke-width="1.5"/>'
                   f'<line x1="{cx:.1f}" x2="{cx:.1f}" y1="{y_of(s["q1"]):.1f}" y2="{y_of(s["low"]):.1f}" stroke="var(--soft)" stroke-width="1.5"/>'
                   f'<line x1="{cx - half / 2:.1f}" x2="{cx + half / 2:.1f}" y1="{y_of(s["high"]):.1f}" y2="{y_of(s["high"]):.1f}" stroke="var(--soft)" stroke-width="1.5"/>'
                   f'<line x1="{cx - half / 2:.1f}" x2="{cx + half / 2:.1f}" y1="{y_of(s["low"]):.1f}" y2="{y_of(s["low"]):.1f}" stroke="var(--soft)" stroke-width="1.5"/>'
                   f'<rect x="{cx - half:.1f}" y="{y_of(s["q3"]):.1f}" width="{2 * half:.1f}" height="{max(y_of(s["q1"]) - y_of(s["q3"]), 1):.1f}" '
                   f'rx="3" fill="{PALETTE[0]}" fill-opacity=".22" stroke="{PALETTE[0]}" stroke-width="1.5">{tip}</rect>'
                   f'<line x1="{cx - half:.1f}" x2="{cx + half:.1f}" y1="{y_of(s["median"]):.1f}" y2="{y_of(s["median"]):.1f}" stroke="{PALETTE[0]}" stroke-width="2.5"/>')
        for value in s["outliers"][:60]:
            svg.append(f'<circle cx="{cx:.1f}" cy="{y_of(value):.1f}" r="3" fill="none" stroke="{PALETTE[5]}" stroke-width="1.5">{_tip(f"{key} outlier", value)}</circle>')
        svg.append(f'<text x="{cx:.1f}" y="{height - bottom + 16}" text-anchor="middle" class="ax">{escape(_short(key, max(4, int(band / 6))))}</text>')
    svg.append("</svg>")
    return "".join(svg)


def chart_heatmap(rows: list[str], cols: list[str], values: dict, label: str, diverging: bool = False) -> str:
    """Sequential (one hue, light to dark) for magnitudes; diverging (blue / orange around a neutral zero) for correlations."""
    width, left, top, pad, legend_h = 560, 120, 70, 16, 30
    cell_w = (width - left - pad) / len(cols)
    cell_h = 40 if len(rows) <= 5 else 30 if len(rows) <= 10 else 22
    height = top + cell_h * len(rows) + legend_h + pad
    peak = 1.0 if diverging else max((abs(v) for v in values.values()), default=1) or 1
    svg = [_svg_open(width, int(height), label)]
    for j, col in enumerate(cols):
        x = left + j * cell_w + cell_w / 2
        svg.append(f'<text x="{x:.1f}" y="{top - 8}" transform="rotate(-35 {x:.1f} {top - 8})" class="ax">{escape(_short(col, 16))}</text>')
    for i, row in enumerate(rows):
        y = top + i * cell_h
        svg.append(f'<text x="{left - 8}" y="{y + cell_h / 2 + 4:.1f}" text-anchor="end" class="ax">{escape(_short(row, 18))}</text>')
        for j, col in enumerate(cols):
            x = left + j * cell_w
            value = values.get((row, col))
            if value is None:
                svg.append(f'<rect x="{x + 1:.1f}" y="{y + 1:.1f}" width="{cell_w - 2:.1f}" height="{cell_h - 2}" rx="3" fill="var(--grid)">'
                           f'<title>{escape(row)} · {escape(col)}: no data</title></rect>')
                continue
            strength = min(abs(value) / peak, 1)
            color = PALETTE[5] if diverging and value < 0 else PALETTE[0]
            opacity = 0.06 + 0.94 * strength
            svg.append(f'<rect x="{x + 1:.1f}" y="{y + 1:.1f}" width="{cell_w - 2:.1f}" height="{cell_h - 2}" rx="3" '
                       f'fill="{color}" fill-opacity="{opacity:.2f}">{_tip(f"{row} · {col}", value)}</rect>')
            if cell_w >= 34:
                text = f"{value:.2f}" if diverging else _fmt(value)
                ink = "#fff" if opacity > 0.55 else "var(--ink)"
                svg.append(f'<text x="{x + cell_w / 2:.1f}" y="{y + cell_h / 2 + 4:.1f}" text-anchor="middle" font-size="11" fill="{ink}">{escape(text)}</text>')
    # Colour scale: swatches with the two ends labelled (labels on every step collide).
    ly = top + cell_h * len(rows) + 12
    steps = [(-1 + i * 0.5) for i in range(5)] if diverging else [peak * i / 4 for i in range(5)]
    for k, step in enumerate(steps):
        color = PALETTE[5] if diverging and step < 0 else PALETTE[0]
        opacity = 0.06 + 0.94 * min(abs(step) / peak, 1)
        svg.append(f'<rect x="{left + k * 30}" y="{ly}" width="28" height="10" rx="2" fill="{color}" fill-opacity="{opacity:.2f}"/>')
    low_label, high_label = ("-1 (opposite)", "+1 (together)") if diverging else ("0", _fmt(peak))
    svg.append(f'<text x="{left - 6}" y="{ly + 9}" text-anchor="end" class="ax">{escape(low_label)}</text>'
               f'<text x="{left + 5 * 30 + 4}" y="{ly + 9}" class="ax">{escape(high_label)}</text>')
    svg.append("</svg>")
    return "".join(svg)


def squarify(items: list[tuple[str, float]], x: float, y: float, w: float, h: float) -> list[tuple[str, float, float, float, float, float]]:
    """Squarified treemap layout: returns (key, value, x, y, width, height) for positive values, largest first."""
    items = sorted(((k, v) for k, v in items if v > 0), key=lambda item: -item[1])
    total = sum(v for _, v in items)
    if not total:
        return []
    scale = w * h / total
    areas = [(k, v, v * scale) for k, v in items]

    def worst(row, short):
        s = sum(a for *_, a in row)
        return max(max(short * short * a / (s * s), (s * s) / (short * short * a)) for *_, a in row)

    out = []
    while areas:
        short = min(w, h) or 1
        row, i = [areas[0]], 1
        while i < len(areas) and worst(row + [areas[i]], short) <= worst(row, short):
            row.append(areas[i])
            i += 1
        areas = areas[i:]
        s = sum(a for *_, a in row)
        if w >= h:
            rw, cy = s / h if h else 0, y
            for k, v, a in row:
                rh = a / rw if rw else 0
                out.append((k, v, x, cy, rw, rh))
                cy += rh
            x, w = x + rw, w - rw
        else:
            rh, cx = s / w if w else 0, x
            for k, v, a in row:
                cw = a / rh if rh else 0
                out.append((k, v, cx, y, cw, rh))
                cx += cw
            y, h = y + rh, h - rh
    return out


def chart_treemap(data: list[tuple[str, float]], label: str) -> str:
    data = [(k, v) for k, v in data if v > 0]
    if not data:
        raise DataError("a treemap needs positive values")
    if len(data) > 12:
        data = data[:11] + [("Other", sum(v for _, v in data[11:]))]
    width, height = 560, 300
    total, peak = sum(v for _, v in data), max(v for _, v in data)
    svg = [_svg_open(width, height, label)]
    for key, value, x, y, w, h in squarify(data, 0, 0, width, height):
        opacity = 0.3 + 0.7 * value / peak
        ink = "#fff" if opacity > 0.6 else "var(--ink)"
        svg.append(f'<rect x="{x + 1:.1f}" y="{y + 1:.1f}" width="{max(w - 2, 0):.1f}" height="{max(h - 2, 0):.1f}" rx="4" '
                   f'fill="{PALETTE[0]}" fill-opacity="{opacity:.2f}">{_tip(key, value)}</rect>')
        if w > 48 and h > 30:
            svg.append(f'<text x="{x + 8:.1f}" y="{y + 18:.1f}" fill="{ink}" font-weight="600">{escape(_short(key, max(4, int(w / 7))))}</text>'
                       f'<text x="{x + 8:.1f}" y="{y + 32:.1f}" fill="{ink}" font-size="10">{escape(_fmt(value))} · {value / total:.0%}</text>')
    svg.append("</svg>")
    return "".join(svg)


def chart_scatter(points: list[tuple[float, float]], label: str, x_name: str, y_name: str) -> str:
    width, height, left, bottom, pad = 560, 300, 56, 44, 16
    if len(points) > 1500:
        points = points[:: math.ceil(len(points) / 1500)]
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    x_low, x_high, y_low, y_high = min(xs), max(xs), min(ys), max(ys)
    x_span, y_span = (x_high - x_low) or 1, (y_high - y_low) or 1
    svg = [_svg_open(width, height, label)]
    for i in range(5):
        fx = i / 4
        gx, gy = left + (width - left - pad) * fx, pad + (height - bottom - pad) * (1 - fx)
        svg.append(f'<line x1="{left}" x2="{width - pad}" y1="{gy:.1f}" y2="{gy:.1f}" class="grid"/>'
                   f'<text x="{left - 6}" y="{gy + 4:.1f}" text-anchor="end" class="ax">{escape(_fmt(y_low + y_span * fx))}</text>'
                   f'<text x="{gx:.1f}" y="{height - bottom + 16}" text-anchor="middle" class="ax">{escape(_fmt(x_low + x_span * fx))}</text>')
    for x, y in points:
        px = left + (width - left - pad) * (x - x_low) / x_span
        py = pad + (height - bottom - pad) * (1 - (y - y_low) / y_span)
        svg.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3" fill="{PALETTE[0]}" fill-opacity=".6"/>')
    svg.append(f'<text x="{(left + width - pad) / 2}" y="{height - 6}" text-anchor="middle" class="ax">{escape(x_name)}</text>'
               f'<text x="12" y="{height / 2}" transform="rotate(-90 12 {height / 2})" text-anchor="middle" class="ax">{escape(y_name)}</text></svg>')
    return "".join(svg)


# ---- dashboard ------------------------------------------------------------


@dataclass
class ChartSpec:
    kind: str
    title: str
    x: str | None = None
    y: str | None = None
    agg: str = "sum"
    where: str | None = None
    limit: int = 12
    series: str | None = None  # Second category: stacks/groups for stacked and grouped, columns for heatmap.


CHART_FORMAT = "kind | title | x | y | aggregate | series"
KIND_ALIASES = {"doughnut": "donut", "boxplot": "box", "box plot": "box", "heat": "heatmap", "heat map": "heatmap",
                "column": "bar", "stacked bar": "stacked", "grouped bar": "grouped", "histo": "histogram"}


def parse_chart_specs(items: list[str]) -> list[ChartSpec]:
    """Reads 'kind | title | x | y | agg | series' strings, which small models produce more reliably than nested JSON."""
    specs = []
    for item in items:
        parts = [p.strip() for p in item.split("|")]
        kind = KIND_ALIASES.get(parts[0].lower(), parts[0].lower()) if parts else ""
        if len(parts) < 2 or kind not in CHART_KINDS:
            raise DataError(f"bad chart '{item}'. Format: {CHART_FORMAT}. Kinds: {', '.join(sorted(CHART_KINDS))}")
        parts += [""] * (6 - len(parts))
        default_agg = "count" if kind == "heatmap" and not parts[3] else "sum"
        specs.append(ChartSpec(kind, parts[1], parts[2] or None, parts[3] or None, parts[4].lower() or default_agg,
                               series=parts[5] or None))
    return specs


def suggest_charts(table: Table) -> list[ChartSpec]:
    numbers = _ranked_numbers(table)
    labels = [c for c in table.columns if table.types[c] == "text" and 1 < len({str(v) for v in table.values(c)}) <= 30]
    dates = [c for c in table.columns if table.types[c] == "date"]
    specs: list[ChartSpec] = []
    if dates and numbers:
        specs.append(ChartSpec("line", f"{numbers[0]} over time", dates[0], numbers[0], "sum"))
    for label in labels[:2]:
        if numbers:
            specs.append(ChartSpec("bar", f"{numbers[0]} by {label}", label, numbers[0], "sum"))
        else:
            specs.append(ChartSpec("bar", f"Rows by {label}", label, None, "count"))
    if labels and numbers:
        specs.append(ChartSpec("donut", f"Share of {numbers[0]} by {labels[0]}", labels[0], numbers[0], "sum", limit=8))
    if len(labels) >= 2:
        metric = numbers[0] if numbers else None
        specs.append(ChartSpec("heatmap", f"{metric or 'Rows'} by {labels[0]} and {labels[1]}", labels[0], metric,
                               "sum" if metric else "count", series=labels[1]))
    if numbers:
        specs.append(ChartSpec("histogram", f"Distribution of {numbers[0]}", numbers[0]))
        group = next((label for label in labels if len({str(v) for v in table.values(label)}) <= 10), None)
        specs.append(ChartSpec("box", f"Spread of {numbers[0]}" + (f" by {group}" if group else ""), group, numbers[0]))
    if len(numbers) >= 3:
        specs.append(ChartSpec("heatmap", "Correlation between numeric columns", None, None, "count"))
    elif len(numbers) == 2:
        specs.append(ChartSpec("scatter", f"{numbers[0]} vs {numbers[1]}", numbers[0], numbers[1]))
    return specs[:8]


MONEY_WORDS = ("revenue", "sales", "amount", "total", "profit", "income", "cost", "spend", "price", "value", "count", "qty", "quantity")


def _ranked_numbers(table: Table) -> list[str]:
    """Numeric, non-id columns with business-looking measures first, so charts lead with revenue rather than a row index."""
    numbers = [c for c in table.columns if table.types[c] == "number" and not _looks_like_id(table, c)]
    def rank(column: str) -> int:
        name = column.lower()
        return next((i for i, w in enumerate(MONEY_WORDS) if w in name), len(MONEY_WORDS))
    return sorted(numbers, key=rank)


def _looks_like_id(table: Table, column: str) -> bool:
    values = [v for v in table.values(column) if v is not None]
    return bool(values) and "id" in column.lower().split("_") + [column.lower()] and len({str(v) for v in values}) == len(values)


@dataclass
class ChartData:
    """The computed numbers behind one chart, shared by the SVG, the slide deck and the PowerPoint export."""
    spec: ChartSpec
    pairs: list[tuple[str, float]] = field(default_factory=list)  # bar, line, area, pie, donut, histogram, treemap
    points: list[tuple[float, float]] = field(default_factory=list)  # scatter
    groups: list[tuple[str, dict]] = field(default_factory=list)  # box
    rows: list[str] = field(default_factory=list)  # heatmap, stacked, grouped
    cols: list[str] = field(default_factory=list)
    cells: dict = field(default_factory=dict)
    diverging: bool = False
    x_name: str = ""
    y_name: str = ""


def prepare_chart(table: Table, spec: ChartSpec) -> ChartData:
    """Computes a chart's data, or raises DataError that names the problem."""
    kind, data = spec.kind, ChartData(spec)
    if kind == "histogram":
        if not spec.x and not spec.y:
            raise DataError("histogram needs a numeric column as x")
        data.pairs = [(f"from {k}", v) for k, v in histogram(table, spec.x or spec.y, where=spec.where)]
    elif kind == "scatter":
        if not spec.x or not spec.y:
            raise DataError("scatter needs x and y columns")
        x_col, y_col = resolve_column(table, spec.x), resolve_column(table, spec.y)
        data.points = [(a, b) for a, b in ((_number(r.get(x_col)), _number(r.get(y_col))) for r in apply_filter(table, spec.where))
                       if a is not None and b is not None]
        if len(data.points) < 2:
            raise DataError("scatter needs at least two rows with numeric x and y")
        data.x_name, data.y_name = x_col, y_col
    elif kind == "box":
        column = spec.y or (spec.x if spec.x and table.types.get(resolve_column(table, spec.x)) == "number" else None)
        if not column:
            raise DataError("box needs a numeric column as y (and optionally a category as x)")
        group = spec.x if spec.x and spec.x != column else None
        data.groups = box_groups(table, column, group, spec.where)
        data.y_name = resolve_column(table, column)
    elif kind == "heatmap":
        if not spec.x or not spec.series:
            # No categories named: show how the numeric columns move together.
            data.rows, data.cells = correlation_matrix(table)
            data.cols, data.diverging = data.rows, True
        else:
            agg = spec.agg if spec.y or spec.agg == "count" else "count"
            data.rows, data.cols, data.cells = crosstab(table, spec.x, spec.series, spec.y, agg, spec.where)
    elif kind in {"stacked", "grouped"}:
        if not spec.x or not spec.series:
            raise DataError(f"{kind} needs x (category) and series (the column to split each bar by)")
        agg = spec.agg if spec.y or spec.agg == "count" else "count"
        if kind == "stacked" and agg not in {"sum", "count"}:
            agg = "sum" if spec.y else "count"  # Stacking only makes sense for additive measures.
        data.rows, data.cols, data.cells = crosstab(table, spec.x, spec.series, spec.y, agg, spec.where,
                                                    row_limit=spec.limit or 12, col_limit=6)
    else:
        data.pairs = aggregate(table, spec.x, spec.y, spec.agg if spec.y or spec.agg == "count" else "count", spec.where,
                               limit=spec.limit or 12, sort="asc" if kind in {"line", "area"} else "desc")
    return data


def draw_chart(data: ChartData) -> str:
    spec = data.spec
    kind, title = spec.kind, spec.title
    if kind == "histogram":
        return chart_bar(data.pairs, title, horizontal=False)
    if kind == "scatter":
        return chart_scatter(data.points, title, data.x_name, data.y_name)
    if kind == "box":
        return chart_box(data.groups, title)
    if kind == "heatmap":
        return chart_heatmap(data.rows, data.cols, data.cells, title, diverging=data.diverging)
    if kind in {"stacked", "grouped"}:
        return chart_stacked(data.rows, data.cols, data.cells, title, grouped=kind == "grouped")
    if kind in {"line", "area"}:
        return chart_line(data.pairs, title, fill=kind == "area")
    if kind in {"pie", "donut"}:
        return chart_pie(data.pairs, title, donut=kind == "donut")
    if kind == "treemap":
        return chart_treemap(data.pairs, title)
    return chart_bar(data.pairs, title)


def render_chart(table: Table, spec: ChartSpec) -> str:
    """Returns the SVG for one chart, or raises DataError that names the problem."""
    return draw_chart(prepare_chart(table, spec))


def chart_insight(data: ChartData) -> str:
    """One plain sentence about what the chart shows, computed from its data so it is never invented."""
    kind = data.spec.kind
    if data.pairs and kind in {"line", "area"}:
        (first_key, first), (last_key, last) = data.pairs[0], data.pairs[-1]
        peak_key, peak = max(data.pairs, key=lambda p: p[1])
        change = f", {(last - first) / abs(first):+.0%}" if first else ""
        return f"From {first_key} to {last_key} it moved from {_fmt(first)} to {_fmt(last)}{change}. Peak: {peak_key} at {_fmt(peak)}."
    if data.pairs and kind == "histogram":
        mode_key, mode = max(data.pairs, key=lambda p: p[1])
        return f"The most common range starts {mode_key.removeprefix('from ')}, with {_fmt(mode)} rows."
    if data.pairs:
        total = sum(v for _, v in data.pairs)
        (top_key, top), (low_key, low) = data.pairs[0], data.pairs[-1]
        share = f" ({top / total:.0%} of the shown total)" if total > 0 and data.spec.agg in {"sum", "count"} else ""
        if len(data.pairs) == 1:
            return f"{top_key}: {_fmt(top)}."
        return f"{top_key} is highest at {_fmt(top)}{share}; {low_key} is lowest at {_fmt(low)}."
    if data.points:
        xs, ys = zip(*data.points)
        try:
            r = statistics.correlation(xs, ys)
        except statistics.StatisticsError:
            return f"{len(data.points)} points plotted."
        return f"Correlation r = {r:.2f} ({strength_word(r)}) across {len(data.points)} rows."
    if data.groups:
        if len(data.groups) == 1:
            s = data.groups[0][1]
            return (f"Median {_fmt(s['median'])}; the middle half lies between {_fmt(s['q1'])} and {_fmt(s['q3'])}; "
                    f"{len(s['outliers'])} outlier(s).")
        high = max(data.groups, key=lambda g: g[1]["median"])
        low = min(data.groups, key=lambda g: g[1]["median"])
        outliers = sum(len(s["outliers"]) for _, s in data.groups)
        return (f"{high[0]} has the highest median ({_fmt(high[1]['median'])}), {low[0]} the lowest "
                f"({_fmt(low[1]['median'])}); {outliers} outlier(s) in total.")
    if data.cells and data.diverging:
        pairs = [((a, b), v) for (a, b), v in data.cells.items() if a < b]
        if not pairs:
            return "No column pairs had enough numeric rows to correlate."
        (a, b), v = max(pairs, key=lambda p: abs(p[1]))
        return f"Strongest relationship: {a} and {b}, r = {v:.2f} ({strength_word(v)})."
    if data.cells:
        (a, b), v = max(data.cells.items(), key=lambda p: p[1])
        if kind in {"stacked", "grouped"}:
            totals = Counter()
            for (row, _), value in data.cells.items():
                totals[row] += value
            leader, amount = totals.most_common(1)[0]
            return f"{leader} leads overall with {_fmt(amount)}; the largest single part is {b} in {a} ({_fmt(v)})."
        return f"The highest cell is {a} × {b} at {_fmt(v)}."
    return ""


def strength_word(r: float) -> str:
    size = abs(r)
    word = "strong" if size >= 0.7 else "moderate" if size >= 0.4 else "weak" if size >= 0.2 else "negligible"
    return word if word == "negligible" else f"{word} {'positive' if r > 0 else 'negative'}"


def build_kpis(table: Table) -> list[tuple[str, str]]:
    kpis = [("Rows", f"{len(table.rows):,}")]
    for column in _ranked_numbers(table)[:3]:
        stats = _number_stats(sorted(n for n in (_number(v) for v in table.values(column)) if n is not None))
        if stats:
            kpis.append((f"Total {column}", _fmt(stats["sum"])))
            kpis.append((f"Average {column}", _fmt(stats["mean"])))
    missing = sum(1 for row in table.rows for v in row.values() if v is None)
    kpis.append(("Missing cells", f"{missing:,}"))
    return kpis[:8]


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1c2128;--soft:#5b6570;--line:#dde1e6;--grid:#e8ebee}
@media (prefers-color-scheme:dark){:root{--bg:#14171b;--card:#1d2127;--ink:#e6e9ec;--soft:#9aa3ad;--line:#2d333b;--grid:#2a3038}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,sans-serif;padding:20px}
h1{font-size:20px;margin:0 0 2px}.sub{color:var(--soft);font-size:12px;margin-bottom:16px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:14px}
.kpi,.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.kpi .v{font-size:22px;font-weight:650;font-variant-numeric:tabular-nums}.kpi .l{color:var(--soft);font-size:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:12px}
.card h2{font-size:13px;margin:0 0 8px;font-weight:600}.card .err{color:var(--soft);font-size:12px}
.ax{fill:var(--soft)}line.grid{stroke:var(--grid)}.cap{color:var(--soft);font-size:12px;margin:6px 0 0}
table{border-collapse:collapse;width:100%;font-size:12px;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:4px 8px;border-bottom:1px solid var(--line);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:200px}
th{color:var(--soft);font-weight:600}.wide{grid-column:1/-1;overflow-x:auto}
"""


def build_dashboard(table: Table, title: str, specs: list[ChartSpec] | None = None) -> tuple[str, list[str]]:
    """Returns (html, notes). Charts that cannot be drawn are listed in notes and shown as a message."""
    specs = specs or suggest_charts(table)
    notes: list[str] = []
    cards = []
    for spec in specs:
        try:
            data = prepare_chart(table, spec)
            body = draw_chart(data)
            insight = chart_insight(data)
            if insight:
                body += f'<p class="cap">{escape(insight)}</p>'
        except DataError as exc:
            notes.append(f"'{spec.title}' skipped: {exc}")
            body = f'<p class="err">Could not draw this chart: {escape(str(exc))}</p>'
        cards.append(f'<section class="card"><h2>{escape(spec.title)}</h2>{body}</section>')
    kpis = "".join(f'<div class="kpi"><div class="v">{escape(v)}</div><div class="l">{escape(k)}</div></div>' for k, v in build_kpis(table))
    head = "".join(f"<th>{escape(c)}</th>" for c in table.columns)
    body_rows = "".join(
        "<tr>" + "".join(f"<td>{escape(str(_plain(row.get(c)) if row.get(c) is not None else ''))}</td>" for c in table.columns) + "</tr>"
        for row in table.rows[:15]
    )
    sample = f'<section class="card wide"><h2>Data preview (first {min(15, len(table.rows))} rows)</h2><table><tr>{head}</tr>{body_rows}</table></section>'
    html = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)}</title><style>{CSS}</style></head><body>"
        f'<h1>{escape(title)}</h1><div class="sub">Source: {escape(table.name)} · {len(table.rows):,} rows · '
        f"generated {datetime.now():%Y-%m-%d %H:%M}</div>"
        f'<div class="kpis">{kpis}</div><div class="grid">{"".join(cards)}{sample}</div></body></html>'
    )
    return html, notes


def safe_dashboard_name(title: str) -> str:
    name = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    return (name or "dashboard")[:60]
