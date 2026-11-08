"""#data: profile, query, analyse, transform, merge and visualise tabular files (CSV, TSV, JSON, Excel)
inside the permitted paths, and write results as CSV, JSON or Excel workbooks.

Everything runs in-process: no code written by the model is executed, and source files are never modified.
"""

from __future__ import annotations

from pathlib import Path

from .. import analysis, datatools, dataviz, excel, presentation
from ..fs_guard import atomic_write
from ..tools import Adapter, Tool, ToolContext, ToolError, ToolResult

PATH = {"type": "string", "description": "Data file (CSV, TSV, JSON or Excel .xlsx) relative to the workspace, e.g. projects/sales/data/orders.csv"}
SHEET = {"type": "string", "description": "Excel only: sheet name to read (default: the first sheet with data)"}


def _table(ctx: ToolContext, path: str, sheet: str | None = None) -> tuple[Path, dataviz.Table]:
    resolved = ctx.guard.resolve(path)
    try:
        return resolved, dataviz.load_table(resolved, sheet or None)
    except dataviz.DataError as exc:
        raise ToolError(str(exc)) from exc


async def profile_data(ctx: ToolContext, args: dict) -> ToolResult:
    _, table = _table(ctx, args["path"], args.get("sheet"))
    return ToolResult(True, dataviz.describe_text(table))


async def query_data(ctx: ToolContext, args: dict) -> ToolResult:
    _, table = _table(ctx, args["path"], args.get("sheet"))
    try:
        limit = max(1, min(int(args.get("limit", 20)), 100))
        if args.get("correlate"):
            first, second = (dataviz.resolve_column(table, c.strip()) for c in args["correlate"].split(",")[:2])
            value = dataviz.correlation(table, first, second)
            if value is None:
                return ToolResult(True, f"Not enough numeric rows to correlate {first} and {second}.")
            return ToolResult(True, f"Pearson correlation of {first} and {second}: {value:.3f}")
        if args.get("histogram"):
            pairs = dataviz.histogram(table, args["histogram"], where=args.get("filter"))
            return ToolResult(True, dataviz.format_pairs(pairs, f"Distribution of {args['histogram']}:"))
        if not args.get("group_by") and not args.get("metric"):
            rows = dataviz.apply_filter(table, args.get("filter"))[:limit]
            if not rows:
                return ToolResult(True, "No rows match the filter.")
            lines = [f"{len(rows)} row(s) shown:"] + [
                " | ".join(f"{c}={dataviz._plain(r.get(c))}" for c in table.columns) for r in rows
            ]
            return ToolResult(True, "\n".join(lines))
        agg = args.get("aggregate") or ("count" if not args.get("metric") else "sum")
        pairs = dataviz.aggregate(table, args.get("group_by"), args.get("metric"), agg, args.get("filter"), limit,
                                  args.get("sort", "desc"))
    except dataviz.DataError as exc:
        raise ToolError(str(exc)) from exc
    header = f"{agg} of {args.get('metric') or 'rows'}" + (f" by {args['group_by']}" if args.get("group_by") else "")
    return ToolResult(True, dataviz.format_pairs(pairs, header + ":"))


async def analyze_data(ctx: ToolContext, args: dict) -> ToolResult:
    _, table = _table(ctx, args["path"], args.get("sheet"))
    try:
        return ToolResult(True, analysis.run(table, args.get("analysis", ""), args))
    except dataviz.DataError as exc:
        raise ToolError(str(exc)) from exc


def _charts(args: dict, table: dataviz.Table) -> list[dataviz.ChartSpec]:
    charts = args.get("charts")
    if isinstance(charts, str):  # Small models sometimes send one string instead of a list.
        charts = [line for line in charts.splitlines() if line.strip()]
    try:
        return dataviz.parse_chart_specs(charts) if charts else dataviz.suggest_charts(table)
    except dataviz.DataError as exc:
        raise ToolError(str(exc)) from exc


def _output_path(ctx: ToolContext, source: Path, folder: str, title: str, suffix: str) -> Path:
    project = source.parent.parent if source.parent.name.lower() in {"data", "datasets"} else source.parent
    return ctx.guard.resolve(str(project / folder / f"{dataviz.safe_dashboard_name(title)}{suffix}"))


