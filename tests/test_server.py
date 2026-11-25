import json
import threading
import unittest
import urllib.request

from server import calculate_costs, create_server, estimate_tokens


class TokenStudioTestCase(unittest.TestCase):
    def test_estimate_tokens_mixed_text(self):
        empty = estimate_tokens("")
        self.assertEqual(empty["total_tokens"], 0)

        mixed = estimate_tokens("请分析 Python 函数 calc_total(a, b)")
        self.assertGreater(mixed["cjk_chars"], 0)
        self.assertGreater(mixed["latin_words"], 0)
        self.assertGreater(mixed["total_tokens"], 5)

    def test_calculate_costs_sorted_and_cache_reduces_cost(self):
        no_cache = calculate_costs(10000, 500, 1000, cache_hit_rate=0.0, days=30)
        with_cache = calculate_costs(10000, 500, 1000, cache_hit_rate=0.8, days=30)

        self.assertLessEqual(no_cache[0]["period_cny"], no_cache[-1]["period_cny"])
        ds_no = next(r for r in no_cache if r["id"] == "deepseek-v3")
        ds_yes = next(r for r in with_cache if r["id"] == "deepseek-v3")
        self.assertLess(ds_yes["period_cny"], ds_no["period_cny"])

    def test_http_endpoints(self):
        server = create_server("127.0.0.1", 0)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                health = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(health["status"], "ok")

            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/estimate",
                data=json.dumps(
                    {
                        "prompt": "帮我优化这段 SQL 查询性能 SELECT * FROM orders;",
                        "output_tokens": 400,
                        "requests_per_day": 200,
                        "cache_hit_rate": 0.5,
                        "days": 30,
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertGreater(data["input_tokens"], 0)
                self.assertGreaterEqual(len(data["results"]), 6)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
