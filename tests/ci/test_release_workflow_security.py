"""Release workflow secret-scope regressions; parses the YAML text, no network or secrets."""
from __future__ import annotations

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
SECRET_NAMES = ("DUBFLOW_RELEASE_PRIVATE_KEY", "DUBFLOW_UPDATE_SIGNING_KEY")


def _blocks(lines: list[str], indent: int, pattern: str) -> dict[str, list[str]]:
    """Split lines into named blocks that start at exactly `indent` spaces."""
    head = re.compile(" " * indent + pattern)
    blocks: dict[str, list[str]] = {}
    name = None
    for line in lines:
        match = head.match(line)
        if match:
            name = match.group(1).strip()
            blocks[name] = [line]
        elif name is not None:
            if line.strip() and len(line) - len(line.lstrip()) < indent:
                name = None
            else:
                blocks[name].append(line)
    return blocks


def _key(block: list[str], key: str, indent: int) -> str | None:
    prefix = " " * indent + key + ":"
    for line in block:
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return None


def _env(block: list[str], indent: int) -> dict[str, str]:
    """The `env:` mapping directly under a job (indent 4) or step (indent 8)."""
    values: dict[str, str] = {}
    inside = False
    for line in block:
        if line.startswith(" " * indent + "env:"):
            inside = True
            continue
        if inside:
            if line.startswith(" " * (indent + 2)) and not line.startswith(" " * (indent + 3)):
                name, _, value = line.strip().partition(":")
                values[name] = value.strip()
            elif line.strip():
                inside = False
    return values


def evaluate(expression: str | None, context: dict[str, object]) -> object:
    """Evaluate the small GitHub expression subset the release workflow uses."""
    if expression is None:
        return True
    text = expression.strip()
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2]
    elif "${{" not in text:
        return text
    out, index = [], 0
    token = re.compile(r"\s+|'[^']*'|&&|\|\||==|!=|[()]|[A-Za-z_][A-Za-z0-9_.\-]*")
    while index < len(text):
        match = token.match(text, index)
        if not match:
            raise ValueError(f"unsupported expression: {expression}")
        part = match.group(0)
        index = match.end()
        if part.isspace():
            continue
        if part == "&&":
            out.append(" and ")
        elif part == "||":
            out.append(" or ")
        elif part in ("==", "!=", "(", ")") or part.startswith("'"):
            out.append(f" {part} ")
        elif part in ("true", "false"):
            out.append(" True " if part == "true" else " False ")
        else:
            if part not in context:
                raise KeyError(f"no test value for {part}")
            out.append(f" _ctx[{part!r}] ")
    return eval("".join(out), {"__builtins__": {}}, {"_ctx": context})


def scenario(event: str, ref: str, channel: str = "", publish: bool = False) -> dict[str, object]:
    context: dict[str, object] = {
        "github.event_name": event,
        "github.ref": ref,
        "github.ref_name": ref.rsplit("/", 1)[-1],
        "github.sha": "0" * 40,
        "github.token": "token-placeholder",
        "inputs.version": "9.9.9" if event == "workflow_dispatch" else "",
        "inputs.channel": channel if event == "workflow_dispatch" else "",
        "inputs.publish": publish if event == "workflow_dispatch" else "",
        "env.RELEASE_VERSION": "9.9.9",
    }
    for name in SECRET_NAMES:
        context[f"secrets.{name}"] = f"<{name}>"
    return context


SCENARIOS = {
    "feature-branch-dispatch": scenario("workflow_dispatch", "refs/heads/agent/issue-173-updater", "candidate"),
    "feature-branch-stable-publish": scenario("workflow_dispatch", "refs/heads/agent/evil", "stable", True),
    "pull-request-ref-dispatch": scenario("workflow_dispatch", "refs/pull/1/merge", "stable", True),
    "tag-push": scenario("push", "refs/tags/v9.9.9"),
    "main-candidate": scenario("workflow_dispatch", "refs/heads/main", "candidate"),
    "main-candidate-publish": scenario("workflow_dispatch", "refs/heads/main", "candidate", True),
    "main-stable": scenario("workflow_dispatch", "refs/heads/main", "stable"),
    "main-stable-publish": scenario("workflow_dispatch", "refs/heads/main", "stable", True),
}


class ReleaseWorkflowSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
        start = lines.index("jobs:")
        cls.header = lines[:start]
        cls.jobs = _blocks(lines[start + 1:], 2, r"([A-Za-z0-9_-]+):\s*$")
        cls.steps = {job: _blocks(block, 6, r"- name: (.+)$") for job, block in cls.jobs.items()}

    def job_runs(self, job: str, context: dict[str, object]) -> bool:
        return bool(evaluate(_key(self.jobs[job], "if", 4), context))

    def exposed_secrets(self, context: dict[str, object]) -> dict[str, set[str]]:
        """Secret values any started step receives, keyed by step name."""
        exposed: dict[str, set[str]] = {}
        for job, steps in self.steps.items():
            if not self.job_runs(job, context):
                continue
            job_env = _env(self.jobs[job], 4)
            for name, block in steps.items():
                if not evaluate(_key(block, "if", 8), context):
                    continue
                values = {str(evaluate(value, context)) for value in {**job_env, **_env(block, 8)}.values()}
                found = {secret for secret in SECRET_NAMES if f"<{secret}>" in values}
                if found:
                    exposed[name] = found
        return exposed

    def test_secrets_are_never_workflow_or_job_wide(self) -> None:
        self.assertFalse([line for line in self.header if "secrets." in line])
        for job, block in self.jobs.items():
            self.assertFalse([v for v in _env(block, 4).values() if "secrets." in v], job)

    def test_each_secret_is_referenced_by_exactly_one_step(self) -> None:
        owners = {"DUBFLOW_RELEASE_PRIVATE_KEY": "Build final release bundle after qualification",
                  "DUBFLOW_UPDATE_SIGNING_KEY": "Sign the in-app update manifest"}
        for secret, step in owners.items():
            users = [(job, name) for job, steps in self.steps.items()
                     for name, block in steps.items() if any(f"secrets.{secret}" in line for line in block)]
            self.assertEqual(users, [("build", step)], secret)

    def test_untrusted_dispatch_refs_start_no_job(self) -> None:
        for label in ("feature-branch-dispatch", "feature-branch-stable-publish", "pull-request-ref-dispatch"):
            context = SCENARIOS[label]
            for job in self.jobs:
                self.assertFalse(self.job_runs(job, context), f"{label} starts {job}")
            self.assertEqual(self.exposed_secrets(context), {}, label)

    def test_tag_push_builds_without_signing_secrets(self) -> None:
        context = SCENARIOS["tag-push"]
        self.assertTrue(self.job_runs("build", context))
        self.assertFalse(self.job_runs("publish", context))
        self.assertEqual(self.exposed_secrets(context), {})

    def test_trusted_main_dispatch_limits_each_secret_to_its_step(self) -> None:
        self.assertEqual(self.exposed_secrets(SCENARIOS["main-candidate"]),
                         {"Sign the in-app update manifest": {"DUBFLOW_UPDATE_SIGNING_KEY"}})
        self.assertEqual(self.exposed_secrets(SCENARIOS["main-stable"]), {
            "Build final release bundle after qualification": {"DUBFLOW_RELEASE_PRIVATE_KEY"},
            "Sign the in-app update manifest": {"DUBFLOW_UPDATE_SIGNING_KEY"},
        })

    def test_publish_requires_main_dispatch_publish_and_stable(self) -> None:
        publishing = {label for label, context in SCENARIOS.items() if self.job_runs("publish", context)}
        self.assertEqual(publishing, {"main-stable-publish"})
        self.assertEqual(_key(self.jobs["publish"], "needs", 4), "build")

    def test_untrusted_stable_build_fails_closed_first(self) -> None:
        first = next(iter(self.steps["build"].values()))
        self.assertIn("- name: Fail closed on untrusted release refs", first[0])
        guard = _env(first, 8)["TRUSTED_RELEASE"]
        self.assertIs(evaluate(guard, SCENARIOS["tag-push"]), False)
        self.assertIs(evaluate(guard, SCENARIOS["main-stable"]), True)
        self.assertTrue(any("throw" in line for line in first))


if __name__ == "__main__":
    unittest.main()
