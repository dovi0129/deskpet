import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gpu_adapters import Gpu, kind_of, labels_for, list_gpus, per_gpu  # noqa: E402
import monitor  # noqa: E402

ARC = Gpu("0x00000000_0x0000FE6A", "Intel(R) Arc(TM) 140V GPU (16GB)", "integrated")
RTX = Gpu("0x00000000_0x0001A2B3", "NVIDIA GeForce RTX 4060 Laptop GPU", "discrete")
NPU_LUID = "0x00000000_0x000102CA"      # Intel AI Boost: in the GPU counters, not a GPU
BASIC_LUID = "0x00000000_0x00010273"    # Microsoft Basic Render Driver


class KindTests(unittest.TestCase):
    def test_kinds(self):
        gib = 1024 ** 3
        self.assertEqual(kind_of("Intel(R) Arc(TM) 140V GPU (16GB)", 0x8086, 128 << 20), "integrated")
        self.assertEqual(kind_of("Intel(R) Arc(TM) A770 Graphics", 0x8086, 16 * gib), "discrete")
        self.assertEqual(kind_of("Intel(R) Iris(R) Xe Graphics", 0x8086, 128 << 20), "integrated")
        self.assertEqual(kind_of("NVIDIA GeForce RTX 4060 Laptop GPU", 0x10DE, 8 * gib), "discrete")
        self.assertEqual(kind_of("AMD Radeon(TM) Graphics", 0x1002, 512 << 20), "integrated")
        self.assertEqual(kind_of("AMD Radeon RX 7600M XT", 0x1002, 8 * gib), "discrete")
        self.assertEqual(kind_of("Some GPU", 0x1234, 4 * gib), "discrete")   # unknown vendor: memory decides
        self.assertEqual(kind_of("Some GPU", 0x1234, 0), "unknown")


class LabelTests(unittest.TestCase):
    def test_laptop_pair_is_igpu_dgpu_integrated_first(self):
        use = {ARC.luid.lower(): 3.0, RTX.luid: 71.0, NPU_LUID: 40.0, BASIC_LUID: 0.0}
        self.assertEqual(per_gpu([RTX, ARC], use), (("iGPU", 3.0), ("dGPU", 71.0)))  # NPU never shows

    def test_two_discrete_cards_are_numbered(self):
        other = Gpu("0x00000000_0x0002C4D5", "NVIDIA GeForce RTX 3090", "discrete")
        self.assertEqual(labels_for([RTX, other]), ["GPU0", "GPU1"])

    def test_missing_luid_is_unknown_not_zero(self):
        self.assertEqual(per_gpu([ARC, RTX], {ARC.luid: 0.0}), (("iGPU", 0.0), ("dGPU", None)))

    def test_dxgi_lists_no_software_adapter(self):
        for g in list_gpus():   # real machine: whatever it has, never the Basic Render Driver
            self.assertNotIn("Microsoft Basic", g.name)
            self.assertRegex(g.luid, r"^0x[0-9A-F]{8}_0x[0-9A-F]{8}$")


class SplitTests(unittest.TestCase):
    def machine(self, gpus):
        m = object.__new__(monitor.SystemMonitor)
        m._gpus, m._gpu_luids_seen, m._gpus_scanned_at = list(gpus), None, 0.0
        m.scans = 0

        def scan(now, luids):
            m.scans += 1
            m._gpu_luids_seen, m._gpus_scanned_at = luids, now
        m._scan_gpus = scan
        return m

    def adv(self, luids):
        return monitor.AdvancedSnapshot(gpu_valid=True, gpu_luids=dict(luids))

    def test_one_gpu_keeps_the_single_row(self):
        m = self.machine([ARC])
        self.assertEqual(m._gpu_split(self.adv({ARC.luid: 9.0, NPU_LUID: 50.0}), True, 1.0), ())

    def test_two_gpus_and_stale_values(self):
        m = self.machine([ARC, RTX])
        luids = {ARC.luid: 12.0, RTX.luid: 0.0, NPU_LUID: 0.0}
        self.assertEqual(m._gpu_split(self.adv(luids), True, 1.0), (("iGPU", 12.0), ("dGPU", 0.0)))
        self.assertEqual(m._gpu_split(self.adv(luids), False, 2.0), (("iGPU", None), ("dGPU", None)))
        self.assertEqual(m.scans, 0)   # first sample only records the LUID set

    def test_new_luid_rescans_at_most_once_a_minute(self):
        m = self.machine([ARC, RTX])
        m._gpu_split(self.adv({ARC.luid: 0, RTX.luid: 0}), True, 1.0)
        changed = self.adv({ARC.luid: 0, "0x00000000_0x0009FFFF": 0})   # dGPU restarted with a new LUID
        m._gpu_split(changed, True, 30.0)
        self.assertEqual(m.scans, 0)
        m._gpu_split(changed, True, 61.0)
        self.assertEqual(m.scans, 1)
        m._gpu_split(changed, True, 200.0)
        self.assertEqual(m.scans, 1)   # same set as the last scan: no more scans


if __name__ == "__main__":
    unittest.main()
