# Network Diagnostics Tool 🌐

A comprehensive Internet link diagnostics tool designed for professional network analysis and ISP troubleshooting. Perfect for identifying bufferbloat, jitter, packet loss, route issues, and gathering evidence for ISP support cases.

## ✨ Features

- 🌐 **Bufferbloat Testing** - Measure latency under load (TCP upload/download)
- 📡 **Jitter & Packet Loss** - UDP performance analysis critical for VoIP/VOD
- 🛣️ **Route Analysis** - Enhanced MTR with hop-by-hop problem detection
- 🔍 **DNS Performance** - Multi-resolver latency testing
- 🏠 **CG-NAT Detection** - Identify carrier-grade NAT issues
- 📏 **MTU Discovery** - Path MTU detection
- 📊 **Statistical Analysis** - Multiple runs with min/max/average statistics
- ⚡ **Parallel Testing** - Stress test your connection
- 💾 **Professional Logging** - ISP-ready evidence collection
- 🎯 **Smart Defaults** - Easy to use without complex configuration

## 🚀 Quick Start

### Simple Usage (Recommended)
```bash
# Comprehensive analysis with automatic ISP evidence collection
python netdiag.py

# Quick single test
python netdiag.py --quick

# List available iperf3 servers
python netdiag.py --list-servers
```

## 📦 Installation

### Prerequisites
```bash
# Install system dependencies
# macOS:
brew install iperf3 mtr

# Ubuntu/Debian:
sudo apt install iperf3 mtr

# Optional: For enhanced route analysis
# MTR provides detailed hop-by-hop network analysis
```

### Python Dependencies
```bash
# Create virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install required packages
pip install -r requirements.txt
```

### Requirements.txt
```
ping3
dnspython
miniupnpc
requests
```

## 📖 Usage Guide

### Basic Commands

#### Default Comprehensive Mode
```bash
python netdiag.py
```
**What it does:**
- Runs 5 complete diagnostic tests
- Auto-selects best iperf3 server with UDP support
- Saves detailed results to `network_diagnostics.txt`
- Provides statistical analysis across all runs
- Perfect for ISP troubleshooting

#### Quick Mode
```bash
python netdiag.py --quick
```
**What it does:**
- Single fast test run
- No logging (immediate results only)
- Ideal for quick network checks

### Advanced Usage

#### Custom Test Runs
```bash
# Run 10 tests for extensive analysis
python netdiag.py --runs 10

# Run 6 tests with 3 parallel threads (stress testing)
python netdiag.py --runs 6 --parallel 3

# Custom output file
python netdiag.py --output my_isp_evidence.txt
```

#### Server Selection
```bash
# Use specific iperf3 server
python netdiag.py --iperf3-server ping.online.net

# Custom ping target for bufferbloat testing
python netdiag.py --ping-host 1.1.1.1
```

#### Enhanced Analysis
```bash
# Thorough MTR analysis (more packets = better accuracy)
python netdiag.py --mtr-count 500

# Comprehensive ISP evidence collection
python netdiag.py --runs 10 --parallel 2 --mtr-count 300 --output isp_evidence.txt
```

## 🎯 Parameter Reference

| Parameter | Default | Description | Example |
|-----------|---------|-------------|---------|
| `--runs` | `5` | Number of test runs for statistics | `--runs 10` |
| `--parallel` | `1` | Parallel threads (auto-enabled for 6+ runs) | `--parallel 3` |
| `--output` | `network_diagnostics.txt` | Results file for ISP evidence | `--output evidence.txt` |
| `--mtr-count` | `200` | MTR packets for route analysis | `--mtr-count 500` |
| `--iperf3-server` | Auto-select | Specific iperf3 server | `--iperf3-server ping.online.net` |
| `--ping-host` | `8.8.8.8` | Target for bufferbloat testing | `--ping-host 1.1.1.1` |
| `--quick` | - | Single fast test (disables logging) | `--quick` |
| `--list-servers` | - | Show available iperf3 servers | `--list-servers` |

## 🌍 Available iperf3 Servers

| Server | Location | Ports | Speed | Features |
|--------|----------|--------|--------|----------|
| `ping.online.net` | France (Scaleway) | 5200-5209 | 100 Gbit/s | TCP + UDP |
| `speedtest.milkywan.fr` | France (CBO) | 9200-9240 | 40 Gbit/s | TCP + UDP |
| `str.cubic.iperf.bytel.fr` | France (Bouygues) | 9200-9240 | 10 Gbit/s | TCP + UDP |
| `ch.iperf.014.fr` | Switzerland (HostHatch) | 15315-15320 | 3 Gbit/s | TCP + UDP |

## 📊 Understanding Results

### Bufferbloat Grades
- **Grade A**: < 30ms latency increase (Excellent)
- **Grade B**: 30-100ms latency increase (Good)  
- **Grade C**: > 100ms latency increase (Poor - ISP issue)

