"""Reading PDF, Word, Excel and HTML; writing Word documents; plan mode and @-mentioned context files."""

import asyncio
from io import BytesIO

import pytest
from docx import Document

from core import documents
from core.adapters import documents as doc_adapter
from core.permissions import PermissionManager
from core.tools import ToolContext
from tests.conftest import of_type


def make_pdf(text: str) -> bytes:
    """A minimal one-page PDF with a line of text."""
    stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for number, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref))
    return out.getvalue()


def make_docx(path):
    document = Document()
    document.add_heading("Quarterly plan", level=1)
    document.add_paragraph("Revenue grew 12% in Q3.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Region", "Sales"
    table.cell(1, 0).text, table.cell(1, 1).text = "North", "100"
    document.add_paragraph("Next steps", style="List Bullet")
    document.save(path)


def test_reads_every_document_type(tmp_path):
    (tmp_path / "a.pdf").write_bytes(make_pdf("Invoice total 4200 EUR"))
    make_docx(tmp_path / "b.docx")
    (tmp_path / "c.html").write_text("<html><head><style>x{}</style><script>alert(1)</script></head><body><h1>Title</h1><p>Hello &amp; welcome</p><ul><li>One</li></ul></body></html>")
    (tmp_path / "d.md").write_text("# Notes\n- item\n")
    pdf, _ = documents.extract_text(tmp_path / "a.pdf", 10_000)
    assert "Invoice total 4200 EUR" in pdf and "page 1 of 1" in pdf
    docx, _ = documents.extract_text(tmp_path / "b.docx", 10_000)
    assert "# Quarterly plan" in docx and "| Region | Sales |" in docx and "| North | 100 |" in docx and "- Next steps" in docx
    assert docx.index("Revenue") < docx.index("| Region")  # Tables stay where they are in the document.
    html, _ = documents.extract_text(tmp_path / "c.html", 10_000)
    assert "# Title" in html and "Hello & welcome" in html and "- One" in html and "alert" not in html
    raw, _ = documents.extract_text(tmp_path / "c.html", 10_000, html_as_text=False)
    assert raw.startswith("<html>")
    assert documents.extract_text(tmp_path / "d.md", 10_000)[0] == "# Notes\n- item\n"
    text, truncated = documents.extract_text(tmp_path / "b.docx", 10)
    assert truncated and len(text) == 10
    (tmp_path / "bad.docx").write_text("nope")
    with pytest.raises(documents.DocumentError):
        documents.extract_text(tmp_path / "bad.docx", 100)
    (tmp_path / "bin.dat").write_bytes(b"\x00\x01binary")
    with pytest.raises(documents.DocumentError):
        documents.extract_text(tmp_path / "bin.dat", 100)


@pytest.mark.skipif(not __import__("pathlib").Path("/usr/bin/textutil").exists(), reason="macOS only")
def test_reads_old_word_doc_with_textutil(tmp_path):
    import subprocess
    (tmp_path / "x.txt").write_text("Legacy contract terms")
    subprocess.run(["/usr/bin/textutil", "-convert", "doc", str(tmp_path / "x.txt"), "-output", str(tmp_path / "x.doc")], check=True)
    assert "Legacy contract terms" in documents.extract_text(tmp_path / "x.doc", 1000)[0]


def test_builds_word_documents():
    content = """Intro with **bold** and *italic* and `code`.
Second line of the same paragraph.

# Findings
- First point
  - Nested point
1. Step one

| Region | Sales |
| --- | --- |
| North | 100 |

> A quote

```
print("hi")
```

---

## Next page"""
    document = Document(BytesIO(documents.build_docx("Report", content, subtitle="March 2026")))
    paragraphs = [(p.style.name, p.text) for p in document.paragraphs if p.text]
    assert ("Title", "Report") in paragraphs and ("Heading 1", "Findings") in paragraphs and ("Heading 2", "Next page") in paragraphs
    assert ("List Bullet", "First point") in paragraphs and ("List Bullet 2", "Nested point") in paragraphs
    assert ("Normal", "Intro with bold and italic and code. Second line of the same paragraph.") in paragraphs
    bold = [r.text for p in document.paragraphs for r in p.runs if r.bold]
    assert "bold" in bold
    table = document.tables[0]
    assert [c.text for c in table.rows[1].cells] == ["North", "100"] and len(document.tables) == 1


def test_create_document_tool(app, settings):
    ctx = ToolContext(settings, PermissionManager(settings.arch_dir / "permission.md"), ["./workspace/projects"], None,
                      active_path="projects/demo")
    run = lambda args: asyncio.run(doc_adapter.create_document(ctx, args))
    result = run({"title": "Client Proposal", "content": "# Scope\nWe will build it."})
    assert result.changed_paths == ["projects/demo/documents/client_proposal.docx"]
    saved = settings.workspace_root / "projects/demo/documents/client_proposal.docx"
    assert "We will build it." in documents.extract_text(saved, 1000)[0]
    with pytest.raises(Exception, match="already exists"):
        run({"title": "Client Proposal", "content": "x"})
    with pytest.raises(Exception, match=".docx"):
        run({"content": "x", "path": "projects/demo/x.txt"})
    with pytest.raises(Exception):
        run({"content": "x", "path": "../outside.docx"})
    assert "Scope" in doc_adapter._preview(ctx, {"title": "Other", "content": "# Scope\nx"})


def test_read_file_tool_reads_documents(ctx, run_chat, settings):
    make_docx(settings.workspace_root / "projects/demo/plan.docx")
    ctx.ollama.script = [[("read_file", {"path": "projects/demo/plan.docx"})], "done"]
    events = run_chat("/summarize plan.docx")
    assert "Revenue grew 12% in Q3." in of_type(events, "tool_result")[0]["output"]


def test_editor_shows_documents_read_only(client, settings):
    (settings.workspace_root / "projects/demo/a.pdf").write_bytes(make_pdf("Hello PDF"))
    data = client.get("/api/files/read", params={"session_id": "testsession01", "path": "projects/demo/a.pdf"}).json()
    assert data["readonly"] and "Hello PDF" in data["content"]
    data = client.get("/api/files/read", params={"session_id": "testsession01", "path": "projects/demo/hello.py"}).json()
    assert not data["readonly"]


def test_uploads_accept_documents(client):
    files = {"file": ("plan.docx", BytesIO(b"PK\x03\x04" + b"0" * 100))}
    assert client.post("/api/files/upload", data={"session_id": "testsession01"}, files=files).status_code == 200
    fake = {"file": ("fake.docx", BytesIO(b"not a zip"))}
    assert client.post("/api/files/upload", data={"session_id": "testsession01"}, files=fake).status_code == 400
    old = {"file": ("old.doc", BytesIO(b"\xd0\xcf\x11\xe0" + b"0" * 100))}
    assert client.post("/api/files/upload", data={"session_id": "testsession01"}, files=old).status_code == 200


def test_mentioned_files_are_given_as_context(ctx, run_chat, settings):
    make_docx(settings.workspace_root / "projects/demo/plan.docx")
    ctx.ollama.script = ["ok"]
    run_chat("/general what does it say?", context_files=["projects/demo/plan.docx", "projects/demo", "../outside.txt"])
    sent = ctx.ollama.calls[0]["messages"][-1]["content"]
    assert "Revenue grew 12% in Q3." in sent and "hello.py" in sent and "host secret" not in sent
    assert "cannot read it" in sent


def test_plan_mode_offers_only_read_only_tools(ctx, run_chat):
    ctx.ollama.script = ["Plan: 1. edit calc.py"]
    run_chat("/general add a function", plan_only=True)
    offered = set(ctx.ollama.tool_names[0])
    assert {"read_file", "list_directory"} <= offered
    assert not offered & {"write_file", "create_file", "delete_file", "execute_command", "create_document"}
    assert "PLAN MODE" in ctx.ollama.calls[0]["messages"][0]["content"]
