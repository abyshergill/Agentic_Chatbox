"""Reading the file formats people actually have: CSV in any encoding, old and new Excel, OpenDocument, Parquet,
PowerPoint, notebooks; relative paths inside the selected project; and data files attached to the chat."""

import asyncio
import json

import pytest

from core import dataviz
from core.adapters import data as data_adapter
from core.adapters import filesystem as fs_adapter
from core.arch_loader import load_registry
from core.documents import extract_text
from core.permissions import PermissionManager
from core.sessions import Session
from core.textcodec import decode, looks_binary
from core.tools import ToolContext
from core.uploads import Attachment, validate_content

ROWS = "region,city,sales\nNorth,Zürich,10\nSouth,Malmö,20\nNorth,São Paulo,5\n"


def ctx_for(settings, active_path=None, session=None):
    return ToolContext(settings, PermissionManager(settings.arch_dir / "permission.md"), ["./workspace/projects"],
                       session, active_path=active_path)


def run(fn, ctx, args):
    return asyncio.run(fn(ctx, args))


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp1252", "utf-16", "utf-16-le"])
def test_csv_in_any_encoding(tmp_path, encoding):
    path = tmp_path / "sales.csv"
    path.write_bytes(ROWS.encode(encoding))
    table = dataviz.load_table(path)
    assert table.columns == ["region", "city", "sales"]
    assert [r["city"] for r in table.rows] == ["Zürich", "Malmö", "São Paulo"]
    assert table.types["sales"] == "number"
    text, _ = extract_text(path, 10_000)
    assert "Zürich" in text and not looks_binary(path.read_bytes())


def test_semicolon_tab_pipe_and_txt_tables(tmp_path):
    (tmp_path / "eu.csv").write_text(ROWS.replace(",", ";"))
    (tmp_path / "t.tsv").write_text(ROWS.replace(",", "\t"))
    (tmp_path / "p.psv").write_text(ROWS.replace(",", "|"))
    (tmp_path / "export.txt").write_text(ROWS.replace(",", "\t"))
    for name in ["eu.csv", "t.tsv", "p.psv", "export.txt"]:
        assert dataviz.load_table(tmp_path / name).columns == ["region", "city", "sales"], name


def test_ods_parquet_and_ndjson(tmp_path):
    from odf.opendocument import OpenDocumentSpreadsheet
    from odf.table import Table, TableCell, TableRow
    from odf.text import P

    doc = OpenDocumentSpreadsheet()
    sheet = Table(name="Sales")
    for values in [["region", "sales"], ["North", 10], ["South", 20.5]]:
        row = TableRow()
        for value in values:
            cell = TableCell(valuetype="float", value=value) if isinstance(value, (int, float)) else TableCell(valuetype="string")
            cell.addElement(P(text=str(value)))
            row.addElement(cell)
        sheet.addElement(row)
    doc.spreadsheet.addElement(sheet)
    doc.save(str(tmp_path / "s.ods"))
    table = dataviz.load_table(tmp_path / "s.ods")
    assert table.sheet == "Sales" and [r["sales"] for r in table.rows] == [10, 20.5]
    assert "North" in extract_text(tmp_path / "s.ods", 10_000)[0]

    import pyarrow as pa
    import pyarrow.parquet as pq

    pq.write_table(pa.table({"region": ["North", "South"], "sales": [1, 2]}), tmp_path / "s.parquet")
    assert [r["sales"] for r in dataviz.load_table(tmp_path / "s.parquet").rows] == [1, 2]
    assert "South" in extract_text(tmp_path / "s.parquet", 10_000)[0]

    (tmp_path / "s.ndjson").write_text('{"a": 1}\n{"a": 2}\n')
    assert len(dataviz.load_table(tmp_path / "s.ndjson").rows) == 2


def test_pptx_and_notebook_text(tmp_path):
    from pptx import Presentation

    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[1])
    slide.shapes.title.text = "Quarterly results"
    slide.placeholders[1].text = "Revenue grew 12%"
    deck.save(tmp_path / "d.pptx")
    text, _ = extract_text(tmp_path / "d.pptx", 10_000)
    assert "Quarterly results" in text and "Revenue grew 12%" in text

    notebook = {"cells": [{"cell_type": "markdown", "source": ["# Title"]},
                          {"cell_type": "code", "source": ["print(1)"], "outputs": [{"text": ["1\n"]}]}]}
    (tmp_path / "n.ipynb").write_text(json.dumps(notebook))
    text, _ = extract_text(tmp_path / "n.ipynb", 10_000)
    assert "# Title" in text and "print(1)" in text and "[output]" in text


