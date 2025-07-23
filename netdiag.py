#!/usr/bin/env python3
"""
netdiag.py

Comprehensive Internet link diagnostics:
  1. Bufferbloat test via ping vs. TCP saturation (iperf3)
  2. Jitter & packet loss via iperf3 UDP
  3. Route health via mtr
  4. Path MTU discovery via ping+DF
  5. DNS lookup timing vs. 1.1.1.1 and 8.8.8.8
  6. CG-NAT check via UPnP vs. public IP

Usage:
  python netdiag.py                    # Uses default iperf3 server
  python netdiag.py --list-servers     # Shows available servers
  python netdiag.py --iperf3-server X.X.X.X
"""
import argparse
import json
import os
import platform
import random
import shutil
import socket
import statistics
import subprocess
import sys
import threading
import time

# Available iperf3 servers with their ports
IPERF3_SERVERS = {
    "ping.online.net": {"description": "Scaleway France", "port": 5200},
    "speedtest.milkywan.fr": {"description": "CBO France", "port": 9200}, 
    "str.cubic.iperf.bytel.fr": {"description": "Bouygues France", "port": 9200},
    "ch.iperf.014.fr": {"description": "HostHatch Switzerland", "port": 15315}
}

try:
    from ping3 import ping
except ImportError:
    print("Missing dependency: ping3 (pip install ping3)")
    sys.exit(1)

try:
    import dns.resolver
except ImportError:
    print("Missing dependency: dnspython (pip install dnspython)")
    sys.exit(1)

try:
    import miniupnpc
except ImportError:
    miniupnpc = None

try:
    import requests
except ImportError:
    print("Missing dependency: requests (pip install requests)")
    sys.exit(1)


def test_server_connectivity(server: str, port: int, timeout: int = 5) -> bool:
    """
    Test if an iperf3 server is responding by attempting a quick connection.
    """
    try:
        cmd = ["iperf3", "-c", server, "-p", str(port), "-t", "1", "--json"]
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, Exception):
        return False


def select_best_server() -> tuple:
    """
    Test available servers and return the first responsive one as (server, port).
    """
    print("Testing iperf3 server connectivity...")
    for server, config in IPERF3_SERVERS.items():
        port = config["port"]
        description = config["description"]
        print(f"  Testing {server}:{port} ({description})...", end=" ")
        if test_server_connectivity(server, port):
            print("✓")
            return server, port
        else:
            print("✗")
    
    # If none respond, return the first one anyway
    fallback_server = list(IPERF3_SERVERS.keys())[0]
    fallback_port = IPERF3_SERVERS[fallback_server]["port"]
    print(f"  No servers responded, using fallback: {fallback_server}:{fallback_port}")
    return fallback_server, fallback_port


def list_servers():
    """
    Display available iperf3 servers.
    """
    print("Available iperf3 servers:")
    for server, config in IPERF3_SERVERS.items():
        port = config["port"]
        description = config["description"]
        print(f"  {server:<30} - {description} (port {port})")


def measure_ping(target: str, interval: float, duration: float):
    """
    Send ICMP pings via ping3.ping() every 'interval' seconds for 'duration' seconds.
    Returns list of RTTs in milliseconds (drops are omitted).
    """
    end = time.monotonic() + duration
    delays = []
    while time.monotonic() < end:
        try:
            r = ping(target, timeout=1)
        except Exception:
            r = None
        if r is not None:
            delays.append(r * 1000.0)
        time.sleep(interval)
    return delays


