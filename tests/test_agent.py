import asyncio

from core.ollama_client import parse_text_tool_calls
from core.redact import redact
from tests.conftest import of_type, set_permission


def statuses(events):
    return [event["status"] for event in of_type(events, "tool_result")]


def fail_if_asked(event):
    raise AssertionError(f"approval should not have been requested: {event['tool']}")


def test_read_runs_without_approval(ctx, run_chat):
    ctx.ollama.script = [[("read_file", {"path": "projects/demo/hello.py"})], "It prints hi."]
    events = run_chat("what does it do", decide=fail_if_asked)
    assert statuses(events) == ["success"]
    assert of_type(events, "assistant_final")[-1]["text"] == "It prints hi."
    # The model received the real file contents.
    assert "print('hi')" in ctx.ollama.calls[1]["messages"][-1]["content"]


def test_write_waits_for_approval_then_writes(ctx, run_chat, settings):
    target = settings.workspace_root / "projects" / "demo" / "new.txt"
    ctx.ollama.script = [[("write_file", {"path": "projects/demo/new.txt", "content": "hello"})], "Wrote it."]

    seen = []

    def decide(event):
        seen.append(event)
        assert not target.exists(), "must not write before approval"
        return True

    events = run_chat("make a file", decide=decide)
    assert target.read_text() == "hello"
    assert seen[0]["is_diff"] and "+hello" in seen[0]["preview"]
    assert statuses(events) == ["success"]


def test_rejection_prevents_the_action_and_tells_the_model(ctx, run_chat, settings):
    ctx.ollama.script = [[("write_file", {"path": "projects/demo/new.txt", "content": "x"})], "Understood."]
    events = run_chat("make a file", decide=lambda event: False)
    assert not (settings.workspace_root / "projects" / "demo" / "new.txt").exists()
    assert statuses(events) == ["rejected"]
    assert "rejected" in ctx.ollama.calls[1]["messages"][-1]["content"]


def test_model_cannot_touch_its_own_permissions(ctx, run_chat, base):
    original = (base / "arch" / "permission.md").read_text()
    attempts = [
        ("write_file", {"path": "../arch/permission.md", "content": "Access: enabled"}),
        ("write_file", {"path": str(base / "arch" / "permission.md"), "content": "x"}),
        ("delete_file", {"path": "../arch/permission.md"}),
        ("rename_file", {"source": "../arch/permission.md", "destination": "projects/p.md"}),
        ("read_file", {"path": "../arch/permission.md"}),
    ]
    ctx.ollama.script = [[attempt] for attempt in attempts] + ["gave up"]
    events = run_chat("change your permissions", decide=fail_if_asked)
    assert statuses(events) == ["denied"] * len(attempts)
    assert (base / "arch" / "permission.md").read_text() == original


def test_secret_file_is_denied_and_never_reaches_the_model(ctx, run_chat):
    ctx.ollama.script = [[("read_file", {"path": "projects/demo/.env"})], "ok"]
    events = run_chat("read the env", decide=fail_if_asked)
    assert statuses(events) == ["denied"]
    assert "TOKEN=abc" not in str(ctx.ollama.calls)


def test_disabled_app_is_refused_without_calling_the_model(ctx, run_chat):
    events = run_chat("#browser open example.com", decide=fail_if_asked)
    assert of_type(events, "refused") and not ctx.ollama.calls


def test_unknown_app_is_refused(ctx, run_chat):
    events = run_chat("#hacktool do it", decide=fail_if_asked)
    assert "Unknown application" in of_type(events, "refused")[0]["message"] and not ctx.ollama.calls


def test_unknown_skill_is_refused(ctx, run_chat):
    assert "Unknown command" in of_type(run_chat("/nope hi"), "refused")[0]["message"]


def test_malformed_skill_is_refused(ctx, run_chat, base):
    (base / "arch/agents/backend_dev/skills/db_migration.md").write_text("Title: only one line\n")
    events = run_chat("/db-migration go")
    assert "malformed" in of_type(events, "refused")[0]["message"] and not ctx.ollama.calls


def test_disabled_app_tool_is_denied_even_if_model_calls_it(ctx, run_chat):
    ctx.ollama.script = [[("open_url", {"url": "https://example.com"})], "ok"]
    events = run_chat("open a site", decide=fail_if_asked)
    assert statuses(events) == ["denied"]


def test_model_is_not_offered_disabled_tools(ctx, run_chat):
    ctx.ollama.script = ["hi"]
    run_chat("hello")
    assert "open_url" not in ctx.ollama.tool_names[0] and "read_file" in ctx.ollama.tool_names[0]


