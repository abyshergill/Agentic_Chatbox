import asyncio
import pytest

from core import dataviz
from core.adapters import data as data_adapter
from core.tools import ToolContext

CSV = """order_id,date,region,product,units,revenue
1,2025-01-03,North,Laptop,2,"1,900"
2,2025-01-09,South,Phone,1,600
3,2025-02-11,North,Phone,3,1800
4,2025-02-20,West,Laptop,1,
5,2025-03-02,South,Tablet,4,1600
"""


@pytest.fixture
def table(tmp_path):
    path = tmp_path / "orders.csv"
    path.write_text(CSV)
    return dataviz.load_table(path)


def test_types_and_missing(table):
    assert table.types["revenue"] == "number" and table.types["date"] == "date" and table.types["region"] == "text"
    info = {c["name"]: c for c in dataviz.describe(table)["columns"]}
    assert info["revenue"]["missing"] == 1 and info["revenue"]["max"] == 1900  # "1,900" parsed


def test_aggregate_filter_and_errors(table):
    assert dict(dataviz.aggregate(table, "region", "revenue", "sum")) == {"North": 3700, "South": 2200}
    assert dataviz.aggregate(table, "product", None, "count", where="region == North")[0][1] == 1
    assert dataviz.aggregate(table, "region", "units", "sum", where="units > 1")[0] == ("North", 5.0)
    with pytest.raises(dataviz.DataError, match="no column"):
        dataviz.aggregate(table, "nope", "revenue")
    with pytest.raises(dataviz.DataError, match="aggregate"):
        dataviz.aggregate(table, "region", "revenue", "bogus")


def test_dashboard_is_script_free_and_escaped(table, tmp_path):
    table.rows[0]["region"] = "<script>alert(1)</script>"
    html, notes = dataviz.build_dashboard(table, "T <b>", None)
    assert "<script" not in html.lower().replace("&lt;script", "") and "<svg" in html and notes == []
    assert "T &lt;b&gt;" in html


def test_bad_chart_is_reported_not_fatal(table):
    html, notes = dataviz.build_dashboard(table, "t", dataviz.parse_chart_specs(["bar | x | nope | revenue | sum"]))
    assert notes and "Could not draw" in html
    with pytest.raises(dataviz.DataError):
        dataviz.parse_chart_specs(["spiral | x"])


def test_json_and_unsupported(tmp_path):
    (tmp_path / "a.json").write_text('{"rows": [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]}')
    assert len(dataviz.load_table(tmp_path / "a.json").rows) == 2
    (tmp_path / "a.bin").write_text("x")
    with pytest.raises(dataviz.DataError):
        dataviz.load_table(tmp_path / "a.bin")


def test_tools_through_guard(app, settings):
    from core.permissions import PermissionManager

    project = settings.workspace_root / "projects" / "demo"
    (project / "data").mkdir()
    (project / "data" / "orders.csv").write_text(CSV)
    tool_ctx = ToolContext(settings, PermissionManager(settings.arch_dir / "permission.md"), ["./workspace/projects"], None)
    run = lambda fn, args: asyncio.run(fn(tool_ctx, args))

    assert "5 rows" in run(data_adapter.profile_data, {"path": "projects/demo/data/orders.csv"}).output
    assert "North" in run(data_adapter.query_data, {"path": "projects/demo/data/orders.csv", "group_by": "region", "metric": "revenue"}).output
    with pytest.raises(Exception):  # Outside the workspace is refused by the guard.
        run(data_adapter.profile_data, {"path": "../outside.txt"})

    result = run(data_adapter.create_dashboard, {"path": "projects/demo/data/orders.csv", "title": "Orders"})
    assert result.changed_paths == ["projects/demo/dashboards/orders.html"]
    assert (project / "dashboards" / "orders.html").read_text().startswith("<!doctype html>")