def _plan(ctx: ToolContext, args: dict) -> tuple[Path, dataviz.Table, list[dataviz.ChartSpec], Path]:
    source, table = _table(ctx, args["path"], args.get("sheet"))
    specs = _charts(args, table)
    title = args.get("title") or f"{source.stem} dashboard"
    return source, table, specs, _output_path(ctx, source, "dashboards", title, ".html")


def _check_dashboard(ctx: ToolContext, args: dict) -> None:
    if not ctx.permissions.check("filesystem", "write").allowed:
        raise ToolError("writing files is not permitted, so a dashboard cannot be saved")
    _plan(ctx, args)


def _preview(ctx: ToolContext, args: dict) -> str:
    _, table, specs, target = _plan(ctx, args)
    lines = [f"Create dashboard {ctx.guard.display(target)}", f"From {table.name}: {len(table.rows)} rows", ""]
    lines += [f"- {s.kind}: {s.title}" for s in specs] or ["- (no charts could be suggested)"]
    return "\n".join(lines)


async def create_dashboard(ctx: ToolContext, args: dict) -> ToolResult:
    _check_dashboard(ctx, args)  # Revalidated after approval.
    _, table, specs, target = _plan(ctx, args)
    title = args.get("title") or f"{table.name.rsplit('.', 1)[0]} dashboard"
    html, notes = dataviz.build_dashboard(table, title, specs)
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(target, html)
    label = ctx.guard.display(target)
    text = f"Dashboard saved to {label} with {len(specs) - len(notes)} chart(s). The user can open it with Preview."
    if notes:
        text += "\nNot drawn:\n" + "\n".join(f"- {n}" for n in notes)
    return ToolResult(True, text, [label])


FORMATS = {"html", "pptx", "both"}


def _deck_plan(ctx: ToolContext, args: dict):
    source, table = _table(ctx, args["path"], args.get("sheet"))
    specs = _charts(args, table)[:8]
    fmt = (args.get("format") or ("both" if presentation.HAS_PPTX else "html")).lower()
    if fmt not in FORMATS:
        raise ToolError("format must be html, pptx or both")
    if fmt in {"pptx", "both"} and not presentation.HAS_PPTX:
        raise ToolError("PowerPoint output needs python-pptx, which is not installed. Use format 'html', "
                        "or ask the user to run: pip install python-pptx")
    title = args.get("title") or f"{source.stem} analysis"
    targets = [_output_path(ctx, source, "presentations", title, suffix)
               for suffix in ([".html"] if fmt != "pptx" else []) + ([".pptx"] if fmt != "html" else [])]
    return table, specs, title, targets


def _check_presentation(ctx: ToolContext, args: dict) -> None:
    if not ctx.permissions.check("filesystem", "write").allowed:
        raise ToolError("writing files is not permitted, so a presentation cannot be saved")
    _deck_plan(ctx, args)


def _findings(args: dict) -> list[str]:
    findings = args.get("findings") or []
    return [line for line in findings.splitlines()] if isinstance(findings, str) else [str(f) for f in findings]


def _deck_preview(ctx: ToolContext, args: dict) -> str:
    table, specs, title, targets = _deck_plan(ctx, args)
    findings = _findings(args)
    lines = [f"Create presentation '{title}'", *(f"  {ctx.guard.display(t)}" for t in targets),
             f"From {table.name}: {len(table.rows)} rows", "", "Slides: title, overview"]
    lines += [f"- {s.kind}: {s.title}" for s in specs]
    lines.append(f"- key findings ({len(findings)} supplied)" if findings else "- key findings (taken from the charts)")
    lines.append("- about this analysis")
    return "\n".join(lines)


