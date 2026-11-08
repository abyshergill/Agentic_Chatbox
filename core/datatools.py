"""Cleaning, reshaping, merging and converting tabular data, written to a new file.

Steps are plain lines such as 'filter | region == West' or 'compute | total = units * unit_price', which small models
write more reliably than nested JSON. Formulas are parsed into a tiny arithmetic tree and evaluated here; nothing the
model writes is ever run as code. The source file is never modified.
"""

from __future__ import annotations

import ast
import csv
import io
import json
import math
import operator
import re
import statistics
from collections import defaultdict
from datetime import datetime

from . import dataviz, excel
from .dataviz import DataError, Table, _number, _plain, apply_filter, resolve_column

WRITABLE = {".csv", ".tsv", ".json", ".jsonl", ".xlsx"}
STEPS = ("select", "drop", "rename", "filter", "sort", "dedupe", "dropna", "fill", "trim", "case", "replace",
         "compute", "type", "extract", "aggregate", "limit")
STEP_HELP = (
    "Steps, one per line: select | a, b · drop | a, b · rename | old -> new · filter | region == West · "
    "sort | revenue desc, date asc · dedupe (| columns) · dropna (| columns) · fill | col = 0 (or mean, median, previous, any text) · "
    "trim (| columns) · case | col = upper|lower|title · replace | col: old -> new · compute | total = units * price · "
    "type | col = number|text|date · extract | date -> year, month, quarter, weekday · "
    "aggregate | group cols | metric:sum, metric2:mean, *:count · limit | 100"
)


# ---- writing --------------------------------------------------------------------


def render(columns: list[str], rows: list[dict], suffix: str, table: Table | None = None, sheet: str = "Data") -> str | bytes:
    """The file contents for the given format."""
    suffix = suffix.lower()
    if suffix not in WRITABLE:
        raise DataError(f"cannot write '{suffix}' files. Use one of: {', '.join(sorted(WRITABLE))}")
    if suffix == ".xlsx":
        return excel.export_table(columns, rows, table, sheet)
    if suffix in {".json", ".jsonl"}:
        kinds = table.types if table else {}
        records = [{c: _json_value(r.get(c), kinds.get(c, "")) for c in columns} for r in rows]
        if suffix == ".jsonl":
            return "".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in records)
        return json.dumps(records, ensure_ascii=False, indent=2, default=str) + "\n"
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter="\t" if suffix == ".tsv" else ",", lineterminator="\n")
    writer.writerow(columns)
    for r in rows:
        writer.writerow(["" if r.get(c) is None else _text(r.get(c)) for c in columns])
    return buffer.getvalue()


def _json_value(value, kind: str):
    """Numbers as JSON numbers and dates as ISO text, whatever the source format stored them as."""
    if value is None:
        return None
    if kind == "number":
        number = _number(value)
        if number is not None:
            return int(number) if number == int(number) and abs(number) < 1e15 else number
    if isinstance(value, datetime):
        return _text(value)
    return value


def _text(value) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d") if (value.hour, value.minute, value.second) == (0, 0, 0) else value.isoformat(sep=" ")
    if isinstance(value, float) and value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return str(value)


def retype(columns: list[str], rows: list[dict], name: str) -> Table:
    """Builds a Table from transformed rows, re-inferring column types."""
    table = Table(name, list(columns), rows)
    table.types = {c: dataviz._infer_type(table.values(c)) for c in columns}
    return table


# ---- safe formulas ------------------------------------------------------------------

OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
       ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
FUNCS = {"round": round, "abs": abs, "min": min, "max": max, "sqrt": math.sqrt, "log": math.log}