def test_uploads_accept_common_formats_and_encodings():
    assert validate_content("sales.csv", ROWS.encode("cp1252")) == "text"
    assert validate_content("sales.txt", ROWS.encode("utf-16")) == "text"
    assert validate_content("old.xls", b"\xd0\xcf\x11\xe0" + b"\x00" * 20) == "excel"
    assert validate_content("s.ods", b"PK\x03\x04rest") == "excel"
    assert validate_content("d.pptx", b"PK\x03\x04rest") == "document"
    assert validate_content("t.parquet", b"PAR1....") == "data"
    from core.uploads import UploadRejected
    with pytest.raises(UploadRejected):
        validate_content("fake.xls", b"hello")
    with pytest.raises(UploadRejected):
        validate_content("x.csv", b"\x89PNG\r\n\x1a\n\x00\x00\x00")


def test_paths_relative_to_the_selected_project(settings):
    project = settings.workspace_root / "projects" / "demo"
    (project / "data").mkdir()
    (project / "data" / "orders.csv").write_text(ROWS)
    (project / "top.csv").write_text(ROWS)
    ctx = ctx_for(settings, active_path="projects/demo")
    # The model says "top.csv" or "data/orders.csv", meaning inside the selected project.
    assert "3 rows" in run(data_adapter.profile_data, ctx, {"path": "top.csv"}).output
    assert "3 rows" in run(data_adapter.profile_data, ctx, {"path": "data/orders.csv"}).output
    # Workspace-relative paths still work.
    assert "3 rows" in run(data_adapter.profile_data, ctx, {"path": "projects/demo/top.csv"}).output
    # New files land in the project too.
    assert ctx.guard.resolve("documents/report.docx") == (project / "documents" / "report.docx").resolve()
    # And escaping is still refused.
    with pytest.raises(Exception):
        run(data_adapter.profile_data, ctx, {"path": "../../../outside.txt"})


def test_attached_data_file_is_read_and_outputs_go_to_the_project(settings, tmp_path):
    upload = tmp_path / "abc123_sales.csv"
    upload.write_bytes(ROWS.encode("cp1252"))
    session = Session(id="testsession01")
    session.attachments["abc123"] = Attachment("abc123", "sales.csv", upload, upload.stat().st_size, "text")
    ctx = ctx_for(settings, active_path="projects/demo", session=session)

    profile = run(data_adapter.profile_data, ctx, {"path": "sales.csv"}).output
    assert "sales.csv" in profile and "3 rows" in profile
    assert "North" in run(data_adapter.query_data, ctx, {"path": "abc123", "group_by": "region", "metric": "sales"}).output
    result = run(data_adapter.create_dashboard, ctx, {"path": "sales.csv", "title": "Sales"})
    assert result.ok and result.changed_paths == ["projects/demo/dashboards/sales.html"]
    assert "Zürich" in run(fs_adapter.read_file, ctx, {"path": "sales.csv"}).output


def test_general_assistant_has_data_tools_and_delegation_is_forgiving(settings):
    registry = load_registry(settings.arch_dir)
    assert "data" in registry.agents["general"].applications
    assert registry.find_skill("/create_dashboard").command == "/dashboard"
    assert registry.find_skill("analyze_data").command == "/analyze"
    assert registry.find_skill("/no-such-thing") is None


def test_decode_never_raises():
    assert decode(b"\xff\xfe\x00") is not None
    assert decode("café".encode("cp1252")) == "café"
    assert looks_binary(b"\x00\x01\x02\x03binary\x00")


def test_empty_reply_from_a_thinking_model_gets_one_nudge(ctx, run_chat):
    ctx.ollama.script = [[("list_directory", {"path": "projects/demo"})], "", "There are 2 files."]
    events = run_chat("/general list it")
    final = [e for e in events if e["type"] == "assistant_final"][-1]
    assert final["text"] == "There are 2 files."
    assert "Write your final answer" in ctx.ollama.calls[-1]["messages"][-1]["content"]


def test_think_tags_are_not_shown():
    from core.agent import _last_paragraphs, _strip_think

    assert _strip_think("<think>hmm</think>\nThe total is 35.") == "The total is 35."
    assert _last_paragraphs("a\n\nb\n\nThe answer is 35.", limit=18) == "b\n\nThe answer is 35."
