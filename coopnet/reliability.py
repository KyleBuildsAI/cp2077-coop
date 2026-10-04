"""One hop of a v2 session (client <-> relay): packet acks, RTT and channels.

Delivery model (per hop, the relay re-sequences for every receiver):

* Every DATA packet has a 16-bit sequence (0 is never used, so ``ack == 0``
  means "nothing received yet") and acknowledges the newest packet seen from
  the other side plus the 32 before it (``ack_bits``). Acks ride on normal
  traffic; an ack-only packet is sent when nothing else goes out for a while.
* Unreliable messages are sent once. Receivers keep only the newest state
  (snapshot sequence / tick inside the message body).
* Reliable messages carry a 16-bit message sequence, stay queued until a
  packet that contained them is acknowledged, are retransmitted after an
  RTO with exponential backoff, and are delivered exactly once, in order.
  Every retransmission goes out in a new packet with a new sequence, so each
  ack yields an unambiguous RTT sample (no Karn problem).
* A DATA packet holds at most MAX_MESSAGES_PER_PACKET messages, the limit
  decode_messages enforces on the receiving side.
"""
from __future__ import annotations

from collections import OrderedDict

from . import proto

SEQ_SPACE = 65536
ACK_BITS = 32
RELIABLE_WINDOW = 256
SENT_HISTORY_LIMIT = 2048
SENT_EXPIRY_S = 3.0
MIN_RTO_S = 0.05
MAX_RTO_S = 2.0
ACK_DELAY_ALLOWANCE_S = 0.04
MAX_BACKOFF_SHIFT = 3


def seq_diff(newer: int, older: int) -> int:
    """Signed distance newer - older in 16-bit serial arithmetic (RFC 1982)."""
    return ((newer - older + 32768) % SEQ_SPACE) - 32768


def next_packet_seq(seq: int) -> int:
    seq = (seq + 1) % SEQ_SPACE
    return 1 if seq == 0 else seq


class PendingReliable:
    __slots__ = ("rel_seq", "mtype", "peer", "body", "queued_at", "last_sent", "sends")

    def __init__(self, rel_seq: int, mtype: int, peer: int, body: bytes, now: float):
        self.rel_seq = rel_seq
        self.mtype = mtype
        self.peer = peer
        self.body = body
        self.queued_at = now
        self.last_sent = None
        self.sends = 0


class SentPacket:
    __slots__ = ("time", "rel_seqs", "size")

    def __init__(self, time: float, rel_seqs: list, size: int):
        self.time = time
        self.rel_seqs = rel_seqs
        self.size = size


class LinkStats:
    def __init__(self):
        self.packets_sent = 0
        self.packets_received = 0
        self.packets_acked = 0
        self.packets_lost = 0
        self.duplicates = 0
        self.too_old = 0
        self.bytes_sent = 0
        self.bytes_received = 0
        self.reliable_sent = 0
        self.reliable_resent = 0
        self.reliable_delivered = 0
        self.reliable_duplicates = 0
        self.reliable_out_of_window = 0
        self.recv_span = 0

    def as_dict(self) -> dict:
        return dict(self.__dict__)


