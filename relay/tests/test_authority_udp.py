"""Real localhost UDP/CB77 proof with impairments; no game or native mocks.

Both clients learn epoch/revision/member generation from received messages.
The relay and clients run cooperatively on one test thread, using real sockets
and their real packet/reliable codecs. Only latency/loss/duplication is simulated.
"""
import os
import socket
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import relay_v2
from coopnet import authority as a, proto
from coopnet.linksim import LinkSim
from coopnet.reliability import Connection


class UDPClient:
    serial = 0

    def __init__(self, relay, role, room="authority-udp"):
        UDPClient.serial += 1
        self.relay = relay
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.setblocking(False)
        self.sim = LinkSim(5, 15, 12, 10, 80 + UDPClient.serial)
        self.info = {"minor": 1, "role": int(role), "join_flags": 0, "caps": int(proto.Cap.PLAYER),
                     "game_build": 7, "mod_major": 0, "mod_minor": 2, "mod_patch": 0, "mod_hash": 1,
                     "mod_count": 1, "client_nonce": 7000 + UDPClient.serial, "resume_token": 0,
                     "room": room, "name": f"authority-test-{UDPClient.serial}"}
        self.connection = None
        self.peer = None
        self.auth = None
        self.next_handshake = 0
        self.messages = []
        self.unreliable = []
        self.active = True

    def send_raw(self, data, now):
        self.sim.submit(now, data, self.relay.sock.getsockname())

    def pump(self, now):
        while True:
            try:
                data, source = self.socket.recvfrom(2048)
            except BlockingIOError:
                break
            if source != self.relay.sock.getsockname():
                continue
            ptype, token, sequence, ack, bits, body = proto.decode_packet(data)
            if ptype == proto.PacketType.CHALLENGE and self.connection is None:
                cookie = proto.decode_challenge(body)[2]
                self.auth = proto.encode_auth(self.info, cookie, proto.room_key_hash(self.info["room"], "test-only"))
                self.send_raw(self.auth, now)
                self.next_handshake = now + .3
            elif ptype == proto.PacketType.WELCOME:
                welcome = proto.decode_welcome(body)
                if self.connection is None:
                    self.connection = Connection(welcome["token"])
                    self.peer = welcome["peer_id"]
            elif ptype == proto.PacketType.DATA and self.connection and token == self.connection.token:
                for kind, sender, reliable, payload in self.connection.on_packet(now, sequence, ack, bits, body):
                    if kind == proto.MsgType.SCRIPT_MSG:
                        decoded = proto.SCRIPT_MSG.decode(payload)
                        self.messages.append((sender, reliable, decoded["channel"], decoded["text"].split("|")))
            elif ptype == proto.PacketType.REJECT:
                raise AssertionError(proto.decode_reject(body))
        if self.active:
            if not self.connection and now >= self.next_handshake:
                self.send_raw(self.auth or proto.encode_hello(self.info), now)
                self.next_handshake = now + .3
            elif self.connection:
                force = now - (self.connection.last_send or 0) >= .05
                messages, self.unreliable = self.unreliable, []
                for data in self.connection.build_packets(now, messages, force=force):
                    self.send_raw(data, now)
        for data, target in self.sim.pop_due(now):
            self.socket.sendto(data, target)

    def control(self, text):
        body = proto.SCRIPT_MSG.encode({"channel": a.CONTROL, "flags": 0, "text": text})
        queued = self.connection.queue_reliable(proto.MsgType.SCRIPT_MSG, proto.PEER_RELAY, body, time.perf_counter())
        if not queued:
            raise AssertionError("test client reliable window full")

    def pose(self, text):
        body = proto.SCRIPT_MSG.encode({"channel": a.POSE, "flags": 0, "text": text})
        self.unreliable.append((proto.MsgType.SCRIPT_MSG, proto.PEER_RELAY, body))

    def last(self, kind):
        for sender, reliable, channel, fields in reversed(self.messages):
            expected_channel = a.POSE if kind == "POSE" else a.CONTROL
            if sender == proto.PEER_RELAY and channel == expected_channel \
                    and reliable == (expected_channel == a.CONTROL) and fields[:2] == ["C3A1", kind]:
                return fields
        return None

    def disconnect(self):
        self.active = False
        data = proto.encode_disconnect(self.connection.token, proto.DisconnectReason.QUIT)
        for delay in (0, .05, .1):
            self.send_raw(data, time.perf_counter() + delay)


