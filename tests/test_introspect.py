"""The capabilities tool and read-only agents (used by /why-not and /explain-code)."""

from tests.conftest import of_type, set_permission


def test_why_not_skill_is_read_only_and_can_describe_limits(ctx, run_chat):
    ctx.ollama.script = ["ok"]
    run_chat("/why-not why can't I push with git?")
    offered = ctx.ollama.tool_names[0]
    assert "describe_capabilities" in offered and "read_file" in offered and "git_status" in offered
    assert not {"write_file", "delete_file", "git_commit", "git_add", "rename_file"} & set(offered)


def test_capabilities_explain_a_disabled_app_and_missing_operation(ctx, run_chat, base):
    set_permission(base, "git", "Operations", "status, diff")
    ctx.ollama.script = [[("describe_capabilities", {})], "done"]
    events = run_chat("/why-not why can't you commit?")
    result = of_type(events, "tool_result")[0]
    text = result.get("output") or result.get("result") or str(result)
    assert "#git: allowed operations: status, diff" in text
    assert "#browser: DISABLED" in text
    assert "ACTIVE SKILL" in text and "Limitation Analysis" in text and "read-only agent" in text
    assert "BY DESIGN" in text and ".xlsx" in text


def test_capabilities_focus_on_one_app(ctx, run_chat):
    ctx.ollama.script = [[("describe_capabilities", {"application": "#nonexistent"})], "done"]
    events = run_chat("/why-not x")
    text = str(of_type(events, "tool_result")[0])
    assert "not defined in permission.md" in text and "ACTIVE SKILL" not in text


def test_read_only_agent_cannot_write_even_if_the_model_tries(ctx, run_chat, settings):
    ctx.ollama.script = [[("write_file", {"path": "projects/demo/x.txt", "content": "x"})], "ok"]
    events = run_chat("/explain-code write a file", decide=lambda e: True)
    assert not (settings.workspace_root / "projects/demo/x.txt").exists()
    assert of_type(events, "tool_result")[0]["status"] != "success"
