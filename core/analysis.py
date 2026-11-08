"""Deeper analyses on a loaded Table: data quality, outliers, correlations, pivots, trends, group comparison, distribution.

Each function returns plain text with exact numbers, ready to hand to the model as a tool result.
Standard library only, like dataviz.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime

from .dataviz import (
    AGGREGATES, REDUCERS, DataError, Table, _fmt, _group_key, _number, _plain, _ranked_numbers, apply_filter,
    box_stats, correlation_matrix, crosstab, numbers_of, resolve_column, strength_word,
)

ANALYSES = ("quality", "outliers", "correlations", "pivot", "trend", "compare", "distribution")
PERIODS = ("day", "week", "month", "quarter", "year")


def run(table: Table, analysis: str, args: dict) -> str:
    analysis = (analysis or "").lower().strip()
    if analysis not in ANALYSES:
        raise DataError(f"unknown analysis '{analysis}'. Use one of: {', '.join(ANALYSES)}")
    where = args.get("filter")
    if analysis == "quality":
        return quality_report(table)
    if analysis == "outliers":
        return outlier_report(table, args.get("column") or args.get("metric"), where)
    if analysis == "correlations":
        columns = [c.strip() for c in (args.get("columns") or "").split(",") if c.strip()] or None
        return correlation_report(table, columns)
    if analysis == "pivot":
        return pivot_report(table, _need(args, "group_by", "pivot"), _need(args, "pivot_by", "pivot"),
                            args.get("metric"), args.get("aggregate"), where)
    if analysis == "trend":
        return trend_report(table, args.get("metric"), args.get("aggregate"), args.get("date_column"),
                            args.get("period") or "month", where)
    if analysis == "compare":
        return compare_report(table, _need(args, "group_by", "compare"), _need(args, "metric", "compare"), where)
    return distribution_report(table, _need(args, "column", "distribution", fallback=args.get("metric")), where)


def _need(args: dict, key: str, analysis: str, fallback: str | None = None) -> str:
    value = args.get(key) or fallback
    if not value:
        raise DataError(f"the '{analysis}' analysis needs '{key}'")
    return value


def _grid(headers: list[str], rows: list[list[str]]) -> str:
    widths = [max(len(str(r[i])) for r in [headers, *rows]) for i in range(len(headers))]
    line = lambda cells: "  ".join(str(c).ljust(w) for c, w in zip(cells, widths)).rstrip()
    return "\n".join([line(headers), line(["-" * w for w in widths]), *(line(r) for r in rows)])


def _pct(part: float, whole: float) -> str:
    return f"{part / whole:.1%}" if whole else "-"


# ---- quality ----------------------------------------------------------------


def quality_report(table: Table) -> str:
    n, cols = len(table.rows), table.columns
    total_cells = n * len(cols)
    missing_cells = sum(1 for row in table.rows for v in row.values() if v is None)
    seen, duplicates = set(), 0
    for row in table.rows:
        key = tuple(str(_plain(row.get(c))) for c in cols)
        duplicates += key in seen
        seen.add(key)

    lines = [f"Data quality for {table.name}: {n:,} rows, {len(cols)} columns" + (" (file truncated)" if table.truncated else ""),
             f"Completeness: {1 - missing_cells / total_cells:.1%} of cells filled ({missing_cells:,} missing)" if total_cells else "",
             f"Duplicate rows: {duplicates:,} ({_pct(duplicates, n)})", ""]
    rows, issues = [], []
    for column in cols:
        values = table.values(column)
        present = [v for v in values if v is not None]
        kind = table.types[column]
        missing = n - len(present)
        unique = len({str(v) for v in present})
        note = []
        if kind == "empty":
            issues.append(f"'{column}' is completely empty")
        elif unique == 1:
            issues.append(f"'{column}' has a single value for every row, so it carries no information")
        if missing and missing / n >= 0.2:
            issues.append(f"'{column}' is {_pct(missing, n)} missing")
        if kind == "number":
            bad = [v for v in present if _number(v) is None]
            if bad:
                note.append(f"{len(bad)} non-numeric")
                issues.append(f"'{column}' is numeric but has {len(bad)} non-numeric value(s), e.g. {bad[0]!r}")
            numbers = sorted(x for x in (_number(v) for v in present) if x is not None)
            if len(numbers) >= 4:
                outliers = len(box_stats(numbers)["outliers"])
                if outliers:
                    note.append(f"{outliers} outliers")
            negatives = sum(1 for x in numbers if x < 0)
            if negatives:
                note.append(f"{negatives} negative")
        elif kind == "text":
            variants = defaultdict(set)
            for v in present:
                variants[" ".join(str(v).lower().split())].add(str(v))
            clashes = [sorted(group) for group in variants.values() if len(group) > 1]
            if clashes:
                note.append(f"{len(clashes)} spelling variants")
                issues.append(f"'{column}' has values that differ only by case or spacing, e.g. {', '.join(clashes[0][:3])}")
            if unique == len(present) and n > 20:
                note.append("all unique (identifier?)")
        rows.append([column, kind, f"{missing:,}", _pct(missing, n), f"{unique:,}", ", ".join(note) or "ok"])
    lines.append(_grid(["column", "type", "missing", "missing %", "unique", "notes"], rows))
    lines += ["", "Issues to look at:" if issues else "No major issues found."] + [f"- {i}" for i in issues]
    if duplicates:
        lines.append(f"- {duplicates:,} row(s) are exact duplicates of an earlier row")
    return "\n".join(line for line in lines if line is not None)


# ---- outliers ---------------------------------------------------------------


def outlier_report(table: Table, column: str | None = None, where: str | None = None, limit: int = 10) -> str:
    columns = [resolve_column(table, column)] if column else _ranked_numbers(table)
    if not columns:
        raise DataError("no numeric columns to check for outliers")
    label_col = next((c for c in table.columns if table.types[c] == "text"), None)
    rows = apply_filter(table, where)
    lines = ["Outliers by the 1.5 × IQR rule (values beyond the whisker fences of a box plot):"]
    for col in columns:
        indexed = [(i, x) for i, x in ((i, _number(r.get(col))) for i, r in enumerate(rows)) if x is not None]
        if len(indexed) < 4:
            lines.append(f"\n{col}: too few numeric values")
            continue
        stats = box_stats(sorted(x for _, x in indexed))
        flagged = [(i, x) for i, x in indexed if x < stats["low_fence"] or x > stats["high_fence"]]
        median = stats["median"]
        flagged.sort(key=lambda item: -abs(item[1] - median))
        lines.append(f"\n{col}: {len(flagged)} of {len(indexed)} values ({_pct(len(flagged), len(indexed))}) are outliers. "
                     f"Normal range {_fmt(stats['low_fence'])} to {_fmt(stats['high_fence'])}, median {_fmt(median)}.")
        for i, x in flagged[:limit]:
            who = f" ({label_col}={rows[i].get(label_col)})" if label_col else ""
            lines.append(f"  row {i + 1}{who}: {_fmt(x)} ({'above' if x > median else 'below'} the median)")
        if len(flagged) > limit:
            lines.append(f"  … and {len(flagged) - limit} more")
    return "\n".join(lines)


# ---- correlations -----------------------------------------------------------


def correlation_report(table: Table, columns: list[str] | None = None) -> str:
    cols, matrix = correlation_matrix(table, columns)
    grid = [[a] + [f"{matrix[(a, b)]:.2f}" if (a, b) in matrix else "-" for b in cols] for a in cols]
    pairs = sorted(((a, b, matrix[(a, b)]) for i, a in enumerate(cols) for b in cols[i + 1:] if (a, b) in matrix),
                   key=lambda p: -abs(p[2]))
    lines = ["Pearson correlation matrix (-1 opposite, 0 unrelated, +1 move together):", _grid([""] + cols, grid), "",
             "Strongest pairs:"]
    lines += [f"- {a} & {b}: r = {r:.3f} ({strength_word(r)})" for a, b, r in pairs[:10]] or ["- none"]
    lines.append("Correlation shows association, not cause.")
    return "\n".join(lines)


# ---- pivot ------------------------------------------------------------------


def pivot_report(table: Table, group_by: str, pivot_by: str, metric: str | None, agg: str | None, where: str | None) -> str:
    agg = (agg or ("sum" if metric else "count")).lower()
    rows, cols, cells = crosstab(table, group_by, pivot_by, metric, agg, where, row_limit=25, col_limit=10)
    additive = agg in {"count", "sum"}
    headers = [resolve_column(table, group_by)] + cols + (["total"] if additive else [])
    body = []
    for r in rows:
        values = [cells.get((r, c)) for c in cols]
        line = [r] + [_fmt(v) if v is not None else "-" for v in values]
        if additive:
            line.append(_fmt(math.fsum(v for v in values if v is not None)))
        body.append(line)
    if additive:
        col_totals = [math.fsum(cells.get((r, c), 0) for r in rows) for c in cols]
        body.append(["total"] + [_fmt(v) for v in col_totals] + [_fmt(math.fsum(col_totals))])
    title = f"{agg} of {metric or 'rows'} by {group_by} (rows) and {pivot_by} (columns)"
    return title + ":\n" + _grid(headers, body)


# ---- trend ------------------------------------------------------------------


def _period_key(value: datetime, period: str) -> str:
    if period == "day":
        return value.strftime("%Y-%m-%d")
    if period == "week":
        year, week, _ = value.isocalendar()
        return f"{year}-W{week:02d}"
    if period == "quarter":
        return f"{value.year}-Q{(value.month - 1) // 3 + 1}"
    if period == "year":
        return str(value.year)
    return value.strftime("%Y-%m")


def trend_report(table: Table, metric: str | None, agg: str | None, date_column: str | None, period: str,
                 where: str | None) -> str:
    period = period.lower()
    if period not in PERIODS:
        raise DataError(f"unknown period '{period}'. Use: {', '.join(PERIODS)}")
    if date_column:
        date_col = resolve_column(table, date_column)
        if table.types[date_col] != "date":
            raise DataError(f"'{date_col}' is not a date column")
    else:
        date_col = next((c for c in table.columns if table.types[c] == "date"), None)
        if not date_col:
            raise DataError("no date column found, so there is no trend to measure")
    agg = (agg or ("sum" if metric else "count")).lower()
    if agg not in AGGREGATES:
        raise DataError(f"unknown aggregate '{agg}'. Use: {', '.join(sorted(AGGREGATES))}")
    metric_col = resolve_column(table, metric) if metric else None
    if agg != "count" and not metric_col:
        raise DataError(f"'{agg}' needs a metric column")
    buckets: dict[str, list[float]] = defaultdict(list)
    last_date = None
    for row in apply_filter(table, where):
        when = row.get(date_col)
        if not isinstance(when, datetime):
            continue
        last_date = max(last_date, when) if last_date else when
        value = 1.0 if agg == "count" else _number(row.get(metric_col))
        if value is not None:
            buckets[_period_key(when, period)].append(value)
    if len(buckets) < 2:
        raise DataError("need at least two periods with data to show a trend")
    series = [(key, float(REDUCERS[agg](buckets[key]))) for key in sorted(buckets)]
    label = f"{agg} of {metric_col or 'rows'} per {period}"
    body = []
    for i, (key, value) in enumerate(series):
        prev = series[i - 1][1] if i else None
        change = "" if prev is None else f"{value - prev:+,.2f}"
        pct = "" if not prev else f"{(value - prev) / abs(prev):+.1%}"
        body.append([key, _fmt(value), change, pct])
    first, last = series[0][1], series[-1][1]
    peak, low = max(series, key=lambda s: s[1]), min(series, key=lambda s: s[1])
    slope = statistics.linear_regression(range(len(series)), [v for _, v in series]).slope
    mean = statistics.fmean(v for _, v in series)
    direction = "flat" if mean and abs(slope) < 0.01 * abs(mean) else "rising" if slope > 0 else "falling"
    changes = [(b[1] - a[1]) / abs(a[1]) for a, b in zip(series, series[1:]) if a[1]]
    lines = [f"{label} ({date_col}, {len(series)} periods):", _grid([period, "value", "change", "change %"], body), "",
             f"Overall: {_fmt(first)} → {_fmt(last)}" + (f" ({(last - first) / abs(first):+.1%})" if first else ""),
             f"Direction (linear fit): {direction}, about {slope:+,.2f} per {period}",
             f"Highest: {peak[0]} ({_fmt(peak[1])}). Lowest: {low[0]} ({_fmt(low[1])}).",
             f"Average change per {period}: {statistics.fmean(changes):+.1%}" if changes else ""]
    if last_date and ((period == "month" and last_date.day < 28) or (period == "year" and last_date.month < 12)):
        lines.append(f"Note: data ends {last_date.date().isoformat()}, so the last {period} may be incomplete.")
    return "\n".join(line for line in lines if line)


# ---- compare groups ---------------------------------------------------------


def compare_report(table: Table, group_by: str, metric: str, where: str | None, limit: int = 20) -> str:
    group_col, metric_col = resolve_column(table, group_by), resolve_column(table, metric)
    buckets: dict[str, list[float]] = defaultdict(list)
    for row in apply_filter(table, where):
        key, value = _group_key(row.get(group_col)), _number(row.get(metric_col))
        if key is not None and value is not None:
            buckets[key].append(value)
    if not buckets:
        raise DataError(f"no numeric '{metric_col}' values to compare")
    grand = math.fsum(math.fsum(v) for v in buckets.values())
    ordered = sorted(buckets.items(), key=lambda item: -math.fsum(item[1]))
    body = []
    for key, values in ordered[:limit]:
        total = math.fsum(values)
        body.append([key, f"{len(values):,}", _fmt(total), _pct(total, grand), _fmt(statistics.fmean(values)),
                     _fmt(statistics.median(values)), _fmt(statistics.pstdev(values)) if len(values) > 1 else "0",
                     _fmt(min(values)), _fmt(max(values))])
    everything = [v for values in buckets.values() for v in values]
    body.append(["(all)", f"{len(everything):,}", _fmt(grand), "100%", _fmt(statistics.fmean(everything)),
                 _fmt(statistics.median(everything)), _fmt(statistics.pstdev(everything)) if len(everything) > 1 else "0",
                 _fmt(min(everything)), _fmt(max(everything))])
    means = {k: statistics.fmean(v) for k, v in buckets.items()}
    best, worst = max(means, key=means.get), min(means, key=means.get)
    lines = [f"{metric_col} compared across {group_col} ({len(buckets)} groups" + (f", top {limit} shown" if len(buckets) > limit else "") + "):",
             _grid([group_col, "rows", "sum", "share", "mean", "median", "std", "min", "max"], body), "",
             f"Highest average: {best} ({_fmt(means[best])}); lowest average: {worst} ({_fmt(means[worst])})"
             + (f", a {means[best] / means[worst]:.2f}x gap." if means[worst] > 0 else ".")]
    top_key, top_values = ordered[0]
    lines.append(f"{top_key} contributes the largest share of the total: {_pct(math.fsum(top_values), grand)}.")
    small = [k for k, v in buckets.items() if len(v) < 5]
    if small:
        lines.append(f"Caution: {len(small)} group(s) have fewer than 5 rows, so their averages are unreliable.")
    return "\n".join(lines)


# ---- distribution -----------------------------------------------------------


def distribution_report(table: Table, column: str, where: str | None) -> str:
    column = resolve_column(table, column)
    numbers = numbers_of(table, column, where)
    if len(numbers) < 2:
        raise DataError(f"column '{column}' needs at least two numeric values")
    mean, sd = statistics.fmean(numbers), statistics.pstdev(numbers)
    cuts = statistics.quantiles(numbers, n=100, method="inclusive")
    pct = {p: cuts[p - 1] for p in (1, 5, 10, 25, 50, 75, 90, 95, 99)}
    skew = (math.fsum((x - mean) ** 3 for x in numbers) / len(numbers)) / sd ** 3 if sd else 0.0
    shape = ("roughly symmetric" if abs(skew) < 0.5 else
             "right-skewed: a long tail of high values pulls the mean above the median" if skew > 0 else
             "left-skewed: a long tail of low values pulls the mean below the median")
    stats = box_stats(numbers)
    missing = len(table.rows) - len(numbers) if not where else None
    lines = [f"Distribution of {column}: {len(numbers):,} values" + (f", {missing:,} missing or non-numeric" if missing else ""),
             f"mean {_fmt(mean)} · median {_fmt(pct[50])} · std {_fmt(sd)} · min {_fmt(numbers[0])} · max {_fmt(numbers[-1])}",
             "Percentiles: " + ", ".join(f"p{p} {_fmt(v)}" for p, v in pct.items()),
             f"Middle half (IQR): {_fmt(pct[25])} to {_fmt(pct[75])}, width {_fmt(pct[75] - pct[25])}",
             f"Skewness {skew:.2f}: {shape}.",
             f"Outliers (1.5 × IQR): {len(stats['outliers'])}",
             f"Coefficient of variation: {sd / abs(mean):.1%}" if mean else ""]
    zeros, negatives = sum(1 for x in numbers if x == 0), sum(1 for x in numbers if x < 0)
    if zeros or negatives:
        lines.append(f"Zeros: {zeros:,}. Negative values: {negatives:,}.")
    common = Counter(numbers).most_common(1)[0]
    if common[1] > 1 and common[1] >= 0.1 * len(numbers):
        lines.append(f"Most frequent value: {_fmt(common[0])} ({common[1]:,} times).")
    return "\n".join(line for line in lines if line)