class AuthorityUDPTests(unittest.TestCase):
    def setUp(self):
        self.relay = relay_v2.Relay(relay_v2.parse_args([
            "--host", "127.0.0.1", "--port", "0", "--quiet", "--entity-authority-test",
            "--latency-ms", "5", "--jitter-ms", "15", "--loss-pct", "12", "--dup-pct", "10", "--seed", "43"]))
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            client.socket.close()
        self.relay.sock.close()
        self.relay.log.close()

    def add(self, role):
        client = UDPClient(self.relay, role)
        self.clients.append(client)
        self.until(lambda: client.connection is not None)
        return client

    def step(self):
        now = time.perf_counter()
        for client in self.clients:
            client.pump(now)
        self.relay._drain_socket()
        self.relay.tick(now)
        for data, address in self.relay.sim.pop_due(now):
            self.relay._sendto(data, address)
        time.sleep(.002)

    def until(self, condition, timeout=8):
        deadline = time.perf_counter() + timeout
        while not condition() and time.perf_counter() < deadline:
            self.step()
        self.assertTrue(condition(), "real UDP condition timed out")

    def admit(self, client, nonce):
        client.control(f"C3A1|DISCOVER|{nonce}")
        self.until(lambda: client.last("OFFER") is not None)
        offer = client.last("OFFER")
        self.assertEqual(offer[3], nonce)
        epoch = offer[2]
        client.control(f"C3A1|JOIN|{epoch}|{nonce}")
        self.until(lambda: client.last("STATE") is not None)
        baseline = client.last("STATE")
        client.control(f"C3A1|READY|{epoch}|{nonce}|{baseline[4]}")
        self.until(lambda: client.last("READY_OK") is not None)
        return epoch, int(baseline[6])

    def test_lossy_udp_complete_seat_lifecycle_and_reconnect_rejects_old_approval(self):
        host, joiner = self.add(proto.Role.HOST), self.add(proto.Role.JOINER)
        hn, jn = "1" * 32, "2" * 32
        host.control(f"C3A1|BEGIN|{hn}")
        self.until(lambda: host.last("STATE") is not None)
        epoch, generation = self.admit(joiner, jn)
        self.assertEqual(host.last("STATE")[2], epoch)
        host.control(f"C3A1|SPAWN|{epoch}|{hn}|1|1|0|test.vehicle|10|20|3|0")
        self.until(lambda: joiner.last("STATE")[7] == "1")
        joiner.control(f"C3A1|REQUEST|{epoch}|{jn}|1|1|1|enter")
        self.until(lambda: host.last("INTENT") is not None)
        intent = host.last("INTENT")
        self.assertEqual((int(intent[4]), int(intent[6])), (joiner.peer, generation))
        # An ungranted request is not an occupied seat on either client's baseline.
        self.assertEqual(joiner.last("STATE")[13], "0")
        host.control(f"C3A1|GRANT|{epoch}|{hn}|2|1|1|{joiner.peer}|1|{generation}")
        self.until(lambda: joiner.last("STATE")[13] == str(joiner.peer))

        # Snapshot is host-measured metadata, never a requested pose fabricated as
        # actual engine motion. Repetition here only compensates unreliable loss.
        for sequence in range(1, 12):
            stamp = self.relay.relay_ms(time.perf_counter())
            host.pose(f"C3A1|POSE|{epoch}|{hn}|1|{sequence}|{stamp}|42|20|3|0")
            deadline = time.perf_counter() + .12
            while time.perf_counter() < deadline:
                self.step()
            if joiner.last("POSE"):
                break
        self.assertEqual(joiner.last("POSE")[7], "42")
        old_peer, old_token = joiner.peer, joiner.connection.token
        joiner.disconnect()
        self.until(lambda: old_token not in self.relay.peers_by_token)
        self.until(lambda: host.last("STATE")[13] == "0")
        revision = int(host.last("STATE")[4])

        replacement = self.add(proto.Role.JOINER)
        new_epoch, new_generation = self.admit(replacement, jn)  # same app nonce deliberately
        self.assertEqual((replacement.peer, new_epoch), (old_peer, epoch))
        self.assertNotEqual(replacement.connection.token, old_token)
        self.assertNotEqual(new_generation, generation)
        replacement.control(f"C3A1|REQUEST|{epoch}|{jn}|1|1|{revision}|enter")
        self.until(lambda: host.last("INTENT")[6] == str(new_generation))
        host.control(f"C3A1|GRANT|{epoch}|{hn}|3|1|{revision}|{old_peer}|1|{generation}")
        self.until(lambda: host.last("RESULT") is not None and host.last("RESULT")[5] == "reject")
        self.assertEqual(replacement.last("STATE")[13], "0")
        host.control(f"C3A1|GRANT|{epoch}|{hn}|3|1|{revision}|{old_peer}|1|{new_generation}")
        self.until(lambda: replacement.last("STATE")[13] == str(old_peer))
        host.disconnect()
        self.until(lambda: replacement.last("END") is not None)
        self.assertEqual(replacement.last("END")[4], "host_left")
        self.assertGreater(self.relay.sim.dropped + sum(c.sim.dropped for c in self.clients), 0)
        self.assertGreater(sum(c.connection.stats.reliable_resent for c in self.clients), 0)
        print("authority UDP: real sockets, epoch learned from OFFER, loss/duplicate profile, "
              f"dropped={self.relay.sim.dropped + sum(c.sim.dropped for c in self.clients)}, "
              f"client_retries={sum(c.connection.stats.reliable_resent for c in self.clients)}, "
              "seat/reconnect/stale-grant/host-loss checks passed")


if __name__ == "__main__":
    unittest.main()
