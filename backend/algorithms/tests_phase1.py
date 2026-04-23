"""
Phase 1 unit tests — Token Bucket + WFQ + Traffic Generator.

Run with:  python -m pytest tests_phase1.py -v
"""

import time
import threading
import pytest

from token_bucket import TokenBucket, TokenBucketStats
from wfq import WFQScheduler, WFQSnapshot
from traffic_generator import TrafficGenerator


# ══════════════════════════════════════════════════════════════════════════════
# Token Bucket Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestTokenBucket:

    def test_forward_when_enough_tokens(self):
        """Bucket starts full — first packet should always forward."""
        tb = TokenBucket(rate_bps=1_000_000)      # 1 Mbps
        result = tb.consume(100)
        assert result["action"] == "forward"
        assert result["delay_ms"] == 0.0

    def test_delay_when_insufficient_tokens(self):
        """Drain the bucket then send another packet — must delay."""
        tb = TokenBucket(rate_bps=8_000, capacity=1_000, max_delay_ms=2_000)
        # Consume the full capacity (1000 bytes = 1000 tokens)
        tb.consume(1_000)
        # Now try 500 more bytes — bucket is empty, must delay
        result = tb.consume(500)
        assert result["action"] in ("delay", "drop")

    def test_drop_when_delay_exceeds_limit(self):
        """Very small bucket, large packet → should drop."""
        tb = TokenBucket(rate_bps=8_000, capacity=100, max_delay_ms=10)
        tb.consume(100)             # drain bucket
        result = tb.consume(10_000)  # 10 KB would take forever
        assert result["action"] == "drop"

    def test_tokens_refill_over_time(self):
        """After waiting, tokens should refill and packet should forward."""
        rate_bps = 800_000   # 100 KB/s
        tb = TokenBucket(rate_bps=rate_bps, capacity=1_000)
        tb.consume(1_000)    # drain

        time.sleep(0.05)     # wait 50 ms → ~5 KB should refill
        result = tb.consume(100)
        assert result["action"] == "forward"

    def test_stats_tracking(self):
        """Stats should accumulate correctly."""
        tb = TokenBucket(rate_bps=8_000_000, capacity=100_000)
        for _ in range(5):
            tb.consume(100)
        stats = tb.reset_stats()
        assert stats.packets_forwarded == 5
        assert stats.bytes_forwarded == 500

    def test_stats_reset_after_snapshot(self):
        """reset_stats should zero the counters."""
        tb = TokenBucket(rate_bps=8_000_000, capacity=100_000)
        tb.consume(200)
        tb.reset_stats()
        tb.consume(100)
        stats = tb.reset_stats()
        assert stats.bytes_forwarded == 100     # only the second packet

    def test_loss_percent_calculation(self):
        """loss_percent should be correct fraction of drops."""
        stats = TokenBucketStats()
        stats.packets_forwarded = 8
        stats.packets_dropped = 2
        assert stats.loss_percent == pytest.approx(20.0)

    def test_invalid_rate_raises(self):
        with pytest.raises(ValueError):
            TokenBucket(rate_bps=0)

    def test_update_rate_live(self):
        """Updating rate should not crash and should change allocation."""
        tb = TokenBucket(rate_bps=1_000_000)
        tb.update_rate(2_000_000)
        assert tb._rate_bps == 2_000_000

    def test_thread_safety(self):
        """Concurrent consume calls must not corrupt state."""
        tb = TokenBucket(rate_bps=800_000_000, capacity=10_000_000)  # huge, never blocks
        errors = []

        def worker():
            for _ in range(200):
                try:
                    tb.consume(100)
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0