async def create_presentation(ctx: ToolContext, args: dict) -> ToolResult:
    _check_presentation(ctx, args)  # Revalidated after approval.
    table, specs, title, targets = _deck_plan(ctx, args)
    deck = presentation.build_deck(table, title, specs, _findings(args))
    labels = []
    try:
        for target in targets:
            content = presentation.render_html(deck) if target.suffix == ".html" else presentation.render_pptx(deck)
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(target, content)
            labels.append(ctx.guard.display(target))
    except dataviz.DataError as exc:
        raise ToolError(str(exc)) from exc
    drawn = sum(1 for c in deck.charts if not c.error)
    slides = 4 + len(deck.charts)
    text = f"Presentation saved: {', '.join(labels)} ({slides} slides, {drawn} chart(s))."
    if any(label.endswith(".html") for label in labels):
        text += " The HTML deck opens with Preview and prints to PDF one slide per page."
    if any(label.endswith(".pptx") for label in labels):
        text += " The .pptx opens in PowerPoint, Keynote or Google Slides, with editable charts."
    errors = [f"- '{c.spec.title}': {c.error}" for c in deck.charts if c.error]
    if errors:
        text += "\nNot drawn:\n" + "\n".join(errors)
    text += "\nChart captions in the deck:\n" + "\n".join(f"- {c.spec.title}: {c.insight}" for c in deck.charts if c.insight)
    return ToolResult(True, text, labels)


# ---- transform, merge and Excel ---------------------------------------------------


def _project_dir(source: Path) -> Path:
    return source.parent.parent if source.parent.name.lower() in {"data", "datasets"} else source.parent


def _output(ctx: ToolContext, source: Path, args: dict, default_name: str, default_suffix: str) -> Path:
    """Where a tool writes: the given output path, or a new file beside the source. Never the source itself."""
    raw = (args.get("output") or "").strip()
    if raw:
        target = ctx.guard.resolve(raw)
    else:
        target = ctx.guard.resolve(str(source.parent / f"{default_name}{default_suffix}"))
    if target.suffix.lower() not in datatools.WRITABLE:
        raise ToolError(f"output must end in one of: {', '.join(sorted(datatools.WRITABLE))}")
    if target == source:
        raise ToolError("output cannot be the source file; the original is never changed. Pick a new name.")
    if target.exists() and not args.get("overwrite"):
        raise ToolError(f"'{ctx.guard.display(target)}' already exists. Choose another output name, or pass overwrite true.")
    if target.is_dir():
        raise ToolError("output is a folder; give a file name")
    return target


def _check_write(ctx: ToolContext) -> None:
    if not ctx.permissions.check("filesystem", "write").allowed:
        raise ToolError("writing files is not permitted")


def _steps(args: dict) -> list[str]:
    steps = args.get("steps") or []
    return [line for line in steps.splitlines() if line.strip()] if isinstance(steps, str) else [str(x) for x in steps]


def _transform_plan(ctx: ToolContext, args: dict):
    source, table = _table(ctx, args["path"], args.get("sheet"))
    steps = _steps(args)
    suffix = source.suffix.lower() if source.suffix.lower() in datatools.WRITABLE else ".csv"
    if source.suffix.lower() in dataviz.EXCEL:
        suffix = ".xlsx"
    target = _output(ctx, source, args, f"{source.stem}_{'clean' if steps else 'copy'}", suffix)
    try:
        result, log = datatools.transform(table, steps)
    except dataviz.DataError as exc:
        raise ToolError(str(exc)) from exc
    return source, table, result, log, target


def _transform_check(ctx: ToolContext, args: dict) -> None:
    _check_write(ctx)
    _transform_plan(ctx, args)


def _transform_preview(ctx: ToolContext, args: dict) -> str:
    _, table, result, log, target = _transform_plan(ctx, args)
    lines = [f"Write {ctx.guard.display(target)} ({len(result.rows):,} rows, {len(result.columns)} columns)",
             f"From {table.name}: {len(table.rows):,} rows. The original file is not changed.", ""]
    lines += [f"- {line}" for line in log] or ["- no changes: a straight copy / format conversion"]
    lines += ["", "First rows:", datatools.preview_rows(result, 3)]
    return "\n".join(lines)


async def transform_data(ctx: ToolContext, args: dict) -> ToolResult:
    _transform_check(ctx, args)  # Revalidated after approval.
    _, table, result, log, target = _transform_plan(ctx, args)
    try:
        content = datatools.render(result.columns, result.rows, target.suffix, result, sheet=target.stem)
    except dataviz.DataError as exc:
        raise ToolError(str(exc)) from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(target, content)
    label = ctx.guard.display(target)
    text = [f"Saved {label}: {len(result.rows):,} rows, {len(result.columns)} columns (from {len(table.rows):,} rows)."]
    text += [f"- {line}" for line in log]
    text += ["", "First rows:", datatools.preview_rows(result, 5)]
    return ToolResult(True, "\n".join(text), [label])


