"""Offline tests for app/miners.py (fake cgminer replies only; no network). Run: python3 -m unittest discover -s tests"""
import json, os, re, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import miners

KEY = "ab" * 32
DOC_A = ".".join(["192", "0", "2", "10"]); DOC_B = ".".join(["198", "51", "100", "7"])  # RFC 5737 documentation addresses (fake)
SUMMARY = {"STATUS": [{"When": 1760900000}], "SUMMARY": [{"Elapsed": 427781, "MHS 5s": 90123456.0, "Found Blocks": 0, "Accepted": 107249, "Rejected": 182,
    "Stale": 29, "Discarded": 0, "Get Failures": 1, "Remote Failures": 1, "Hardware Errors": 1}]}
STATS = {"STATS": [{"MM ID0": "Ver[Q-25052801] DNA[0123456789abcdef] TMax[71] TAvg[63] Fan1[3120] Fan2[3080] FanR[42%] WALLPOWER[1674] Freq[480.5]"}]}
POOLS = {"STATUS": [{"When": 1760900000}], "POOLS": [
    {"POOL": 1, "Priority": 1, "URL": "stratum+tcp://" + DOC_A + ":3333", "User": "backup.worker", "Status": "Alive", "Stratum Active": False,
     "Accepted": 98, "Rejected": 0, "Stale": 3, "Last Share Time": 200031, "Stratum Difficulty": 0.0},
    {"POOL": 0, "Priority": 0, "URL": "stratum+tcp://Pool.Example.com:3333", "User": "bc1qrenterworker.rig1", "Status": "Alive", "Stratum Active": True,
     "Accepted": 107151, "Rejected": 182, "Stale": 26, "Last Share Time": 427770, "Stratum Difficulty": 45239.0}]}
VERSION = {"VERSION": [{"CGMiner": "4.11.1", "API": "3.7", "CGVERSION": "25052801_14a19a2", "MAC": "00:11:22:33:44:55", "DNA": "0123456789abcdef"}]}
FAKE = {"summary": SUMMARY, "stats": STATS, "pools": POOLS, "version": VERSION}
IPV4 = re.compile(r"\b\d{1,3}(\.\d{1,3}){3}\b"); IPV6 = re.compile(r"\b[0-9a-f]{1,4}(:[0-9a-f]{0,4}){2,7}\b", re.I)

class CgminerTest(unittest.TestCase):
    def setUp(self):
        self._cg = miners._cg; miners._cg = lambda host, cmd, t0: json.loads(json.dumps(FAKE[cmd])); miners._ver_cache.clear()
        os.environ["GM_SLOT_KEY"] = KEY
    def tearDown(self):
        miners._cg = self._cg; os.environ.pop("GM_SLOT_KEY", None)

    def test_numbers(self):
        extra = {}; m = miners.cgminer("h", extra)
        self.assertEqual((m["acc"], m["rej"], m["stale"], m["hw_err"]), (107249, 182, 29, 1))
        self.assertEqual((m["found_blocks"], m["get_fail"], m["remote_fail"], m["discarded"]), (0, 1, 1, 0))
        self.assertEqual(m["stratum_diff"], 45239.0); self.assertEqual(m["last_share_s"], 11)  # Avalon reports seconds since start
        self.assertTrue(all(isinstance(v, (int, float)) for v in m.values()))
        self.assertEqual(extra["ver"], {"cgminer": "4.11.1", "fw": "25052801_14a19a2"})
        self.assertEqual([p["prio"] for p in extra["pools"]], [0, 1])  # ordered by priority
        self.assertEqual(extra["pools"][0]["active"], 1); self.assertEqual(extra["pools"][1]["active"], 0)

    def test_unix_last_share(self):
        FAKE["pools"]["POOLS"][1]["Last Share Time"] = 1760899990
        try: self.assertEqual(miners.cgminer("h")["last_share_s"], 10)
        finally: FAKE["pools"]["POOLS"][1]["Last Share Time"] = 427770

    def test_no_urls_ips_users_macs_leave(self):
        extra = {}; m = miners.cgminer("h", extra); out = json.dumps({"m": m, **extra})
        for bad in ("example", DOC_A, "bc1q", "backup.worker", "stratum+", "://", "00:11:22", "0123456789abcdef", "Pool."):
            self.assertNotIn(bad, out)
        self.assertIsNone(IPV4.search(out)); self.assertIsNone(IPV6.search(out))

    def test_fingerprint_canonical(self):
        extra = {}; miners.cgminer("h", extra)
        h0 = miners.slot_fp(bytes.fromhex(KEY), "stratum+tcp://pool.example.com:3333/", "bc1qrenterworker.rig1")
        self.assertRegex(extra["pools"][0]["h"], r"^[0-9a-f]{32}$"); self.assertEqual(extra["pools"][0]["h"], h0)
        self.assertNotEqual(extra["pools"][1]["h"], h0)

    def test_no_or_bad_key_sends_no_fingerprint(self):
        for k in (None, "not-hex"):
            if k is None: os.environ.pop("GM_SLOT_KEY", None)
            else: os.environ["GM_SLOT_KEY"] = k
            extra = {}; miners.cgminer("h", extra)
            self.assertTrue(all("h" not in p for p in extra["pools"]))

    def test_bad_version_strings_dropped(self):
        for v in ({"CGMiner": "4.11.1 <script>", "CGVERSION": "x" * 80}, {"CGMiner": ".".join(["4", "11", "1", "2"]), "CGVERSION": "fw-" + DOC_B}):
            FAKE["version"] = {"VERSION": [v]}; miners._ver_cache.clear()
            try: extra = {}; miners.cgminer("h", extra); self.assertNotIn("ver", extra)
            finally: FAKE["version"] = VERSION

if __name__ == "__main__": unittest.main()