# ══════════════════════════════════════════════════════════════════════════════
# WFQ Scheduler Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestWFQScheduler:

    def _make_scheduler(self, bw_mbps=100):
        return WFQScheduler(total_bandwidth_bps=bw_mbps * 1e6)

    def test_add_and_consume(self):
        sched = self._make_scheduler()
        sched.add_process(pid=100, name="chrome.exe", weight=5)
        result = sched.consume(pid=100, packet_size_bytes=512)
        assert result["action"] in ("forward", "delay")

    def test_unknown_pid_drops(self):
        sched = self._make_scheduler()
        result = sched.consume(pid=9999, packet_size_bytes=512)
        assert result["action"] == "drop"
        assert result.get("reason") == "unknown_pid"

    def test_weight_share_math(self):
        """
        Fitgirl=5, Chrome=2, Steam=1 → 62.5 / 25 / 12.5 %
        Verify allocated_bps matches expected share.
        """
        sched = self._make_scheduler(bw_mbps=80)
        sched.add_process(pid=1, name="fitgirl.exe", weight=5)
        sched.add_process(pid=2, name="chrome.exe",  weight=2)
        sched.add_process(pid=3, name="steam.exe",   weight=1)

        snaps = {s.name: s for s in sched.snapshot()}
        total_bw = 80e6

        assert snaps["fitgirl.exe"].allocated_bps == pytest.approx(total_bw * 5/8, rel=1e-3)
        assert snaps["chrome.exe"].allocated_bps  == pytest.approx(total_bw * 2/8, rel=1e-3)
        assert snaps["steam.exe"].allocated_bps   == pytest.approx(total_bw * 1/8, rel=1e-3)

    def test_share_percent_sums_to_100(self):
        sched = self._make_scheduler()
        sched.add_process(pid=1, name="a", weight=3)
        sched.add_process(pid=2, name="b", weight=7)
        snaps = sched.snapshot()
        total = sum(s.share_percent for s in snaps)
        assert total == pytest.approx(100.0, rel=1e-3)

    def test_live_weight_update_rebalances(self):
        sched = self._make_scheduler(bw_mbps=100)
        sched.add_process(pid=1, name="a", weight=5)
        sched.add_process(pid=2, name="b", weight=5)

        # Both equal → each gets 50 %
        snaps_before = {s.pid: s for s in sched.snapshot()}
        assert snaps_before[1].share_percent == pytest.approx(50.0, rel=1e-2)

        # Boost pid=1 to weight=9, pid=2 stays at 5
        sched.set_weight(pid=1, weight=9)
        snaps_after = {s.pid: s for s in sched.snapshot()}
        assert snaps_after[1].share_percent > snaps_after[2].share_percent

    def test_remove_process_rebalances(self):
        sched = self._make_scheduler()
        sched.add_process(pid=1, name="a", weight=5)
        sched.add_process(pid=2, name="b", weight=5)
        sched.remove_process(pid=2)
        snaps = sched.snapshot()
        assert len(snaps) == 1
        assert snaps[0].share_percent == pytest.approx(100.0, rel=1e-2)

    def test_duplicate_pid_raises(self):
        sched = self._make_scheduler()
        sched.add_process(pid=42, name="x", weight=5)
        with pytest.raises(ValueError):
            sched.add_process(pid=42, name="x2", weight=5)

    def test_weight_clamped_1_to_10(self):
        sched = self._make_scheduler()
        sched.add_process(pid=1, name="a", weight=999)   # should clamp to 10
        snap = sched.snapshot()[0]
        assert snap.weight == 10.0

    def test_weight_floor(self):
        sched = self._make_scheduler()
        sched.add_process(pid=1, name="a", weight=0)     # should clamp to 1
        snap = sched.snapshot()[0]
        assert snap.weight == 1.0

    def test_bandwidth_update(self):
        sched = self._make_scheduler(bw_mbps=100)
        sched.add_process(pid=1, name="a", weight=5)
        sched.set_total_bandwidth(200e6)
        snap = sched.snapshot()[0]
        assert snap.allocated_bps == pytest.approx(200e6, rel=1e-3)


# ══════════════════════════════════════════════════════════════════════════════
# Traffic Generator (Sim Mode) Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestTrafficGenerator:

    def _make_gen(self, bw_mbps=100):
        sched = WFQScheduler(total_bandwidth_bps=bw_mbps * 1e6)
        return TrafficGenerator(sched), sched

    def test_start_stop(self):
        gen, _ = self._make_gen()
        gen.add_sim_process(pid=1001, name="sim_chrome.exe", weight=5, load_mbps=10)
        gen.start()
        assert gen.is_running()
        gen.stop()
        assert not gen.is_running()

    def test_packets_are_generated(self):
        gen, _ = self._make_gen()
        gen.add_sim_process(pid=1001, name="sim_a.exe", weight=5, load_mbps=50)
        gen.start()
        time.sleep(0.2)
        gen.stop()
        events = gen.pop_events()
        assert len(events) > 0, "Expected some packets to be generated"

    def test_event_fields(self):
        gen, _ = self._make_gen()
        gen.add_sim_process(pid=2001, name="sim_b.exe", weight=5, load_mbps=50)
        gen.start()
        time.sleep(0.2)
        gen.stop()
        events = gen.pop_events()
        assert len(events) > 0
        ev = events[0]
        assert "pid" in ev
        assert "size" in ev
        assert "action" in ev
        assert ev["action"] in ("forward", "delay", "drop")

    def test_pop_events_drains_buffer(self):
        gen, _ = self._make_gen()
        gen.add_sim_process(pid=3001, name="sim_c.exe", weight=5, load_mbps=50)
        gen.start()
        time.sleep(0.2)
        gen.stop()
        gen.pop_events()
        assert gen.pop_events() == []   # second call should be empty

    def test_multiple_processes(self):
        gen, _ = self._make_gen()
        gen.add_sim_process(pid=4001, name="sim_fitgirl.exe", weight=5, load_mbps=40)
        gen.add_sim_process(pid=4002, name="sim_steam.exe",   weight=2, load_mbps=20)
        gen.start()
        time.sleep(0.3)
        gen.stop()
        events = gen.pop_events()
        pids_seen = {e["pid"] for e in events}
        assert 4001 in pids_seen
        assert 4002 in pids_seen

    def test_remove_sim_process(self):
        gen, sched = self._make_gen()
        gen.add_sim_process(pid=5001, name="sim_x.exe", weight=5, load_mbps=10)
        gen.start()
        time.sleep(0.1)
        gen.remove_sim_process(pid=5001)
        time.sleep(0.1)
        gen.stop()
        assert 5001 not in gen.active_pids()
        assert 5001 not in sched.get_pids()

    def test_scheduler_shared_correctly(self):
        """Packets should go through the real WFQ algorithm."""
        gen, sched = self._make_gen(bw_mbps=10)
        gen.add_sim_process(pid=6001, name="greedy.exe", weight=5, load_mbps=100)  # overloaded
        gen.start()
        time.sleep(0.3)
        gen.stop()
        events = gen.pop_events()
        actions = [e["action"] for e in events]
        # With an overloaded stream, we expect some delays or drops
        assert "forward" in actions     # some packets do get through


# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