class Formula:
    """'units * unit_price', '`unit price` * 1.2', 'round(revenue / units, 2)'. Arithmetic on columns only."""

    def __init__(self, text: str, table_columns: list[str]):
        self.columns: dict[str, str] = {}
        counter = 0

        def quoted(match: re.Match) -> str:
            nonlocal counter
            counter += 1
            alias = f"__c{counter}"
            self.columns[alias] = self._column(match.group(1), table_columns)
            return alias

        source = re.sub(r"`([^`]+)`", quoted, text.strip())
        try:
            self.tree = ast.parse(source, mode="eval")
        except SyntaxError as exc:
            raise DataError(f"could not read the formula '{text}': {exc.msg}") from exc
        self._check(self.tree.body, table_columns)

    @staticmethod
    def _column(name: str, table_columns: list[str]) -> str:
        if name in table_columns:
            return name
        lowered = {c.lower(): c for c in table_columns}
        if name.lower() in lowered:
            return lowered[name.lower()]
        raise DataError(f"no column '{name}' in the formula. Columns: {', '.join(table_columns)}. "
                        "Wrap names with spaces in backticks, e.g. `unit price`")

    def _check(self, node: ast.AST, table_columns: list[str]) -> None:
        if isinstance(node, ast.BinOp) and type(node.op) in OPS:
            self._check(node.left, table_columns)
            self._check(node.right, table_columns)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            self._check(node.operand, table_columns)
        elif isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            pass
        elif isinstance(node, ast.Name):
            if node.id not in self.columns:
                self.columns[node.id] = self._column(node.id, table_columns)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCS
              and not node.keywords and 1 <= len(node.args) <= 3):
            for arg in node.args:
                self._check(arg, table_columns)
        else:
            raise DataError("formulas may only use column names, numbers, + - * / // % ** and round, abs, min, max, sqrt, log")

    def evaluate(self, record: dict) -> float | None:
        try:
            value = self._eval(self.tree.body, record)
        except (ZeroDivisionError, ValueError, OverflowError, TypeError):
            return None
        if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
            return None
        return round(value, 10)

    def _eval(self, node: ast.AST, record: dict):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return _number(record.get(self.columns[node.id]))
        if isinstance(node, ast.UnaryOp):
            value = self._eval(node.operand, record)
            return None if value is None else (-value if isinstance(node.op, ast.USub) else value)
        if isinstance(node, ast.BinOp):
            left, right = self._eval(node.left, record), self._eval(node.right, record)
            if left is None or right is None:
                return None
            if isinstance(node.op, ast.Pow) and abs(right) > 64:
                raise OverflowError
            return OPS[type(node.op)](left, right)
        args = [self._eval(arg, record) for arg in node.args]
        if any(arg is None for arg in args):
            return None
        if node.func.id == "round" and len(args) == 2:
            args[1] = int(args[1])
        return FUNCS[node.func.id](*args)


# ---- steps ----------------------------------------------------------------------


def _cols(table: Table, text: str) -> list[str]:
    return [resolve_column(table, c.strip()) for c in text.split(",") if c.strip()]


def _pairs(text: str, sep: str) -> list[tuple[str, str]]:
    out = []
    for part in text.split(","):
        if sep not in part:
            raise DataError(f"expected 'a {sep} b' in '{part.strip()}'")
        left, right = part.split(sep, 1)
        out.append((left.strip(), right.strip().strip("'\"")))
    return out


def transform(table: Table, steps: list[str]) -> tuple[Table, list[str]]:
    """Applies the steps in order. Returns the new table and one line per step saying what changed."""
    current = Table(table.name, list(table.columns), [dict(r) for r in table.rows], types=dict(table.types))
    log: list[str] = []
    for raw in steps:
        text = str(raw).strip()
        if not text:
            continue
        name, _, arg = text.partition("|")
        name, arg = name.strip().lower(), arg.strip()
        if name not in STEPS:
            raise DataError(f"unknown step '{name}'. {STEP_HELP}")
        before = len(current.rows)
        current, note = STEP_FUNCS[name](current, arg)
        log.append(f"{name}: {note}" + (f" ({before:,} -> {len(current.rows):,} rows)" if len(current.rows) != before else ""))
    return current, log


