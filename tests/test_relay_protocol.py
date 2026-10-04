"""Black-box test of tools/coopnet_relay.py over real UDP sockets (no link simulation).

Covers HELLO/WELCOME, PEERS, PING/PONG, forwarding by target, broadcast, sender spoofing,
protocol-version REJECT and BYE. Exit code 0 = all checks passed.
"""
import os
import socket
import struct
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import coopnet_relay as relay  # noqa: E402

PORT = 11799
RELAY_ADDRESS = ("127.0.0.1", PORT)
RELAY_LOG = os.path.join(ROOT, "build", "relay_protocol_test.log")
READY_TIMEOUT_SECONDS = 20.0


class Client:
    def __init__(self, nonce, room="test"):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(1.0)
        self.nonce = nonce
        self.room = room.encode()
        self.client_id = None

    def send(self, channel, target, payload, sender=None, version=relay.PROTOCOL_VERSION):
        sender_id = (self.client_id or 0) if sender is None else sender
        header = relay.HEADER.pack(relay.MAGIC, version, channel, sender_id, target, 0, 0, 0, len(payload))
        self.sock.sendto(header + payload, RELAY_ADDRESS)

    def receive(self, want_op=None, timeout=1.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, _ = self.sock.recvfrom(2048)
            except socket.timeout:
                return None
            except ConnectionResetError:
                continue  # ICMP port unreachable: relay not listening yet
            decoded = relay.decode_frame(data)
            if decoded is None:
                continue
            fields, payload = decoded
            if want_op is None or (fields[2] == relay.CONTROL_CHANNEL and payload and payload[0] == want_op):
                return fields, payload
        return None

    def hello(self):
        body = struct.pack("<BIB", relay.OP_HELLO, self.nonce, len(self.room)) + self.room
        welcome = None
        for _ in range(10):  # the relay process may still be starting
            self.send(relay.CONTROL_CHANNEL, relay.RELAY_ID, body, sender=0)
            welcome = self.receive(relay.OP_WELCOME, timeout=0.5)
            if welcome is not None:
                break
        if welcome is None:
            return False
        assigned, echoed = struct.unpack_from("<HI", welcome[1], 1)
        self.client_id = assigned
        return echoed == self.nonce


def read_relay_log():
    try:
        with open(RELAY_LOG, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError as error:
        return f"<could not read {RELAY_LOG}: {error}>"


def wait_until_ready(process):
    """Waits for the relay's startup banner (printed after bind) instead of a fixed sleep."""
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        if "coopnet relay v" in read_relay_log():
            return True
        time.sleep(0.05)
    return False


def main():
    os.makedirs(os.path.dirname(RELAY_LOG), exist_ok=True)
    with open(RELAY_LOG, "w", encoding="utf-8") as log_handle:
        process = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "tools", "coopnet_relay.py"), "--port", str(PORT)],
            stdout=log_handle, stderr=subprocess.STDOUT,
        )
    checks = []
    try:
        ready = wait_until_ready(process)
        checks.append(("relay started", ready))
        alpha, bravo = Client(0x1111), Client(0x2222)
        checks.append(("alpha welcomed", ready and alpha.hello() and alpha.client_id == 1))
        checks.append(("bravo welcomed", ready and bravo.hello() and bravo.client_id == 2))
        if alpha.client_id is None or bravo.client_id is None:
            raise RuntimeError("relay did not welcome both clients; skipping the remaining checks")

        peers = alpha.receive(relay.OP_PEERS)
        while peers is not None and struct.unpack_from("<H", peers[1], 1)[0] < 2:
            peers = alpha.receive(relay.OP_PEERS)
        listed = []
        if peers is not None:
            count = struct.unpack_from("<H", peers[1], 1)[0]
            listed = [struct.unpack_from("<HI", peers[1], 3 + 6 * i) for i in range(count)]
        checks.append(("peers list", sorted(listed) == [(1, 0x1111), (2, 0x2222)]))

        alpha.send(relay.CONTROL_CHANNEL, relay.RELAY_ID, struct.pack("<BIQ", relay.OP_PING, 7, 123456))
        pong = alpha.receive(relay.OP_PONG)
        checks.append(("pong echoes", pong is not None and struct.unpack_from("<IQ", pong[1], 1) == (7, 123456)))

        alpha.send(16, bravo.client_id, b"hello bravo")
        got = bravo.receive()
        while got is not None and got[0][2] == relay.CONTROL_CHANNEL:
            got = bravo.receive()
        checks.append(("forward to target", got is not None and got[1] == b"hello bravo" and got[0][3] == 1))

        alpha.send(1, relay.BROADCAST_ID, b"snap")
        got = bravo.receive()
        while got is not None and got[0][2] == relay.CONTROL_CHANNEL:
            got = bravo.receive()
        checks.append(("broadcast", got is not None and got[1] == b"snap"))

        alpha.send(16, bravo.client_id, b"spoofed", sender=bravo.client_id)
        got = bravo.receive(timeout=0.4)
        while got is not None and got[0][2] == relay.CONTROL_CHANNEL:
            got = bravo.receive(timeout=0.4)
        checks.append(("spoofed sender dropped", got is None))

        stranger = Client(0x3333)
        stranger.send(relay.CONTROL_CHANNEL, relay.RELAY_ID, bytes([relay.OP_HELLO]), sender=0, version=2)
        reject = stranger.receive(relay.OP_REJECT)
        checks.append(("version mismatch rejected", reject is not None and b"protocol_version" in reject[1]))

        alpha.send(relay.CONTROL_CHANNEL, relay.RELAY_ID, bytes([relay.OP_BYE]))
        peers = bravo.receive(relay.OP_PEERS)
        while peers is not None and struct.unpack_from("<H", peers[1], 1)[0] != 1:
            peers = bravo.receive(relay.OP_PEERS)
        checks.append(("bye removes client", peers is not None))
    except RuntimeError as error:
        checks.append((str(error), False))
    finally:
        process.terminate()
        process.wait(timeout=10)

    for name, passed in checks:
        print(f"  {'ok  ' if passed else 'FAIL'} {name}")
    failed = [name for name, passed in checks if not passed]
    if failed:
        print(f"relay exit code: {process.returncode}; relay log ({RELAY_LOG}):")
        print(read_relay_log().rstrip() or "<empty>")
    print("RELAY PASS" if not failed else f"RELAY FAIL: {failed}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
