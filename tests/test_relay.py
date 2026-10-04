"""Exercise coop_relay.py with two fake clients: forwarding, WELCOME, latency, loss."""
import socket
import subprocess
import sys
import tempfile
import time

RELAY = sys.argv[1]
START_ATTEMPTS = 3


def free_udp_port():
    """A port no one is bound to right now, so parallel runs never share a relay."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]
    finally:
        probe.close()


def start_relay(extra_args):
    """Starts the relay on a free port; retries when it exits early (port taken in between)."""
    for _ in range(START_ATTEMPTS):
        port = free_udp_port()
        errors = tempfile.TemporaryFile()
        proc = subprocess.Popen([sys.executable, RELAY, "--port", str(port), "--seed", "1", "--log", "NUL"] + extra_args,
                                stdout=subprocess.DEVNULL, stderr=errors)
        time.sleep(0.8)
        if proc.poll() is None:
            errors.close()
            return proc, port
        errors.seek(0)
        reason = errors.read().decode(errors="replace").strip().splitlines()
        errors.close()
        print(f"relay on port {port} exited early (code {proc.returncode}): {reason[-1] if reason else 'no output'}")
    print(f"FAIL  relay could not start in {START_ATTEMPTS} attempts")
    sys.exit(1)


def client():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.01)
    return sock


def drain(sock):
    out = []
    while True:
        try:
            data, _ = sock.recvfrom(2048)
            out.append((time.monotonic(), data.decode()))
        except (socket.timeout, ConnectionResetError):
            return out


def session(extra_args, packets=60):
    proc, port = start_relay(extra_args)
    a, b = client(), client()
    sent_at = {}
    received = []
    try:
        for seq in range(1, packets + 1):
            payload = f"CP1,{seq},1.5,2.5,3.5,1.0,{0.6 * 101:.6f},{0.8 * 101:.6f}"
            sent_at[seq] = time.monotonic()
            a.sendto(payload.encode(), ("127.0.0.1", port))
            b.sendto(f"CP1,{seq},0,0,0,1,0,1".encode(), ("127.0.0.1", port))
            end = time.monotonic() + 0.033
            while time.monotonic() < end:
                received += drain(b)
        end = time.monotonic() + 0.6
        while time.monotonic() < end:
            received += drain(b)
    finally:
        proc.terminate()
        proc.wait()
    # only this run's own packets (a stray sender on the port must not count)
    rp1 = [(t, m) for t, m in received if m.startswith("RP1") and m.count(",") >= 2
           and m.split(",")[2].isdigit() and int(m.split(",")[2]) in sent_at]
    welcome = [m for _, m in received if m.startswith("WELCOME")]
    delays = []
    for t, message in rp1:
        seq = int(message.split(",")[2])
        delays.append((t - sent_at[seq]) * 1000)
    sample = rp1[0][1] if rp1 else ""
    return rp1, welcome, delays, sample


clean_rp1, clean_welcome, clean_delays, sample = session([])
print("clean: RP1", len(clean_rp1), "WELCOME", len(clean_welcome), "sample", sample)
lag_rp1, _, lag_delays, _ = session(["--latency-ms", "120", "--jitter-ms", "20", "--loss-pct", "10"])
avg = sum(lag_delays) / len(lag_delays) if lag_delays else float("nan")
print(f"lagged: RP1 {len(lag_rp1)}/60, delay avg {avg:.0f} ms min {min(lag_delays, default=float('nan')):.0f} "
      f"max {max(lag_delays, default=float('nan')):.0f}")

ok = (
    len(clean_rp1) >= 58
    and clean_welcome and clean_welcome[0].startswith("WELCOME,")
    and sample.split(",")[3:] == ["1.5", "2.5", "3.5", "1.0", "60.600000", "80.800000"]
    and 45 <= len(lag_rp1) <= 58
    and 115 <= avg <= 160
)
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
