"""The advisor agents read untrusted app answers, so they must not be able to act on them
(mcp-prompts#143: separate reading untrusted text from acting). No phone needed."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ADVISORS = ["gemini", "deepseek", "kimi", "grok", "chatgpt", "perplexity"]
# Tools that tap, type, open, publish or run something on the user's behalf.
ACTING = {"act", "open", "run_tasker_task", "mqtt_publish", "phone_command", "notify", "speak", "listen"}


def frontmatter_tools(name: str) -> list[str]:
    text = (ROOT / "agents" / f"{name}.md").read_text()
    m = re.search(r"^tools:\s*(.+)$", text, re.M)
    assert m, f"agents/{name}.md has no tools line"
    return [t.strip() for t in m.group(1).split(",")]


def server_tools() -> set[str]:
    src = (ROOT / "mcp" / "server.py").read_text()
    return set(re.findall(r"@mcp\.tool\([^\n]*\)\ndef (\w+)\(", src))


@pytest.mark.parametrize("name", ADVISORS)
def test_advisors_cannot_act(name):
    tools = {t.split("__")[-1] for t in frontmatter_tools(name) if t.startswith("mcp__android-assistant__")}
    assert not tools & ACTING, f"{name} can act: {sorted(tools & ACTING)}"
    assert "scroll" in tools, f"{name} needs scroll to read long answers"


@pytest.mark.parametrize("name", ADVISORS)
def test_advisor_instructions_do_not_use_act(name):
    body = (ROOT / "agents" / f"{name}.md").read_text()
    assert "act(" not in body, f"agents/{name}.md still tells the agent to call act()"


@pytest.mark.parametrize("name", ADVISORS + ["phone"])
def test_every_listed_tool_exists(name):
    have = server_tools()
    for t in frontmatter_tools(name):
        if t.startswith("mcp__android-assistant__"):
            assert t.split("__")[-1] in have, f"agents/{name}.md lists unknown tool {t}"


def test_phone_agent_keeps_act():
    assert "mcp__android-assistant__act" in frontmatter_tools("phone")