def bufferbloat_test(server: str, port: int, ping_host: str, duration: int = 10, ping_interval: float = 0.1):
    """
    Measure baseline ping, then ping under iperf3 TCP load (upload & download).
    Returns a dict of stats.
    """
    print("\n=== Bufferbloat Test ===")
    # 1) Baseline
    print(f"Measuring baseline ping to {ping_host} for {duration}s...")
    base = measure_ping(ping_host, ping_interval, duration)
    if not base:
        print("  ERROR: No replies during baseline ping.")
        return None
    base_avg = statistics.mean(base)
    base_sd = statistics.stdev(base) if len(base) > 1 else 0.0
    print(f"  Baseline RTT: avg={base_avg:.1f} ms  sd={base_sd:.1f} ms")

    results = {"baseline_avg": base_avg, "baseline_sd": base_sd, "upload_avg": None,
               "download_avg": None}

    def run_load_and_ping(cmd):
        # Measure ping concurrently while iperf3 runs.
        loaded = []

        def do_ping():
            endt = time.monotonic() + duration
            while time.monotonic() < endt:
                try:
                    r = ping(ping_host, timeout=1)
                    if r is not None:
                        loaded.append(r * 1000.0)
                except Exception:
                    pass  # Skip failed pings
                time.sleep(ping_interval)

        th = threading.Thread(target=do_ping)
        th.start()
        # Launch iperf3
        try:
            with open(os.devnull, "w") as devnull:
                proc = subprocess.Popen(cmd, stdout=devnull, stderr=devnull)
                proc.wait()
        except Exception as e:
            print(f"  ERROR running iperf3: {e}")
        th.join()
        return loaded

    # 2) Upload saturation
    print(f"Running iperf3 TCP upload saturate to {server}:{port} for {duration}s...")
    cmd_up = ["iperf3", "-c", server, "-p", str(port), "-t", str(duration)]
    up_ping = run_load_and_ping(cmd_up)
    if up_ping:
        up_avg = statistics.mean(up_ping)
        results["upload_avg"] = up_avg
        print(f"  Upload-loaded RTT avg={up_avg:.1f} ms  ↑{up_avg-base_avg:.1f} ms")
    else:
        print("  ERROR: No ping replies during upload test.")

    # 3) Download saturation
    print(f"Running iperf3 TCP download saturate (-R) from {server}:{port} for {duration}s...")
    cmd_down = ["iperf3", "-c", server, "-p", str(port), "-R", "-t", str(duration)]
    down_ping = run_load_and_ping(cmd_down)
    if down_ping:
        down_avg = statistics.mean(down_ping)
        results["download_avg"] = down_avg
        print(f"  Download-loaded RTT avg={down_avg:.1f} ms  ↑{down_avg-base_avg:.1f} ms")
    else:
        print("  ERROR: No ping replies during download test.")

    # Grade
    inc_up = (results["upload_avg"] or base_avg) - base_avg
    inc_down = (results["download_avg"] or base_avg) - base_avg
    worst_inc = max(inc_up, inc_down)
    if worst_inc < 30:
        grade = "A"
    elif worst_inc < 100:
        grade = "B"
    else:
        grade = "C"
    results["grade"] = grade
    print(f"  **Grade: {grade}**  (worst ↑{worst_inc:.1f} ms)")
    return results


def jitter_test(server: str, port: int, duration: int = 10, bw: str = "100M"):
    """
    Run iperf3 UDP test. Returns jitter, lost, total.
    """
    print("\n=== Jitter & Packet Loss Test (iperf3 UDP) ===")
    cmd = ["iperf3", "-c", server, "-p", str(port), "-u", "-b", bw, "-t", str(duration), "--json"]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
        data = json.loads(out)
        summary = data.get("end", {}).get("sum", {})
        jitter = summary.get("jitter_ms")
        lost = summary.get("lost_packets")
        total = summary.get("packets")
        print(f"  Jitter: {jitter:.1f} ms; Lost: {lost}/{total}")
        status = "OK" if jitter < 20 and lost == 0 else "WARN"
        print(f"  Status: {status}")
        return {"jitter_ms": jitter, "lost": lost, "total": total, "status": status}
    except Exception as e:
        print("  ERROR running iperf3 UDP:", e)
        return None


def mtr_test(target: str = "8.8.8.8", count: int = 100):
    """
    Run mtr -r -c count target and parse output for any hop loss or spikes.
    """
    print("\n=== MTR Test ===")
    mtr_bin = shutil.which("mtr")
    if not mtr_bin:
        print("  mtr not installed; skipping.")
        return None
    cmd = [mtr_bin, "-r", "-c", str(count), target]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, universal_newlines=True)
        lines = out.strip().splitlines()
        header = lines[0]
        print("  " + header)
        hops = []
        for line in lines[1:]:
            parts = line.split()
            if len(parts) >= 8 and parts[0].isdigit():
                hop = int(parts[0])
                loss = float(parts[1].strip("%"))
                avg = float(parts[4])
                stdev = float(parts[7])
                hops.append((hop, loss, avg, stdev))
                flag = ""
                if loss > 0 or stdev > 10:
                    flag = "  <<<"
                print(f"  {line}{flag}")
        return hops
    except Exception as e:
        print("  ERROR running mtr:", e)
        return None


def mtu_test(host: str = "8.8.8.8"):
    """
    Discover path MTU by ping with DF. Returns MTU in bytes.
    Linux uses "-M do -c1 -s size", Windows "-f -l size -n1".
    """
    print("\n=== MTU Discovery Test ===")
    system = platform.system().lower()
    if system.startswith("linux"):
        df_args = ["-M", "do"]
        size_flag = "-s"
        count_flag = "-c"
    elif system.startswith("windows"):
        df_args = ["-f"]
        size_flag = "-l"
        count_flag = "-n"
    else:
        print("  MTU test unsupported on", system)
        return None

    # IP+ICMP overhead ~= 28 bytes; start at 1472
    low, high = 0, 1472
    mtu = None
    while low <= high:
        mid = (low + high) // 2
        if system.startswith("linux"):
            cmd = ["ping", *df_args, count_flag, "1", size_flag, str(mid), host]
        else:
            cmd = ["ping", *df_args, count_flag, "1", size_flag, str(mid), host]
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode == 0:
            mtu = mid + 28
            low = mid + 1
        else:
            high = mid - 1
    if mtu:
        print(f"  Path MTU ≈ {mtu} bytes")
    else:
        print("  Failed to discover MTU")
    return mtu


