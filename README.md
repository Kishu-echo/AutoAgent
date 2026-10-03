# Autonomous Coding Agent

A review-first coding agent that turns natural-language requirements into a proposed code change through explicit engineering review stages.

## Requirements

- Python 3.10+
- A Groq API key in a local `.env` file as `GROQ_API_KEY`

Install from this directory with `python -m pip install -e ".[test]"`. Add your Groq API key to `.env` as `GROQ_API_KEY`. The agent uses the OpenAI Python SDK pointed at Groq's OpenAI-compatible Responses API. It defaults to `openai/gpt-oss-120b`; set `GROQ_MODEL` or pass `--model` to select another available Groq model. The `test` extra installs pytest for the agent's own regression suite.

## Use

From the root of the repository you want to change:

```powershell
autocode "Add a health check endpoint that returns JSON"
```

For a requirements document and engineering standards, pass file paths:

```powershell
autocode --requirements-doc .\requirements.md --standards .\engineering-standards.md --target-environment local
```

The agent follows intake, analysis, requirements approval, design, design approval, build, test, security review, release candidate, deployment approval, deployment, evaluation, and acceptance phases. It records a run under `.autocode/runs/` and asks you to approve requirements, design, the exact code diff, the exact validation command, the release candidate, and final acceptance. Unresolved analysis or design questions stop the run before code changes. It inspects Git status and asks before proceeding in place if the workspace is dirty or is not a Git repository.

Test and validation commands are entered by the user and run only after explicit approval. Commands are launched without a shell, with a 10-minute timeout. Review the final changes with `git diff`.

To skip the Groq API, specify `--offline`; this produces a read-only repository intake report and does not generate or apply a change.

## Safety boundaries

- Repository files are read with size and count limits; common credential files and generated directories are excluded from model context.
- Model output is treated as untrusted data. It cannot invoke tools or execute commands.
- Paths are checked against the selected repository root, including symlink traversal.
- Files containing source code, documents, or configuration are sent to the configured Groq model; do not use this agent with data you are not authorized to share with that service.
- File changes, test commands, release-candidate acceptance, and final acceptance have separate human approval gates.
- The agent does not commit, push, create pull requests, deploy, or run external security scanners. Deployment phases are recorded as not applicable; no environment is changed.
- The security review is model-assisted static review, not a substitute for approved security scanners or an independent audit.
- Run artifacts store requirements hashes, phase summaries, approvals, and reports, not source snapshots or test output. `.autocode/` is Git-ignored by default.

The specialist roles in the master prompt are represented as review workstreams in one model workflow; they are not separate, independently verified agents. Start with a clean Git branch/worktree so you can review the change independently.