def _select(t: Table, arg: str):
    cols = _cols(t, arg)
    if not cols:
        raise DataError("select needs column names")
    return _with(t, cols, [{c: r.get(c) for c in cols} for r in t.rows]), f"kept {', '.join(cols)}"


def _drop(t: Table, arg: str):
    gone = set(_cols(t, arg))
    cols = [c for c in t.columns if c not in gone]
    return _with(t, cols, [{c: r.get(c) for c in cols} for r in t.rows]), f"removed {', '.join(sorted(gone))}"


def _rename(t: Table, arg: str):
    mapping = {resolve_column(t, old): new for old, new in _pairs(arg, "->")}
    cols = [mapping.get(c, c) for c in t.columns]
    if len(set(cols)) != len(cols):
        raise DataError("rename would create two columns with the same name")
    rows = [{mapping.get(c, c): v for c, v in r.items()} for r in t.rows]
    out = _with(t, cols, rows)
    out.types = {mapping.get(c, c): k for c, k in t.types.items()}
    return out, ", ".join(f"{a} -> {b}" for a, b in mapping.items())


def _filter(t: Table, arg: str):
    kept = apply_filter(t, arg)
    return _with(t, t.columns, kept), f"kept rows where {arg}"


def _sort(t: Table, arg: str):
    rows = list(t.rows)
    keys = []
    for part in reversed([p.strip() for p in arg.split(",") if p.strip()]):
        words = part.split()
        descending = words[-1].lower() in {"desc", "descending"} if len(words) > 1 else False
        name = " ".join(words[:-1]) if len(words) > 1 and words[-1].lower() in {"asc", "desc", "ascending", "descending"} else part
        column = resolve_column(t, name)
        kind = t.types.get(column)

        def key(r, column=column, kind=kind):
            v = r.get(column)
            if v is None:
                return (1, 0)
            if kind == "number":
                return (0, _number(v) if _number(v) is not None else math.inf)
            if isinstance(v, datetime):
                return (0, v.timestamp())
            return (0, str(v).lower())

        present = [r for r in rows if r.get(column) is not None]
        missing = [r for r in rows if r.get(column) is None]
        present.sort(key=key, reverse=descending)
        rows = present + missing  # Blanks always last, like Excel.
        keys.append(f"{column} {'desc' if descending else 'asc'}")
    return _with(t, t.columns, rows), "by " + ", ".join(reversed(keys))


def _dedupe(t: Table, arg: str):
    cols = _cols(t, arg) if arg else t.columns
    seen, kept = set(), []
    for r in t.rows:
        key = tuple(str(_plain(r.get(c))).strip().lower() for c in cols)
        if key not in seen:
            seen.add(key)
            kept.append(r)
    return _with(t, t.columns, kept), f"removed {len(t.rows) - len(kept):,} duplicate(s)" + (f" by {', '.join(cols)}" if arg else "")


def _dropna(t: Table, arg: str):
    cols = _cols(t, arg) if arg else t.columns
    kept = [r for r in t.rows if all(r.get(c) is not None for c in cols)]
    return _with(t, t.columns, kept), f"removed rows missing {', '.join(cols) if arg else 'any value'}"


def _fill(t: Table, arg: str):
    notes = []
    rows = [dict(r) for r in t.rows]
    for name, how in _pairs(arg, "="):
        column = resolve_column(t, name)
        missing = sum(1 for r in rows if r.get(column) is None)
        mode = how.lower()
        numbers = [n for n in (_number(r.get(column)) for r in rows) if n is not None]
        if mode in {"mean", "median"}:
            if not numbers:
                raise DataError(f"'{column}' has no numbers to take the {mode} of")
            value = statistics.fmean(numbers) if mode == "mean" else statistics.median(numbers)
            value = round(value, 4)
        elif mode in {"zero", "0"}:
            value = 0
        elif mode in {"previous", "ffill", "forward"}:
            last = None
            for r in rows:
                if r.get(column) is None:
                    r[column] = last
                else:
                    last = r[column]
            notes.append(f"{column}: {missing:,} filled from the row above")
            continue
        else:
            value = _number(how) if _number(how) is not None and t.types.get(column) == "number" else how
        for r in rows:
            if r.get(column) is None:
                r[column] = value
        notes.append(f"{column}: {missing:,} filled with {value}")
    return _with(t, t.columns, rows), "; ".join(notes)


