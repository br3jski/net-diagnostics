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
import queue
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
    "ping.online.net": {
        "description": "Scaleway France", 
        "ports": list(range(5200, 5210))  # 5200-5209
    },
    "speedtest.milkywan.fr": {
        "description": "CBO France", 
        "ports": list(range(9200, 9241))  # 9200-9240
    }, 
    "str.cubic.iperf.bytel.fr": {
        "description": "Bouygues France", 
        "ports": list(range(9200, 9241))  # 9200-9240
    },
    "ch.iperf.014.fr": {
        "description": "HostHatch Switzerland", 
        "ports": list(range(15315, 15321))  # 15315-15320
    }
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


def test_server_connectivity(server: str, port: int, timeout: int = 3) -> bool:
    """
    Test if a port is open and accepting connections using a simple socket test.
    This is much faster and more reliable than running full iperf3 tests.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((server, port))
        sock.close()
        return result == 0  # 0 means connection successful
    except Exception:
        return False


def test_udp_support(server: str, port: int, timeout: int = 5) -> bool:
    """
    Test if server supports UDP iperf3 tests by running a very short test.
    """
    try:
        cmd = ["iperf3", "-c", server, "-p", str(port), "-u", "-b", "1M", "-t", "1", "--json"]
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if result.returncode == 0:
            # Parse JSON to check if we got valid UDP results
            try:
                data = json.loads(result.stdout)
                summary = data.get("end", {}).get("sum", {})
                return (summary.get("jitter_ms") is not None and 
                       summary.get("lost_packets") is not None and 
                       summary.get("packets") is not None)
            except (json.JSONDecodeError, KeyError):
                return False
        return False
    except (subprocess.TimeoutExpired, Exception):
        return False


def select_best_server() -> tuple:
    """
    Test available servers and return the first one with both TCP and UDP support.
    Falls back to TCP-only servers if no UDP support is found.
    """
    print("Testing iperf3 server connectivity...")
    
    # First pass: look for servers with both TCP and UDP support
    for server, config in IPERF3_SERVERS.items():
        description = config["description"]
        ports = config["ports"]
        
        print(f"  Testing {server} ({description}) on ports {ports[0]}-{ports[-1]}...")
        
        # Try each port until we find one that works
        for port in ports:
            print(f"    Trying port {port}...", end=" ")
            
            # Test TCP connectivity first
            if test_server_connectivity(server, port, timeout=3):
                print("TCP ✓", end=" ")
                
                # Test UDP support
                print("UDP...", end=" ")
                if test_udp_support(server, port, timeout=5):
                    print("✓")
                    print(f"  → Selected {server}:{port} (TCP + UDP support)")
                    return server, port
                else:
                    print("✗")
                    print(f"    Port {port}: TCP works but no UDP support")
            else:
                print("TCP ✗")
        
        print(f"  → No working ports found on {server}")
    
    print("\n  No servers with UDP support found. Trying TCP-only servers...")
    
    # Second pass: fallback to TCP-only servers
    for server, config in IPERF3_SERVERS.items():
        description = config["description"]
        ports = config["ports"]
        
        print(f"  Testing {server} ({description}) for TCP-only...")
        
        for port in ports:
            print(f"    Trying port {port}...", end=" ")
            if test_server_connectivity(server, port, timeout=3):
                print("✓")
                print(f"  → Selected {server}:{port} (TCP only - UDP may fail)")
                return server, port
            else:
                print("✗")
    
    # If none respond, return the first server and first port anyway
    fallback_server = list(IPERF3_SERVERS.keys())[0]
    fallback_port = IPERF3_SERVERS[fallback_server]["ports"][0]
    print(f"  No servers responded, using fallback: {fallback_server}:{fallback_port}")
    return fallback_server, fallback_port


def list_servers():
    """
    Display available iperf3 servers.
    """
    print("Available iperf3 servers:")
    for server, config in IPERF3_SERVERS.items():
        description = config["description"]
        ports = config["ports"]
        if len(ports) == 1:
            port_display = f"port {ports[0]}"
        else:
            port_display = f"ports {ports[0]}-{ports[-1]}"
        print(f"  {server:<30} - {description} ({port_display})")


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
        
        # Handle None values with proper fallbacks
        if jitter is None or lost is None or total is None:
            print("  ERROR: Incomplete UDP test results from server")
            return None
            
        print(f"  Jitter: {jitter:.1f} ms; Lost: {lost}/{total}")
        status = "OK" if jitter < 20 and lost == 0 else "WARN"
        print(f"  Status: {status}")
        return {"jitter_ms": jitter, "lost": lost, "total": total, "status": status}
    except subprocess.CalledProcessError as e:
        print(f"  ERROR: iperf3 UDP test failed (exit code {e.returncode})")
        return None
    except json.JSONDecodeError:
        print("  ERROR: Invalid JSON response from iperf3 UDP test")
        return None
    except Exception as e:
        print(f"  ERROR running iperf3 UDP: {e}")
        return None


def mtr_test(target: str = "8.8.8.8", count: int = 100):
    """
    Run mtr -r -c count target and parse output for comprehensive hop analysis.
    Enhanced for ISP troubleshooting with detailed statistics.
    """
    print("\n=== MTR Test ===")
    mtr_bin = shutil.which("mtr")
    if not mtr_bin:
        print("  mtr not installed; skipping.")
        print("  Install with: brew install mtr (macOS) or apt install mtr (Linux)")
        return None
    
    print(f"  Running MTR to {target} with {count} packets...")
    cmd = [mtr_bin, "-r", "-c", str(count), target]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, universal_newlines=True)
        lines = out.strip().splitlines()
        
        if len(lines) < 2:
            print("  ERROR: No MTR data received")
            return None
            
        header = lines[0]
        print("  " + header)
        
        hops = []
        problem_hops = []
        
        for line in lines[1:]:
            parts = line.split()
            if len(parts) >= 8 and parts[0].isdigit():
                hop = int(parts[0])
                loss = float(parts[1].strip("%"))
                avg = float(parts[4])
                stdev = float(parts[7])
                
                # Additional MTR fields for comprehensive analysis
                sent = int(parts[2]) if len(parts) > 2 else count
                last = float(parts[3]) if len(parts) > 3 else avg
                best = float(parts[5]) if len(parts) > 5 else avg
                worst = float(parts[6]) if len(parts) > 6 else avg
                
                hop_data = {
                    'hop': hop,
                    'loss': loss,
                    'avg': avg,
                    'stdev': stdev,
                    'last': last,
                    'best': best,
                    'worst': worst,
                    'sent': sent
                }
                
                hops.append((hop, loss, avg, stdev))
                
                # Flag problematic hops for ISP evidence
                flags = []
                if loss > 0:
                    flags.append("🔴 LOSS")
                    problem_hops.append(f"Hop {hop}: {loss:.1f}% packet loss")
                if stdev > 20:  # High jitter threshold
                    flags.append("🟡 HIGH-JITTER")
                    problem_hops.append(f"Hop {hop}: {stdev:.1f}ms jitter")
                if worst - best > 100:  # High latency variation
                    flags.append("🟠 LATENCY-VAR")
                    problem_hops.append(f"Hop {hop}: {worst-best:.1f}ms latency variation")
                
                flag_str = " " + " ".join(flags) if flags else ""
                print(f"  {line}{flag_str}")
                
        # Summary for ISP troubleshooting
        if problem_hops:
            print(f"\n  ⚠️  PROBLEMS DETECTED ({len(problem_hops)} issues):")
            for problem in problem_hops:
                print(f"     {problem}")
        else:
            print(f"\n  ✅ All {len(hops)} hops look healthy")
            
        return hops
        
    except subprocess.CalledProcessError as e:
        print(f"  ERROR: MTR command failed (exit code {e.returncode})")
        return None
    except Exception as e:
        print(f"  ERROR running MTR: {e}")
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
        discovered = upnp.discover()
        if discovered == 0:
            print("  No UPnP devices found on network")
            return {"public_ip": public_ip, "router_ip": None, "cgnat": None}
        
        upnp.selectigd()
        router_ext = upnp.externalipaddress()
        
        if not router_ext or router_ext == "0.0.0.0":
            print("  Router UPnP available but no external IP reported")
            return {"public_ip": public_ip, "router_ip": None, "cgnat": None}
            
        print(f"  Router-reported external IP = {router_ext}")
        cgnat = (router_ext != public_ip)
        print("  CG-NAT Detected!" if cgnat else "  Public IP matches router → No CG-NAT")
        return {"public_ip": public_ip, "router_ip": router_ext, "cgnat": cgnat}
        
    except Exception as e:
        error_msg = str(e).lower()
        if "success" in error_msg:
            print("  UPnP query completed but router configuration unavailable")
        elif "no igd found" in error_msg:
            print("  No UPnP Internet Gateway Device found")
        elif "timeout" in error_msg or "connection" in error_msg:
            print("  UPnP connection timeout - router may not support UPnP")
        else:
            print(f"  UPnP query failed: {e}")
        return {"public_ip": public_ip, "router_ip": None, "cgnat": None}


def main():
    p = argparse.ArgumentParser(description="Comprehensive Internet link diagnostics")
    p.add_argument("--iperf3-server", 
                   help="IP or hostname of an iperf3 server for TCP/UDP tests (default: auto-select)")
    p.add_argument("--list-servers", action="store_true",
                   help="List available iperf3 servers and exit")
    p.add_argument("--ping-host", default="8.8.8.8",
                   help="Host to ping for bufferbloat (default 8.8.8.8)")
    p.add_argument("--runs", type=int, default=5,
                   help="Number of test runs to perform (default: 5 for comprehensive analysis)")
    p.add_argument("--parallel", type=int, default=1,
                   help="Number of parallel test threads (default: 1)")
    p.add_argument("--output", type=str, default="network_diagnostics.txt",
                   help="Save detailed results to file (default: network_diagnostics.txt)")
    p.add_argument("--mtr-count", type=int, default=200,
                   help="Number of MTR packets to send (default: 200 for thorough analysis)")
    p.add_argument("--quick", action="store_true",
                   help="Quick single run mode (disables multiple runs and logging)")
    args = p.parse_args()

    if args.list_servers:
        list_servers()
        return

    # Quick mode for fast testing
    if args.quick:
        args.runs = 1
        args.parallel = 1
        args.output = None
        args.mtr_count = 100
        print("=== Quick Mode: Single Test Run ===")
    else:
        print("=== Comprehensive Mode: Multiple Runs for ISP Evidence ===")
        print(f"Running {args.runs} tests, saving results to {args.output}")
        print("Use --quick for single fast test, or --help for all options")

    # Initialize logging if output file specified
    log_file = None
    if args.output:
        log_file = open(args.output, 'w')
        log_file.write(f"Network Diagnostics Report - {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        log_file.write("=" * 60 + "\n\n")

    # Select iperf3 server
    if args.iperf3_server:
        iperf_server = args.iperf3_server
        # Check if server is in our list to get the port
        if iperf_server in IPERF3_SERVERS:
            # Test the server's ports to find a working one
            config = IPERF3_SERVERS[iperf_server]
            ports = config["ports"]
            description = config["description"]
            print(f"Testing specified server {iperf_server} ({description})...")
            
            working_port = None
            udp_support = False
            
            for port in ports:
                print(f"  Trying port {port}...", end=" ")
                if test_server_connectivity(iperf_server, port, timeout=3):
                    print("TCP ✓", end=" ")
                    
                    # Test UDP support
                    print("UDP...", end=" ")
                    if test_udp_support(iperf_server, port, timeout=5):
                        print("✓")
                        working_port = port
                        udp_support = True
                        break
                    else:
                        print("✗")
                        if working_port is None:  # Keep first working TCP port as fallback
                            working_port = port
                else:
                    print("TCP ✗")
            
            if working_port:
                iperf_port = working_port
                if udp_support:
                    print(f"Using {iperf_server}:{iperf_port} (TCP + UDP support)")
                else:
                    print(f"Using {iperf_server}:{iperf_port} (TCP only - UDP tests may fail)")
            else:
                iperf_port = ports[0]  # Use first port as fallback
                print(f"No ports responded, using fallback {iperf_server}:{iperf_port}")
        else:
            iperf_port = 5201  # Default iperf3 port
            print(f"Using specified iperf3 server: {iperf_server}:{iperf_port}")
            print("Note: Custom server - UDP support unknown")
    else:
        iperf_server, iperf_port = select_best_server()

    # Run tests (single or multiple runs)
    if args.runs == 1:
        # Single run mode
        print(f"\n=== Running Single Diagnostic Test ===")
        results = run_single_test(iperf_server, iperf_port, args.ping_host, args.mtr_count, log_file)
        display_final_summary(results, log_file)
    else:
        # Multiple runs mode with intelligent batching
        print(f"\n=== Running {args.runs} Tests for Comprehensive Analysis ===")
        
        # Auto-enable parallel for stress testing if many runs
        effective_parallel = args.parallel
        if args.runs >= 6 and args.parallel == 1:
            effective_parallel = 2
            print(f"Auto-enabling parallel execution ({effective_parallel} threads) for stress testing")
        
        all_results = run_multiple_tests(iperf_server, iperf_port, args.ping_host, args.mtr_count, 
                                       args.runs, effective_parallel, log_file)
        display_statistical_summary(all_results, log_file)

    if log_file:
        log_file.close()
        print(f"\n📄 Detailed results saved to: {args.output}")
        print("💡 Use this file as evidence when contacting your ISP about network issues")


def run_single_test(iperf_server, iperf_port, ping_host, mtr_count, log_file):
    """Run a single complete diagnostic test."""
    results = {}
    
    results['bufferbloat'] = bufferbloat_test(iperf_server, iperf_port, ping_host)
    results['jitter'] = jitter_test(iperf_server, iperf_port)
    results['mtr'] = mtr_test(count=mtr_count)
    results['mtu'] = mtu_test()
    results['dns'] = dns_test()
    results['cgnat'] = cgnat_test()
    
    if log_file:
        log_test_results(results, log_file, run_number=1)
    
    return results


def run_multiple_tests(iperf_server, iperf_port, ping_host, mtr_count, num_runs, parallel, log_file):
    """Run multiple diagnostic tests and collect statistics."""
    all_results = []
    results_queue = queue.Queue()
    
    def run_test_worker(run_id):
        print(f"  Run {run_id}: Starting...")
        try:
            results = run_single_test(iperf_server, iperf_port, ping_host, mtr_count, log_file)
            results['run_id'] = run_id
            results_queue.put(results)
            print(f"  Run {run_id}: Complete")
        except Exception as e:
            print(f"  Run {run_id}: Failed - {e}")
            results_queue.put({'run_id': run_id, 'error': str(e)})
    
    # Run tests in batches if parallel > 1
    remaining_runs = list(range(1, num_runs + 1))
    
    while remaining_runs:
        # Create batch of parallel runs
        batch_size = min(parallel, len(remaining_runs))
        current_batch = remaining_runs[:batch_size]
        remaining_runs = remaining_runs[batch_size:]
        
        # Start threads for current batch
        threads = []
        for run_id in current_batch:
            thread = threading.Thread(target=run_test_worker, args=(run_id,))
            thread.start()
            threads.append(thread)
        
        # Wait for batch to complete
        for thread in threads:
            thread.join()
    
    # Collect all results
    while not results_queue.empty():
        all_results.append(results_queue.get())
    
    return sorted(all_results, key=lambda x: x.get('run_id', 0))


def log_test_results(results, log_file, run_number):
    """Log detailed test results to file."""
    log_file.write(f"=== Test Run {run_number} ===\n")
    log_file.write(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
    
    # Log each test result
    for test_name, result in results.items():
        if result:
            log_file.write(f"{test_name.upper()} Results:\n")
            log_file.write(f"{json.dumps(result, indent=2)}\n\n")
    
    log_file.write("-" * 40 + "\n\n")
    log_file.flush()


def display_statistical_summary(all_results, log_file):
    """Display comprehensive statistics across all test runs."""
    print("\n" + "=" * 60)
    print("STATISTICAL SUMMARY")
    print("=" * 60)
    
    # Extract successful results (filter out errors)
    valid_results = [r for r in all_results if 'error' not in r]
    failed_runs = [r for r in all_results if 'error' in r]
    
    if failed_runs:
        print(f"⚠️  {len(failed_runs)} of {len(all_results)} runs failed")
        for failed in failed_runs:
            print(f"   Run {failed['run_id']}: {failed['error']}")
        print()
    
    if not valid_results:
        print("❌ No successful test runs to analyze")
        return
    
    print(f"📊 Analyzing {len(valid_results)} successful runs")
    print()
    
    # Bufferbloat statistics
    analyze_bufferbloat_stats(valid_results)
    
    # Jitter statistics  
    analyze_jitter_stats(valid_results)
    
    # DNS statistics
    analyze_dns_stats(valid_results)
    
    # MTR statistics (enhanced)
    analyze_mtr_stats(valid_results)
    
    if log_file:
        log_file.write("STATISTICAL SUMMARY\n")
        log_file.write("=" * 40 + "\n")
        log_file.write(f"Successful runs: {len(valid_results)}\n")
        log_file.write(f"Failed runs: {len(failed_runs)}\n\n")


def analyze_bufferbloat_stats(results):
    """Analyze bufferbloat test statistics across multiple runs."""
    baseline_rtts = []
    upload_increases = []
    download_increases = []
    grades = []
    
    for result in results:
        buf = result.get('bufferbloat')
        if buf:
            if buf.get('baseline_avg'):
                baseline_rtts.append(buf['baseline_avg'])
            if buf.get('upload_avg') and buf.get('baseline_avg'):
                upload_increases.append(buf['upload_avg'] - buf['baseline_avg'])
            if buf.get('download_avg') and buf.get('baseline_avg'):
                download_increases.append(buf['download_avg'] - buf['baseline_avg'])
            if buf.get('grade'):
                grades.append(buf['grade'])
    
    print("🌐 BUFFERBLOAT ANALYSIS")
    if baseline_rtts:
        print(f"   Baseline RTT: {min(baseline_rtts):.1f} - {max(baseline_rtts):.1f} ms (avg: {statistics.mean(baseline_rtts):.1f})")
    if upload_increases:
        print(f"   Upload impact: {min(upload_increases):.1f} - {max(upload_increases):.1f} ms (avg: {statistics.mean(upload_increases):.1f})")
    if download_increases:
        print(f"   Download impact: {min(download_increases):.1f} - {max(download_increases):.1f} ms (avg: {statistics.mean(download_increases):.1f})")
    if grades:
        grade_counts = {g: grades.count(g) for g in set(grades)}
        print(f"   Grades: {grade_counts}")
    print()


def analyze_jitter_stats(results):
    """Analyze jitter test statistics across multiple runs.""" 
    jitters = []
    loss_rates = []
    
    for result in results:
        jitter = result.get('jitter')
        if jitter:
            if jitter.get('jitter_ms'):
                jitters.append(jitter['jitter_ms'])
            if jitter.get('lost') is not None and jitter.get('total'):
                loss_rate = (jitter['lost'] / jitter['total']) * 100
                loss_rates.append(loss_rate)
    
    print("📡 JITTER & PACKET LOSS ANALYSIS")
    if jitters:
        print(f"   Jitter: {min(jitters):.1f} - {max(jitters):.1f} ms (avg: {statistics.mean(jitters):.1f})")
    if loss_rates:
        print(f"   Packet loss: {min(loss_rates):.3f}% - {max(loss_rates):.3f}% (avg: {statistics.mean(loss_rates):.3f}%)")
    print()


def analyze_dns_stats(results):
    """Analyze DNS lookup statistics across multiple runs."""
    dns_times = {'1.1.1.1': [], '8.8.8.8': []}
    
    for result in results:
        dns = result.get('dns')
        if dns:
            for resolver, time_ms in dns.items():
                if time_ms is not None and resolver in dns_times:
                    dns_times[resolver].append(time_ms)
    
    print("🔍 DNS LOOKUP ANALYSIS")
    for resolver, times in dns_times.items():
        if times:
            print(f"   {resolver}: {min(times):.1f} - {max(times):.1f} ms (avg: {statistics.mean(times):.1f})")
    print()


def analyze_mtr_stats(results):
    """Enhanced MTR analysis with hop-by-hop statistics."""
    print("🛣️  MTR ROUTE ANALYSIS")
    
    # Collect all MTR results
    all_hops = {}  # hop_number -> list of (loss, avg_rtt, stdev)
    
    for result in results:
        mtr = result.get('mtr')
        if mtr:
            for hop, loss, avg, stdev in mtr:
                if hop not in all_hops:
                    all_hops[hop] = []
                all_hops[hop].append((loss, avg, stdev))
    
    if not all_hops:
        print("   No MTR data available")
        return
    
    print("   Hop-by-hop analysis:")
    for hop in sorted(all_hops.keys()):
        hop_data = all_hops[hop]
        losses = [d[0] for d in hop_data]
        rtts = [d[1] for d in hop_data]
        stdevs = [d[2] for d in hop_data]
        
        max_loss = max(losses)
        avg_rtt = statistics.mean(rtts)
        max_stdev = max(stdevs)
        
        status = ""
        if max_loss > 0:
            status += " 🔴 LOSS"
        if max_stdev > 10:
            status += " 🟡 JITTER"
        if not status:
            status = " ✅"
            
        print(f"   Hop {hop:2d}: {avg_rtt:6.1f}ms avg, {max_loss:4.1f}% max loss, {max_stdev:5.1f}ms max jitter{status}")
    
    print()


def display_final_summary(results, log_file):
    """Display final summary for single test run."""
    print("\n=== SUMMARY ===")
    ok = True
    if results.get('bufferbloat') and results['bufferbloat'].get("grade", "C") not in ("A", "B"):
        print(f"Bufferbloat grade {results['bufferbloat'].get('grade')} → PROBLEM")
        ok = False
    if results.get('jitter') and results['jitter'].get("status") != "OK":
        print("Jitter test WARN → PROBLEM")
        ok = False
    if results.get('mtr'):
        # any hop with loss?
        for hop, loss, avg, sd in results['mtr']:
            if loss > 0:
                print(f"MTR hop {hop} has {loss:.1f}% loss → PROBLEM")
                ok = False
                break
    if results.get('mtu') and results['mtu'] < 1400:
        print("MTU very low → PROBLEM")
        ok = False
    if results.get('dns'):
        for r, d in results['dns'].items():
            if d is None or d > 200:
                print(f"DNS {r} lookup slow/failing → PROBLEM")
                ok = False
    if results.get('cgnat') and results['cgnat'].get("cgnat"):
        print("Carrier-Grade NAT detected → may break inbound connections")
        # not necessarily "fail", but note it
    if ok:
        print("All tests passed within expected thresholds.")
    else:
        print("One or more tests indicated potential issues. Please review above details.")


if __name__ == "__main__":
    main()
