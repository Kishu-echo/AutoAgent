"""Repository inspection, guarded edits, validation, and run records."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

IGNORED_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".autocode", ".uv-cache", "dist", "build", "autonomous_coding_agent.egg-info",
}
MAX_FILES = 200
MAX_FILE_BYTES = 50_000
MAX_CONTEXT_BYTES = 400_000
MAX_PATCH_FILES = 40
SENSITIVE_NAME = re.compile(
    r"(^\.env($|\.)|\.pem$|\.key$|\.p12$|\.pfx$|id_rsa|id_ed25519|credentials?\.json$|secrets?\.)",
    re.IGNORECASE,
)
SECRET_VALUE = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*['\"]?\S{8,}"
)


class AgentError(Exception):
    """An actionable error for the CLI user."""


def collect_repository(root: Path) -> dict[str, str]:
    """Read a bounded set of text files beneath root, skipping symlinks and secrets."""
    root = root.resolve(strict=True)
    files: dict[str, str] = {}
    total = 0
    for path in sorted(root.rglob("*")):
        relative_parts = path.relative_to(root).parts
        if any(part in IGNORED_DIRS for part in relative_parts) or SENSITIVE_NAME.search(path.name):
            continue
        if path.is_symlink() or not path.is_file():
            continue
        try:
            size = path.stat().st_size
            if size > MAX_FILE_BYTES or total + size > MAX_CONTEXT_BYTES:
                continue
            content = path.read_bytes()
            decoded = content.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        files[path.relative_to(root).as_posix()] = decoded
        total += size
        if len(files) >= MAX_FILES:
            break
    return files


def inspect_repository(root: Path, files: dict[str, str]) -> dict[str, Any]:
    """Collect intake metadata using only read-only Git commands."""
    info: dict[str, Any] = {
        "root": str(root.resolve()),
        "file_count": len(files),
        "files": list(files),
        "application_type": [],
        "test_files": [name for name in files if re.search(r"(^|/)(tests?|spec)(/|_)|(^|/)test_.*\.py$", name, re.I)],
        "build_and_ci": [name for name in files if name in {"pyproject.toml", "package.json", "Cargo.toml", "go.mod", "Makefile", ".github/workflows/ci.yml"}],
        "git": {"available": False, "branch": None, "working_tree": "unknown"},
    }
    suffixes = {Path(name).suffix.lower() for name in files}
    if ".py" in suffixes:
        info["application_type"].append("Python")
    if ".ts" in suffixes or ".tsx" in suffixes or ".js" in suffixes:
        info["application_type"].append("JavaScript/TypeScript")
    if ".go" in suffixes:
        info["application_type"].append("Go")
    try:
        branch = subprocess.run(["git", "branch", "--show-current"], cwd=root, text=True, capture_output=True, timeout=5, check=False)
        status = subprocess.run(["git", "status", "--short"], cwd=root, text=True, capture_output=True, timeout=5, check=False)
        if branch.returncode == 0 and status.returncode == 0:
            info["git"] = {
                "available": True,
                "branch": branch.stdout.strip() or "(detached HEAD)",
                "working_tree": "clean" if not status.stdout.strip() else "has changes",
                "status": status.stdout.strip(),
            }
    except (OSError, subprocess.SubprocessError):
        pass
    return info


def make_client(model: str) -> Callable[[str], str]:
    """Create a Groq-backed Responses API text generator."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError as exc:
        raise AgentError("python-dotenv is missing. Install with: python -m pip install -e .") from exc
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise AgentError("The OpenAI SDK is missing. Install with: python -m pip install -e .") from exc
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise AgentError("GROQ_API_KEY is missing. Add it to .env, or use --offline.")
    client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")

    def generate(prompt: str) -> str:
        try:
            response = client.responses.create(model=model, input=prompt, store=False)
        except Exception as exc:
            raise AgentError(f"Model request failed: {exc}") from exc
        return response.output_text

    return generate


def parse_json(text: str) -> Any:
    """Parse a JSON response, optionally wrapped in a Markdown code fence."""
    content = text.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1]
        if content.endswith("```"):
            content = content[:-3].strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise AgentError(f"The model returned invalid JSON: {exc}") from exc