def _trim(t: Table, arg: str):
    cols = _cols(t, arg) if arg else [c for c in t.columns if t.types.get(c) == "text"]
    changed = 0
    rows = []
    for r in t.rows:
        r = dict(r)
        for c in cols:
            if isinstance(r.get(c), str):
                clean = " ".join(r[c].split())
                changed += clean != r[c]
                r[c] = clean
        rows.append(r)
    return _with(t, t.columns, rows), f"tidied spaces in {changed:,} value(s)"


def _case(t: Table, arg: str):
    rows = [dict(r) for r in t.rows]
    for name, how in _pairs(arg, "="):
        column, how = resolve_column(t, name), how.lower()
        if how not in {"upper", "lower", "title"}:
            raise DataError("case must be upper, lower or title")
        for r in rows:
            if isinstance(r.get(column), str):
                r[column] = getattr(r[column], how)()
    return _with(t, t.columns, rows), arg


def _replace(t: Table, arg: str):
    if ":" not in arg or "->" not in arg:
        raise DataError("replace needs 'column: old -> new'")
    name, rest = arg.split(":", 1)
    column = resolve_column(t, name.strip())
    old, new = (s.strip().strip("'\"") for s in rest.split("->", 1))
    count, rows = 0, []
    for r in t.rows:
        r = dict(r)
        if r.get(column) is not None and str(r[column]).lower() == old.lower():
            r[column], count = (new or None), count + 1
        rows.append(r)
    return _with(t, t.columns, rows), f"{column}: {count:,} value(s) '{old}' -> '{new}'"


def _compute(t: Table, arg: str):
    if "=" not in arg:
        raise DataError("compute needs 'new_column = formula', e.g. total = units * unit_price")
    name, expression = (s.strip() for s in arg.split("=", 1))
    name = name.strip("`")
    formula = Formula(expression, t.columns)
    rows = [{**r, name: formula.evaluate(r)} for r in t.rows]
    cols = t.columns + ([name] if name not in t.columns else [])
    out = _with(t, cols, rows)
    out.types[name] = "number"
    blanks = sum(1 for r in rows if r[name] is None)
    return out, f"{name} = {expression}" + (f" ({blanks:,} blank where inputs were missing)" if blanks else "")


def _type(t: Table, arg: str):
    rows = [dict(r) for r in t.rows]
    out_types = dict(t.types)
    notes = []
    for name, kind in _pairs(arg, "="):
        column, kind = resolve_column(t, name), kind.lower()
        failed = 0
        for r in rows:
            v = r.get(column)
            if v is None:
                continue
            if kind == "number":
                r[column] = _number(v)
            elif kind == "date":
                r[column] = dataviz._parse_date(v)
            elif kind == "text":
                r[column] = _text(v)
            else:
                raise DataError("type must be number, text or date")
            failed += r[column] is None
        out_types[column] = kind
        notes.append(f"{column} -> {kind}" + (f" ({failed:,} could not be converted and are now blank)" if failed else ""))
    out = _with(t, t.columns, rows)
    out.types = out_types
    return out, "; ".join(notes)


