#!/usr/bin/env python3
"""Institution-to-AICTE over a constrained link: proof time and success rate at realistic bandwidth and latency.

The prover (institution side) reaches AICTE's verifier through a link emulator that adds one-way delay and caps
bandwidth in each direction, so the MPC-TLS traffic pays real transmission time. The bank link is unchanged.

  SCOURGE_VERIFIER_ADDR=127.0.0.1:7147 ./scripts/run.sh &
  python3 scripts/wan_bench.py            # profiles below, 5 proofs each; prints one JSON report

This emulates a network on one machine. It is not a measurement between two physical machines: run the
prover on a laptop and the verifier on a server (scourge_prove --verifier <host:7047>) for that."""
import asyncio
import json
import os
import statistics
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.environ.setdefault("SCOURGE_NO_AUTOSTART", "1")
import test_acceptance as T  # noqa: E402

LISTEN, UPSTREAM = ("127.0.0.1", 7147), ("127.0.0.1", 7047)
PROFILES = [  # (name, Mbit/s each way, round-trip ms)
    ("lan_1gbps_2ms", 1000, 2), ("office_100mbps_30ms", 100, 30), ("broadband_50mbps_40ms", 50, 40),
    ("broadband_20mbps_60ms", 20, 60), ("slow_10mbps_80ms", 10, 80)]
RUNS = int(os.environ.get("RUNS", 5))
link = {"bps": 1e9, "delay": 0.001}
moved = {"up": 0, "down": 0}


async def pipe(reader, writer, direction):
    q = asyncio.Queue()

    async def recv():
        while data := await reader.read(65536):
            await q.put((time.monotonic() + link["delay"], data))
        await q.put(None)

    async def send():
        free = 0.0
        while (item := await q.get()) is not None:
            at, data = item
            start = max(at, free, time.monotonic())
            free = start + len(data) * 8 / link["bps"]
            await asyncio.sleep(max(0.0, free - time.monotonic()))
            writer.write(data)
            await writer.drain()
            moved[direction] += len(data)
        writer.close()

    await asyncio.gather(recv(), send(), return_exceptions=True)


async def handle(cr, cw):
    ur, uw = await asyncio.open_connection(*UPSTREAM)
    await asyncio.gather(pipe(cr, uw, "up"), pipe(ur, cw, "down"), return_exceptions=True)


def serve():
    loop = asyncio.new_event_loop()
    loop.run_until_complete(asyncio.start_server(handle, *LISTEN))
    loop.run_forever()


threading.Thread(target=serve, daemon=True).start()
time.sleep(0.5)
off = T.S("officer")
off.post("/system/reset")
report = {"note": "emulated link between prover and verifier on one machine; synthetic bank", "runs_per_profile": RUNS, "profiles": []}
for name, mbps, rtt in PROFILES:
    link["bps"], link["delay"] = mbps * 1e6, rtt / 2000
    times, ok, up, down = [], 0, [], []
    for _ in range(RUNS):
        moved["up"] = moved["down"] = 0
        t0 = time.time()
        j, _ = T.prove(off, "FDR-001")
        times.append(time.time() - t0)
        ok += j.get("decision") == "verified"
        up.append(moved["up"] / 1e6)
        down.append(moved["down"] / 1e6)
    report["profiles"].append({"profile": name, "mbps": mbps, "rtt_ms": rtt, "verified": f"{ok}/{RUNS}",
                               "seconds_median": round(statistics.median(times), 1), "seconds_max": round(max(times), 1),
                               "MB_up_median": round(statistics.median(up), 1), "MB_down_median": round(statistics.median(down), 1)})
    print(json.dumps(report["profiles"][-1]), file=sys.stderr)
print(json.dumps(report, indent=1))
