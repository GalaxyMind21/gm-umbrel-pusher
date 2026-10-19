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
        for bad in (DOC_A, "bc1q", "backup.worker", "00:11:22", "0123456789abcdef", "Pool."):
            self.assertNotIn(bad, out)
        self.assertTrue(all(p.get("ph") in (None, "node", "ip") or re.fullmatch(r"stratum\+(tcp|ssl)://[a-z0-9.-]+:\d+", p["ph"]) for p in extra.get("pools", [])))
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

    def _with(self, cmd, reply, fn):
        had = cmd in FAKE; prev = FAKE.get(cmd); FAKE[cmd] = reply
        try: return fn()
        finally:
            if had: FAKE[cmd] = prev
            else: FAKE.pop(cmd, None)

    def test_pool_ping_from_estats(self):
        est = {"STATS": [{"MM ID0": "Ver[Q-25052801] PING[33] TMax[71]"}]}
        m = self._with("estats", est, lambda: miners.cgminer("h"))
        self.assertEqual(m["pool_ping_ms"], 33); self.assertIsInstance(m["pool_ping_ms"], int)

    def test_pool_ping_falls_back_to_stats(self):
        st = {"STATS": [{"MM ID0": STATS["STATS"][0]["MM ID0"] + " PING[10]"}]}
        self.assertEqual(self._with("stats", st, lambda: miners.cgminer("h"))["pool_ping_ms"], 10)  # no estats reply at all
        est = {"STATS": [{"MM ID0": "Ver[Q-25052801] TMax[71]"}]}  # estats without PING
        self.assertEqual(self._with("stats", st, lambda: self._with("estats", est, lambda: miners.cgminer("h")))["pool_ping_ms"], 10)

    def test_pool_ping_missing_bad_or_huge(self):
        self.assertNotIn("pool_ping_ms", miners.cgminer("h"))
        for raw, want in (("abc", None), ("-5", None), ("", None), ("12.9", 12), ("999999", 60000), ("0", 0)):
            est = {"STATS": [{"MM ID0": "PING[%s]" % raw}]}
            m = self._with("estats", est, lambda: miners.cgminer("h"))
            if want is None: self.assertNotIn("pool_ping_ms", m, raw)
            else: self.assertEqual(m["pool_ping_ms"], want, raw)

    def test_slot_display(self):
        extra = {}; miners.cgminer("h", extra)
        self.assertEqual(extra["pools"][0]["ph"], "stratum+tcp://pool.example.com:3333")  # host lowercased, no user
        self.assertNotIn("ph", extra["pools"][1])                                          # standby slot: no pool display sent
        sd = miners.slot_display
        self.assertEqual(sd("stratum+tcp://" + DOC_A + ":2018"), "ip")                     # public-range IP, even on the node port
        self.assertEqual(sd("stratum+tcp://" + ".".join(["192", "168", "0", "9"]) + ":2018"), "node")
        self.assertEqual(sd("stratum+tcp://" + ".".join(["10", "0", "0", "9"]) + ":3333"), "ip")
        self.assertEqual(sd("stratum+tcp://umbrel.local:2018"), "node")
        self.assertEqual(sd("stratum+tcp://nas.lan:3333"), "ip")
        self.assertEqual(sd("stratum+tcp://[2001:db8::1]:3333"), "ip")
        self.assertEqual(sd("stratum+ssl://Stratum.Example.org:443"), "stratum+ssl://stratum.example.org:443")
        for h in ("8-8-8-8.sslip.io", "pool.c0a80001.example.com", "134744072.example.com", "2001-db8-0-0--1.example.com"):
            self.assertEqual(sd("stratum+tcp://%s:3333" % h), "ip", h)
        self.assertIsNone(sd("stratum+tcp://pool.example.com")); self.assertIsNone(sd(""))
        out = json.dumps(extra["pools"])
        for bad in (DOC_A, "bc1q", "backup.worker", "Pool."): self.assertNotIn(bad, out)

if __name__ == "__main__": unittest.main()