def _extract(t: Table, arg: str):
    if "->" not in arg:
        raise DataError("extract needs 'date_column -> year, month, quarter, weekday'")
    name, parts = arg.split("->", 1)
    column = resolve_column(t, name.strip())
    wanted = [p.strip().lower() for p in parts.split(",") if p.strip()]
    makers = {"year": lambda d: d.year, "month": lambda d: d.month, "quarter": lambda d: (d.month - 1) // 3 + 1,
              "weekday": lambda d: d.strftime("%A"), "day": lambda d: d.day, "week": lambda d: d.isocalendar()[1],
              "month_name": lambda d: d.strftime("%B"), "yearmonth": lambda d: d.strftime("%Y-%m")}
    bad = [w for w in wanted if w not in makers]
    if bad:
        raise DataError(f"can extract: {', '.join(makers)}")
    rows, cols = [], list(t.columns)
    for part in wanted:
        if f"{column}_{part}" not in cols:
            cols.append(f"{column}_{part}")
    for r in t.rows:
        r = dict(r)
        d = r.get(column) if isinstance(r.get(column), datetime) else dataviz._parse_date(r.get(column)) if r.get(column) else None
        for part in wanted:
            r[f"{column}_{part}"] = makers[part](d) if d else None
        rows.append(r)
    return _with(t, cols, rows), f"added {', '.join(f'{column}_{p}' for p in wanted)}"


def _aggregate(t: Table, arg: str):
    groups_text, _, metrics_text = arg.partition("|")
    group_cols = _cols(t, groups_text) if groups_text.strip() else []
    metrics = []
    for part in [p.strip() for p in (metrics_text or "*:count").split(",") if p.strip()]:
        name, _, agg = part.partition(":")
        agg = (agg or "sum").strip().lower()
        if agg not in dataviz.AGGREGATES:
            raise DataError(f"unknown aggregate '{agg}'. Use: {', '.join(sorted(dataviz.AGGREGATES))}")
        column = None if name.strip() in {"*", ""} else resolve_column(t, name.strip())
        if column is None and agg != "count":
            raise DataError("'*' only works with count")
        metrics.append((column, agg, f"{agg}_{column}" if column else "count"))
    buckets: dict[tuple, list[dict]] = defaultdict(list)
    for r in t.rows:
        buckets[tuple(dataviz._group_key(r.get(c)) for c in group_cols)].append(r)
    rows = []
    for key, members in buckets.items():
        out = dict(zip(group_cols, key))
        for column, agg, label in metrics:
            if column is None:
                out[label] = len(members)
                continue
            values = [n for n in (_number(m.get(column)) for m in members) if n is not None]
            out[label] = round(float(dataviz.REDUCERS[agg](values)), 6) if values else None
        rows.append(out)
    first = metrics[0][2]
    rows.sort(key=lambda r: -(r.get(first) or 0))
    cols = group_cols + [m[2] for m in metrics]
    return retype(cols, rows, t.name), f"grouped by {', '.join(group_cols) or 'everything'}: {len(rows):,} group(s)"


def _limit(t: Table, arg: str):
    try:
        n = max(0, int(arg))
    except ValueError as exc:
        raise DataError("limit needs a number") from exc
    return _with(t, t.columns, t.rows[:n]), f"first {n:,} rows"


def _with(t: Table, columns: list[str], rows: list[dict]) -> Table:
    return Table(t.name, list(columns), rows, types={c: t.types.get(c, "text") for c in columns})


STEP_FUNCS = {"select": _select, "drop": _drop, "rename": _rename, "filter": _filter, "sort": _sort, "dedupe": _dedupe,
              "dropna": _dropna, "fill": _fill, "trim": _trim, "case": _case, "replace": _replace, "compute": _compute,
              "type": _type, "extract": _extract, "aggregate": _aggregate, "limit": _limit}


# ---- merging ------------------------------------------------------------------------

HOW = ("inner", "left", "right", "outer", "append")


def _key(value) -> str:
    number = _number(value)
    if number is not None:
        return repr(float(number))
    return str(_plain(value)).strip().lower()


