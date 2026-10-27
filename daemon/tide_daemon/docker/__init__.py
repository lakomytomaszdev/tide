"""Docker w tide-daemon: Engine API przez lokalny socket, migawka kontenerów, akcje i porządki.

client.py — HTTP/1.1 po sockecie unix (bez bibliotek spoza standardowej), snapshot.py — grupy compose
i stany słowami, repos.py — katalog projektu compose → repozytorium, disk.py — miejsce i prune,
stats.py — CPU i pamięć, engine.py — całość bez Qt. Wątki i D-Bus: docker_monitor.py.
"""
from .client import DockerError, DockerUnavailable, EngineClient, socket_path  # noqa: F401
from .engine import DockerEngine  # noqa: F401