def dns_test(domain: str = "google.com", resolvers=None, timeout: float = 1.0):
    """
    Time A-record lookups against each resolver.
    """
    print("\n=== DNS Lookup Test ===")
    if resolvers is None:
        resolvers = ["1.1.1.1", "8.8.8.8"]
    results = {}
    for r in resolvers:
        res = dns.resolver.Resolver(configure=False)
        res.nameservers = [r]
        res.lifetime = timeout
        start = time.monotonic()
        try:
            _answer = res.resolve(domain, "A")
            delay = (time.monotonic() - start) * 1000.0
            print(f"  {r} → {delay:.1f} ms")
            results[r] = delay
        except Exception as e:
            print(f"  {r} → ERROR: {e}")
            results[r] = None
    return results


def cgnat_test():
    """
    Compare router's external IP via UPnP (if available) to public IP from ipify.
    """
    print("\n=== CG-NAT Check ===")
    public_ip = None
    try:
        public_ip = requests.get("https://api.ipify.org", timeout=3).text.strip()
    except Exception:
        print("  ERROR fetching public IP")
        return None

    print(f"  Public IP = {public_ip}")
    if not miniupnpc:
        print("  miniupnpc not installed; cannot query router UPnP")
        return {"public_ip": public_ip, "router_ip": None, "cgnat": None}

    try:
        upnp = miniupnpc.UPnP()
        upnp.discoverdelay = 200
        upnp.discover()
        upnp.selectigd()
        router_ext = upnp.externalipaddress()
        print(f"  Router-reported external IP = {router_ext}")
        cgnat = (router_ext != public_ip)
        print("  CG-NAT Detected!" if cgnat else "  Public IP matches router → No CG-NAT")
        return {"public_ip": public_ip, "router_ip": router_ext, "cgnat": cgnat}
    except Exception as e:
        print("  ERROR querying UPnP:", e)
        return {"public_ip": public_ip, "router_ip": None, "cgnat": None}


def main():
    p = argparse.ArgumentParser(description="Comprehensive Internet link diagnostics")
    p.add_argument("--iperf3-server", 
                   help="IP or hostname of an iperf3 server for TCP/UDP tests (default: auto-select)")
    p.add_argument("--list-servers", action="store_true",
                   help="List available iperf3 servers and exit")
    p.add_argument("--ping-host", default="8.8.8.8",
                   help="Host to ping for bufferbloat (default 8.8.8.8)")
    args = p.parse_args()

    if args.list_servers:
        list_servers()
        return

    # Select iperf3 server
    if args.iperf3_server:
        iperf_server = args.iperf3_server
        # Check if server is in our list to get the port
        if iperf_server in IPERF3_SERVERS:
            iperf_port = IPERF3_SERVERS[iperf_server]["port"]
        else:
            iperf_port = 5201  # Default iperf3 port
        print(f"Using specified iperf3 server: {iperf_server}:{iperf_port}")
    else:
        iperf_server, iperf_port = select_best_server()
        print(f"Auto-selected iperf3 server: {iperf_server}:{iperf_port}")

    buf = bufferbloat_test(iperf_server, iperf_port, args.ping_host)
    jitter = jitter_test(iperf_server, iperf_port)
    mtr = mtr_test()
    mtu = mtu_test()
    dns = dns_test()
    cgnat = cgnat_test()

    # Final summary
    print("\n=== SUMMARY ===")
    ok = True
    if buf and buf.get("grade", "C") not in ("A", "B"):
        print(f"Bufferbloat grade {buf.get('grade')} → PROBLEM")
        ok = False
    if jitter and jitter.get("status") != "OK":
        print("Jitter test WARN → PROBLEM")
        ok = False
    if mtr:
        # any hop with loss?
        for hop, loss, avg, sd in mtr:
            if loss > 0:
                print(f"MTR hop {hop} has {loss:.1f}% loss → PROBLEM")
                ok = False
                break
    if mtu and mtu < 1400:
        print("MTU very low → PROBLEM")
        ok = False
    for r, d in dns.items():
        if d is None or d > 200:
            print(f"DNS {r} lookup slow/failing → PROBLEM")
            ok = False
    if cgnat and cgnat.get("cgnat"):
        print("Carrier-Grade NAT detected → may break inbound connections")
        # not necessarily "fail", but note it
    if ok:
        print("All tests passed within expected thresholds.")
    else:
        print("One or more tests indicated potential issues. Please review above details.")


if __name__ == "__main__":
    main()
