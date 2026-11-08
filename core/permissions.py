"""Parses arch/permission.md and answers "may this application do this?".

Everything fails closed: a missing file, an unknown application, or an unreadable
value means no. The file is re-read for every decision, so revoking a permission
takes effect on the next action.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

APP_LINE = re.compile(r"^#([a-z][\w-]*)\s*$")
FIELD_LINE = re.compile(r"^([A-Za-z][A-Za-z ]*):\s*(.*)$")


@dataclass
class AppPermission:
    id: str
    name: str = ""
    executable: str = ""
    enabled: bool = False
    operations: list[str] = field(default_factory=list)
    approval_required: bool = True
    approval_exempt: list[str] = field(default_factory=list)
    fields: dict[str, str] = field(default_factory=dict)

    def csv(self, key: str) -> list[str]:
        return [part.strip() for part in self.fields.get(key, "").split(",") if part.strip()]

    def timeout(self, default: int = 120) -> int:
        try:
            return max(1, int(self.fields.get("Timeout Seconds", default)))
        except ValueError:
            return default


@dataclass
class Decision:
    allowed: bool
    needs_approval: bool
    reason: str


class PermissionManager:
    def __init__(self, path: Path):
        self.path = path
        self.apps: dict[str, AppPermission] = {}
        self.warnings: list[str] = []
        self.reload()

    def reload(self) -> None:
        self.apps, self.warnings = parse_permissions(self.path)

    def app(self, app_id: str) -> AppPermission | None:
        return self.apps.get(app_id)

    def check(self, app_id: str, operation: str, *, destructive: bool = False) -> Decision:
        self.reload()
        app = self.apps.get(app_id)
        if app is None:
            return Decision(False, True, f"#{app_id} is not defined in permission.md")
        if not app.enabled:
            return Decision(False, True, f"#{app_id} is disabled in permission.md")
        if operation not in app.operations:
            return Decision(False, True, f"#{app_id} is not allowed to perform '{operation}'")

        needs_approval = app.approval_required and operation not in app.approval_exempt
        if destructive:
            needs_approval = True
        return Decision(True, needs_approval, "allowed by permission.md")


def parse_permissions(path: Path) -> tuple[dict[str, AppPermission], list[str]]:
    warnings: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        return {}, [f"permission.md could not be read ({exc}); everything is denied"]

    apps: dict[str, AppPermission] = {}
    current: AppPermission | None = None
    for raw in lines:
        line = raw.strip()
        app_match = APP_LINE.match(line)
        if app_match:
            current = AppPermission(id=app_match.group(1))
            apps[current.id] = current
            continue
        if line.startswith("#"):  # A Markdown heading ends the current block.
            current = None
            continue
        field_match = FIELD_LINE.match(line)
        if current is None or not field_match:
            continue
        current.fields[field_match.group(1).strip()] = field_match.group(2).strip()

    for app in apps.values():
        app.name = app.fields.get("Name", app.id)
        app.executable = app.fields.get("Executable", "")
        app.operations = app.csv("Operations")
        app.approval_exempt = app.csv("Approval Exempt")

        access = app.fields.get("Access", "").lower()
        app.enabled = access == "enabled"
        if access not in {"enabled", "disabled"}:
            warnings.append(f"#{app.id}: Access is '{access or 'missing'}'; treating as disabled")

        approval = app.fields.get("Approval", "").lower()
        app.approval_required = approval != "not_required"
        if approval not in {"required", "not_required"}:
            warnings.append(f"#{app.id}: Approval is '{approval or 'missing'}'; treating as required")
    return apps, warnings
