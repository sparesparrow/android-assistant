# phone: services for Claude Code in Debian PRoot on this phone

Not a repo. Gives Claude Code (running in PRoot) phone control, a working microphone and a
way out to native Termux.

## The tricks
- **adb on loopback** (`127.0.0.1:5555`): set up once by `su -c sh ~/droid/setup-root.sh`
  in native Termux (Magisk boot script). adb shell = uid 2000, which can tap/type/screenshot
  and **holds RECORD_AUDIO**. `droid doctor` uses it.
- **Mic via adb shell user**: Termux does not declare RECORD_AUDIO, so PulseAudio inside
  Termux can never open the mic. Instead `pa/build-bundle.sh` copies Termux's PulseAudio
  plus `module-sles-source` + `module-native-protocol-tcp` and libs to `/data/local/tmp/pa`,
  and it runs there as uid 2000. PRoot clients use `PULSE_SERVER=127.0.0.1`,
  `AUDIODRIVER=pulseaudio` (SoX, Claude `/voice`).
- **Native shell over ssh**: Termux `sshd` on `127.0.0.1:8022` (key only). From PRoot,
  `phone-services native <cmd>` runs outside PRoot: real Magisk `su`, `termux-am`,
  `proot-distro`. PRoot itself cannot see `/debug_ramdisk` (Magisk socket) or
  `files/apps` (termux-am socket).

## Files
| Path | Role |
|---|---|
| `phone-services` | `ensure`, `status`, `pa-restart [--push]`, `pa-stop`, `test-mic`, `native`, `ssh-key` |
| `pa/` | mic server: `build-bundle.sh`, `mic.pa`, `run.sh`, built `bundle/` |
| `termux/bin/debian` | Termux: log into Debian with `files/apps` bound (intents work) |
| `termux/bin/cc` | Termux: start Claude Code in Debian (`cc --continue`) |
| `termux/bashrc.snippet` | Termux `~/.bashrc`: `~/bin` on PATH + start loopback sshd |
| `root/setup-boot.sh` | Magisk boot script: mic server + sshd at boot |
| `/etc/profile.d/phone-services.sh` | Debian login: env vars + `phone-services ensure` in background |
| `notif-wait.py` | wait for a new Android notification (dumpsys polling) and print it as JSON |
| `event-pub.py` | publish one event on MQTT `phone/events/<kind>` for the `wait_event` tool; `notif-wait.py ... \| event-pub.py --stdin` bridges notifications |
| `logs/` | `ensure.log`, `test-mic.wav` |

## Autostart
1. Every Debian login shell starts `phone-services watch` (one instance): it runs `ensure`
   every 60 s, because an adbd restart kills the mic server along with every adb-spawned process.
   `ensure`/`watch` never run `su`: a down mic is restarted as a plain adb child (dies with the
   next adbd restart) and `logs/ensure.log` says `run: phone-services pa-restart`.
2. Optional, at phone boot (root): `sudo sh ~/phone/setup-boot.sh` in native Termux.

## Root (Magisk su)
Only `pa-restart` and `pa-stop`, and only with a tty on stdin (a human at a terminal), use
`su` (start the mic server as uid 2000 outside adbd's process group / kill a boot-started one).
`ensure`/`watch` never use su, tty or not; headless callers (`claude -p`, Tasker, agent sshd with
`PermitTTY no`) have no tty and get the plain path.
