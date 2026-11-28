#!/usr/bin/env python3
"""Edit Tasker's configuration directly (root), much faster than driving Tasker's UI.

Tasker keeps its whole configuration (projects, profiles, tasks, scenes) in one file,
/data/data/net.dinglisch.android.taskerm/files/autobackup.xml, which it reads at start and
rewrites on every save. Global variables live elsewhere (shared_prefs/...varry.xml) and are
not touched. This tool:

  pull                       copy the live file to the work dir (and a timestamped backup)
  ls [projects|profiles|tasks|scenes]       list what is there (ids, names, projects)
  edit OPS... [--dry-run]    apply operations to the work copy, check references, show a summary
  push                       force-stop Tasker, write the work copy in place (same inode, so
                             owner and SELinux label stay), start Tasker, verify it loaded it
  restore FILE               push a backup back the same way
  tile N TASK [LABEL]        point Tasker's Quick Settings tile N (0, 1, 2) at TASK; the tile
                             settings live in shared_prefs/...preffy.xml, written the same way

push, restore and tile write as root, so each asks for "yes" on the controlling terminal
(/dev/tty). A headless agent (claude -p, a Tasker run, ssh without -t) has none and gets exit 3:
a person runs these commands in Termux. pull, ls and edit need no confirmation.

OPS (repeatable):
  --delete-project NAME      the project and everything in it (profiles, tasks, scenes)
  --delete-profile ID|NAME   a profile (its enter/exit tasks too if nothing else uses them)
  --delete-task ID|NAME
  --move-profile ID|NAME PROJECT    also moves its enter/exit tasks
  --add-tasks FILE.prj.xml PROJECT  copy the named tasks of a built project file (e.g.
                             build-claude-agents.py output) into PROJECT; tasks whose name
                             already exists are skipped, new ones get fresh ids
  --enable-project NAME / --disable-project NAME are NOT offered: use Tasker's UI switch.

Edits are text surgery on Tasker's own file (it is not quite XML: attributes are written without
a space between them), so everything not edited stays byte-for-byte as Tasker wrote it.
Before writing, references are checked: profiles' enter/exit tasks, project lists, scene
button tasks and Perform Task (by name) calls from kept tasks. A dangling reference stops the
edit unless --force.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

PKG = "net.dinglisch.android.taskerm"
LIVE = f"/data/data/{PKG}/files/autobackup.xml"
PREFS = f"/data/data/{PKG}/shared_prefs/net.dinglisch.android.tasker.preffy.xml"
SERIAL = "127.0.0.1:5555"
WORK = Path("/root/phone/state/tasker-edit")
BACKUPS = Path("/root/phone/state/tasker-backups")


# ------------------------------------------------------------------ device
def ask_tty(question: str) -> str:
    """Ask on the controlling terminal, not stdin. A headless agent (claude -p, a Tasker run, ssh
    without -t) has no /dev/tty, so it cannot answer; piping "yes" into stdin does not count."""
    with open("/dev/tty", "r+") as tty:
        tty.write(question)
        tty.flush()
        return tty.readline().strip()


def confirm_root_write(what: str) -> None:
    """Root writes (push, restore, tile) need a person at a terminal to type yes (mcp-prompts#143)."""
    try:
        answer = ask_tty(f"{what}\nThis writes Tasker's files as root (su) and restarts Tasker. Type yes to go ahead: ")
    except OSError:
        print("tasker-edit: this writes Tasker's files as root and needs a yes typed at a terminal; "
              "a headless agent cannot give it. Run the same command yourself in Termux (`debian`).",
              file=sys.stderr)
        raise SystemExit(3)
    if answer != "yes":
        print("tasker-edit: not confirmed, nothing written", file=sys.stderr)
        raise SystemExit(3)


def adb(cmd: str, inp: bytes | None = None, timeout: int = 60) -> bytes:
    p = subprocess.run(["adb", "-s", SERIAL, "shell", cmd], input=inp, capture_output=True,
                       timeout=timeout)
    if p.returncode != 0:
        sys.exit(f"adb: {cmd[:80]}: {p.stderr.decode(errors='replace').strip()}")
    return p.stdout


def read_live() -> str:
    raw = adb(f"su -c 'cat {LIVE}'").decode("utf-8")
    if not raw.startswith("<TaskerData"):
        sys.exit("the live file does not look like Tasker data")
    return raw


def write_live(text: str, path: str = LIVE) -> None:
    data = text.encode("utf-8")
    tmp = "/data/local/tmp/tasker-edit.xml"
    adb(f"cat > {tmp}", inp=data)
    size = adb(f"stat -c %s {tmp}").decode().strip()
    if size != str(len(data)):
        sys.exit(f"upload incomplete ({size} of {len(data)} bytes); live file untouched")
    # Tasker is restarted by the system within a second or two of a force-stop (its
    # accessibility service gets re-bound), and it reads its config only at start. So stop it,
    # write, and stop it again in one root command: any instance that slipped in before the
    # write finished dies, and the next start reads the new file.
    adb(f"su -c 'am force-stop {PKG}; cat {tmp} > {path}; am force-stop {PKG}; rm -f {tmp}'")
    got = adb(f"su -c 'md5sum {path}'").decode().split()[0]
    import hashlib
    if got != hashlib.md5(data).hexdigest():
        sys.exit("written file differs from the work copy; restore a backup")
    # the main activity; the LAUNCHER category also matches the "Secondary App" entry, which
    # would fire the Secondary App Opened event
    adb(f"am start -n {PKG}/.Tasker >/dev/null 2>&1")
    time.sleep(4)
    # Tasker must have started after the write, or it is running the old config
    st = adb(f"su -c 'stat -c %Y {path}; p=$(pidof {PKG}); [ -n \"$p\" ] && stat -c %Y /proc/$p'").decode().split()
    if len(st) == 2 and int(st[1]) < int(st[0]):
        sys.exit("Tasker started before the new file was written. The live file already matches the "
                 "work copy, so `push --force` is safe: it writes the same file and restarts Tasker")


# ------------------------------------------------------------------ parsing
def is_tasker_data(raw: str) -> bool:
    """True for a Tasker config (autobackup.xml); False for prefs files and anything else."""
    try:
        return tree(raw).tag == "TaskerData"   # tree(): Tasker omits spaces between attributes
    except ET.ParseError:
        return False


def tree(raw: str) -> ET.Element:
    return ET.fromstring(re.sub(r'"(?=[A-Za-z_][\w-]*=")', '" ', raw))


class Config:
    def __init__(self, raw: str):
        self.raw = raw
        self.reload()

    def reload(self):
        r = tree(self.raw)
        self.tasks = {t.findtext("id"): t for t in r.findall("Task")}
        self.profiles = {p.findtext("id"): p for p in r.findall("Profile")}
        self.scenes = {s.findtext("nme"): s for s in r.findall("Scene")}
        self.projects = {p.findtext("name"): p for p in r.findall("Project")}

    def ids(self, proj: ET.Element, tag: str) -> list[str]:
        return [x for x in (proj.findtext(tag) or "").split(",") if x]

    def project_of(self, kind: str, key: str) -> str | None:
        tag = {"profile": "pids", "task": "tids", "scene": "scenes"}[kind]
        return next((n for n, p in self.projects.items() if key in self.ids(p, tag)), None)

    def find_profile(self, key: str) -> str:
        if key in self.profiles:
            return key
        hits = [i for i, p in self.profiles.items() if p.findtext("nme") == key]
        if len(hits) != 1:
            sys.exit(f"profile {key!r}: {'not found' if not hits else 'ambiguous ' + str(hits)}")
        return hits[0]

    def find_task(self, key: str) -> str:
        if key in self.tasks:
            return key
        hits = [i for i, t in self.tasks.items() if t.findtext("nme") == key]
        if len(hits) != 1:
            sys.exit(f"task {key!r}: {'not found' if not hits else 'ambiguous ' + str(hits)}")
        return hits[0]

    # -------------------------------------------------- text surgery
    def _cut(self, start_pat: str, end_tag: str) -> None:
        m = re.search(start_pat, self.raw, re.S)
        if not m:
            sys.exit(f"cannot find {start_pat} in the raw file")
        end = self.raw.index(end_tag, m.start()) + len(end_tag)
        self.raw = self.raw[:m.start()] + self.raw[end:]

    def _set_list(self, project: str, tag: str, values: list[str]) -> None:
        m = re.search(r"<Project\b[^>]*>(?:(?!</Project>).)*?<name>" + re.escape(project)
                      + r"</name>(?:(?!</Project>).)*?</Project>", self.raw, re.S)
        if not m:
            m = next((mm for mm in re.finditer(r"<Project\b.*?</Project>", self.raw, re.S)
                      if f"<name>{project}</name>" in mm.group(0)), None)
        if not m:
            sys.exit(f"project {project!r} not found in the raw file")
        block = m.group(0)
        new = ",".join(values)
        if re.search(f"<{tag}>.*?</{tag}>", block, re.S):
            block2 = re.sub(f"<{tag}>.*?</{tag}>", f"<{tag}>{new}</{tag}>" if new else "", block, flags=re.S)
        elif new:
            block2 = block.replace(f"<name>{project}</name>", f"<name>{project}</name><{tag}>{new}</{tag}>")
        else:
            block2 = block
        self.raw = self.raw[:m.start()] + block2 + self.raw[m.end():]

    def remove_task(self, tid: str) -> None:
        self._cut(rf'<Task sr="task{tid}"', "</Task>")
        proj = self.project_of("task", tid)
        if proj:
            self._set_list(proj, "tids", [x for x in self.ids(self.projects[proj], "tids") if x != tid])
        self.reload()

    def remove_profile(self, pid: str, with_tasks: bool = True) -> list[str]:
        p = self.profiles[pid]
        own = [t for t in (p.findtext("mid0"), p.findtext("mid1")) if t]
        self._cut(rf'<Profile sr="prof{pid}"', "</Profile>")
        proj = self.project_of("profile", pid)
        if proj:
            self._set_list(proj, "pids", [x for x in self.ids(self.projects[proj], "pids") if x != pid])
        self.reload()
        gone = []
        if with_tasks:
            for t in own:
                if t in self.tasks and not self.tasks[t].findtext("nme") and not self.users_of(t):
                    self.remove_task(t)       # anonymous task only this profile used
                    gone.append(t)
        return gone

    def remove_scene(self, name: str) -> None:
        self._cut(rf'<Scene sr="scene{re.escape(name)}"', "</Scene>")
        proj = self.project_of("scene", name)
        if proj:
            self._set_list(proj, "scenes", [x for x in self.ids(self.projects[proj], "scenes") if x != name])
        self.reload()

    def remove_project(self, name: str) -> dict:
        p = self.projects.get(name)
        if p is None:
            sys.exit(f"project {name!r} not found")
        pids, tids, scs = self.ids(p, "pids"), self.ids(p, "tids"), self.ids(p, "scenes")
        for pid in pids:
            if pid in self.profiles:
                self.remove_profile(pid, with_tasks=False)
        for sc in scs:
            if sc in self.scenes:
                self.remove_scene(sc)
        for tid in tids:
            if tid in self.tasks:
                self.remove_task(tid)
        # anonymous tasks of the deleted profiles live outside projects
        for tid in list(self.tasks):
            t = self.tasks[tid]
            if not t.findtext("nme") and tid not in sum((self.ids(x, "tids") for x in self.projects.values()), []) \
                    and not self.users_of(tid):
                self.remove_task(tid)
        self._cut(r'<Project sr="proj\d+"[^>]*>(?:(?!</Project>).)*?<name>' + re.escape(name) + "</name>",
                  "</Project>")
        self.renumber_projects()
        self.reload()
        return {"profiles": len(pids), "tasks": len(tids), "scenes": len(scs)}

    def renumber_projects(self) -> None:
        n = iter(range(10_000))
        self.raw = re.sub(r'<Project sr="proj\d+"', lambda m: f'<Project sr="proj{next(n)}"', self.raw)

    def move_profile(self, pid: str, dest: str) -> None:
        if dest not in self.projects:
            sys.exit(f"project {dest!r} not found")
        src = self.project_of("profile", pid)
        p = self.profiles[pid]
        own = [t for t in (p.findtext("mid0"), p.findtext("mid1")) if t]
        if src:
            self._set_list(src, "pids", [x for x in self.ids(self.projects[src], "pids") if x != pid])
            self.reload()
        self._set_list(dest, "pids", self.ids(self.projects[dest], "pids") + [pid])
        self.reload()
        for t in own:
            tsrc = self.project_of("task", t)
            if tsrc and tsrc != dest:
                self._set_list(tsrc, "tids", [x for x in self.ids(self.projects[tsrc], "tids") if x != t])
                self.reload()
                self._set_list(dest, "tids", self.ids(self.projects[dest], "tids") + [t])
                self.reload()

    def add_tasks(self, src_xml: str, project: str) -> list[str]:
        """Copy named tasks from another Tasker XML (a .prj.xml) into project, fresh ids.
        Returns the names added; names that already exist are left alone."""
        if project not in self.projects:
            sys.exit(f"project {project!r} not found")
        have = {t.findtext("nme") for t in self.tasks.values()}
        next_id = max([int(i) for i in self.tasks if i and i.isdigit()] + [0]) + 1
        added, new_ids = [], []
        for m in re.finditer(r'<Task sr="task\d+">.*?</Task>', src_xml, re.S):
            block = m.group(0)
            name = re.search(r"<nme>([^<]*)</nme>", block)
            if not name or name.group(1) in have:
                continue
            tid = str(next_id); next_id += 1
            block = re.sub(r'<Task sr="task\d+">', f'<Task sr="task{tid}">', block, count=1)
            block = re.sub(r"<id>\d+</id>", f"<id>{tid}</id>", block, count=1)
            end = self.raw.rindex("</TaskerData>")
            self.raw = self.raw[:end] + block + "\n" + self.raw[end:]
            added.append(name.group(1)); new_ids.append(tid)
        if new_ids:
            self.reload()
            self._set_list(project, "tids", self.ids(self.projects[project], "tids") + new_ids)
            self.reload()
        return added

    # -------------------------------------------------- references
    def users_of(self, tid: str) -> list[str]:
        out = []
        for pid, p in self.profiles.items():
            if tid in (p.findtext("mid0"), p.findtext("mid1")):
                out.append(f"profile {p.findtext('nme') or pid}")
        for sn, s in self.scenes.items():
            if any(x.text == tid for x in s.iter() if x.tag.endswith("Task")):
                out.append(f"scene {sn}")
        name = self.tasks[tid].findtext("nme") if tid in self.tasks else None
        if name:
            for oid, t in self.tasks.items():
                if oid != tid and any(a.findtext("code") == "130" and (a.findtext("Str") or "") == name
                                      for a in t.findall("Action")):
                    out.append(f"task {t.findtext('nme') or oid}")
        return out

    def problems(self) -> list[str]:
        bad = []
        names = {t.findtext("nme") for t in self.tasks.values() if t.findtext("nme")}
        for pid, p in self.profiles.items():
            for k in ("mid0", "mid1"):
                t = p.findtext(k)
                if t and t not in self.tasks:
                    bad.append(f"profile {p.findtext('nme') or pid} -> missing task {t}")
        for n, pr in self.projects.items():
            for pid in self.ids(pr, "pids"):
                if pid not in self.profiles:
                    bad.append(f"project {n} lists missing profile {pid}")
            for tid in self.ids(pr, "tids"):
                if tid not in self.tasks:
                    bad.append(f"project {n} lists missing task {tid}")
            for sc in self.ids(pr, "scenes"):
                if sc not in self.scenes:
                    bad.append(f"project {n} lists missing scene {sc}")
        for sn, s in self.scenes.items():
            for x in s.iter():
                if x.tag.endswith("Task") and x.text and x.text.isdigit() and x.text not in self.tasks:
                    bad.append(f"scene {sn} -> missing task {x.text}")
        for tid, t in self.tasks.items():
            for a in t.findall("Action"):
                if a.findtext("code") == "130":
                    s = a.find("Str[@sr='arg0']")
                    tgt = (s.text or "") if s is not None else ""
                    if tgt and "%" not in tgt and tgt not in names:
                        bad.append(f"task {t.findtext('nme') or tid} -> Perform Task '{tgt}' (gone)")
        return bad

    def summary(self) -> str:
        return (f"{len(self.projects)} projects, {len(self.profiles)} profiles, "
                f"{len(self.tasks)} tasks, {len(self.scenes)} scenes")


# ------------------------------------------------------------------ quick settings tiles
def set_tile(prefs: str, n: int, task: str, label: str) -> str:
    """Point tile n at task (by name) with label; other prefs stay byte-for-byte."""
    def esc(v: str) -> str:
        return v.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for key, val in ((f"qst{n}", task), (f"qstlab{n}", label)):
        pat = re.compile(rf'<string name="{key}">.*?</string>|<string name="{key}" />', re.S)
        rep = f'<string name="{key}">{esc(val)}</string>'
        if pat.search(prefs):
            prefs = pat.sub(lambda m: rep, prefs, count=1)
        else:
            prefs = prefs.replace("</map>", f"    {rep}\n</map>", 1)
    return prefs


# ------------------------------------------------------------------ commands
def cmd_ls(cfg: Config, what: str) -> None:
    if what == "projects":
        for n, p in cfg.projects.items():
            off = " [DISABLED]" if p.findtext("enbl") == "false" else ""
            print(f"{n}{off}: {len(cfg.ids(p, 'pids'))} profiles, {len(cfg.ids(p, 'tids'))} tasks, "
                  f"{len(cfg.ids(p, 'scenes'))} scenes")
    elif what == "profiles":
        for i, p in cfg.profiles.items():
            t = cfg.tasks.get(p.findtext("mid0"))
            print(f"{i:>5} [{cfg.project_of('profile', i) or '-'}] {p.findtext('nme') or '(unnamed)'}"
                  f" -> {(t.findtext('nme') or 'anon ' + p.findtext('mid0')) if t is not None else '-'}")
    elif what == "tasks":
        for i, t in cfg.tasks.items():
            print(f"{i:>5} [{cfg.project_of('task', i) or '-'}] {t.findtext('nme') or '(anonymous)'}")
    else:
        for n in cfg.scenes:
            print(f"{n} [{cfg.project_of('scene', n) or '-'}]")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=["pull", "ls", "edit", "push", "restore", "tile"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("rest", nargs="*")
    ap.add_argument("--add-tasks", nargs=2, action="append", default=[], metavar=("FILE", "PROJECT"))
    ap.add_argument("--delete-project", action="append", default=[])
    ap.add_argument("--delete-profile", action="append", default=[])
    ap.add_argument("--delete-task", action="append", default=[])
    ap.add_argument("--move-profile", nargs=2, action="append", default=[], metavar=("PROFILE", "PROJECT"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "push":                    # push has no dry run
        confirm_root_write("push the edited work copy over Tasker's live config")
    elif a.cmd == "restore":
        confirm_root_write(f"restore {a.arg} over Tasker's live config")
    elif a.cmd == "tile" and not a.dry_run:
        confirm_root_write(f"point Quick Settings tile {a.arg} at {' '.join(a.rest)!r}")
    WORK.mkdir(parents=True, exist_ok=True)
    work = WORK / "autobackup.xml"

    if a.cmd == "pull":
        raw = read_live()
        BACKUPS.mkdir(parents=True, exist_ok=True)
        b = BACKUPS / time.strftime("autobackup-%Y%m%d-%H%M%S.xml")
        b.write_text(raw)
        work.write_text(raw)
        print(f"pulled: {Config(raw).summary()}  (backup {b})")
        return 0
    if a.cmd == "restore":
        raw = Path(a.arg).read_text()
        if not is_tasker_data(raw):        # e.g. a preffy-*.xml tile backup would wipe the config
            sys.exit(f"{a.arg} is not a Tasker config backup (root element is not <TaskerData>); "
                     "undo a tile change with `tile N TASK LABEL` instead")
        Config(raw)                        # must parse
        write_live(raw)
        print("restored", a.arg)
        return 0
    if a.cmd == "tile":
        if a.arg not in ("0", "1", "2") or not a.rest:
            sys.exit("usage: tile N TASK [LABEL]   (N = 0, 1 or 2)")
        task, label = a.rest[0], (a.rest[1] if len(a.rest) > 1 else a.rest[0])
        names = {t.findtext("nme") for t in Config(read_live()).tasks.values()}
        if task not in names and not a.force:
            sys.exit(f"no task named {task!r} in Tasker (import it first, or --force)")
        prefs = adb(f"su -c 'cat {PREFS}'").decode("utf-8")
        BACKUPS.mkdir(parents=True, exist_ok=True)
        (BACKUPS / time.strftime("preffy-%Y%m%d-%H%M%S.xml")).write_text(prefs)
        new = set_tile(prefs, int(a.arg), task, label)
        if a.dry_run:
            print(new); return 0
        write_live(new, PREFS)
        print(f"tile {a.arg} -> {task} ({label}); Tasker restarted")
        return 0
    if not work.exists():
        sys.exit("run `pull` first")
    cfg = Config(work.read_text())
    if a.cmd == "ls":
        cmd_ls(cfg, a.arg or "projects")
        return 0
    if a.cmd == "push":
        live = read_live()
        pulled = sorted(BACKUPS.glob("autobackup-*.xml"))[-1].read_text()
        if live != pulled and not a.force:
            sys.exit("Tasker saved changes since `pull`; pull again (or --force to overwrite them)")
        bad = cfg.problems()
        if bad and not a.force:
            sys.exit("refusing to push, dangling references:\n  " + "\n  ".join(bad))
        write_live(cfg.raw)
        time.sleep(5)
        print(f"pushed: {cfg.summary()}; Tasker restarted")
        return 0

    before = cfg.summary()
    base_bad = set(cfg.problems())
    for pr, dest in a.move_profile:
        cfg.move_profile(cfg.find_profile(pr), dest)
        print(f"moved profile {pr} -> {dest}")
        if cfg.projects[dest].findtext("enbl") == "false":
            print(f"WARNING: project {dest} is disabled in Tasker; the profile will not fire there")
    for f, proj in a.add_tasks:
        added = cfg.add_tasks(Path(f).read_text(), proj)
        print(f"added to {proj}: {', '.join(added) or 'nothing (all names exist)'}")
    for name in a.delete_project:
        print(f"deleted project {name}: {cfg.remove_project(name)}")
    for key in a.delete_profile:
        pid = cfg.find_profile(key)
        gone = cfg.remove_profile(pid)
        print(f"deleted profile {key}" + (f" (+ its anonymous tasks {gone})" if gone else ""))
    for key in a.delete_task:
        tid = cfg.find_task(key)
        users = cfg.users_of(tid)
        if users and not a.force:
            sys.exit(f"task {key} is still used by {users}")
        cfg.remove_task(tid)
        print(f"deleted task {key}")
    new_bad = [b for b in cfg.problems() if b not in base_bad]
    print(f"before: {before}\nafter:  {cfg.summary()}")
    if base_bad:
        print(f"already broken before this edit ({len(base_bad)}): " + "; ".join(sorted(base_bad))[:600])
    if new_bad:
        print("NEW dangling references:\n  " + "\n  ".join(new_bad))
        if not a.force:
            sys.exit("not saved (fix the ops or use --force)")
    if a.dry_run:
        print("dry run: work copy not changed")
    else:
        work.write_text(cfg.raw)
        print(f"saved to {work}; run `push` to load it into Tasker")
    return 0


if __name__ == "__main__":
    sys.exit(main())
