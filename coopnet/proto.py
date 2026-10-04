"""CP2077 Coop network protocol v2: wire format, message codecs and validation.

This module is the single source of truth for the v2 binary layout. The C
header ``include/coop_proto_v2.h`` mirrors it for the RED4ext plugin and
``tools/check_c_header.py`` verifies that both agree byte for byte.

Conventions
-----------
* All integers are little-endian, all floats IEEE-754 binary32.
* A datagram is at most ``MAX_PACKET`` (1200) bytes so it never fragments on a
  1280-byte IPv6 path, PPPoE or a VPN tunnel.
* The first two bytes of every v2 datagram are ``MAGIC`` (0xCB 0x77). The
  first byte is not ASCII, so a v2 datagram can never be mistaken for Jakub's
  text protocol (``CP1,...`` / ``RP1,...`` / ``WELCOME,...``) and one UDP port
  serves both side by side.
* Bytes 0..3 (magic, major, packet type) are frozen for every future major
  version, so a v2 relay can always answer a v3 HELLO with a v2 REJECT that
  names the supported range.

Packet = 20-byte header + body. DATA bodies hold a list of messages, each with
a 4-byte header (6 bytes when reliable). See ``encode_message``.
"""
from __future__ import annotations

import enum
import hashlib
import hmac
import math
import struct

MAGIC = b"\xcb\x77"
PROTO_MAJOR = 2
PROTO_MINOR = 0
MIN_SUPPORTED_MINOR = 0
MAX_PACKET = 1200

# magic[2] major:u8 ptype:u8 token:u64 seq:u16 ack:u16 ack_bits:u32
PACKET_HEADER = struct.Struct("<2sBBQHHI")
# type:u8 (bit7 = reliable) peer:u8 length:u16
MESSAGE_HEADER = struct.Struct("<BBH")
RELIABLE_SEQ = struct.Struct("<H")
RELIABLE_BIT = 0x80
MAX_MESSAGE_BODY = MAX_PACKET - PACKET_HEADER.size - MESSAGE_HEADER.size - RELIABLE_SEQ.size
MAX_MESSAGES_PER_PACKET = 96

# HELLO must be at least this big (padding) so the relay's CHALLENGE (40 bytes)
# can never be used to amplify a spoofed-source flood.
HELLO_MIN_PACKET = 240
COOKIE_SIZE = 16
KEY_HASH_SIZE = 16
COOKIE_MAX_AGE_S = 10

PEER_RELAY = 0
PEER_BROADCAST = 0xFF

WORLD_XY_LIMIT_M = 20000.0
WORLD_Z_LIMIT_M = 5000.0
MAX_NAME_BYTES = 24
MAX_ROOM_BYTES = 32
MAX_CHAT_BYTES = 200
MAX_MODLIST_BYTES = 1000
MAX_ENTITY_RECORDS = 255
ROOM_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")


class ProtocolError(ValueError):
    """Raised for any malformed, truncated, out-of-range or disallowed input."""


class VersionMismatch(ProtocolError):
    """The datagram is v2-framed but carries a different major version."""

    def __init__(self, major: int, ptype: int):
        super().__init__(f"unsupported protocol major {major}")
        self.major = major
        self.ptype = ptype


class PacketType(enum.IntEnum):
    HELLO = 1        # client -> relay, unauthenticated, padded to HELLO_MIN_PACKET
    CHALLENGE = 2    # relay -> client, stateless cookie
    AUTH = 3         # client -> relay, join info + cookie + room key hash
    WELCOME = 4      # relay -> client, session token + peer id + negotiated minor
    REJECT = 5       # relay -> client, reason + supported minor range
    DATA = 6         # both ways, authenticated by session token, carries messages
    DISCONNECT = 7   # both ways, reason byte


class Role(enum.IntEnum):
    ANY = 0
    HOST = 1
    JOINER = 2
    SPECTATOR = 3


class RejectReason(enum.IntEnum):
    VERSION = 1
    BAD_COOKIE = 2
    ROOM_FULL = 3
    BAD_KEY = 4
    ROLE_TAKEN = 5
    MOD_MISMATCH = 6
    RATE_LIMITED = 7
    SERVER_FULL = 8
    MALFORMED = 9
    GAME_BUILD = 10


class DisconnectReason(enum.IntEnum):
    QUIT = 1
    TIMEOUT = 2
    KICKED = 3
    RATE_LIMIT = 4
    PROTOCOL_ERROR = 5
    SERVER_SHUTDOWN = 6
    SLOW_CONSUMER = 7


class JoinFlag(enum.IntFlag):
    STRICT_MODS = 0x01     # room creator: joiners must match mod list hash and game build
    LEGACY_BRIDGE = 0x02   # room creator: bridge this room with v1 (CP1/RP1) clients


class Cap(enum.IntFlag):
    PLAYER = 0x001
    ENTITIES = 0x002
    VEHICLES = 0x004
    COMBAT = 0x008
    WORLD_STATE = 0x010
    CHAT = 0x020
    TELEPORT = 0x040
    LEGACY_BRIDGE = 0x080
    QUEST_FACTS = 0x100


ALL_CAPS = int(Cap.PLAYER | Cap.ENTITIES | Cap.VEHICLES | Cap.COMBAT | Cap.WORLD_STATE
               | Cap.CHAT | Cap.TELEPORT | Cap.LEGACY_BRIDGE | Cap.QUEST_FACTS)


