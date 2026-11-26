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


# --- known token formats (#143). Fakes are assembled at run time, so no token-shaped
# literal sits in the repository for secret scanners or readers to trip over.
def fake(prefix: str, n: int, alphabet: str = "A1b2C3d4E5f6G7h8") -> str:
    return prefix + (alphabet * (n // len(alphabet) + 1))[:n]


KNOWN_FORMATS = {
    "aws access key": fake("AK" + "IA", 16, "ABCDEFGHIJ2345KL"),
    "google oauth token": fake("ya" + "29.", 40),
    "google refresh token": fake("1//" + "0g", 40),
    "gitlab token": fake("gl" + "pat-", 20),
    "github fine-grained token": fake("github" + "_pat_", 60),
    "anthropic key": fake("sk-" + "ant-api03-", 40),
    "huggingface token": fake("h" + "f_", 34),
    "npm token": fake("np" + "m_", 36),
    "short bearer token": "Bearer " + fake("", 12),
    "private key block": "-----BEGIN " + "OPENSSH PRIVATE KEY-----\n" + fake("", 70) + "\nshort\n-----END " + "OPENSSH PRIVATE KEY-----",
}


import pytest  # noqa: E402


@pytest.mark.parametrize("name", sorted(KNOWN_FORMATS))
def test_known_token_formats_are_redacted(name):
    secret = KNOWN_FORMATS[name]
    out = ne.Redactor([])(f"before {secret} after")
    assert secret not in out and "[redacted]" in out, out
    assert out.startswith("before ") and out.endswith(" after")


@pytest.mark.parametrize("text, secret, kept", [
    ("https://admin:" + "hunter22@example.com/x", "hunter22", "https://"),
    ("GET /v1?api_key=" + "s3cr3tv4l&x=1", "s3cr3tv4l", "api_key="),
    ("curl 'https://h/x?token=" + "abc12345'", "abc12345", "token="),
    ("password=" + "hunter22 next", "hunter22", "password="),
    ('"client_secret": "' + 'zzTop1234"', "zzTop1234", "client_secret"),
    ("Authorization: Basic " + "dXNlcjpwYXNz", "dXNlcjpwYXNz", "Authorization: Basic"),
])
def test_credentials_in_urls_and_key_value_pairs(text, secret, kept):
    out = ne.Redactor([])(text)
    assert secret not in out and kept in out, out


@pytest.mark.parametrize("text", [
    "Tasker 6.7.9 on Android 16, build 2026.11.26",
    'arg0="%api_key"; arg1="%greeting"',
    "https://github.com/sparesparrow/android-assistant/pull/9",
    "Perform Task 'Claude Say' with %par1",
    "the password field stays empty; token count 12",
])
def test_ordinary_text_is_left_alone(text):
    assert ne.Redactor([])(text) == text
