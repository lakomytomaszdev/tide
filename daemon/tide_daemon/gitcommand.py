"""Wspólna granica wywołań gita usługi: bez haków i monitora systemu plików."""

HOST_OPTIONS = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")


def git_argv(*args, executable="git") -> list:
    return [executable, *HOST_OPTIONS, *args]
