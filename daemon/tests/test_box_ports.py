"""Porty pudełka: współbieżność procesów, trwałość i granice puli; bez Incusa."""
from concurrent.futures import ProcessPoolExecutor
import os
import tempfile
import unittest

from tide_daemon.box.ports import PortPool, PoolExhausted


def allocate(args):
    root, owner = args
    return PortPool("proj", "proj", root).acquire(owner, {"ports": {"pool": [4300, 4399]}})


class PortsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pool = PortPool("proj", "proj", self.tmp.name)
        self.profile = {"ports": {"pool": [4313, 4332], "env": ["APP_PORT", "WEB_PORT"]}}

    def test_ten_consecutive_ports_and_all_names_on_first(self):
        a = self.pool.acquire("K-1/s1", self.profile)
        b = self.pool.acquire("K-2/s1", self.profile)
        self.assertEqual(a, dict.fromkeys(("PORT", "E2E_PORT", "TIDE_PORT_BASE", "APP_PORT", "WEB_PORT"), "4313"))
        self.assertEqual(b["TIDE_PORT_BASE"], "4323")
        with self.assertRaises(PoolExhausted):
            self.pool.acquire("K-3/s1", self.profile)
        self.assertEqual(PortPool("proj", "proj", self.tmp.name).acquire("K-1/s1", self.profile), a)
        self.pool.release("K-1/s1")
        self.pool.release("K-1/s1")
        self.assertEqual(self.pool.acquire("K-3/s1", self.profile), a)

    def test_no_complete_block_and_no_pool(self):
        with self.assertRaises(PoolExhausted):
            self.pool.acquire("s1", {"ports": {"pool": [4300, 4308]}})
        self.assertEqual(self.pool.acquire("s1", {}), {})
        self.assertFalse(os.path.exists(self.pool.folder + "/proj.json"))

    def test_changed_pool_still_respects_previous_ranges(self):
        self.pool.acquire("a", self.profile)
        env = self.pool.acquire("b", {"ports": {"pool": [4315, 4344]}})
        self.assertEqual(env["PORT"], "4325")

    def test_separate_boxes_can_use_same_ports(self):
        self.pool.acquire("a", self.profile)
        env = PortPool("proj", "proj-k-2", self.tmp.name).acquire("b", self.profile)
        self.assertEqual(env["PORT"], "4313")

    def test_parallel_processes_never_allocate_overlapping_blocks(self):
        with ProcessPoolExecutor(max_workers=4) as workers:
            envs = list(workers.map(allocate, [(self.tmp.name, str(i)) for i in range(10)]))
        self.assertEqual(sorted(int(e["PORT"]) for e in envs), list(range(4300, 4400, 10)))
        with self.assertRaises(PoolExhausted):
            allocate((self.tmp.name, "overflow"))