def safe_target(root: Path, relative: str) -> Path:
    """Resolve a model-proposed path safely beneath the repository root."""
    root = root.resolve(strict=True)
    if not isinstance(relative, str) or not relative.strip():
        raise AgentError("A proposed file path was empty or invalid.")
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise AgentError(f"Refusing path outside the repository: {relative}")
    target = root / rel
    if target.exists() and target.is_symlink():
        raise AgentError(f"Refusing to write through a symlink: {relative}")
    if not target.resolve().is_relative_to(root):
        raise AgentError(f"Refusing path outside the repository: {relative}")
    return target


def validate_changes(root: Path, raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, dict) or not isinstance(raw.get("files"), list):
        raise AgentError("Expected a JSON object with a 'files' array.")
    if len(raw["files"]) > MAX_PATCH_FILES:
        raise AgentError(f"The proposal exceeds the limit of {MAX_PATCH_FILES} files.")
    seen: set[str] = set()
    changes = []
    for item in raw["files"]:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("content"), str):
            raise AgentError("Each proposed file must contain string 'path' and 'content' fields.")
        target = safe_target(root, item["path"])
        key = target.relative_to(root.resolve()).as_posix()
        if key in seen:
            raise AgentError(f"Duplicate proposed path: {key}")
        seen.add(key)
        changes.append({"path": key, "content": item["content"]})
    return changes


def create_diff(root: Path, changes: list[dict[str, str]]) -> str:
    chunks: list[str] = []
    for item in changes:
        path = item["path"]
        target = safe_target(root, path)
        old = target.read_text(encoding="utf-8") if target.is_file() else ""
        new = item["content"]
        chunks.extend(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                           fromfile=f"a/{path}", tofile=f"b/{path}"))
    return "".join(chunks) or "(No changes proposed.)\n"


def apply_changes(root: Path, changes: list[dict[str, str]]) -> None:
    for item in changes:
        target = safe_target(root, item["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(item["content"], encoding="utf-8", newline="")


def run_approved_command(command: str, root: Path) -> subprocess.CompletedProcess[str]:
    """Run an explicitly approved command without a shell and with a timeout."""
    import shlex
    try:
        args = shlex.split(command, posix=os.name != "nt")
    except ValueError as exc:
        raise AgentError(f"Could not parse command: {exc}") from exc
    if not args:
        raise AgentError("Command cannot be empty.")
    try:
        return subprocess.run(args, cwd=root, text=True, capture_output=True, timeout=600, check=False)
    except subprocess.TimeoutExpired as exc:
        raise AgentError("Approved command exceeded the 10 minute limit.") from exc
    except OSError as exc:
        raise AgentError(f"Could not start command: {exc}") from exc


def secret_findings(changes: list[dict[str, str]]) -> list[str]:
    """Flag common credential-like values in proposed file contents."""
    findings = []
    for item in changes:
        if SENSITIVE_NAME.search(Path(item["path"]).name):
            findings.append(f"Sensitive-looking filename: {item['path']}")
        if SECRET_VALUE.search(item["content"]):
            findings.append(f"Possible secret value in: {item['path']}")
    return findings


def repository_secret_findings(files: dict[str, str]) -> list[str]:
    """Report paths with common inline credential patterns without exposing values."""
    return [name for name, content in files.items() if SECRET_VALUE.search(content)]


def new_run_record(root: Path, requirement: str, intake: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    """Create a local audit record without copying repository contents or secrets."""
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_dir = safe_target(root, f".autocode/runs/{run_id}")
    run_dir.mkdir(parents=True, exist_ok=False)
    record = {
        "run_id": run_id,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "requirement_sha256": hashlib.sha256(requirement.encode("utf-8")).hexdigest(),
        "state": "INTAKE",
        "phases": [],
        "approvals": [],
        "repository": intake,
        "limitations": ["No source file contents, API keys, or test output are stored in the audit record."],
    }
    return run_dir / "audit.json", record


def reject_secrets(text: str, label: str) -> None:
    """Prevent common credential assignments in supplied prose from reaching the model."""
    if SECRET_VALUE.search(text):
        raise AgentError(f"Possible credential material found in {label}; remove it before sending this input to the model.")


def save_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