### Jitter & Packet Loss
- **Excellent**: < 5ms jitter, 0% loss
- **Good**: < 20ms jitter, < 0.1% loss
- **Poor**: > 20ms jitter or > 1% loss (VoIP/VOD issues)

### DNS Performance
- **Excellent**: < 50ms
- **Good**: 50-100ms
- **Poor**: > 200ms or timeouts

### MTR Route Analysis
- 🔴 **LOSS**: Packet loss detected
- 🟡 **HIGH-JITTER**: Excessive latency variation
- 🟠 **LATENCY-VAR**: High latency variation
- ✅ **Healthy**: No issues detected

## 🏥 ISP Troubleshooting Examples

### Document Intermittent Issues
```bash
# Run throughout the day to catch peak-time problems
python netdiag.py --runs 10 --output morning_test.txt
python netdiag.py --runs 10 --output evening_test.txt
```

### Stress Test Your Connection
```bash
# Simulate heavy usage with parallel tests
python netdiag.py --runs 8 --parallel 4 --output stress_test.txt
```

### Gaming/VoIP Performance
```bash
# Focus on jitter and latency with frequent testing
python netdiag.py --runs 15 --mtr-count 500 --output gaming_analysis.txt
```

### Video Streaming Issues
```bash
# UDP performance critical for streaming
python netdiag.py --runs 10 --iperf3-server ping.online.net --output streaming_test.txt
```

## 📄 Evidence Files

### What's Included
- **Complete JSON logs** for each test run
- **Timestamps** for all measurements  
- **Statistical summaries** with min/max/average
- **Problem identification** with specific metrics
- **Professional formatting** ready for ISP support

### Sample Evidence Content
```json
BUFFERBLOAT Results:
{
  "baseline_avg": 25.4,
  "upload_avg": 145.7,
  "download_avg": 89.2,
  "grade": "C"
}

STATISTICAL SUMMARY:
Baseline RTT: 23.1 - 28.7 ms (avg: 25.9)
Upload impact: 98.3 - 156.2 ms (avg: 127.4)
Download impact: 45.1 - 112.8 ms (avg: 78.3)
Grades: {'C': 8, 'B': 2}
```

## 🔧 Troubleshooting

### Common Issues

#### "No iperf3 servers responding"
```bash
# Check if iperf3 is installed
iperf3 --version

# Try specific server
python netdiag.py --iperf3-server ping.online.net

# List available servers
python netdiag.py --list-servers
```

#### "MTR not installed"
```bash
# macOS
brew install mtr

# Ubuntu/Debian  
sudo apt install mtr

# The script will work without MTR but route analysis will be skipped
```

#### "UDP tests failing"
- Some networks block UDP traffic
- Try different servers: `--iperf3-server speedtest.milkywan.fr`
- Corporate firewalls often block UDP

### Performance Tips

#### For Faster Testing
```bash
python netdiag.py --quick --mtr-count 50
```

#### For Maximum Accuracy
```bash
python netdiag.py --runs 15 --mtr-count 500 --parallel 1
```

## 🎯 Use Cases

### For End Users
- **Quick Check**: `python netdiag.py --quick`
- **ISP Issues**: `python netdiag.py` (default comprehensive mode)
- **Gaming Performance**: Focus on jitter and route analysis

### For Network Administrators  
- **Baseline Documentation**: Regular comprehensive testing
- **Capacity Planning**: Parallel stress testing
- **Vendor SLA Validation**: Statistical evidence collection

### For ISP Support Cases
- **Evidence Collection**: Multiple runs with detailed logging
- **Problem Reproduction**: Scheduled testing during problem periods
- **Performance Degradation**: Before/after comparisons

## 🤝 Contributing

### Adding New iperf3 Servers
Edit the `IPERF3_SERVERS` dictionary in `netdiag.py`:
```python
"your.server.com": {
    "description": "Your ISP Name", 
    "ports": list(range(5200, 5210))
}
```

### Reporting Issues
Please include:
- Command used
- Complete output/error message
- Operating system and Python version
- Network configuration (if relevant)

## 📋 Technical Details

### Test Methodology
- **Bufferbloat**: ICMP ping during TCP saturation
- **Jitter**: iperf3 UDP with 1-100Mbps bandwidth
- **Route Analysis**: MTR with 100-500 packets
- **DNS**: A-record lookups with 1-second timeout

### Supported Platforms
- ✅ **macOS** (tested)
- ✅ **Linux** (Ubuntu, Debian, CentOS)
- ⚠️ **Windows** (partial - MTU discovery unsupported)

### Dependencies
- **Python 3.7+**
- **iperf3** (system package)
- **mtr** (optional, for route analysis)
- **Python packages**: ping3, dnspython, miniupnpc, requests

## 📞 Support

For ISP support cases, include:
1. The generated evidence file (`network_diagnostics.txt`)
2. Multiple test runs from different times of day
3. Comparison with a known-good network (if available)
4. Specific symptoms (slow uploads, video buffering, etc.)

---

**Built for professional network diagnostics and ISP troubleshooting** 🚀