class Connection:
    def __init__(self, token: int = 0, max_packet: int = proto.MAX_PACKET):
        self.token = token
        self.max_packet = max_packet
        self.next_seq = 1
        self.sent: dict[int, SentPacket] = {}
        self.remote_seq = 0
        self.recv_bits = 0
        self.ack_pending = False
        self.rel_next = 0
        self.rel_pending: OrderedDict[int, PendingReliable] = OrderedDict()
        self.rel_expected = 0
        self.rel_buffer: dict[int, tuple] = {}
        self.srtt = None
        self.rttvar = None
        self.rto = 0.25
        self.last_send = None
        self.last_recv = None
        self.stats = LinkStats()

    # ------------------------------------------------------------------ send

    def queue_reliable(self, mtype: int, peer: int, body: bytes, now: float) -> bool:
        """Queues a reliable message. False when the send window is full."""
        if len(body) > proto.MAX_MESSAGE_BODY:
            raise proto.ProtocolError("reliable message too large")
        if self.rel_pending:
            oldest = next(iter(self.rel_pending))
            if seq_diff(self.rel_next, oldest) >= RELIABLE_WINDOW:
                return False
        rel_seq = self.rel_next
        self.rel_next = (self.rel_next + 1) % SEQ_SPACE
        self.rel_pending[rel_seq] = PendingReliable(rel_seq, mtype, peer, body, now)
        return True

    def _resend_interval(self, pending: PendingReliable) -> float:
        return self.rto * (1 << min(max(pending.sends - 1, 0), MAX_BACKOFF_SHIFT))

    def _due_reliable(self, now: float) -> list:
        return [pending for pending in self.rel_pending.values()
                if pending.last_sent is None or now - pending.last_sent >= self._resend_interval(pending)]

    def reliable_due(self, now: float) -> bool:
        return any(pending.last_sent is None or now - pending.last_sent >= self._resend_interval(pending)
                   for pending in self.rel_pending.values())

    def build_packets(self, now: float, unreliable=(), force: bool = False) -> list:
        """Packs due reliable + the given unreliable (mtype, peer, body) into DATA packets.

        ``force`` emits an (ack-only if empty) packet even with nothing to send.
        """
        items = [(True, pending) for pending in self._due_reliable(now)]
        items += [(False, message) for message in unreliable]
        packets = []
        parts = []
        rel_items = []
        size = proto.PACKET_HEADER.size
        for reliable, item in items:
            if reliable:
                encoded = proto.encode_message(item.mtype, item.peer, item.body, item.rel_seq)
            else:
                mtype, peer, body = item
                encoded = proto.encode_message(mtype, peer, body)
            if parts and (size + len(encoded) > self.max_packet or len(parts) >= proto.MAX_MESSAGES_PER_PACKET):
                packets.append(self._finish_packet(now, parts, rel_items))
                parts, rel_items, size = [], [], proto.PACKET_HEADER.size
            parts.append(encoded)
            size += len(encoded)
            if reliable:
                rel_items.append(item)
        if parts or (force and not packets):
            packets.append(self._finish_packet(now, parts, rel_items))
        return packets

    def _finish_packet(self, now: float, parts: list, rel_items: list) -> bytes:
        seq = self.next_seq
        self.next_seq = next_packet_seq(seq)
        packet = proto.encode_packet(proto.PacketType.DATA, self.token, seq, self.remote_seq,
                                     self.recv_bits, b"".join(parts))
        for pending in rel_items:
            if pending.sends == 0:
                self.stats.reliable_sent += 1
            else:
                self.stats.reliable_resent += 1
            pending.sends += 1
            pending.last_sent = now
        self.sent[seq] = SentPacket(now, [pending.rel_seq for pending in rel_items], len(packet))
        self.ack_pending = False
        self.last_send = now
        self.stats.packets_sent += 1
        self.stats.bytes_sent += len(packet)
        self._expire_sent(now)
        return packet

    def _expire_sent(self, now: float) -> None:
        """Forgets unacknowledged packets that are too old to be acked any more (oldest first)."""
        while self.sent:
            oldest = next(iter(self.sent))
            if now - self.sent[oldest].time <= SENT_EXPIRY_S and len(self.sent) <= SENT_HISTORY_LIMIT:
                break
            del self.sent[oldest]
            self.stats.packets_lost += 1

    # --------------------------------------------------------------- receive

    def on_packet(self, now: float, seq: int, ack: int, ack_bits: int, body: bytes, size: int = 0):
        """Processes one DATA packet. Returns [(mtype, peer, reliable, body), ...] in delivery order.

        Raises ProtocolError before changing any state if the framing is bad.
        """
        messages = proto.decode_messages(body)
        if seq == 0:
            raise proto.ProtocolError("packet sequence 0 is reserved")
        self.ack_pending = True
        if not self._record_received(seq):
            return []
        self.stats.packets_received += 1
        self.stats.bytes_received += size or (len(body) + proto.PACKET_HEADER.size)
        self.last_recv = now
        self._process_acks(now, ack, ack_bits)
        delivered = []
        for mtype, peer, rel_seq, payload in messages:
            if rel_seq is None:
                delivered.append((mtype, peer, False, payload))
            else:
                self._receive_reliable(rel_seq, (mtype, peer, payload), delivered)
        return delivered

    def _record_received(self, seq: int) -> bool:
        """Updates ack state. False for duplicates and packets too old to judge."""
        if self.remote_seq == 0:
            self.remote_seq = seq
            self.recv_bits = 0
            self.stats.recv_span += 1
            return True
        distance = seq_diff(seq, self.remote_seq)
        if distance > 0:
            if distance <= ACK_BITS:
                self.recv_bits = ((self.recv_bits << distance) | (1 << (distance - 1))) & 0xFFFFFFFF
            else:
                self.recv_bits = 0
            self.remote_seq = seq
            self.stats.recv_span += distance
            return True
        if distance == 0:
            self.stats.duplicates += 1
            return False
        bit = -distance - 1
        if bit >= ACK_BITS:
            self.stats.too_old += 1
            return False
        if self.recv_bits & (1 << bit):
            self.stats.duplicates += 1
            return False
        self.recv_bits |= 1 << bit
        return True

    def _process_acks(self, now: float, ack: int, ack_bits: int) -> None:
        if ack == 0:
            return
        if ack in self.sent:
            self._on_acked(now, ack)
        for bit in range(ACK_BITS):
            if ack_bits & (1 << bit):
                seq = (ack - 1 - bit) % SEQ_SPACE
                if seq in self.sent:
                    self._on_acked(now, seq)
        for seq in [s for s in self.sent if seq_diff(ack, s) > ACK_BITS]:
            del self.sent[seq]
            self.stats.packets_lost += 1

    def _on_acked(self, now: float, seq: int) -> None:
        info = self.sent.pop(seq)
        self.stats.packets_acked += 1
        self._update_rtt(now - info.time)
        for rel_seq in info.rel_seqs:
            self.rel_pending.pop(rel_seq, None)

    def _update_rtt(self, sample: float) -> None:
        if self.srtt is None:
            self.srtt = sample
            self.rttvar = sample / 2.0
        else:
            self.rttvar = 0.75 * self.rttvar + 0.25 * abs(self.srtt - sample)
            self.srtt = 0.875 * self.srtt + 0.125 * sample
        rto = self.srtt + max(4.0 * self.rttvar, 0.01) + ACK_DELAY_ALLOWANCE_S
        self.rto = min(MAX_RTO_S, max(MIN_RTO_S, rto))

    def _receive_reliable(self, rel_seq: int, item: tuple, delivered: list) -> None:
        distance = seq_diff(rel_seq, self.rel_expected)
        if distance < 0 or rel_seq in self.rel_buffer:
            self.stats.reliable_duplicates += 1
            return
        if distance >= RELIABLE_WINDOW:
            self.stats.reliable_out_of_window += 1
            return
        self.rel_buffer[rel_seq] = item
        while self.rel_expected in self.rel_buffer:
            mtype, peer, payload = self.rel_buffer.pop(self.rel_expected)
            delivered.append((mtype, peer, True, payload))
            self.stats.reliable_delivered += 1
            self.rel_expected = (self.rel_expected + 1) % SEQ_SPACE

    # ----------------------------------------------------------------- stats

    def loss_in(self) -> float:
        span = self.stats.recv_span
        return 0.0 if span == 0 else max(0.0, 1.0 - self.stats.packets_received / span)

    def loss_out(self) -> float:
        judged = self.stats.packets_acked + self.stats.packets_lost
        return 0.0 if judged == 0 else self.stats.packets_lost / judged

    def rtt_ms(self) -> float:
        return 0.0 if self.srtt is None else self.srtt * 1000.0
