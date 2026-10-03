import json

import pytest

import autocode.cli
from autocode.core import (
    AgentError,
    apply_changes,
    collect_repository,
    create_diff,
    parse_json,
    reject_secrets,
    repository_secret_findings,
    safe_target,
    secret_findings,
    validate_changes,
)


def test_collect_repository_skips_sensitive_and_generated_files(tmp_path):
    (tmp_path / "app.py").write_text("print('ok')", encoding="utf-8")
    (tmp_path / ".env").write_text("GROQ_API_KEY=secret", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "ignored.py").write_text("ignored", encoding="utf-8")

    assert collect_repository(tmp_path) == {"app.py": "print('ok')"}


def test_safe_target_rejects_parent_traversal(tmp_path):
    with pytest.raises(AgentError, match="outside"):
        safe_target(tmp_path, "../outside.txt")


def test_validate_changes_rejects_duplicate_paths(tmp_path):
    proposal = {"files": [{"path": "app.py", "content": "one"}, {"path": "app.py", "content": "two"}]}
    with pytest.raises(AgentError, match="Duplicate"):
        validate_changes(tmp_path, proposal)


def test_diff_and_apply_reviewed_changes(tmp_path):
    (tmp_path / "app.py").write_text("old\n", encoding="utf-8")
    changes = validate_changes(tmp_path, {"files": [{"path": "app.py", "content": "new\n"}]})

    assert "-old" in create_diff(tmp_path, changes)
    apply_changes(tmp_path, changes)
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "new\n"


def test_parse_json_accepts_markdown_fence():
    assert parse_json("```json\n{\"files\": []}\n```") == {"files": []}


def test_secret_findings_flags_credential_like_content():
    findings = secret_findings([{"path": "settings.py", "content": "api_key = 'abcdefghijk123'"}])
    assert findings


def test_reject_secrets_blocks_credentials_in_documents():
    with pytest.raises(AgentError, match="credential"):
        reject_secrets("api_key = 'abcdefghijk123'", "requirements")


def test_repository_secret_scan_only_reports_path():
    assert repository_secret_findings({"settings.py": "password = 'topsecretvalue'"}) == ["settings.py"]


def test_run_audit_json_round_trips(tmp_path):
    record = {"state": "INTAKE", "phases": []}
    path = tmp_path / "audit.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8")) == record
