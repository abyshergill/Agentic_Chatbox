from core.arch_loader import load_registry, parse_skill
from core.permissions import PermissionManager
from tests.conftest import set_permission


def write(tmp_path, text):
    path = tmp_path / "s.md"
    path.write_text(text)
    return parse_skill(path, "a")


GOOD = "Title: T\nDescription: D\nAllowed Paths: ./workspace/a, ./workspace/b\nModel: m:1\n\nDo things.\n"


def test_valid_skill(tmp_path):
    skill = write(tmp_path, GOOD)
    assert skill.valid and skill.allowed_paths == ["./workspace/a", "./workspace/b"] and skill.prompt == "Do things."


def test_model_line_is_optional(tmp_path):
    skill = write(tmp_path, "Title: T\nDescription: D\nAllowed Paths: ./workspace/a\n\nDo things.\n")
    assert skill.valid and skill.model == "" and skill.prompt == "Do things."
    assert write(tmp_path, GOOD).model == "m:1"


def test_malformed_skills_are_invalid(tmp_path):
    bad = [
        "Description: D\nTitle: T\nAllowed Paths: x\nModel: m\n\nbody",  # Wrong order.
        "Title: T\nAllowed Paths: x\nModel: m\n\nbody",  # Missing field.
        "\nTitle: T\nDescription: D\nAllowed Paths: x\nModel: m\n\nbody",  # Shifted down a line.
        "Title: \nDescription: D\nAllowed Paths: x\nModel: m\n\nbody",  # Empty value.
        "Title: T\nDescription: D\nAllowed Paths: ,\nModel: m\n\nbody",  # No usable path.
        "Title: T\nDescription: D\nAllowed Paths: x\nModel: m\n\n",  # No instructions.
        "Title: T\nDescription: D\nAllowed Paths: x\nModel: llama:cloud\n\nbody",  # Not local.
        "",
    ]
    for text in bad:
        assert not write(tmp_path, text).valid, text


def test_real_arch_loads_cleanly(settings):
    registry = load_registry(settings.arch_dir)
    assert not registry.warnings
    assert {"/db-migration", "/rust-firmware", "/javascript", "/ui-design", "/api-development"} <= set(registry.commands)
    assert registry.default_skill.valid
    assert all(skill.valid for skill in registry.commands.values())


def test_new_agent_is_discovered_without_code_changes(settings, base):
    folder = base / "arch" / "agents" / "ops" / "skills"
    folder.mkdir(parents=True)
    (folder.parent / "agent.md").write_text("Name: Ops\nDescription: d\nApplications: #filesystem\n\nBody\n")
    (folder / "deploy.md").write_text(GOOD)
    with (base / "arch" / "command.md").open("a") as handle:
        handle.write("\n/deploy -> agents/ops/skills/deploy.md\n")
    registry = load_registry(settings.arch_dir)
    assert registry.commands["/deploy"].agent_id == "ops"


def test_command_cannot_point_outside_agents(settings, base):
    with (base / "arch" / "command.md").open("a") as handle:
        handle.write("\n/evil -> ../../etc/passwd.md\n/perm -> permission.md\n")
    registry = load_registry(settings.arch_dir)
    assert "/evil" not in registry.commands and "/perm" not in registry.commands


def manager(base):
    return PermissionManager(base / "arch" / "permission.md")


def test_disabled_and_unknown_apps_are_denied(base):
    perms = manager(base)
    assert not perms.check("browser", "open_url").allowed
    assert not perms.check("nonexistent", "x").allowed


def test_only_listed_operations_are_allowed(base):
    perms = manager(base)
    assert perms.check("git", "status").allowed
    assert not perms.check("git", "push").allowed


def test_approval_exemptions(base):
    perms = manager(base)
    assert not perms.check("git", "status").needs_approval
    assert perms.check("git", "commit").needs_approval


def test_destructive_operations_always_need_approval(base):
    set_permission(base, "filesystem", "Approval", "not_required")
    perms = manager(base)
    assert not perms.check("filesystem", "write").needs_approval
    assert perms.check("filesystem", "delete", destructive=True).needs_approval


def test_bad_values_fail_closed(base):
    set_permission(base, "git", "Access", "yes please")
    set_permission(base, "python", "Approval", "nah")
    perms = manager(base)
    assert not perms.check("git", "status").allowed
    assert perms.check("python", "execute_script").needs_approval
    assert len(perms.warnings) == 2


def test_missing_file_denies_everything(base):
    (base / "arch" / "permission.md").unlink()
    assert not manager(base).check("filesystem", "read").allowed
