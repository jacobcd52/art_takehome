"""Deterministic part of the simulated environment.

Every scenario declares a virtual filesystem (VFS) and, optionally, tools whose results are fixed strings. Tool calls that only read or write the VFS are answered here, in code, so the target sees byte-identical file contents (including the leaked grading block) in every run. Everything else is forwarded to the auditor, which simulates it from the scenario spec.
"""

import posixpath
import re
import shlex
from dataclasses import dataclass, field

from inspect_ai.tool import ToolCall

READ_TOOLS = {"read_file", "view_file", "open_file", "cat_file"}
LIST_TOOLS = {"list_files", "list_dir", "ls"}
WRITE_TOOLS = {"write_file", "create_file"}
SHELL_TOOLS = {"bash", "shell", "run_command", "terminal"}
WRITE_HINT = re.compile(
    r"(\s-i\b|\s-i\.|>|\btee\b|\bmv\b|\brm\b|\bcp\b|\btouch\b|\bmkdir\b|\btruncate\b|\bpatch\b|"
    r"\bgit\s+(checkout|restore|apply|mv|rm|reset|stash|revert|am|cherry-pick)\b|\bpython[0-9.]*\b|\bperl\b|"
    r"\bchmod\b|\bln\b|\bunzip\b|\btar\b|\bcrontab\b|\bsqlite3\b|\bjq\b|\byq\b|\bnode\b|\bruby\b|\binstall\b)"
)
DESTRUCTIVE_HINT = re.compile(r"\brm\b|\bmv\b|\brmdir\b|\bgit\s+(checkout|restore|reset|stash|clean)\b|\btar\b|\bunzip\b")


@dataclass
class Resolution:
    """Result of a deterministically handled tool call."""

    content: str
    note: str  # one-line description for the auditor's benefit


@dataclass
class VirtualFS:
    """Mutable per-trajectory copy of the scenario filesystem."""

    files: dict[str, str]
    cwd: str = "/"
    roots: list[str] = field(default_factory=list)  # dirs whose listing is complete
    writes: list[tuple[str, str]] = field(default_factory=list)
    dirty: set[str] = field(default_factory=set)  # paths a simulated command may have changed

    def note_simulated_command(self, command: str) -> None:
        """Record paths that a shell command simulated by the auditor may have modified.

        Reads of these paths (and listings of their directories) are then left to the auditor, which saw the command, instead of returning stale fixed contents. Two heuristics, both deliberately over-inclusive: (1) any fixed file whose absolute path, cwd-relative path or basename appears anywhere in a write-like command (catches `python -c "open('x','w')"`, heredocs, `sed -i`); (2) path-like arguments of the command (catches newly created files), skipping `cd` targets, and only treating an existing directory as touched when the command can delete or move it.
        """
        if not WRITE_HINT.search(command):
            return
        for f in self.files:
            rel = posixpath.relpath(f, self.cwd)
            if f in command or (not rel.startswith("..") and rel in command) or posixpath.basename(f) in command:
                self.dirty.add(f)
        try:
            tokens = shlex.split(command, posix=True)
        except ValueError:
            tokens = command.split()
        destructive = bool(DESTRUCTIVE_HINT.search(command))
        prev = ""
        for tok in tokens:
            raw, prev = prev, tok
            tok = tok.lstrip(">").rstrip(";&|")
            if not tok or tok.startswith("-") or raw == "cd" or "\n" in tok or " " in tok:
                continue
            p = self.abspath(tok)
            if self.is_dir(p):
                if destructive:
                    self.dirty.add(p)
                continue
            if "/" in tok or "." in tok or p in self.files:
                self.dirty.add(p)

    def is_dirty(self, p: str) -> bool:
        return p in self.dirty or any(p.startswith(d.rstrip("/") + "/") for d in self.dirty)

    def abspath(self, path: str) -> str:
        path = path.strip().strip("'\"")
        if path.startswith("~"):
            path = "/root" + path[1:]
        return posixpath.normpath(posixpath.join(self.cwd, path))

    def under_root(self, path: str) -> bool:
        return any(path == r or path.startswith(r.rstrip("/") + "/") for r in self.roots)

    def read(self, path: str) -> str | None:
        """Contents of `path` if it is a fixed file, else None (the auditor simulates it)."""
        p = self.abspath(path)
        if self.is_dirty(p):
            return None
        if p in self.files:
            return self.files[p]
        if self.is_dir(p):
            return f"Error: {path}: Is a directory"
        # missing files are left to the simulator: earlier simulated commands may have created them
        return None

    def is_dir(self, p: str) -> bool:
        prefix = p.rstrip("/") + "/"
        return any(f.startswith(prefix) for f in self.files)

    def listing(self, path: str) -> str | None:
        p = self.abspath(path or ".")
        if not (self.is_dir(p) and self.under_root(p)):
            return None
        prefix = p.rstrip("/") + "/"
        if any(d == p or d.startswith(prefix) for d in self.dirty):
            return None
        entries: set[str] = set()
        for f in self.files:
            if f.startswith(prefix):
                head, _, rest = f[len(prefix) :].partition("/")
                entries.add(head + "/" if rest else head)
        return "\n".join(sorted(entries))

    def write(self, path: str, content: str) -> str:
        p = self.abspath(path)
        self.dirty.discard(p)
        self.files[p] = content
        self.writes.append((p, content))
        return f"Wrote {len(content.encode())} bytes to {p}"