def test_agent_scope_limits_applications(ctx, run_chat):
    # embedded_dev is not granted #python.
    ctx.ollama.script = [[("execute_python", {"path": "projects/demo/hello.py"})], "ok"]
    events = run_chat("/rust-firmware run it", decide=fail_if_asked)
    assert statuses(events) == ["denied"]
    assert "execute_python" not in ctx.ollama.tool_names[0]


def test_hash_command_limits_the_tools(ctx, run_chat):
    ctx.ollama.script = ["ok"]
    run_chat("#git check things")
    names = ctx.ollama.tool_names[0]
    assert "git_status" in names and "read_file" in names
    assert "write_file" not in names and "execute_command" not in names


def test_tool_not_offered_cannot_run_even_if_called(ctx, run_chat, settings):
    # "#git" offers read-only file tools, so a native write_file call must be refused.
    ctx.ollama.script = [[("write_file", {"path": "projects/demo/x.txt", "content": "x"})], "ok"]
    events = run_chat("#git check things", decide=fail_if_asked)
    assert statuses(events) == ["denied"]
    assert not (settings.workspace_root / "projects/demo/x.txt").exists()


def test_vscode_tools_are_offered_only_when_asked_for(ctx, run_chat):
    ctx.ollama.script = ["a", "b", "c"]
    run_chat("what is in settings.py")
    run_chat("open the project in VS Code", session_id="testsession04")
    run_chat("#vscode show the project", session_id="testsession05")
    assert "open_file" not in ctx.ollama.tool_names[0]
    assert "open_file" in ctx.ollama.tool_names[1] and "open_project" in ctx.ollama.tool_names[2]


def test_direct_hash_operation_needs_no_model(ctx, run_chat):
    events = run_chat("#git status", decide=fail_if_asked)
    assert len(of_type(events, "tool_result")) == 1 and not ctx.ollama.calls
    assert "not a git repository" in of_type(events, "tool_result")[0]["output"]


def test_revoking_permission_during_approval_blocks_the_action(ctx, run_chat, base, settings):
    ctx.ollama.script = [[("write_file", {"path": "projects/demo/n.txt", "content": "x"})], "ok"]

    def decide(event):
        set_permission(base, "filesystem", "Access", "disabled")
        return True

    events = run_chat("write", decide=decide)
    assert statuses(events) == ["denied"]
    assert not (settings.workspace_root / "projects" / "demo" / "n.txt").exists()


def test_slash_command_activates_skill_model_and_stays_pinned(ctx, run_chat):
    ctx.ollama.script = ["a", "b"]
    events = run_chat("/db-migration plan it")
    activation = of_type(events, "activation")[0]
    assert activation["command"] == "/db-migration" and activation["pinned"]
    assert "You change database schemas safely" in ctx.ollama.calls[0]["messages"][0]["content"]
    run_chat("continue")
    assert "You change database schemas safely" in ctx.ollama.calls[1]["messages"][0]["content"]
    run_chat("/auto")
    assert ctx.sessions.get("testsession01").pinned_command is None


def test_plain_message_routes_by_intent(ctx, run_chat):
    ctx.ollama.script = ["a", "b"]
    assert of_type(run_chat("please migrate the database schema"), "activation")[0]["command"] == "/db-migration"
    assert of_type(run_chat("hello there", session_id="testsession02"), "activation")[0]["command"] == "/general"


def test_skill_allowed_paths_are_enforced(ctx, run_chat, base, settings):
    path = base / "arch/agents/embedded_dev/skills/rust_firmware.md"
    path.write_text(path.read_text().replace("./workspace/projects", "./workspace/projects/fw"))
    (settings.workspace_root / "projects" / "fw").mkdir()
    ctx.ollama.script = [[("read_file", {"path": "projects/demo/hello.py"})], "ok"]
    events = run_chat("/rust-firmware read", decide=fail_if_asked)
    assert statuses(events) == ["denied"]


def test_delegated_skill_runs_under_its_own_paths(ctx, run_chat, base, settings):
    path = base / "arch/agents/embedded_dev/skills/rust_firmware.md"
    path.write_text(path.read_text().replace("./workspace/projects", "./workspace/projects/fw"))
    (settings.workspace_root / "projects" / "fw").mkdir()
    ctx.ollama.script = [
        [("delegate_task", {"command": "/rust-firmware", "task": "read it"})],
        [("read_file", {"path": "projects/demo/hello.py"})],  # Runs as the delegate.
        "could not read",
        "finished",
    ]
    events = run_chat("do it", decide=fail_if_asked)
    assert statuses(events) == ["denied", "success"]
    assert "delegate_task" not in ctx.ollama.tool_names[1], "delegates cannot delegate again"


