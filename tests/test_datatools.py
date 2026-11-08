"""Excel reading and writing, transforms, safe formulas, merges, and the new #data tools."""

import asyncio
from datetime import datetime
from io import BytesIO

import pytest
from openpyxl import Workbook, load_workbook

from core import datatools, dataviz, excel
from core.adapters import data as data_adapter
from core.permissions import PermissionManager
from core.tools import ToolContext

CSV = """id,date,region,product,units,unit_price,revenue
1,2025-01-03, north ,Laptop,2,950,1900
2,2025-01-09,South,Phone,1,600,600
3,2025-02-11,North,Phone,3,600,1800
4,2025-02-20,West,Laptop,1,950,
5,2025-03-02,South,Tablet,4,400,1600
5,2025-03-02,South,Tablet,4,400,1600
"""


@pytest.fixture
def table(tmp_path):
    path = tmp_path / "orders.csv"
    path.write_text(CSV)
    return dataviz.load_table(path)


def make_xlsx(path, sheets):
    book = Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append(row)
    book.save(path)


def test_reads_excel_with_types_and_sheets(tmp_path):
    path = tmp_path / "book.xlsx"
    make_xlsx(path, {
        "Empty": [],
        "Sales": [[None], ["Region", "When", "Revenue", None], ["North", datetime(2025, 1, 2), 10.5], ["South", datetime(2025, 2, 3), 7]],
        "Other": [["a", "b"], [1, 2]],
    })
    table = dataviz.load_table(path)
    assert table.sheet == "Sales" and table.sheets == ["Empty", "Sales", "Other"]
    assert table.columns == ["Region", "When", "Revenue"]
    assert table.types == {"Region": "text", "When": "date", "Revenue": "number"}
    assert "other sheets: Empty, Other" in dataviz.describe_text(table)
    assert dataviz.load_table(path, "other").columns == ["a", "b"]
    with pytest.raises(dataviz.DataError, match="no sheet"):
        dataviz.load_table(path, "nope")
    (tmp_path / "fake.xlsx").write_text("not a zip")
    with pytest.raises(dataviz.DataError, match="Excel"):
        dataviz.load_table(tmp_path / "fake.xlsx")


def test_cleaning_steps(table):
    out, log = datatools.transform(table, ["trim", "case | region = title", "dedupe", "fill | revenue = median"])
    assert len(out.rows) == 5 and {r["region"] for r in out.rows} == {"North", "South", "West"}
    assert out.rows[3]["revenue"] == 1700  # Median of 1900, 600, 1800, 1600.
    assert any("removed 1 duplicate" in line for line in log)


def test_reshaping_steps(table):
    out, _ = datatools.transform(table, [
        "filter | units >= 2", "compute | value = units * unit_price", "sort | value desc",
        "select | id, region, value", "rename | value -> sales", "limit | 2",
    ])
    assert out.columns == ["id", "region", "sales"] and [r["sales"] for r in out.rows] == [1900, 1800]
    out, _ = datatools.transform(table, ["dedupe", "aggregate | product | revenue:sum, *:count"])
    assert {r["product"]: r["sum_revenue"] for r in out.rows} == {"Laptop": 1900, "Phone": 2400, "Tablet": 1600}
    out, _ = datatools.transform(table, ["extract | date -> year, quarter, weekday"])
    assert out.rows[0]["date_year"] == 2025 and out.rows[3]["date_quarter"] == 1 and out.rows[0]["date_weekday"] == "Friday"
    out, _ = datatools.transform(table, ["replace | product: Phone -> Mobile", "type | units = text"])
    assert out.rows[1]["product"] == "Mobile" and out.types["units"] == "text"


def test_formulas_are_arithmetic_only(table):
    out, _ = datatools.transform(table, ["compute | avg = round(revenue / units, 1)", "compute | `big one` = units ** 2 + 1"])
    assert out.rows[0]["avg"] == 950 and out.rows[0]["big one"] == 5 and out.rows[3]["avg"] is None
    for bad in ["x = __import__('os').system('echo hi')", "x = units.__class__", "x = open('f')", "x = [1][0]",
                "x = units if units else 0", "x = lambda: 1", "x = nope * 2"]:
        with pytest.raises(dataviz.DataError):
            datatools.transform(table, [f"compute | {bad}"])
    with pytest.raises(dataviz.DataError, match="unknown step"):
        datatools.transform(table, ["explode | everything"])


def test_merge_join_and_append(tmp_path):
    (tmp_path / "orders.csv").write_text("order,customer_id,amount\n1,10,5\n2,11,7\n3,99,1\n")
    (tmp_path / "customers.csv").write_text("id,name,amount\n10,Ann,x\n11,Bo,y\n12,Cy,z\n")
    orders, customers = (dataviz.load_table(tmp_path / f) for f in ("orders.csv", "customers.csv"))
    out, notes = datatools.merge(orders, customers, "left", "customer_id = id")
    assert [r["name"] for r in out.rows] == ["Ann", "Bo", None] and "amount_customers" in out.columns
    assert "2 of 3 rows" in notes[1]
    out, _ = datatools.merge(orders, customers, "inner", "customer_id = id")
    assert len(out.rows) == 2
    out, _ = datatools.merge(orders, customers, "outer", "customer_id = id")
    assert len(out.rows) == 4
    stacked, _ = datatools.merge(orders, orders, "append")
    assert len(stacked.rows) == 6 and stacked.columns[-1] == "source_file"