def test_projects_and_dashboard_endpoints(client, settings):
    sid = "testsession01"
    created = client.post("/api/projects", json={"session_id": sid, "name": "Analysis One"})
    assert created.status_code == 200
    assert client.post("/api/projects", json={"session_id": sid, "name": "Analysis One"}).status_code == 409
    assert client.post("/api/projects", json={"session_id": sid, "name": "../evil"}).status_code == 400
    names = [p["name"] for p in client.get("/api/projects", params={"session_id": sid}).json()["projects"]]
    assert "Analysis One" in names and "demo" in names

    page = settings.workspace_root / "projects" / "demo" / "dashboards"
    page.mkdir()
    (page / "d.html").write_text("<h1>hi</h1>")
    ok = client.get("/api/dashboards/view", params={"session_id": sid, "path": "projects/demo/dashboards/d.html"})
    assert ok.status_code == 200 and "script-src" not in ok.headers["content-security-policy"]
    assert client.get("/api/dashboards/view", params={"session_id": sid, "path": "projects/demo/hello.py"}).status_code == 404
    assert client.get("/api/dashboards/view", params={"session_id": sid, "path": "../outside.txt"}).status_code in (403, 404)


def test_mkdir_and_upload_into_folder_without_attaching(client, settings):
    sid = "testsession01"
    made = client.post("/api/files/mkdir", json={"session_id": sid, "path": "projects/demo/data/raw/2025"})
    assert made.status_code == 200 and made.json()["created"]
    assert (settings.workspace_root / "projects/demo/data/raw/2025").is_dir()
    assert client.post("/api/files/mkdir", json={"session_id": sid, "path": "projects/demo/data/raw/2025"}).json()["created"] is False
    for bad in ["projects/demo/../../x", "../outside", "projects/demo/.git", "projects/demo/a<b"]:
        assert client.post("/api/files/mkdir", json={"session_id": sid, "path": bad}).status_code in (400, 403), bad

    up = client.post("/api/files/upload", data={"session_id": sid, "target_dir": "projects/demo/data/raw", "attach": "false"},
                     files={"file": ("a.csv", "x,y\n1,2\n")})
    assert up.status_code == 200 and up.json()["saved_to"] == "projects/demo/data/raw/a.csv" and up.json()["id"] is None
    assert (settings.workspace_root / "projects/demo/data/raw/a.csv").read_text() == "x,y\n1,2\n"
    assert client.get("/api/session", params={"session_id": sid}).json()["attachments"] == []


# ---- new charts, analyses and presentations ----------------------------------

import random

from core import analysis, presentation


@pytest.fixture
def big(tmp_path):
    rng = random.Random(7)
    lines = ["order_id,date,region,product,units,unit_price,revenue"]
    for i in range(1, 121):
        units, price = rng.randint(1, 9), rng.choice([90, 400, 950])
        region = rng.choice(["North", "South", "East"])
        lines.append(f"{i},2025-{(i % 12) + 1:02d}-{(i % 27) + 1:02d},{region},{ {90: 'Headset', 400: 'Tablet', 950: 'Laptop'}[price]},"
                     f"{units},{price},{units * price}")
    lines.append("121,2025-12-30,north,Laptop,90,950,85500")  # A spelling variant and an extreme value.
    lines.append(lines[1].replace("1,", "1,", 1))  # Exact duplicate of the first row.
    path = tmp_path / "orders.csv"
    path.write_text("\n".join(lines) + "\n")
    return dataviz.load_table(path)


ALL_KINDS = [
    "bar | a | region | revenue", "line | b | date | revenue", "area | c | date | revenue", "pie | d | region | revenue",
    "donut | e | region | revenue", "histogram | f | revenue", "scatter | g | unit_price | revenue",
    "box | h | region | revenue", "heatmap | i | region | revenue | sum | product", "heatmap | j",
    "stacked | k | region | revenue | sum | product", "grouped | l | region | units | sum | product",
    "treemap | m | product | revenue",
]


def test_every_chart_kind_draws_with_a_caption(big):
    specs = dataviz.parse_chart_specs(ALL_KINDS)
    assert {s.kind for s in specs} == dataviz.CHART_KINDS
    for spec in specs:
        data = dataviz.prepare_chart(big, spec)
        svg = dataviz.draw_chart(data)
        assert svg.startswith("<svg") and svg.endswith("</svg>"), spec.kind
        assert dataviz.chart_insight(data), spec.kind


def test_chart_aliases_and_errors(big):
    assert dataviz.parse_chart_specs(["boxplot | x | region | revenue"])[0].kind == "box"
    assert dataviz.parse_chart_specs(["doughnut | x | region | revenue"])[0].kind == "donut"
    with pytest.raises(dataviz.DataError, match="series"):
        dataviz.prepare_chart(big, dataviz.parse_chart_specs(["stacked | x | region | revenue"])[0])


