"""notebook_export: redaction and the Tasker digest. No phone."""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import notebook_export as ne  # noqa: E402

TOKEN = "Abc_def-0123456789xyzABCDEFG"
XML = f"""<TaskerData sr="" dvi="1" tv="6.7.9">
<Task sr="task1"><id>1</id><nme>Call API</nme>
 <Action sr="act0" ve="7"><code>547</code><Str sr="arg0" ve="3">%api_key</Str><Str sr="arg1" ve="3">plainpass1</Str></Action>
 <Action sr="act1" ve="7"><code>339</code><Str sr="arg0" ve="3">https://api.example.com/v1?x=1</Str><Str sr="arg1" ve="3">Authorization: Bearer sk-abcdefghijklmnopqrstuv</Str></Action>
 <Action sr="act2" ve="7"><code>129</code><Str sr="arg0" ve="3">shell("X-Token: {TOKEN}"); call("+420 777 123 456", "me@example.org");</Str></Action>
 <Action sr="act3" ve="7"><code>547</code><Str sr="arg0" ve="3">%greeting</Str><Str sr="arg1" ve="3">hello</Str></Action>
</Task>
<Profile sr="prof2"><id>2</id><mid0>1</mid0><nme>On assist</nme><Event sr="con0" ve="2"><code>201</code></Event></Profile>
<Project sr="proj0" ve="2"><name>Demo</name><pids>2</pids><tids>1</tids></Project>
</TaskerData>"""


def test_digest_redacts_and_names_actions():
    red = ne.Redactor([TOKEN])
    d = ne.tasker_digest(ET.fromstring(XML), red)["Demo"]
    for secret in ("plainpass1", "sk-abcdefghijklmnopqrstuv", TOKEN, "777 123 456", "me@example.org"):
        assert secret not in d
    assert 'arg0="%greeting"; arg1="hello"' in d            # ordinary variables stay
    assert "Profile: On assist" in d and "Assistance Request" in d and "enter task: Call API" in d
    assert "1. Variable Set" in d and "2. HTTP Request" in d and "3. JavaScriptlet" in d
    assert red.count["known token"] == 1 and red.count["secret variable value"] == 1


def test_chunks_stay_under_limit(monkeypatch):
    monkeypatch.setattr(ne, "CHUNK", 200)
    parts = ne.chunk_projects({"A": "x" * 150, "B": "y" * 150,
                               "Big": "## Big\n" + "".join(f"\n#### Task: t{i}\n" + "z" * 90 for i in range(5))})
    assert len(parts) >= 4 and all("Tasker configuration" in body for _, body in parts)
