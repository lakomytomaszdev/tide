"""Uruchamia zainstalowane CLI z deterministyczną zmianą źródła podczas importu."""
import os
import runpy
import sys

module = runpy.run_path(sys.argv[1], run_name="installed_tide_test")
read_source = module["read_attachment_source"]


def changing_source(path):
    fstat = os.fstat
    changed = False

    def alter(fd):
        nonlocal changed
        before = fstat(fd)
        if not changed:
            changed = True
            with open(path, "ab") as stream:
                stream.write(b"zmiana podczas kopiowania")
        return before

    os.fstat = alter
    try:
        return read_source(path)
    finally:
        os.fstat = fstat


module["main"].__globals__["read_attachment_source"] = changing_source
sys.exit(module["main"](sys.argv[2:]))