def _merge_plan(ctx: ToolContext, args: dict):
    left_path, left = _table(ctx, args["path"], args.get("sheet"))
    right_path, right = _table(ctx, args["other_path"], args.get("other_sheet"))
    try:
        result, notes = datatools.merge(left, right, args.get("how") or "left", args.get("on"))
    except dataviz.DataError as exc:
        raise ToolError(str(exc)) from exc
    suffix = ".xlsx" if left_path.suffix.lower() in dataviz.EXCEL else (left_path.suffix.lower() if left_path.suffix.lower() in datatools.WRITABLE else ".csv")
    default = f"{left_path.stem}_{right_path.stem}_{'combined' if (args.get('how') or '') == 'append' else 'merged'}"
    return left, right, result, notes, _output(ctx, left_path, args, default, suffix)


def _merge_check(ctx: ToolContext, args: dict) -> None:
    _check_write(ctx)
    _merge_plan(ctx, args)


def _merge_preview(ctx: ToolContext, args: dict) -> str:
    left, right, result, notes, target = _merge_plan(ctx, args)
    lines = [f"Write {ctx.guard.display(target)} ({len(result.rows):,} rows, {len(result.columns)} columns)",
             f"From {left.name} ({len(left.rows):,} rows) and {right.name} ({len(right.rows):,} rows). Originals are not changed.", ""]
    return "\n".join(lines + [f"- {n}" for n in notes] + ["", "First rows:", datatools.preview_rows(result, 3)])


async def merge_data(ctx: ToolContext, args: dict) -> ToolResult:
    _merge_check(ctx, args)
    _, _, result, notes, target = _merge_plan(ctx, args)
    try:
        content = datatools.render(result.columns, result.rows, target.suffix, result, sheet="Merged")
    except dataviz.DataError as exc:
        raise ToolError(str(exc)) from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(target, content)
    label = ctx.guard.display(target)
    return ToolResult(True, "\n".join([f"Saved {label}: {len(result.rows):,} rows, {len(result.columns)} columns."]
                                      + [f"- {n}" for n in notes] + ["", "First rows:", datatools.preview_rows(result, 5)]), [label])


def _excel_plan(ctx: ToolContext, args: dict):
    source, table = _table(ctx, args["path"], args.get("sheet"))
    title = args.get("title") or f"{source.stem} report"
    charts = args.get("charts") or []
    if isinstance(charts, str):
        charts = [line for line in charts.splitlines() if line.strip()]
    sheets = args.get("sheets") or []
    if isinstance(sheets, str):
        sheets = [line for line in sheets.splitlines() if line.strip()]
    try:
        specs = dataviz.parse_chart_specs(charts) if charts else []
        excel.parse_sheet_specs(sheets)
        if args.get("split_by"):
            dataviz.resolve_column(table, args["split_by"])
    except dataviz.DataError as exc:
        raise ToolError(str(exc)) from exc
    if args.get("output"):
        target = _output(ctx, source, args, "", ".xlsx")
        if target.suffix.lower() != ".xlsx":
            raise ToolError("an Excel report must be saved as .xlsx")
    else:
        target = ctx.guard.resolve(str(_project_dir(source) / "reports" / f"{dataviz.safe_dashboard_name(title)}.xlsx"))
        if target.exists() and not args.get("overwrite"):
            raise ToolError(f"'{ctx.guard.display(target)}' already exists. Use another title, or pass overwrite true.")
    return table, title, sheets, specs, target


def _excel_check(ctx: ToolContext, args: dict) -> None:
    _check_write(ctx)
    _excel_plan(ctx, args)


def _excel_preview(ctx: ToolContext, args: dict) -> str:
    table, title, sheets, specs, target = _excel_plan(ctx, args)
    lines = [f"Create Excel workbook {ctx.guard.display(target)}", f"'{title}' from {table.name}: {len(table.rows):,} rows", "",
             "Sheets: Summary, Data (filterable table)"]
    lines += [f"- {line}" for line in sheets]
    if specs:
        lines.append(f"- Charts: {', '.join(f'{s.kind} ({s.title})' for s in specs)}")
    if args.get("split_by"):
        lines.append(f"- one sheet per value of {args['split_by']}")
    return "\n".join(lines)


