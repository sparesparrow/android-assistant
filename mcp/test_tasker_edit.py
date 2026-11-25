"""phone/tasker/tasker-edit.py: text surgery on Tasker's own (not quite XML) file. No phone."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "tasker_edit", Path(__file__).parent.parent / "phone" / "tasker" / "tasker-edit.py")
te = importlib.util.module_from_spec(spec)
spec.loader.exec_module(te)

# Tasker writes attributes without a space between them, and keeps everything on one line
RAW = ('<TaskerData sr="" dvi="1"tv="6.7.9">'
       '<Task sr="task1"><id>1</id><nme>Keep Me</nme><Action sr="act0"ve="7"><code>130</code>'
       '<Str sr="arg0"ve="3">Shared</Str></Action></Task>'
       '<Task sr="task2"><id>2</id><nme>Shared</nme></Task>'
       '<Task sr="task3"><id>3</id><nme>Old Bot</nme></Task>'
       '<Task sr="task4"><id>4</id><Action sr="act0"ve="7"><code>548</code></Action></Task>'
       '<Profile sr="prof10"ve="2"><id>10</id><mid0>1</mid0><nme>Keep</nme></Profile>'
       '<Profile sr="prof11"ve="2"><id>11</id><mid0>4</mid0><nme>Old Trigger</nme></Profile>'
       '<Scene sr="sceneOld UI"><nme>Old UI</nme><ButtonElement sr="elements0"><clickTask>3</clickTask></ButtonElement></Scene>'
       '<Project sr="proj0"ve="2"><name>Main</name><pids>10</pids><tids>1,2</tids></Project>'
       '<Project sr="proj1"ve="2"><scenes>Old UI</scenes><enbl>false</enbl>\n<name>Old</name><pids>11</pids><tids>3</tids></Project>'
       '</TaskerData>')


def test_delete_project_removes_contents_and_keeps_the_rest_byte_for_byte():
    c = te.Config(RAW)
    assert c.remove_project("Old") == {"profiles": 1, "tasks": 1, "scenes": 1}
    assert set(c.projects) == {"Main"} and set(c.tasks) == {"1", "2"} and not c.scenes
    assert "11" not in c.profiles and "4" not in c.tasks       # anonymous enter task went too
    assert c.raw.startswith('<TaskerData sr="" dvi="1"tv="6.7.9"><Task sr="task1">')
    assert '<Project sr="proj0"ve="2"><name>Main</name><pids>10</pids><tids>1,2</tids></Project>' in c.raw
    assert c.problems() == []


def test_perform_task_target_is_protected():
    c = te.Config(RAW)
    assert c.users_of("2") == ["task Keep Me"]
    c.remove_task("2")
    assert any("Perform Task 'Shared'" in p for p in c.problems())


def test_move_profile_takes_its_task_along():
    c = te.Config(RAW)
    c.move_profile("10", "Old")
    assert c.project_of("profile", "10") == "Old" and c.project_of("task", "1") == "Old"
    assert c.problems() == []


def test_disabled_project_is_visible():
    assert te.Config(RAW).projects["Old"].findtext("enbl") == "false"


def test_ambiguous_or_missing_names_stop():
    with pytest.raises(SystemExit):
        te.Config(RAW).find_task("Nope")