class MsgType(enum.IntEnum):
    # relay control
    TIME_REQ = 0x01
    TIME_RESP = 0x02
    PEER_JOINED = 0x03
    PEER_LEFT = 0x04
    LINK_STATS = 0x05
    # unreliable state
    PLAYER_SNAPSHOT = 0x10
    ENTITY_SNAPSHOT = 0x11
    SNAPSHOT_ACK = 0x12
    FIRE_FX = 0x13
    # reliable ordered events
    EQUIP = 0x20
    VEHICLE_ENTER = 0x21
    VEHICLE_EXIT = 0x22
    HIT = 0x23
    DEATH = 0x24
    TIME_WEATHER = 0x25
    CHAT = 0x26
    TELEPORT_REQ = 0x27
    TELEPORT_RESP = 0x28
    WORLD_FACT = 0x29
    MOD_LIST = 0x2A
    SESSION_CONFIG = 0x2B


class MoveState(enum.IntEnum):
    IDLE = 0
    WALK = 1
    RUN = 2
    SPRINT = 3
    CROUCH_IDLE = 4
    CROUCH_MOVE = 5
    JUMP = 6
    FALL = 7
    SLIDE = 8
    SWIM = 9
    VEHICLE = 10
    DEAD = 11
    LADDER = 12
    DODGE = 13


MOVE_STATE_COUNT = len(MoveState)


class PlayerFlag(enum.IntFlag):
    CROUCH = 1 << 0
    WEAPON_DRAWN = 1 << 1
    AIMING = 1 << 2
    FIRING = 1 << 3
    IN_VEHICLE = 1 << 4
    DRIVING = 1 << 5       # a VehicleBlock follows the base snapshot
    SPRINTING = 1 << 6
    RELOADING = 1 << 7
    # bits 8..11: weapon class (0 = none/fists, 1 pistol, 2 revolver, 3 smg, 4 rifle,
    #             5 shotgun, 6 sniper, 7 lmg, 8 melee blade, 9 melee blunt, 10 throwable)
    DEAD = 1 << 12
    IN_COMBAT = 1 << 13
    LEGACY = 1 << 14       # bridged from a v1 client: no velocity, relay timestamp
    TELEPORTED = 1 << 15   # discontinuity: receivers must not interpolate across it


WEAPON_CLASS_SHIFT = 8
WEAPON_CLASS_MASK = 0xF


class EntityKind(enum.IntEnum):
    CROWD_NPC = 1
    COMBAT_NPC = 2
    QUEST_NPC = 3
    VEHICLE = 4
    DEVICE = 5


class EntityFlag(enum.IntFlag):
    DEAD = 0x01
    COMBAT = 0x02
    WEAPON_DRAWN = 0x04
    CROUCHED = 0x08
    RAGDOLL = 0x10
    HOSTILE = 0x20
    LIGHTS = 0x40
    SIREN = 0x80


class SpawnFlag(enum.IntFlag):
    CROWD = 0x01
    TRAFFIC = 0x02
    QUEST = 0x04
    PERSISTENT = 0x08


# Entity record field mask (u8) and extension mask (u8, present when M_EXT is set).
M_SPAWN = 0x01
M_POS = 0x02
M_POS_DELTA = 0x04
M_YAW = 0x08
M_QUAT = 0x10
M_VEL = 0x20
M_STATE = 0x40
M_EXT = 0x80
X_REMOVE = 0x01
X_TARGET = 0x02
X_WEAPON = 0x04
X_KNOWN = X_REMOVE | X_TARGET | X_WEAPON


# ---------------------------------------------------------------------------
# quantization helpers
# ---------------------------------------------------------------------------

def clamp(value, low, high):
    return low if value < low else high if value > high else value


def meters_to_mm(value: float) -> int:
    return int(clamp(round(value * 1000.0), -2**31, 2**31 - 1))


def mm_to_meters(value: int) -> float:
    return value / 1000.0


def velocity_to_cms(value: float) -> int:
    return int(clamp(round(value * 100.0), -32768, 32767))


def cms_to_velocity(value: int) -> float:
    return value / 100.0


def yaw_to_u16(degrees: float) -> int:
    return int(round((degrees % 360.0) / 360.0 * 65536.0)) & 0xFFFF


def u16_to_yaw(value: int) -> float:
    return value * 360.0 / 65536.0


def pitch_to_i16(degrees: float) -> int:
    return int(clamp(round(degrees * 100.0), -9000, 9000))


def i16_to_pitch(value: int) -> float:
    return value / 100.0


_QUAT_SCALE = 1.0 / math.sqrt(2.0)


def pack_quat(x: float, y: float, z: float, w: float) -> int:
    """Smallest-three quaternion: 2-bit index of the dropped component + 3 x 10 bits."""
    components = [x, y, z, w]
    length = math.sqrt(sum(c * c for c in components))
    if not math.isfinite(length) or length < 1e-6:
        components, length = [0.0, 0.0, 0.0, 1.0], 1.0
    components = [c / length for c in components]
    largest = max(range(4), key=lambda index: abs(components[index]))
    if components[largest] < 0.0:
        components = [-c for c in components]
    packed = largest
    for index in range(4):
        if index == largest:
            continue
        normalized = clamp(components[index] / _QUAT_SCALE * 0.5 + 0.5, 0.0, 1.0)
        packed = (packed << 10) | int(round(normalized * 1023.0))
    return packed


def unpack_quat(packed: int) -> tuple[float, float, float, float]:
    largest = (packed >> 30) & 0x3
    small = [((packed >> shift) & 0x3FF) / 1023.0 for shift in (20, 10, 0)]
    small = [(value - 0.5) * 2.0 * _QUAT_SCALE for value in small]
    missing = math.sqrt(max(0.0, 1.0 - sum(v * v for v in small)))
    result = []
    for index in range(4):
        result.append(missing if index == largest else small.pop(0))
    return tuple(result)