def test_box_stats_and_crosstab(big):
    stats = dataviz.box_stats([1, 2, 3, 4, 100])
    assert stats["median"] == 3 and stats["outliers"] == [100] and stats["high"] == 4
    rows, cols, cells = dataviz.crosstab(big, "region", "product", "revenue", "sum")
    assert "North" in rows and "Laptop" in cols
    expected = sum(r["units"] and float(r["revenue"]) for r in big.rows if r["region"] == "North" and r["product"] == "Laptop")
    assert cells[("North", "Laptop")] == pytest.approx(expected)


def test_treemap_layout_fills_the_area():
    rects = dataviz.squarify([("a", 6), ("b", 6), ("c", 4), ("d", 3), ("e", 2), ("f", 2), ("g", 1)], 0, 0, 600, 400)
    assert len(rects) == 7
    assert sum(w * h for *_, w, h in rects) == pytest.approx(600 * 400)
    assert all(0 <= x and x + w <= 600.001 and 0 <= y and y + h <= 400.001 for _, _, x, y, w, h in rects)


def test_quality_report_finds_problems(big):
    text = analysis.run(big, "quality", {})
    assert "Duplicate rows: 1" in text and "north" in text and "outliers" in text


def test_other_analyses(big):
    assert "row 121" in analysis.run(big, "outliers", {"column": "revenue"})
    assert "revenue" in analysis.run(big, "correlations", {}) and "Strongest pairs" in analysis.run(big, "correlations", {})
    pivot = analysis.run(big, "pivot", {"group_by": "region", "pivot_by": "product", "metric": "revenue"})
    assert "Laptop" in pivot and "total" in pivot
    trend = analysis.run(big, "trend", {"metric": "revenue", "period": "quarter"})
    assert "2025-Q1" in trend and "Direction" in trend
    compare = analysis.run(big, "compare", {"group_by": "region", "metric": "revenue"})
    assert "(all)" in compare and "Caution" in compare  # 'north' has a single row.
    assert "p95" in analysis.run(big, "distribution", {"column": "units"})
    with pytest.raises(dataviz.DataError, match="needs 'group_by'"):
        analysis.run(big, "compare", {"metric": "revenue"})
    with pytest.raises(dataviz.DataError, match="unknown analysis"):
        analysis.run(big, "magic", {})


def test_presentation_html_is_script_free(big):
    deck = presentation.build_deck(big, "Deck <b>", None, ["Finding <script>x</script>"])
    html = presentation.render_html(deck)
    assert "<script" not in html and "&lt;script&gt;" in html and "Deck &lt;b&gt;" in html
    assert html.count('class="slide') == 4 + len(deck.charts)


@pytest.mark.skipif(not presentation.HAS_PPTX, reason="python-pptx not installed")
def test_presentation_pptx_has_every_slide(big):
    from io import BytesIO
    from pptx import Presentation

    specs = dataviz.parse_chart_specs(ALL_KINDS[:6] + ALL_KINDS[7:])  # Every kind, scatter last-but-one.
    deck = presentation.build_deck(big, "Deck", specs, ["One", "Two"])
    prs = Presentation(BytesIO(presentation.render_pptx(deck)))
    assert len(prs.slides) == 4 + len(specs)


def test_analysis_and_presentation_tools(app, settings):
    from core.permissions import PermissionManager

    project = settings.workspace_root / "projects" / "demo"
    (project / "data").mkdir()
    (project / "data" / "orders.csv").write_text(CSV)
    tool_ctx = ToolContext(settings, PermissionManager(settings.arch_dir / "permission.md"), ["./workspace/projects"], None)
    run = lambda fn, args: asyncio.run(fn(tool_ctx, args))
    path = "projects/demo/data/orders.csv"

    assert "Duplicate rows" in run(data_adapter.analyze_data, {"path": path, "analysis": "quality"}).output
    preview = data_adapter._deck_preview(tool_ctx, {"path": path, "title": "Orders"})
    assert "presentations/orders.html" in preview
    result = run(data_adapter.create_presentation, {"path": path, "title": "Orders", "format": "html",
                                                     "findings": ["North leads"]})
    assert result.changed_paths == ["projects/demo/presentations/orders.html"]
    assert "North leads" in (project / "presentations" / "orders.html").read_text()
    if presentation.HAS_PPTX:
        result = run(data_adapter.create_presentation, {"path": path, "title": "Orders", "format": "pptx"})
        assert (project / "presentations" / "orders.pptx").read_bytes()[:2] == b"PK"
