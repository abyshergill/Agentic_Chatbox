import os

import pytest

from core.fs_guard import FsGuard, PathDenied, atomic_write
from core.permissions import PermissionManager


def guard(settings, paths=("./workspace/projects",)):
    return FsGuard(settings, PermissionManager(settings.arch_dir / "permission.md"), list(paths))


def test_allows_a_file_inside_the_root(settings):
    assert guard(settings).resolve("projects/demo/hello.py").name == "hello.py"


def test_accepts_the_workspace_prefix_models_tend_to_add(settings):
    assert guard(settings).resolve("workspace/projects/demo/hello.py").name == "hello.py"


@pytest.mark.parametrize("attempt", [
    "../outside.txt", "../../outside.txt", "projects/../../../outside.txt", "projects/demo/../../../arch/permission.md",
    "/etc/passwd", "~/.ssh/id_rsa", "projects/demo/\x00evil", "../arch/permission.md", "database/x",
])
def test_rejects_paths_outside_the_skill(settings, attempt):
    with pytest.raises(PathDenied):
        guard(settings).resolve(attempt)


@pytest.mark.parametrize("name", [
    ".env", ".ENV", ".env.local", "id_rsa", "ID_RSA.pub", "server.pem", "api.key", "credentials.json", "secrets.yaml",
])
def test_rejects_secret_files(settings, name):
    target = settings.workspace_root / "projects" / "demo" / name
    target.write_text("x")
    with pytest.raises(PathDenied):
        guard(settings).resolve(f"projects/demo/{name}")


def test_rejects_git_internals(settings):
    with pytest.raises(PathDenied):
        guard(settings).resolve("projects/demo/.git/hooks/pre-commit")


def test_rejects_symlinked_file_leading_out(settings, base):
    link = settings.workspace_root / "projects" / "demo" / "link.txt"
    link.symlink_to(base.parent / "outside.txt")
    with pytest.raises(PathDenied):
        guard(settings).resolve("projects/demo/link.txt")


def test_rejects_symlinked_directory_leading_out(settings, base):
    link = settings.workspace_root / "projects" / "demo" / "out"
    link.symlink_to(base.parent)
    with pytest.raises(PathDenied):
        guard(settings).resolve("projects/demo/out/outside.txt")


def test_skill_cannot_exceed_global_roots(settings, base):
    wide = guard(settings, ["/"])
    assert wide.roots == [settings.workspace_root.resolve()]
    with pytest.raises(PathDenied):
        wide.resolve(str(base.parent / "outside.txt"))


def test_skill_path_outside_global_roots_is_dropped(settings, base):
    assert guard(settings, [str(base.parent)]).roots == [settings.workspace_root.resolve()]
    assert guard(settings, ["./arch"]).roots == []
    with pytest.raises(PathDenied):
        guard(settings, ["./arch"]).resolve(None)


def test_app_internals_stay_blocked_even_if_user_widens_roots(settings, base):
    path = base / "arch" / "permission.md"
    path.write_text(path.read_text().replace("Roots: ./workspace", "Roots: ."))
    widened = guard(settings, ["."])
    for internal in ["arch/permission.md", "logs/audit.jsonl", "main.py", "core/agent.py"]:
        with pytest.raises(PathDenied):
            widened.resolve(str(base / internal))
    with pytest.raises(PathDenied):
        widened.resolve(str(base / "ARCH" / "permission.md"))


def test_no_roots_when_permission_file_is_missing(settings, base):
    (base / "arch" / "permission.md").unlink()
    assert guard(settings).roots == []


def test_install_folder_with_secret_like_name_still_works(tmp_path):
    from core.config import Settings
    import shutil
    from core.config import PROJECT_ROOT
    root = tmp_path / "secrets-project"
    (root / "workspace" / "projects").mkdir(parents=True)
    shutil.copytree(PROJECT_ROOT / "arch", root / "arch")
    assert guard(Settings(base_dir=root)).resolve("projects").name == "projects"


def test_atomic_write_replaces_a_symlink_instead_of_following_it(settings, base):
    outside = base.parent / "outside.txt"
    link = settings.workspace_root / "projects" / "demo" / "swap.txt"
    link.symlink_to(outside)
    atomic_write(link, "changed")
    assert outside.read_text() == "host secret\n"
    assert not os.path.islink(link)