# ---------------------------------------------------------------------------
# identity hashes
# ---------------------------------------------------------------------------

def room_key_hash(room: str, password: str) -> bytes:
    """What AUTH carries instead of the password (16 bytes)."""
    material = b"cp2077coop-v2|" + room.encode("utf-8") + b"|" + password.encode("utf-8")
    return hashlib.sha256(material).digest()[:KEY_HASH_SIZE]


def mod_list_hash(entries) -> int:
    """u64 over the sorted gameplay-affecting mods, entries = [(name, version), ...]."""
    lines = sorted(f"{name.strip().lower()}@{version.strip()}" for name, version in entries)
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "little")


def game_build_id(version_text: str) -> int:
    """FNV-1a 32 of the game's version string, e.g. '2.31a'."""
    value = 0x811C9DC5
    for byte in version_text.encode("utf-8"):
        value = ((value ^ byte) * 0x01000193) & 0xFFFFFFFF
    return value


def make_cookie(secret: bytes, address, client_nonce: int, issued_s: int) -> bytes:
    issued = struct.pack("<I", issued_s & 0xFFFFFFFF)
    material = f"{address[0]}|{address[1]}|".encode() + issued + struct.pack("<Q", client_nonce)
    return issued + hmac.new(secret, material, hashlib.sha256).digest()[:COOKIE_SIZE - 4]


def check_cookie(secret: bytes, address, client_nonce: int, cookie: bytes, now_s: int) -> bool:
    if len(cookie) != COOKIE_SIZE:
        return False
    issued_s = struct.unpack_from("<I", cookie)[0]
    age = (now_s - issued_s) & 0xFFFFFFFF
    if age > COOKIE_MAX_AGE_S:
        return False
    expected = make_cookie(secret, address, client_nonce, issued_s)
    return hmac.compare_digest(expected, cookie)


# ---------------------------------------------------------------------------
# text validation
# ---------------------------------------------------------------------------

