"""Pomoc dla testu PT-253: tylko własne jednostki tymczasowe, bez restartu tide-daemon."""
import os
import signal
import subprocess
import time


class UnitProcess:
    def __init__(self, unit, env):
        self.unit, self.env = unit, env
        self.pid = int(self.show("MainPID"))
        self.returncode = None

    def control(self, *args, **kwargs):
        return subprocess.run(["/usr/bin/systemctl", "--user", *args, self.unit],
                              env=self.env, capture_output=True, text=True, timeout=12, **kwargs)

    def show(self, prop):
        return self.control("show", "--value", "-p", prop).stdout.strip()

    def poll(self):
        if self.returncode is None and self.show("ActiveState") in ("inactive", "failed", ""):
            self.returncode = int(self.show("ExecMainStatus") or "0")
        return self.returncode

    def wait(self, timeout):
        deadline = time.monotonic() + timeout
        while self.poll() is None:
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(self.unit, timeout)
            time.sleep(.02)
        return self.returncode

    def send_signal(self, sig):
        os.kill(self.pid, sig)

    def terminate(self):
        self.send_signal(signal.SIGTERM)

    def kill(self):
        self.control("kill", "--kill-whom=all", "--signal=SIGKILL")


def start_unit(unit, argv, env, manager_env, log):
    # Ustawiamy izolowane HOME i XDG także w jednostce. Type=exec: nazwa D-Bus jest na prywatnej magistrali,
    # a nie na magistrali menedżera systemd. Gotowość sprawdza test przez Snapshot().
    subprocess.run(["/usr/bin/systemd-run", "--user", "--quiet", f"--unit={unit}",
                    "-p", "Type=exec", "-p", "Restart=no", "-p", "KillMode=control-group",
                    "-p", f"StandardOutput=append:{log}", "-p", f"StandardError=append:{log}",
                    *[f"--setenv={key}={value}" for key, value in env.items()], "--", *argv],
                   env=manager_env, capture_output=True, text=True, check=True, timeout=12)
    return UnitProcess(unit + ".service", manager_env)


def install_controls(folder, runtime, prefix):
    """Kroki mają prywatne dane, a scope tworzą u prawdziwego menedżera przez jego gniazdo prywatne."""
    for name in ("systemd-run", "systemctl"):
        script = folder / name
        script.write_text("#!/usr/bin/python3\nimport os,sys\n"
                          "env=dict(os.environ)\n"
                          "env.pop('DBUS_SESSION_BUS_ADDRESS', None)\n"
                          f"env['XDG_RUNTIME_DIR']={runtime!r}\n"
                          "args=[]\n"
                          "for arg in sys.argv[1:]:\n"
                          "    if arg.startswith('--unit=tide-'):\n"
                          f"        arg='--unit='+{prefix!r}+arg[len('--unit='):]\n"
                          "    elif arg.startswith(('tide-step-', 'tide-outside-', 'tide-cmd-')) and "
                          "arg.endswith(('.scope', '.service')):\n"
                          f"        arg={prefix!r}+arg\n"
                          "    args.append(arg)\n"
                          f"os.execve('/usr/bin/{name}', ['{name}', *args], env)\n")
        script.chmod(0o755)