async def create_excel(ctx: ToolContext, args: dict) -> ToolResult:
    _excel_check(ctx, args)
    table, title, sheets, specs, target = _excel_plan(ctx, args)
    try:
        content, names, notes = excel.build_report(table, title, sheets, specs, args.get("split_by"))
    except dataviz.DataError as exc:
        raise ToolError(str(exc)) from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(target, content)
    label = ctx.guard.display(target)
    text = f"Excel workbook saved to {label} with sheets: {', '.join(names)}. It opens in Excel, Numbers or Google Sheets."
    if notes:
        text += "\nSkipped:\n" + "\n".join(f"- {n}" for n in notes)
    return ToolResult(True, text, [label])


OUTPUT = {"type": "string", "description": "Optional output file, e.g. projects/sales/data/orders_clean.xlsx (.csv, .tsv, .json, .jsonl or .xlsx). Default: a new file beside the source"}

CHART_HELP_SHORT = ("Chart lines: 'kind | title | x | y | aggregate | series'; kinds bar, line, area, pie, donut, "
                    "histogram, scatter, stacked, grouped (box and heatmap become tables with colour scales).")

CHART_HELP = ("Chart lines use the form 'kind | title | x column | y column | aggregate | series'. Kinds: bar, line, area, "
              "pie, donut, histogram, scatter, box (y numeric, x optional category), heatmap (x and series are two "
              "categories; leave them out for a correlation matrix), stacked and grouped (x category split by series), "
              "treemap.")