def decode_text(raw: bytes, limit: int, what: str) -> str:
    if len(raw) > limit:
        raise ProtocolError(f"{what}: {len(raw)} bytes exceeds {limit}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProtocolError(f"{what}: invalid utf-8") from error
    for char in text:
        code = ord(char)
        if code < 0x20 or 0x7F <= code < 0xA0 or code in (0x2028, 0x2029):
            raise ProtocolError(f"{what}: control character U+{code:04X}")
    return text


def validate_room(room: str) -> str:
    if not 1 <= len(room) <= MAX_ROOM_BYTES or any(char not in ROOM_CHARS for char in room):
        raise ProtocolError("room: 1-32 chars of A-Z a-z 0-9 _ -")
    return room


def check_world_coordinates(x: float, y: float, z: float, what: str) -> None:
    for value in (x, y, z):
        if not math.isfinite(value):
            raise ProtocolError(f"{what}: non-finite coordinate")
    if abs(x) > WORLD_XY_LIMIT_M or abs(y) > WORLD_XY_LIMIT_M or abs(z) > WORLD_Z_LIMIT_M:
        raise ProtocolError(f"{what}: coordinate out of world bounds")


# ---------------------------------------------------------------------------
# packet header
# ---------------------------------------------------------------------------

def is_v2(data: bytes) -> bool:
    return len(data) >= 2 and data[:2] == MAGIC


def encode_packet(ptype: int, token: int = 0, seq: int = 0, ack: int = 0,
                  ack_bits: int = 0, body: bytes = b"") -> bytes:
    packet = PACKET_HEADER.pack(MAGIC, PROTO_MAJOR, ptype, token, seq, ack, ack_bits) + body
    if len(packet) > MAX_PACKET:
        raise ProtocolError(f"packet of {len(packet)} bytes exceeds {MAX_PACKET}")
    return packet


def decode_packet(data: bytes):
    """Returns (ptype, token, seq, ack, ack_bits, body)."""
    if len(data) > MAX_PACKET:
        raise ProtocolError("datagram larger than MAX_PACKET")
    if len(data) < 4 or data[:2] != MAGIC:
        raise ProtocolError("not a v2 datagram")
    if data[2] != PROTO_MAJOR:
        raise VersionMismatch(data[2], data[3])
    if len(data) < PACKET_HEADER.size:
        raise ProtocolError("truncated packet header")
    _, _, ptype, token, seq, ack, ack_bits = PACKET_HEADER.unpack_from(data)
    if ptype not in PacketType._value2member_map_:
        raise ProtocolError(f"unknown packet type {ptype}")
    return ptype, token, seq, ack, ack_bits, data[PACKET_HEADER.size:]


# ---------------------------------------------------------------------------
# handshake bodies
# ---------------------------------------------------------------------------

# minor role join_flags caps game_build mod_major mod_minor mod_patch
# mod_hash mod_count client_nonce resume_token  (44 bytes) + room + name
JOIN_FIXED = struct.Struct("<BBHIIHHHQHQQ")
JOIN_FIELDS = ("minor", "role", "join_flags", "caps", "game_build", "mod_major", "mod_minor",
               "mod_patch", "mod_hash", "mod_count", "client_nonce", "resume_token")
CHALLENGE_BODY = struct.Struct("<BBH16s")
# negotiated_minor peer_id role room_flags token relay_time_ms player_hz entity_hz
# max_packet room_caps  (24 bytes)
WELCOME_BODY = struct.Struct("<BBBBQIBBHI")
WELCOME_FIELDS = ("minor", "peer_id", "role", "room_flags", "token", "relay_time_ms",
                  "player_hz", "entity_hz", "max_packet", "room_caps")
REJECT_FIXED = struct.Struct("<BBBB")


def encode_join(info: dict) -> bytes:
    room = validate_room(info["room"]).encode("ascii")
    name = info["name"].encode("utf-8")
    decode_text(name, MAX_NAME_BYTES, "name")
    fixed = JOIN_FIXED.pack(*(info[field] for field in JOIN_FIELDS))
    return fixed + bytes([len(room)]) + room + bytes([len(name)]) + name


def decode_join(body: bytes, offset: int = 0):
    """Returns (info dict, offset after the join info)."""
    if len(body) - offset < JOIN_FIXED.size + 2:
        raise ProtocolError("join info truncated")
    info = dict(zip(JOIN_FIELDS, JOIN_FIXED.unpack_from(body, offset)))
    offset += JOIN_FIXED.size
    for field, limit in (("room", MAX_ROOM_BYTES), ("name", MAX_NAME_BYTES)):
        if offset >= len(body):
            raise ProtocolError(f"join info: missing {field}")
        length = body[offset]
        offset += 1
        raw = body[offset:offset + length]
        if len(raw) != length:
            raise ProtocolError(f"join info: truncated {field}")
        offset += length
        info[field] = decode_text(raw, limit, field)
    validate_room(info["room"])
    if info["role"] not in Role._value2member_map_:
        raise ProtocolError("join info: bad role")
    return info, offset


def encode_hello(info: dict) -> bytes:
    body = encode_join(info)
    padding = max(0, HELLO_MIN_PACKET - PACKET_HEADER.size - len(body))
    return encode_packet(PacketType.HELLO, body=body + bytes(padding))


def decode_hello(body: bytes) -> dict:
    if len(body) + PACKET_HEADER.size < HELLO_MIN_PACKET:
        raise ProtocolError("HELLO below anti-amplification minimum size")
    info, offset = decode_join(body)
    if any(body[offset:]):
        raise ProtocolError("HELLO padding must be zero")
    return info


def encode_auth(info: dict, cookie: bytes, key_hash: bytes) -> bytes:
    if len(cookie) != COOKIE_SIZE or len(key_hash) != KEY_HASH_SIZE:
        raise ProtocolError("AUTH: bad cookie or key hash size")
    return encode_packet(PacketType.AUTH, body=encode_join(info) + cookie + key_hash)


def decode_auth(body: bytes):
    info, offset = decode_join(body)
    if len(body) - offset != COOKIE_SIZE + KEY_HASH_SIZE:
        raise ProtocolError("AUTH: bad trailer size")
    cookie = body[offset:offset + COOKIE_SIZE]
    key_hash = body[offset + COOKIE_SIZE:]
    return info, cookie, key_hash


def encode_challenge(cookie: bytes) -> bytes:
    body = CHALLENGE_BODY.pack(MIN_SUPPORTED_MINOR, PROTO_MINOR, 0, cookie)
    return encode_packet(PacketType.CHALLENGE, body=body)


def decode_challenge(body: bytes):
    if len(body) != CHALLENGE_BODY.size:
        raise ProtocolError("CHALLENGE: bad size")
    minor_min, minor_max, _, cookie = CHALLENGE_BODY.unpack(body)
    return minor_min, minor_max, cookie


def encode_welcome(fields: dict) -> bytes:
    body = WELCOME_BODY.pack(*(fields[name] for name in WELCOME_FIELDS))
    return encode_packet(PacketType.WELCOME, token=fields["token"], body=body)


def decode_welcome(body: bytes) -> dict:
    if len(body) != WELCOME_BODY.size:
        raise ProtocolError("WELCOME: bad size")
    return dict(zip(WELCOME_FIELDS, WELCOME_BODY.unpack(body)))


def encode_reject(reason: int, text: str = "") -> bytes:
    raw = text.encode("utf-8")[:64]
    body = REJECT_FIXED.pack(reason, MIN_SUPPORTED_MINOR, PROTO_MINOR, len(raw)) + raw
    return encode_packet(PacketType.REJECT, body=body)


def decode_reject(body: bytes):
    if len(body) < REJECT_FIXED.size:
        raise ProtocolError("REJECT: truncated")
    reason, minor_min, minor_max, length = REJECT_FIXED.unpack_from(body)
    text = body[REJECT_FIXED.size:REJECT_FIXED.size + length].decode("utf-8", errors="replace")
    return reason, minor_min, minor_max, text


def encode_disconnect(token: int, reason: int) -> bytes:
    return encode_packet(PacketType.DISCONNECT, token=token, body=bytes([reason]))


# ---------------------------------------------------------------------------
# message framing inside DATA packets
# ---------------------------------------------------------------------------

def message_size(body_len: int, reliable: bool) -> int:
    return MESSAGE_HEADER.size + (RELIABLE_SEQ.size if reliable else 0) + body_len


def encode_message(mtype: int, peer: int, body: bytes, rel_seq: int | None = None) -> bytes:
    if len(body) > MAX_MESSAGE_BODY:
        raise ProtocolError(f"message body of {len(body)} bytes exceeds {MAX_MESSAGE_BODY}")
    type_byte = mtype | (RELIABLE_BIT if rel_seq is not None else 0)
    head = MESSAGE_HEADER.pack(type_byte, peer, len(body))
    if rel_seq is not None:
        head += RELIABLE_SEQ.pack(rel_seq & 0xFFFF)
    return head + body


def decode_messages(payload: bytes):
    """Returns [(mtype, peer, rel_seq or None, body), ...]; raises on any framing error."""
    messages = []
    offset = 0
    end = len(payload)
    while offset < end:
        if end - offset < MESSAGE_HEADER.size:
            raise ProtocolError("truncated message header")
        type_byte, peer, length = MESSAGE_HEADER.unpack_from(payload, offset)
        offset += MESSAGE_HEADER.size
        rel_seq = None
        if type_byte & RELIABLE_BIT:
            if end - offset < RELIABLE_SEQ.size:
                raise ProtocolError("truncated reliable sequence")
            rel_seq = RELIABLE_SEQ.unpack_from(payload, offset)[0]
            offset += RELIABLE_SEQ.size
        if length > end - offset:
            raise ProtocolError("message length beyond packet end")
        messages.append((type_byte & 0x7F, peer, rel_seq, payload[offset:offset + length]))
        offset += length
        if len(messages) > MAX_MESSAGES_PER_PACKET:
            raise ProtocolError("too many messages in one packet")
    return messages


# ---------------------------------------------------------------------------
# message body codecs
# ---------------------------------------------------------------------------

class FixedCodec:
    """A fixed little-endian struct, optionally followed by one u8-length text field."""

    def __init__(self, name: str, fmt: str, fields: str, text: str | None = None,
                 text_limit: int = 0, coords: tuple = (), checks=None):
        self.name = name
        self.struct = struct.Struct("<" + fmt)
        self.fields = tuple(fields.split())
        if len(self.fields) != len(fmt):
            raise ValueError(f"{name}: {len(fmt)} format codes but {len(self.fields)} names")
        self.float_fields = tuple(f for f, code in zip(self.fields, fmt) if code in "fd")
        self.text = text
        self.text_limit = text_limit
        self.coords = coords
        self.checks = checks

    @property
    def fixed_size(self) -> int:
        return self.struct.size

    def encode(self, values: dict) -> bytes:
        try:
            data = self.struct.pack(*(values[field] for field in self.fields))
        except (struct.error, KeyError) as error:
            raise ProtocolError(f"{self.name}: cannot encode: {error}") from error
        if self.text is not None:
            raw = values[self.text].encode("utf-8")
            data += bytes([min(len(raw), 255)]) + raw
        self.decode(data)  # the encoder must never emit what a receiver would reject
        return data

    def decode(self, body: bytes) -> dict:
        if len(body) < self.struct.size:
            raise ProtocolError(f"{self.name}: truncated")
        values = dict(zip(self.fields, self.struct.unpack_from(body)))
        offset = self.struct.size
        if self.text is not None:
            if offset >= len(body):
                raise ProtocolError(f"{self.name}: missing text length")
            length = body[offset]
            offset += 1
            raw = body[offset:offset + length]
            if len(raw) != length:
                raise ProtocolError(f"{self.name}: truncated text")
            offset += length
            values[self.text] = decode_text(raw, self.text_limit, self.name)
        if offset != len(body):
            raise ProtocolError(f"{self.name}: {len(body) - offset} trailing bytes")
        for field in self.float_fields:
            if not math.isfinite(values[field]):
                raise ProtocolError(f"{self.name}: {field} is not finite")
        for group in self.coords:
            check_world_coordinates(*(values[field] for field in group), what=self.name)
        if self.checks is not None:
            self.checks(values)
        return values


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ProtocolError(message)


def _check_player(values: dict) -> None:
    _require(values["move_state"] < MOVE_STATE_COUNT, "PLAYER_SNAPSHOT: bad move_state")
    _require(-9000 <= values["pitch"] <= 9000, "PLAYER_SNAPSHOT: pitch out of range")


def _check_vehicle_block(values: dict) -> None:
    _require(values["vehicle_net"] != 0, "VehicleBlock: vehicle_net must be non-zero")
    _require(-100 <= values["steer"] <= 100, "VehicleBlock: steer out of range")
    _require(values["throttle"] <= 100 and values["brake"] <= 100, "VehicleBlock: pedal out of range")


def _check_hit(values: dict) -> None:
    _require(values["target_kind"] in (0, 1), "HIT: target_kind must be 0 (entity) or 1 (player)")
    _require(0.0 <= values["damage"] <= 1.0e6, "HIT: damage out of range")
    _require(values["hit_zone"] < 16, "HIT: bad hit_zone")
    _require(values["target_net"] != 0, "HIT: target_net must be non-zero")


def _check_death(values: dict) -> None:
    _require(values["target_kind"] in (0, 1) and values["killer_kind"] in (0, 1, 2),
             "DEATH: bad kind")


def _check_time_weather(values: dict) -> None:
    _require(values["weather_id"] < 32, "TIME_WEATHER: bad weather id")
    _require(values["time_scale_x100"] <= 10000, "TIME_WEATHER: time scale out of range")


def _check_teleport_req(values: dict) -> None:
    _require(values["mode"] in (0, 1), "TELEPORT_REQ: bad mode")


def _check_peer_joined(values: dict) -> None:
    _require(values["role"] in Role._value2member_map_, "PEER_JOINED: bad role")


def _check_equip(values: dict) -> None:
    _require(values["slot"] < 8, "EQUIP: bad slot")
    _require(values["weapon_class"] <= WEAPON_CLASS_MASK, "EQUIP: bad weapon class")


def _check_session_config(values: dict) -> None:
    _require(10 <= values["npc_radius_m"] <= 500, "SESSION_CONFIG: npc radius out of range")
    _require(10 <= values["vehicle_radius_m"] <= 1000, "SESSION_CONFIG: vehicle radius out of range")
    _require(1 <= values["entity_hz"] <= 30, "SESSION_CONFIG: entity rate out of range")


def _check_mod_list(values: dict) -> None:
    _require(values["chunk"] < values["chunks"] <= 16, "MOD_LIST: bad chunk index")


TIME_REQ = FixedCodec("TIME_REQ", "I", "t0")
TIME_RESP = FixedCodec("TIME_RESP", "III", "t0 t1 t2")
PEER_JOINED = FixedCodec(
    "PEER_JOINED", "BBBBIQHHHH",
    "peer_id role minor peer_flags caps mod_hash mod_count mod_major mod_minor mod_patch",
    text="name", text_limit=MAX_NAME_BYTES, checks=_check_peer_joined)
PEER_LEFT = FixedCodec("PEER_LEFT", "BB", "peer_id reason")
LINK_STATS = FixedCodec("LINK_STATS", "BBHHH", "peer_id reserved rtt_ms loss_in_permille loss_out_permille")
PLAYER_BASE = FixedCodec(
    "PLAYER_SNAPSHOT", "HIfffHhhhhBBH",
    "snap_seq sample_time x y z yaw pitch vx vy vz move_state health flags",
    coords=(("x", "y", "z"),), checks=_check_player)
VEHICLE_BLOCK = FixedCodec(
    "VehicleBlock", "HfffIhhhhhhbBBB",
    "vehicle_net px py pz quat lvx lvy lvz avx avy avz steer throttle brake vflags",
    coords=(("px", "py", "pz"),), checks=_check_vehicle_block)
SNAPSHOT_ACK = FixedCodec("SNAPSHOT_ACK", "I", "tick")
FIRE_FX = FixedCodec("FIRE_FX", "IiiihhhBB", "time_ms ox oy oz dx dy dz weapon_class shots")
EQUIP = FixedCodec("EQUIP", "BBHQQH", "slot weapon_class flags item_record appearance ammo",
                   checks=_check_equip)
VEHICLE_ENTER = FixedCodec("VEHICLE_ENTER", "HBBQQfffI",
                           "vehicle_net seat flags record appearance x y z quat",
                           coords=(("x", "y", "z"),))
VEHICLE_EXIT = FixedCodec("VEHICLE_EXIT", "HBBfffH", "vehicle_net seat flags x y z yaw",
                          coords=(("x", "y", "z"),))
HIT = FixedCodec("HIT", "HBBBBfQhhhI",
                 "target_net target_kind hit_zone attack flags damage weapon_record rx ry rz time_ms",
                 checks=_check_hit)
DEATH = FixedCodec("DEATH", "HBBHBBI", "target_net target_kind cause killer_net killer_kind flags time_ms",
                   checks=_check_death)
TIME_WEATHER = FixedCodec("TIME_WEATHER", "IHBBQH",
                          "game_seconds time_scale_x100 flags weather_id weather_record transition_s",
                          checks=_check_time_weather)
CHAT = FixedCodec("CHAT", "B", "channel", text="text", text_limit=MAX_CHAT_BYTES)
TELEPORT_REQ = FixedCodec("TELEPORT_REQ", "HBB", "req_id mode reserved", checks=_check_teleport_req)
TELEPORT_RESP = FixedCodec("TELEPORT_RESP", "HBBfffH", "req_id accepted reserved x y z yaw",
                           coords=(("x", "y", "z"),))
WORLD_FACT = FixedCodec("WORLD_FACT", "Qi", "fact_hash value")
# MOD_LIST text: "name@version;name@version;..." (chunked, at most 16 chunks of 255 bytes)
MOD_LIST = FixedCodec("MOD_LIST", "BB", "chunk chunks", text="text", text_limit=255,
                      checks=_check_mod_list)
SESSION_CONFIG = FixedCodec("SESSION_CONFIG", "HHBBH",
                            "npc_radius_m vehicle_radius_m entity_hz joiner_population flags",
                            checks=_check_session_config)


class PlayerSnapshotCodec:
    """PLAYER_SNAPSHOT = 32-byte base + 34-byte VehicleBlock when flags has DRIVING."""

    name = "PLAYER_SNAPSHOT"

    def encode(self, values: dict) -> bytes:
        data = PLAYER_BASE.encode(values)
        if values["flags"] & PlayerFlag.DRIVING:
            data += VEHICLE_BLOCK.encode(values["vehicle"])
        return data

    def decode(self, body: bytes) -> dict:
        base_size = PLAYER_BASE.fixed_size
        if len(body) < base_size:
            raise ProtocolError("PLAYER_SNAPSHOT: truncated")
        flags = struct.unpack_from("<H", body, base_size - 2)[0]
        driving = bool(flags & PlayerFlag.DRIVING)
        expected = base_size + (VEHICLE_BLOCK.fixed_size if driving else 0)
        if len(body) != expected:
            raise ProtocolError(f"PLAYER_SNAPSHOT: expected {expected} bytes, got {len(body)}")
        values = PLAYER_BASE.decode(body[:base_size])
        values["vehicle"] = VEHICLE_BLOCK.decode(body[base_size:]) if driving else None
        return values


ENTITY_HEADER = struct.Struct("<IIIH")      # tick baseline_tick sample_time count
SPAWN_BLOCK = struct.Struct("<BBBBQQ")     # kind spawn_flags attitude reserved record appearance
POS_BLOCK = struct.Struct("<iii")          # millimetres
POS_DELTA_BLOCK = struct.Struct("<hhh")    # millimetres relative to the baseline position
VEL_BLOCK = struct.Struct("<hhh")          # centimetres per second
STATE_BLOCK = struct.Struct("<BBB")        # move_state flags health(0..255)
RECORD_HEAD = struct.Struct("<HB")         # net_id mask


class EntitySnapshotCodec:
    """ENTITY_SNAPSHOT: 14-byte header + count self-describing entity records.

    Record = net_id:u16 mask:u8 [ext:u8] [SPAWN 20] [POS 12 | POS_DELTA 6]
             [YAW 2 | QUAT 4] [VEL 6] [STATE 3] [TARGET 2] [WEAPON 8]
    Records are dicts with optional keys: spawn, pos, pos_delta, yaw, quat, vel,
    state, remove, target, weapon. Absent entity = unchanged since the baseline.
    """

    name = "ENTITY_SNAPSHOT"

    @staticmethod
    def record_size(record: dict) -> int:
        size = RECORD_HEAD.size
        if record.get("remove") or "target" in record or "weapon" in record:
            size += 1
        size += SPAWN_BLOCK.size if "spawn" in record else 0
        size += POS_BLOCK.size if "pos" in record else 0
        size += POS_DELTA_BLOCK.size if "pos_delta" in record else 0
        size += 2 if "yaw" in record else 0
        size += 4 if "quat" in record else 0
        size += VEL_BLOCK.size if "vel" in record else 0
        size += STATE_BLOCK.size if "state" in record else 0
        size += 2 if "target" in record else 0
        size += 8 if "weapon" in record else 0
        return size

    def encode_record(self, record: dict) -> bytes:
        mask = 0
        ext = 0
        if record.get("remove"):
            ext |= X_REMOVE
        if "target" in record:
            ext |= X_TARGET
        if "weapon" in record:
            ext |= X_WEAPON
        parts = []
        if "spawn" in record:
            mask |= M_SPAWN
            spawn = record["spawn"]
            parts.append(SPAWN_BLOCK.pack(spawn["kind"], spawn["spawn_flags"], spawn["attitude"], 0,
                                          spawn["record"], spawn["appearance"]))
        if "pos" in record:
            mask |= M_POS
            parts.append(POS_BLOCK.pack(*record["pos"]))
        if "pos_delta" in record:
            mask |= M_POS_DELTA
            parts.append(POS_DELTA_BLOCK.pack(*record["pos_delta"]))
        if "yaw" in record:
            mask |= M_YAW
            parts.append(struct.pack("<H", record["yaw"]))
        if "quat" in record:
            mask |= M_QUAT
            parts.append(struct.pack("<I", record["quat"]))
        if "vel" in record:
            mask |= M_VEL
            parts.append(VEL_BLOCK.pack(*record["vel"]))
        if "state" in record:
            mask |= M_STATE
            parts.append(STATE_BLOCK.pack(*record["state"]))
        if "target" in record:
            parts.append(struct.pack("<H", record["target"]))
        if "weapon" in record:
            parts.append(struct.pack("<Q", record["weapon"]))
        if ext:
            mask |= M_EXT
        head = RECORD_HEAD.pack(record["net_id"], mask) + (bytes([ext]) if ext else b"")
        return head + b"".join(parts)

    def encode(self, values: dict) -> bytes:
        records = values["records"]
        try:
            head = ENTITY_HEADER.pack(values["tick"], values["baseline"], values["sample_time"], len(records))
            data = head + b"".join(self.encode_record(record) for record in records)
        except struct.error as error:
            raise ProtocolError(f"ENTITY_SNAPSHOT: cannot encode: {error}") from error
        self.decode(data)
        return data

    def decode(self, body: bytes) -> dict:
        if len(body) < ENTITY_HEADER.size:
            raise ProtocolError("ENTITY_SNAPSHOT: truncated header")
        tick, baseline, sample_time, count = ENTITY_HEADER.unpack_from(body)
        if count > MAX_ENTITY_RECORDS:
            raise ProtocolError("ENTITY_SNAPSHOT: too many records")
        if baseline and (baseline >= tick):
            raise ProtocolError("ENTITY_SNAPSHOT: baseline must precede tick")
        offset = ENTITY_HEADER.size
        records = []
        seen = set()
        for _ in range(count):
            record, offset = self._decode_record(body, offset)
            if record["net_id"] in seen:
                raise ProtocolError("ENTITY_SNAPSHOT: duplicate net_id")
            seen.add(record["net_id"])
            records.append(record)
        if offset != len(body):
            raise ProtocolError("ENTITY_SNAPSHOT: trailing bytes")
        return {"tick": tick, "baseline": baseline, "sample_time": sample_time, "records": records}

    @staticmethod
    def _take(body: bytes, offset: int, layout: struct.Struct):
        if len(body) - offset < layout.size:
            raise ProtocolError("ENTITY_SNAPSHOT: truncated record")
        return layout.unpack_from(body, offset), offset + layout.size

    def _decode_record(self, body: bytes, offset: int):
        (net_id, mask), offset = self._take(body, offset, RECORD_HEAD)
        _require(net_id != 0, "ENTITY_SNAPSHOT: net_id 0 is reserved")
        record = {"net_id": net_id}
        ext = 0
        if mask & M_EXT:
            (ext,), offset = self._take(body, offset, struct.Struct("<B"))
            _require(ext != 0 and not ext & ~X_KNOWN, "ENTITY_SNAPSHOT: bad ext mask")
        if ext & X_REMOVE:
            _require(mask == M_EXT and ext == X_REMOVE, "ENTITY_SNAPSHOT: remove carries no fields")
            record["remove"] = True
            return record, offset
        _require(not (mask & M_POS and mask & M_POS_DELTA), "ENTITY_SNAPSHOT: POS and POS_DELTA")
        _require(not (mask & M_YAW and mask & M_QUAT), "ENTITY_SNAPSHOT: YAW and QUAT")
        _require((mask & ~M_EXT) != 0 or (ext & (X_TARGET | X_WEAPON)) != 0, "ENTITY_SNAPSHOT: empty record")
        if mask & M_SPAWN:
            _require(mask & M_POS and mask & (M_YAW | M_QUAT), "ENTITY_SNAPSHOT: spawn needs POS + rotation")
            (kind, spawn_flags, attitude, _, record_id, appearance), offset = self._take(body, offset, SPAWN_BLOCK)
            _require(kind in EntityKind._value2member_map_, "ENTITY_SNAPSHOT: bad kind")
            record["spawn"] = {"kind": kind, "spawn_flags": spawn_flags, "attitude": attitude,
                               "record": record_id, "appearance": appearance}
        if mask & M_POS:
            pos, offset = self._take(body, offset, POS_BLOCK)
            check_world_coordinates(*(mm_to_meters(v) for v in pos), what="ENTITY_SNAPSHOT")
            record["pos"] = pos
        if mask & M_POS_DELTA:
            record["pos_delta"], offset = self._take(body, offset, POS_DELTA_BLOCK)
        if mask & M_YAW:
            (record["yaw"],), offset = self._take(body, offset, struct.Struct("<H"))
        if mask & M_QUAT:
            (record["quat"],), offset = self._take(body, offset, struct.Struct("<I"))
        if mask & M_VEL:
            record["vel"], offset = self._take(body, offset, VEL_BLOCK)
        if mask & M_STATE:
            state, offset = self._take(body, offset, STATE_BLOCK)
            _require(state[0] < MOVE_STATE_COUNT, "ENTITY_SNAPSHOT: bad move state")
            record["state"] = state
        if ext & X_TARGET:
            (record["target"],), offset = self._take(body, offset, struct.Struct("<H"))
        if ext & X_WEAPON:
            (record["weapon"],), offset = self._take(body, offset, struct.Struct("<Q"))
        return record, offset


# ---------------------------------------------------------------------------
# message table: delivery, who may send, where the relay routes it
# ---------------------------------------------------------------------------

class Sender(enum.Enum):
    RELAY = "relay"       # only the relay emits it
    CLIENT = "client"     # any authenticated player
    HOST = "host"         # only the room host (world authority)


class Route(enum.Enum):
    RELAY = "relay"           # consumed by the relay
    BROADCAST = "broadcast"   # every other room member
    HOST = "host"             # the room host
    TARGET = "target"         # the peer named in the message header
    HIT = "hit"               # host for entities, the victim for players


class MsgSpec:
    def __init__(self, mtype: MsgType, reliable: bool, sender: Sender, route: Route, codec):
        self.mtype = mtype
        self.reliable = reliable
        self.sender = sender
        self.route = route
        self.codec = codec


SPECS = {spec.mtype: spec for spec in (
    MsgSpec(MsgType.TIME_REQ, False, Sender.CLIENT, Route.RELAY, TIME_REQ),
    MsgSpec(MsgType.TIME_RESP, False, Sender.RELAY, Route.RELAY, TIME_RESP),
    MsgSpec(MsgType.PEER_JOINED, True, Sender.RELAY, Route.RELAY, PEER_JOINED),
    MsgSpec(MsgType.PEER_LEFT, True, Sender.RELAY, Route.RELAY, PEER_LEFT),
    MsgSpec(MsgType.LINK_STATS, False, Sender.RELAY, Route.RELAY, LINK_STATS),
    MsgSpec(MsgType.PLAYER_SNAPSHOT, False, Sender.CLIENT, Route.BROADCAST, PlayerSnapshotCodec()),
    MsgSpec(MsgType.ENTITY_SNAPSHOT, False, Sender.HOST, Route.BROADCAST, EntitySnapshotCodec()),
    MsgSpec(MsgType.SNAPSHOT_ACK, False, Sender.CLIENT, Route.HOST, SNAPSHOT_ACK),
    MsgSpec(MsgType.FIRE_FX, False, Sender.CLIENT, Route.BROADCAST, FIRE_FX),
    MsgSpec(MsgType.EQUIP, True, Sender.CLIENT, Route.BROADCAST, EQUIP),
    MsgSpec(MsgType.VEHICLE_ENTER, True, Sender.CLIENT, Route.BROADCAST, VEHICLE_ENTER),
    MsgSpec(MsgType.VEHICLE_EXIT, True, Sender.CLIENT, Route.BROADCAST, VEHICLE_EXIT),
    MsgSpec(MsgType.HIT, True, Sender.CLIENT, Route.HIT, HIT),
    MsgSpec(MsgType.DEATH, True, Sender.CLIENT, Route.BROADCAST, DEATH),
    MsgSpec(MsgType.TIME_WEATHER, True, Sender.HOST, Route.BROADCAST, TIME_WEATHER),
    MsgSpec(MsgType.CHAT, True, Sender.CLIENT, Route.BROADCAST, CHAT),
    MsgSpec(MsgType.TELEPORT_REQ, True, Sender.CLIENT, Route.TARGET, TELEPORT_REQ),
    MsgSpec(MsgType.TELEPORT_RESP, True, Sender.CLIENT, Route.TARGET, TELEPORT_RESP),
    MsgSpec(MsgType.WORLD_FACT, True, Sender.HOST, Route.BROADCAST, WORLD_FACT),
    MsgSpec(MsgType.MOD_LIST, True, Sender.CLIENT, Route.BROADCAST, MOD_LIST),
    MsgSpec(MsgType.SESSION_CONFIG, True, Sender.HOST, Route.BROADCAST, SESSION_CONFIG),
)}


def decode_body(mtype: int, body: bytes) -> dict:
    spec = SPECS.get(mtype)
    if spec is None:
        raise ProtocolError(f"unknown message type 0x{mtype:02x}")
    return spec.codec.decode(body)


def encode_body(mtype: int, values: dict) -> bytes:
    return SPECS[mtype].codec.encode(values)