def merge(left: Table, right: Table, how: str = "left", on: str | None = None) -> tuple[Table, list[str]]:
    """Joins two tables on key columns ('id' or 'customer_id = id'), or stacks them with how='append'."""
    how = (how or "left").lower()
    if how not in HOW:
        raise DataError(f"how must be one of: {', '.join(HOW)}")
    if how == "append":
        cols = list(left.columns) + [c for c in right.columns if c not in left.columns]
        rows = [{c: r.get(c) for c in cols} | {"source_file": left.name} for r in left.rows]
        rows += [{c: r.get(c) for c in cols} | {"source_file": right.name} for r in right.rows]
        cols.append("source_file")
        new = [c for c in right.columns if c not in left.columns]
        notes = [f"stacked {len(left.rows):,} + {len(right.rows):,} rows"]
        if new:
            notes.append(f"columns only in {right.name}: {', '.join(new)} (blank for {left.name} rows)")
        return retype(cols, rows[:dataviz.MAX_ROWS], f"{left.name}+{right.name}"), notes

    if not on:
        shared = [c for c in left.columns if c in right.columns]
        if not shared:
            raise DataError("the files share no column name; pass on, e.g. 'customer_id = id'")
        ids = [c for c in shared if "id" in c.lower()] or shared[:1]
        on = ids[0]
    pairs = []
    for part in on.split(","):
        a, _, b = part.partition("=")
        pairs.append((resolve_column(left, a.strip()), resolve_column(right, (b or a).strip())))
    left_keys, right_keys = [a for a, _ in pairs], [b for _, b in pairs]
    extra = [c for c in right.columns if c not in right_keys]
    renamed = {c: (f"{c}_{re.sub(r'[^A-Za-z0-9]+', '_', right.name.rsplit('.', 1)[0])}" if c in left.columns else c) for c in extra}
    index: dict[tuple, list[dict]] = defaultdict(list)
    for r in right.rows:
        key = tuple(_key(r.get(c)) for c in right_keys)
        if all(r.get(c) is not None for c in right_keys):
            index[key].append(r)
    cols = list(left.columns) + [renamed[c] for c in extra]
    rows, matched_right, unmatched_left = [], set(), 0
    for r in left.rows:
        key = tuple(_key(r.get(c)) for c in left_keys)
        partners = index.get(key, []) if all(r.get(c) is not None for c in left_keys) else []
        if partners:
            matched_right.add(key)
            for p in partners:
                rows.append({**r, **{renamed[c]: p.get(c) for c in extra}})
        else:
            unmatched_left += 1
            if how in {"left", "outer"}:
                rows.append({**r, **{renamed[c]: None for c in extra}})
        if len(rows) > dataviz.MAX_ROWS:
            raise DataError(f"the join produces more than {dataviz.MAX_ROWS:,} rows; check the key columns")
    unmatched_right = 0
    if how in {"right", "outer"}:
        for key, partners in index.items():
            if key in matched_right:
                continue
            for p in partners:
                unmatched_right += 1
                row = {c: None for c in cols}
                for a, b in pairs:
                    row[a] = p.get(b)
                row.update({renamed[c]: p.get(c) for c in extra})
                rows.append(row)
    clash = [f"{c} -> {renamed[c]}" for c in extra if renamed[c] != c]
    notes = [f"{how} join on {', '.join(a if a == b else f'{a} = {b}' for a, b in pairs)}: {len(rows):,} rows",
             f"{len(left.rows) - unmatched_left:,} of {len(left.rows):,} rows in {left.name} found a match",
             f"{unmatched_left:,} rows in {left.name} had no match" + ("" if how in {"left", "outer"} else " (dropped)")]
    if how in {"right", "outer"}:
        notes.append(f"{unmatched_right:,} rows from {right.name} had no match (kept)")
    if any(len(v) > 1 for v in index.values()):
        notes.append(f"some keys appear more than once in {right.name}, so matching rows were repeated")
    if clash:
        notes.append("renamed to avoid clashes: " + ", ".join(clash))
    return retype(cols, rows, left.name), notes


def preview_rows(table: Table, limit: int = 5) -> str:
    lines = [", ".join(table.columns)]
    for r in table.rows[:limit]:
        lines.append(", ".join("" if r.get(c) is None else _text(r.get(c)) for c in table.columns))
    return "\n".join(lines)