adapter = Adapter(
    app_id="data",
    tools=[
        Tool("profile_data", "data", "profile",
             "Describe a CSV/TSV/JSON data file: row count, column types, missing values, statistics and sample rows. "
             "Always do this first before analysing a dataset.",
             {"path": PATH, "sheet": SHEET}, profile_data, required=["path"]),
        Tool("query_data", "data", "query",
             "Answer questions about a data file with exact numbers. Group and aggregate "
             "(group_by + metric + aggregate), list matching rows (filter only), show a histogram, or correlate two columns.",
             {"path": PATH, "sheet": SHEET,
              "group_by": {"type": "string", "description": "Column to group by"},
              "metric": {"type": "string", "description": "Numeric column to aggregate"},
              "aggregate": {"type": "string", "description": "count, sum, mean, min, max or median"},
              "filter": {"type": "string", "description": "Row filter such as: region == West, or price > 100, or name ~ phone"},
              "sort": {"type": "string", "description": "desc (default) or asc"},
              "limit": {"type": "integer", "description": "Maximum rows, default 20"},
              "histogram": {"type": "string", "description": "Numeric column to show a distribution for"},
              "correlate": {"type": "string", "description": "Two numeric columns separated by a comma"}},
             query_data, required=["path"]),
        Tool("analyze_data", "data", "analyze",
             "Run a deeper analysis on a data file and get exact numbers. analysis is one of: "
             "quality (missing values, duplicates, bad values), outliers (column optional), "
             "correlations (columns optional), pivot (group_by rows x pivot_by columns, metric, aggregate), "
             "trend (metric over time; date_column and period optional), compare (group_by + metric: count, sum, "
             "share, mean, median, spread per group), distribution (column: percentiles, skew, spread).",
             {"path": PATH, "sheet": SHEET,
              "analysis": {"type": "string", "description": "quality, outliers, correlations, pivot, trend, compare or distribution"},
              "column": {"type": "string", "description": "Numeric column for outliers or distribution"},
              "columns": {"type": "string", "description": "Comma-separated numeric columns for correlations"},
              "group_by": {"type": "string", "description": "Category column for pivot rows or compare"},
              "pivot_by": {"type": "string", "description": "Category column for pivot columns"},
              "metric": {"type": "string", "description": "Numeric column to measure"},
              "aggregate": {"type": "string", "description": "count, sum, mean, min, max or median"},
              "date_column": {"type": "string", "description": "Date column for trend (default: the first date column)"},
              "period": {"type": "string", "description": "day, week, month (default), quarter or year"},
              "filter": {"type": "string", "description": "Row filter such as: region == West, or price > 100"}},
             analyze_data, required=["path", "analysis"]),
        Tool("create_dashboard", "data", "dashboard",
             "Build an HTML dashboard with summary numbers and charts from a data file, saved in the project's "
             "dashboards folder. Omit charts to get automatic suggestions. " + CHART_HELP,
             {"path": PATH, "sheet": SHEET,
              "title": {"type": "string", "description": "Dashboard title"},
              "charts": {"type": "array", "items": {"type": "string"},
                         "description": "e.g. 'bar | Revenue by region | region | revenue | sum' or "
                                        "'heatmap | Orders by region and product | region | | count | product'"}},
             create_dashboard, required=["path"], precheck=_check_dashboard, preview=_preview),
        Tool("create_presentation", "data", "presentation",
             "Build a slide deck from a data file: title, overview, one slide per chart with a computed caption, key "
             "findings, and notes. Saved in the project's presentations folder as an HTML deck and a PowerPoint "
             ".pptx. Omit charts for automatic ones. " + CHART_HELP,
             {"path": PATH, "sheet": SHEET,
              "title": {"type": "string", "description": "Presentation title"},
              "charts": {"type": "array", "items": {"type": "string"}, "description": "Chart lines, up to 8"},
              "findings": {"type": "array", "items": {"type": "string"},
                           "description": "Key findings for the summary slide, each a sentence with exact numbers from query_data or analyze_data"},
              "format": {"type": "string", "description": "both (default), html or pptx"}},
             create_presentation, required=["path"], precheck=_check_presentation, preview=_deck_preview),
        Tool("transform_data", "data", "transform",
             "Clean, reshape or convert a data file and save the result as a NEW file (the original is never changed). "
             "Also converts between CSV, TSV, JSON and Excel: pass no steps and an output with the new extension. "
             + datatools.STEP_HELP,
             {"path": PATH, "sheet": SHEET,
              "steps": {"type": "array", "items": {"type": "string"},
                        "description": "Steps in order, e.g. ['trim', 'dedupe', 'fill | revenue = 0', 'compute | total = units * unit_price']"},
              "output": OUTPUT,
              "overwrite": {"type": "boolean", "description": "Replace the output file if it exists (never the source)"}},
             transform_data, required=["path"], precheck=_transform_check, preview=_transform_preview),
        Tool("merge_data", "data", "transform",
             "Combine two data files into a new file. Join them on matching key columns (how: left, inner, right, outer; "
             "on: 'customer_id' or 'customer_id = id'), or stack rows of files with the same layout (how: append).",
             {"path": PATH, "sheet": SHEET,
              "other_path": {"type": "string", "description": "The second data file"},
              "other_sheet": {"type": "string", "description": "Excel only: sheet of the second file"},
              "how": {"type": "string", "description": "left (default), inner, right, outer, or append"},
              "on": {"type": "string", "description": "Key column(s), e.g. 'id' or 'customer_id = id, region = region'. Default: a shared id-like column"},
              "output": OUTPUT,
              "overwrite": {"type": "boolean", "description": "Replace the output file if it exists"}},
             merge_data, required=["path", "other_path"], precheck=_merge_check, preview=_merge_preview),
        Tool("create_excel", "data", "excel",
             "Create a formatted Excel workbook (.xlsx) report from a data file, saved in the project's reports folder: "
             "a Summary sheet, the data as a filterable Excel table, optional extra sheets and native Excel charts. "
             "Sheet lines: 'pivot | rows | columns | metric | aggregate', 'group | category | metric', "
             "'trend | metric | aggregate | period', 'quality'. " + CHART_HELP_SHORT,
             {"path": PATH, "sheet": SHEET,
              "title": {"type": "string", "description": "Report title"},
              "sheets": {"type": "array", "items": {"type": "string"}, "description": "Extra sheets, e.g. ['pivot | region | product | revenue | sum', 'group | region | revenue']"},
              "charts": {"type": "array", "items": {"type": "string"}, "description": "Chart lines, e.g. 'bar | Revenue by region | region | revenue | sum'"},
              "split_by": {"type": "string", "description": "Optional column: one extra sheet per value (e.g. one per region)"},
              "output": {"type": "string", "description": "Optional .xlsx path. Default: <project>/reports/<title>.xlsx"},
              "overwrite": {"type": "boolean", "description": "Replace the workbook if it exists"}},
             create_excel, required=["path"], precheck=_excel_check, preview=_excel_preview),
    ],
)