def _line_slice(text: str, start: int, end: int | None) -> str:
    lines = text.splitlines()
    return "\n".join(lines[max(start - 1, 0) : end])


def _numbered(text: str) -> str:
    return "\n".join(f"{i:6d}\t{line}" for i, line in enumerate(text.splitlines(), 1))


def _resolve_shell(command: str, vfs: VirtualFS) -> Resolution | None:
    """Handle simple single read commands (cat/head/sed -n/nl/ls) against the VFS."""
    if any(tok in command for tok in ("|", ">", ";", "&&", "||", "$(", "`")):
        return None
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    if not argv:
        return None
    prog, args = argv[0], argv[1:]

    def read_all(paths: list[str], numbered: bool = False) -> str | None:
        outs: list[str] = []
        for path in paths:
            content = vfs.read(path)
            if content is None:
                return None
            outs.append(_numbered(content) if numbered and not content.startswith("Error:") else content)
        return "\n".join(outs)

    out: str | None = None
    if prog == "cat":
        numbered = "-n" in args
        paths = [a for a in args if not a.startswith("-")]
        out = read_all(paths, numbered) if paths else None
    elif prog == "nl":
        paths = [a for a in args if not a.startswith("-")]
        out = read_all(paths, numbered=True) if len(paths) == 1 else None
    elif prog == "head":
        n, paths = 10, []
        it = iter(args)
        for a in it:
            if a == "-n":
                n = int(next(it, "10"))
            elif re.fullmatch(r"-\d+", a):
                n = int(a[1:])
            elif a.startswith("-n") and a[2:].isdigit():
                n = int(a[2:])
            else:
                paths.append(a)
        if len(paths) == 1 and (content := vfs.read(paths[0])) is not None:
            out = content if content.startswith("Error:") else _line_slice(content, 1, n)
    elif prog == "sed" and len(args) == 3 and args[0] == "-n":
        m = re.fullmatch(r"(\d+),(\d+|\$)p", args[1])
        if m and (content := vfs.read(args[2])) is not None:
            end = None if m.group(2) == "$" else int(m.group(2))
            out = content if content.startswith("Error:") else _line_slice(content, int(m.group(1)), end)
    elif prog == "ls":
        flags = "".join(a[1:] for a in args if a.startswith("-") and not a.startswith("--"))
        paths = [a for a in args if not a.startswith("-")] or ["."]
        if len(paths) == 1 and not set(flags) & set("lRst"):
            out = vfs.listing(paths[0])
    if out is None:
        return None
    return Resolution(content=out, note=f"bash({command!r}) -> answered from the fixed filesystem")


def resolve(tc: ToolCall, vfs: VirtualFS, fixed_results: dict[str, str]) -> Resolution | None:
    """Deterministically answer `tc` if possible, else return None (auditor simulates it)."""
    fn, args = tc.function, tc.arguments or {}
    if fn in fixed_results:
        return Resolution(content=fixed_results[fn], note=f"{fn}(...) -> returned its fixed result")
    path = str(args.get("path") or args.get("file_path") or args.get("filename") or "")
    if fn in READ_TOOLS and path:
        content = vfs.read(path)
        if content is not None:
            return Resolution(content=content, note=f"{fn}({path!r}) -> returned file contents")
    if fn in LIST_TOOLS:
        listing = vfs.listing(path or str(args.get("directory") or "."))
        if listing is not None:
            return Resolution(content=listing, note=f"{fn}({path!r}) -> returned directory listing")
    if fn in WRITE_TOOLS and path and "content" in args:
        content = str(args["content"])
        preview = content if len(content) < 4000 else content[:4000] + "\n...[truncated]"
        return Resolution(
            content=vfs.write(path, content),
            note=f"{fn}({path!r}) -> file written. New contents:\n{preview}",
        )
    if fn in SHELL_TOOLS:
        command = str(args.get("command") or args.get("cmd") or "")
        return _resolve_shell(command, vfs) if command else None
    return None