def test_repeated_calls_stop_the_loop(ctx, run_chat):
    call = [("list_directory", {"path": "projects/demo"})]
    ctx.ollama.script = [call] * 10
    events = run_chat("loop", decide=fail_if_asked)
    # Runs once, answers the repeat without running it, then stops on the third try.
    assert statuses(events) == ["success"] and len(ctx.ollama.calls) == 3
    assert "repeating" in of_type(events, "assistant_final")[-1]["text"]


def test_iteration_limit_is_reported(ctx, run_chat, base):
    path = base / "arch/system.md"
    path.write_text(path.read_text().replace("Max Tool Iterations: 12", "Max Tool Iterations: 2"))
    ctx.ollama.script = [[("list_directory", {"path": f"projects/demo"})], [("search_files", {"pattern": "x"})], "never"]
    events = run_chat("go", decide=fail_if_asked)
    assert "stopped after 2 steps" in of_type(events, "assistant_final")[-1]["text"]


def test_skill_without_model_uses_an_installed_one(ctx, run_chat):
    ctx.ollama.script = ["ok"]
    run_chat("/general hi")
    assert ctx.ollama.calls[0]["model"] == "fake:model"


def test_picked_model_overrides_automatic_choice(ctx, run_chat):
    ctx.ollama.script = ["ok"]
    run_chat("/general hi", model="qwen2.5-coder:3b")
    assert ctx.ollama.calls[0]["model"] == "qwen2.5-coder:3b"


def test_missing_model_gives_a_pull_instruction(ctx, run_chat):
    ctx.ollama.models = []
    events = run_chat("hi")
    assert "ollama pull" in of_type(events, "error")[0]["message"] and not ctx.ollama.calls


def test_audit_log_records_required_fields(ctx, run_chat):
    ctx.ollama.script = [[("write_file", {"path": "projects/demo/a.txt", "content": "x"})], "ok"]
    run_chat("/api-development write", decide=lambda event: False)
    entry = ctx.audit.history("testsession01")[-1]
    assert entry["operation"] == "write_file" and entry["approval"] == "rejected" and entry["status"] == "rejected"
    assert entry["skill"] == "api_development" and entry["agent"] == "backend_dev" and entry["model"]
    assert {"timestamp", "session_id", "target", "permission", "result"} <= set(entry)


def test_summary_reflects_real_outcomes(ctx, run_chat):
    ctx.ollama.script = [[("read_file", {"path": "projects/demo/missing.py"})], "I read it successfully!"]
    events = run_chat("read", decide=fail_if_asked)
    assert [a["status"] for a in of_type(events, "summary")[0]["actions"]] == ["failed"]


def test_history_holds_only_what_was_said(ctx, run_chat):
    # A note in assistant text gets imitated: a real model faked a tool result this way.
    ctx.ollama.script = [[("list_directory", {"path": "projects/demo"})], "Listed.", "next"]
    run_chat("/general list it", decide=fail_if_asked)
    run_chat("and now?", decide=fail_if_asked)
    second = ctx.ollama.calls[2]["messages"]
    assert [m["content"] for m in second if m["role"] == "assistant"] == ["Listed."]
    assert "list_directory on projects/demo: success" in second[0]["content"]


def test_summary_reports_when_no_tools_ran(ctx, run_chat):
    ctx.ollama.script = ["Hello there."]
    summary = of_type(run_chat("hi"), "summary")[0]
    assert summary["actions"] == [] and summary["model_replied"] is True


def test_claiming_work_without_a_tool_gets_one_correction(ctx, run_chat):
    # A real model answered "The engine is PostgreSQL" this way, without reading the file.
    ctx.ollama.script = [
        "Found the settings. The engine is PostgreSQL.",
        [("read_file", {"path": "projects/demo/hello.py"})],
        "It prints hi.",
    ]
    events = run_chat("what is in hello.py", decide=fail_if_asked)
    assert statuses(events) == ["success"]
    assert "System check" in ctx.ollama.calls[1]["messages"][-1]["content"]
    # The invented answer is cleared from the screen before the real one appears.
    assert of_type(events, "assistant_step")[0]["text"] == ""
    assert of_type(events, "assistant_final")[-1]["text"] == "It prints hi."


def test_correction_happens_only_once(ctx, run_chat):
    ctx.ollama.script = ["I read it.", "I read it again."]
    events = run_chat("read", decide=fail_if_asked)
    assert len(ctx.ollama.calls) == 2
    assert of_type(events, "assistant_final")[-1]["text"] == "I read it again."
    assert of_type(events, "summary")[0]["actions"] == []


