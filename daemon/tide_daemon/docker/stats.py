"""CPU i pamięć kontenera z `GET /containers/{id}/stats?stream=false` (jak `docker stats`).

Docker przy stream=false sam robi dwa odczyty (precpu_stats i cpu_stats), więc jedno zapytanie
wystarcza do procentu CPU: (Δ czasu kontenera / Δ czasu systemu) × liczba rdzeni × 100.
Pamięć jak w CLI: zużycie minus nieaktywne strony pamięci podręcznej (cgroup v2: inactive_file,
v1: total_inactive_file).
"""
from __future__ import annotations


def cpu_percent(stats: dict) -> float:
    cpu = stats.get("cpu_stats") or {}
    pre = stats.get("precpu_stats") or {}
    total = (cpu.get("cpu_usage") or {}).get("total_usage") or 0
    pre_total = (pre.get("cpu_usage") or {}).get("total_usage") or 0
    system = cpu.get("system_cpu_usage") or 0
    pre_system = pre.get("system_cpu_usage") or 0
    cpus = cpu.get("online_cpus") or len((cpu.get("cpu_usage") or {}).get("percpu_usage") or []) or 1
    delta, system_delta = total - pre_total, system - pre_system
    if delta <= 0 or system_delta <= 0:
        return 0.0
    return round(delta / system_delta * cpus * 100.0, 2)


def memory(stats: dict) -> tuple[int, int]:
    """(zużyte bajty, limit bajtów)."""
    mem = stats.get("memory_stats") or {}
    usage = int(mem.get("usage") or 0)
    details = mem.get("stats") or {}
    cache = details.get("inactive_file")
    if cache is None:
        cache = details.get("total_inactive_file", 0)
    used = usage - int(cache or 0) if int(cache or 0) < usage else usage
    return max(0, used), int(mem.get("limit") or 0)


def entry(container: dict, stats: dict) -> dict:
    used, limit = memory(stats)
    cpu = stats.get("cpu_stats") or {}
    cpus = cpu.get("online_cpus") or len((cpu.get("cpu_usage") or {}).get("percpu_usage") or []) or 1
    return {"id": container["id"], "name": container["name"], "cpuPercent": cpu_percent(stats), "cpus": cpus,
            "memBytes": used, "memLimitBytes": limit,
            "memPercent": round(used / limit * 100.0, 2) if limit else 0.0}


def total(entries: list[dict]) -> dict:
    """Suma jak w `docker stats` (100% = jeden rdzeń) i udział w całym procesorze (hostCpuPercent)."""
    cpu = round(sum(e["cpuPercent"] for e in entries), 2)
    cpus = max((e.get("cpus") or 1 for e in entries), default=1)
    return {"cpuPercent": cpu, "hostCpuPercent": round(cpu / cpus, 2), "cpus": cpus,
            "memBytes": sum(e["memBytes"] for e in entries), "containers": len(entries)}
