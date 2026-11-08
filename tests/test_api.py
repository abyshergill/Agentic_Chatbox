from fastapi.testclient import TestClient

SID = "testsession01"


def upload(client, name, content, **extra):
    return client.post("/api/files/upload", data={"session_id": SID, **extra}, files={"file": (name, content)})


def test_health_and_config_endpoints(client):
    health = client.get("/api/health").json()
    assert health["status"] == "ok" and health["ollama"]["connected"]
    for path in ["/api/agents", "/api/skills", "/api/commands", "/api/permissions", "/api/execution/history"]:
        assert client.get(path).status_code == 200


def test_foreign_host_is_rejected(app):
    # DNS rebinding: a malicious domain resolving to 127.0.0.1.
    evil = TestClient(app, base_url="http://evil.example.com")
    assert evil.get("/api/health").status_code == 403


def test_cross_origin_post_cannot_approve_or_chat(client):
    headers = {"Origin": "https://evil.example.com"}
    body = {"session_id": SID, "approval_id": "x"}
    assert client.post("/api/tools/approve", json=body, headers=headers).status_code == 403
    assert client.post("/api/chat", json={"session_id": SID, "message": "hi"}, headers=headers).status_code == 403


def test_other_local_port_is_cross_origin(client):
    response = client.post("/api/tools/approve", json={"session_id": SID, "approval_id": "x"},
                           headers={"Origin": "http://127.0.0.1:3000"})
    assert response.status_code == 403


def test_same_origin_post_passes_the_check(client):
    response = client.post("/api/tools/approve", json={"session_id": SID, "approval_id": "x"},
                           headers={"Origin": "http://127.0.0.1:8000"})
    assert response.status_code == 404  # Past the origin check; the approval just does not exist.


def test_approval_is_bound_to_its_session(client, ctx):
    import asyncio
    from core.agent import PendingApproval

    loop = asyncio.new_event_loop()
    future = loop.create_future()
    ctx.approvals["abc"] = PendingApproval("abc", SID, future)
    assert client.post("/api/tools/approve", json={"session_id": "othersession1", "approval_id": "abc"}).status_code == 404
    assert not future.done()
    loop.close()


def test_security_headers(client):
    headers = client.get("/api/health").headers
    assert "default-src 'self'" in headers["content-security-policy"]
    assert headers["x-content-type-options"] == "nosniff"


def test_bad_session_id_is_rejected(client):
    assert client.get("/api/files/tree", params={"session_id": "../../etc"}).status_code == 400


def test_tree_hides_secrets(client):
    data = client.get("/api/files/tree", params={"session_id": SID, "path": "projects/demo"}).json()
    names = [entry["name"] for entry in data["entries"]]
    assert "hello.py" in names and ".env" not in names


def test_tree_hides_symlinks_leading_out(client, settings, base):
    (settings.workspace_root / "projects" / "demo" / "out").symlink_to(base.parent)
    data = client.get("/api/files/tree", params={"session_id": SID, "path": "projects/demo"}).json()
    assert "out" not in [entry["name"] for entry in data["entries"]]


def test_read_file_endpoint_enforces_the_guard(client):
    read = lambda path: client.get("/api/files/read", params={"session_id": SID, "path": path})
    assert read("projects/demo/hello.py").json()["content"] == "print('hi')\n"
    for bad in ["../arch/permission.md", "projects/demo/.env", "/etc/passwd", "projects/../../outside.txt"]:
        assert read(bad).status_code == 403, bad


def test_write_file_endpoint_enforces_the_guard(client, base):
    write = lambda path: client.post("/api/files/write", json={"session_id": SID, "path": path, "content": "x"})
    assert write("projects/demo/saved.txt").status_code == 200
    for bad in ["../arch/permission.md", "../main.py", "projects/demo/.env", "/tmp/evil"]:
        assert write(bad).status_code == 403, bad
    assert "APPLICATION" in (base / "arch" / "permission.md").read_text()


def test_write_detects_a_stale_copy(client):
    path = "projects/demo/hello.py"
    response = client.post("/api/files/write", json={"session_id": SID, "path": path, "content": "x", "expected_mtime": 1.0})
    assert response.status_code == 409


def test_uploads_are_validated(client):
    assert upload(client, "notes.md", b"# hi").status_code == 200
    assert upload(client, "evil.exe", b"MZ").status_code == 400
    assert upload(client, "script.sh", b"echo").status_code == 400
    assert upload(client, "fake.pdf", b"not a pdf").status_code == 400
    assert upload(client, "binary.txt", b"\x00\x01\x02").status_code == 400
    assert upload(client, ".env.txt", b"TOKEN=1").status_code == 400
    assert upload(client, "server.key", b"x").status_code == 400


def test_upload_size_limit(client, base):
    path = base / "arch" / "permission.md"
    path.write_text(path.read_text().replace("Max Size MB: 25", "Max Size MB: 1"))
    assert upload(client, "big.txt", b"a" * (2 * 1024 * 1024)).status_code == 400
    assert upload(client, "small.txt", b"a" * 1024).status_code == 200


def test_upload_filename_cannot_traverse_and_is_not_executable(client, settings):
    response = upload(client, "../../../evil.py", b"print(1)")
    assert response.status_code == 200 and response.json()["name"] == "evil.py"
    stored = list(settings.uploads_dir.rglob("*evil.py"))
    assert len(stored) == 1 and stored[0].is_relative_to(settings.uploads_dir)
    assert not stored[0].stat().st_mode & 0o111


def test_upload_target_dir_is_checked(client, settings):
    assert upload(client, "a.md", b"x", target_dir="../arch").status_code == 403
    ok = upload(client, "a.md", b"x", target_dir="projects/demo")
    assert ok.status_code == 200 and (settings.workspace_root / "projects/demo/a.md").read_bytes() == b"x"


def test_uploads_disabled_globally(client, base):
    from tests.conftest import set_permission
    set_permission(base, "uploads", "Access", "disabled")
    assert upload(client, "a.md", b"x").status_code == 400


def test_chat_streams_events(client, ctx):
    ctx.ollama.script = ["Hello"]
    response = client.post("/api/chat", json={"session_id": SID, "message": "hi"})
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    assert '"assistant_final"' in response.text and "Hello" in response.text


def test_index_and_static_assets_are_served(client):
    assert "Local AI Workspace" in client.get("/").text
    assert client.get("/static/js/main.js").status_code == 200
    assert client.get("/static/vendor/monaco/vs/loader.js").status_code == 200
    assert client.get("/static/../main.py").status_code == 404


def test_pdf_attachment_can_be_read(client, ctx):
    from io import BytesIO
    from pypdf import PdfWriter
    from core.uploads import read_attachment_text

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = BytesIO()
    writer.write(buffer)
    response = upload(client, "doc.pdf", buffer.getvalue())
    assert response.status_code == 200
    attachment = ctx.sessions.get(SID).attachments[response.json()["id"]]
    assert "no extractable text" in read_attachment_text(attachment, 10_000)


def test_pdf_text_is_extracted(client, ctx):
    from core.uploads import read_attachment_text

    stream = b"BT /F1 12 Tf 20 100 Td (Quarterly revenue) Tj ET"
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length %d>>stream\n%s\nendstream" % (len(stream), stream),
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    pdf = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(pdf)
    pdf += b"xref\n0 6\n0000000000 65535 f \n" + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    pdf += b"trailer<</Size 6/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF" % xref
    response = upload(client, "report.pdf", pdf)
    attachment = ctx.sessions.get(SID).attachments[response.json()["id"]]
    assert "Quarterly revenue" in read_attachment_text(attachment, 10_000)
