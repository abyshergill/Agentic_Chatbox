"""#documents: write Word documents (.docx) from Markdown-style text.

Reading documents (PDF, Word, Excel, HTML...) is part of #filesystem's read_file, so every skill that can read files
can read documents too.
"""

from __future__ import annotations

import re

from .. import documents
from ..fs_guard import atomic_write
from ..tools import Adapter, Tool, ToolContext, ToolError, ToolResult

FORMAT_HELP = ("content is Markdown-style text: # Heading, ## Subheading, - bullet, 1. numbered, | table | rows |, "
               "> quote, ``` code ```, **bold**, *italic*, and a line with only --- for a page break.")


def _name(title: str) -> str:
    return (re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_") or "document")[:60]


def _plan(ctx: ToolContext, args: dict):
    title = (args.get("title") or "").strip()
    content = args.get("content") or ""
    if not content.strip():
        raise ToolError("content is empty: write the document's text in content")
    raw = (args.get("path") or "").strip()
    guard = ctx.guard
    if raw:
        target = guard.resolve(raw)
    else:
        base = guard.resolve(ctx.active_path) if ctx.active_path else guard.roots[0] if guard.roots else None
        if base is None:
            raise ToolError("no folder to save the document in; pass path")
        folder = base if base.is_dir() else base.parent
        target = guard.resolve(str(folder / "documents" / f"{_name(title or 'document')}.docx"))
    if target.suffix.lower() != ".docx":
        raise ToolError("the document path must end in .docx")
    if target.exists() and not args.get("overwrite"):
        raise ToolError(f"'{guard.display(target)}' already exists. Choose another name, or pass overwrite true.")
    return title, content, target


def _check(ctx: ToolContext, args: dict) -> None:
    if not ctx.permissions.check("filesystem", "write").allowed:
        raise ToolError("writing files is not permitted")
    _plan(ctx, args)


def _preview(ctx: ToolContext, args: dict) -> str:
    title, content, target = _plan(ctx, args)
    headings = [line.strip() for line in content.splitlines() if line.strip().startswith("#")]
    lines = [f"Create Word document {ctx.guard.display(target)}", f"Title: {title or '(none)'}",
             f"{len(content.split()):,} words, {len(headings)} heading(s)", ""]
    lines += headings[:20]
    return "\n".join(lines)


async def create_document(ctx: ToolContext, args: dict) -> ToolResult:
    _check(ctx, args)  # Revalidated after approval.
    title, content, target = _plan(ctx, args)
    try:
        data = documents.build_docx(title, content, subtitle=args.get("subtitle") or "")
    except Exception as exc:  # python-docx raises varied errors for odd input.
        raise ToolError(f"could not build the document: {exc}") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(target, data)
    label = ctx.guard.display(target)
    return ToolResult(True, f"Word document saved to {label} ({len(content.split()):,} words). "
                            "It opens in Word, Pages or Google Docs.", [label])


adapter = Adapter(
    app_id="documents",
    tools=[
        Tool("create_document", "documents", "create",
             "Create a Word document (.docx): reports, letters, proposals, summaries, meeting notes, guides. "
             + FORMAT_HELP + " Saved to path, or <current folder>/documents/<title>.docx.",
             {"title": {"type": "string", "description": "Document title, shown at the top"},
              "content": {"type": "string", "description": "The document body as Markdown-style text"},
              "subtitle": {"type": "string", "description": "Optional line under the title, e.g. date or author"},
              "path": {"type": "string", "description": "Optional .docx path, e.g. projects/acme/documents/report.docx"},
              "overwrite": {"type": "boolean", "description": "Replace the file if it exists"}},
             create_document, required=["content"], precheck=_check, preview=_preview),
    ],
)