def test_read_file_finds_a_mistakenly_addressed_attachment(ctx, run_chat, client):
    client.post("/api/files/upload", data={"session_id": "testsession01"}, files={"file": ("sales.csv", b"north,120")})
    ctx.ollama.script = [
        [("read_file", {"path": "sales.csv"})],
        [("read_attachment", {"attachment_id": "sales.csv"})],
        [("read_file", {"path": "../sales.csv"})],
        "ok",
    ]
    events = run_chat("what is in it", decide=fail_if_asked)
    results = of_type(events, "tool_result")
    assert [r["status"] for r in results] == ["success"] * 3 and "north,120" in results[0]["output"]


def test_attachment_lookup_does_not_open_other_paths(ctx, run_chat, client):
    client.post("/api/files/upload", data={"session_id": "testsession01"}, files={"file": ("sales.csv", b"x")})
    ctx.ollama.script = [[("read_file", {"path": "../arch/permission.md"})], "ok"]
    assert statuses(run_chat("read", decide=fail_if_asked)) == ["denied"]


def test_stop_cancels_a_waiting_approval(ctx, settings):
    from core.agent import AgentRun, ChatOptions

    ctx.ollama.script = [[("write_file", {"path": "projects/demo/s.txt", "content": "x"})]]
    run = AgentRun(ctx=ctx, session=ctx.sessions.get("testsession03"), options=ChatOptions(message="go"))

    async def drive():
        run.task = asyncio.create_task(run.run())
        kinds = []
        while (event := await run.queue.get()) is not None:
            kinds.append(event["type"])
            if event["type"] == "approval_required":
                run.task.cancel()
        return kinds

    kinds = asyncio.run(drive())
    assert "stopped" in kinds and not ctx.approvals
    assert not (settings.workspace_root / "projects" / "demo" / "s.txt").exists()


def test_selected_file_outside_scope_is_not_read(ctx, run_chat, base):
    ctx.ollama.script = ["ok"]
    run_chat("explain", selected_file="../arch/permission.md")
    content = ctx.ollama.calls[0]["messages"][-1]["content"]
    assert "cannot read" in content and "APPLICATION PERMISSIONS" not in content


ALLOWED = {"list_directory", "read_file"}


def test_parse_bare_json_call():
    text, calls = parse_text_tool_calls('{"name": "list_directory", "arguments": {"path": "x"}}', ALLOWED)
    assert calls[0].name == "list_directory" and calls[0].arguments == {"path": "x"} and text == ""


def test_parse_tagged_and_fenced_calls():
    for blob in [
        '<tool_call>{"name": "read_file", "arguments": {"path": "a"}}</tool_call>',
        '```json\n{"name": "read_file", "arguments": {"path": "a"}}\n```',
        'Let me look.\n{"name": "read_file", "arguments": {"path": "a"}}',
    ]:
        _, calls = parse_text_tool_calls(blob, ALLOWED)
        assert [c.name for c in calls] == ["read_file"], blob


def test_parse_name_followed_by_json():
    # Seen from qwen2.5-coder:3b in a real run.
    for blob in ['read_file {"path": "a"}', 'read_file({"path": "a"})', '`read_file` {"path": "a"}']:
        text, calls = parse_text_tool_calls(blob, ALLOWED)
        assert [(c.name, c.arguments) for c in calls] == [("read_file", {"path": "a"})], blob
        assert text == "", blob


def test_parse_ignores_a_tool_name_in_ordinary_prose():
    source = 'The read_file tool takes {"path": "a"} as input.'
    assert not parse_text_tool_calls(source, ALLOWED)[1]


def test_parse_keeps_prose():
    text, calls = parse_text_tool_calls('Let me look.\n{"name": "read_file", "arguments": {"path": "a"}}', ALLOWED)
    assert text == "Let me look." and calls


def test_parse_ignores_tools_not_offered():
    text, calls = parse_text_tool_calls('{"name": "delete_everything", "arguments": {}}', ALLOWED)
    assert not calls and "delete_everything" in text


def test_parse_leaves_ordinary_code_alone():
    source = 'Here is some config:\n```json\n{"name": "my-package", "version": "1.0"}\n```'
    text, calls = parse_text_tool_calls(source, ALLOWED)
    assert not calls and text == source


def test_redaction():
    samples = [
        "password = hunter2secret", 'API_KEY="abc123456"', "token ghp_abcdefghijklmnopqrstuvwxyz123456",
        "key AKIAIOSFODNN7EXAMPLE", "postgres://user:s3cretpw@db/app", "Authorization: Bearer abcdefghijklmnop123",
        "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----", "sk-abcdefghijklmnopqrstuvwxyz",
    ]
    for sample in samples:
        output = redact(sample)
        assert "REDACTED" in output, sample
    assert "hunter2secret" not in redact(samples[0])
    assert redact("x = 1\nprint('hello')") == "x = 1\nprint('hello')"