def test_render_formats(table):
    assert datatools.render(table.columns, table.rows, ".csv").splitlines()[1].startswith("1,2025-01-03,north,Laptop")
    assert '"revenue": 1900' in datatools.render(table.columns, table.rows[:1], ".json", table)
    book = load_workbook(BytesIO(datatools.render(table.columns, table.rows, ".xlsx", table)))
    sheet = book.active
    assert sheet["A1"].value == "id" and sheet["G2"].value == 1900 and isinstance(sheet["B2"].value, datetime)
    assert sheet.tables and sheet.freeze_panes == "A2"
    with pytest.raises(dataviz.DataError):
        datatools.render(table.columns, table.rows, ".exe")


def test_excel_report_has_requested_sheets_and_charts(tmp_path):
    path = tmp_path / "big.csv"
    lines = ["date,region,product,units,revenue"] + [
        f"2025-{m:02d}-1{d},{['North', 'South', 'East'][d % 3]},{['A', 'B'][m % 2]},{m + d},{(m + d) * 10}" for m in range(1, 13) for d in range(3)]
    path.write_text("\n".join(lines))
    table = dataviz.load_table(path)
    specs = dataviz.parse_chart_specs(["bar | Revenue by region | region | revenue", "line | Monthly | date | revenue",
                                       "donut | Share | product | revenue", "stacked | Mix | region | revenue | sum | product",
                                       "scatter | Units vs revenue | units | revenue", "heatmap | Matrix | region | revenue | sum | product"])
    content, names, notes = excel.build_report(table, "Sales", ["pivot | region | product | revenue | sum", "group | region | revenue",
                                                                "trend | revenue | sum | quarter", "quality", "pivot | nope | x"],
                                               specs, split_by="region")
    assert names[:2] == ["Summary", "Data"] and {"Trend", "Quality", "Charts", "North", "South", "East"} <= set(names)
    assert any("nope" in n for n in notes) and not any(n.startswith("Pivot nope") for n in names)
    book = load_workbook(BytesIO(content))
    assert len(book["Charts"]._charts) == 5  # Heatmap becomes a coloured table, not a chart.
    pivot = book[[n for n in names if n.startswith("Pivot")][0]]
    assert str(pivot.cell(row=4, column=4).value).startswith("=SUM(")  # Live totals.
    with pytest.raises(dataviz.DataError, match="bad sheet"):
        excel.build_report(table, "x", ["chartz"])


def test_sheet_titles_are_valid():
    taken = set()
    assert excel.sheet_title("a/b:c*d?[e]", taken) == "a-b-c-d--e-"
    long = "x" * 40
    assert len(excel.sheet_title(long, taken)) == 31 and excel.sheet_title(long, taken).endswith("(2)")


def test_new_tools_through_guard(app, settings):
    project = settings.workspace_root / "projects" / "demo"
    (project / "data").mkdir()
    (project / "data" / "orders.csv").write_text(CSV)
    (project / "data" / "regions.csv").write_text("region,manager\nNorth,Ann\nSouth,Bo\nWest,Cy\n")
    ctx = ToolContext(settings, PermissionManager(settings.arch_dir / "permission.md"), ["./workspace/projects"], None)
    run = lambda fn, args: asyncio.run(fn(ctx, args))
    src = "projects/demo/data/orders.csv"

    preview = data_adapter._transform_preview(ctx, {"path": src, "steps": ["trim", "dedupe"]})
    assert "orders_clean.csv" in preview and "removed 1 duplicate" in preview
    result = run(data_adapter.transform_data, {"path": src, "steps": ["trim", "dedupe"]})
    assert result.changed_paths == ["projects/demo/data/orders_clean.csv"] and (project / "data/orders.csv").read_text() == CSV
    with pytest.raises(Exception, match="already exists"):
        run(data_adapter.transform_data, {"path": src, "steps": ["trim"]})
    with pytest.raises(Exception, match="cannot be the source"):
        run(data_adapter.transform_data, {"path": src, "output": src, "overwrite": True})

    converted = run(data_adapter.transform_data, {"path": src, "output": "projects/demo/data/orders.xlsx"})
    assert (project / "data/orders.xlsx").read_bytes()[:2] == b"PK" and "6 rows" in converted.output
    assert "5 rows" in run(data_adapter.profile_data, {"path": "projects/demo/data/orders_clean.csv"}).output
    assert "orders.xlsx" in run(data_adapter.profile_data, {"path": "projects/demo/data/orders.xlsx"}).output

    merged = run(data_adapter.merge_data, {"path": src, "other_path": "projects/demo/data/regions.csv", "on": "region"})
    assert "orders_regions_merged.csv" in merged.changed_paths[0]

    report = run(data_adapter.create_excel, {"path": src, "title": "Orders review", "sheets": ["group | region | revenue"],
                                             "charts": ["bar | Revenue | region | revenue"]})
    assert report.changed_paths == ["projects/demo/reports/orders_review.xlsx"] and "By region" in report.output
    with pytest.raises(Exception):
        run(data_adapter.transform_data, {"path": src, "output": "../outside.csv"})
