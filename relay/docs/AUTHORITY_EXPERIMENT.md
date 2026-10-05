# C3A1 authority experiment

Status: **implemented and verified headlessly; disabled by default; no game bridge**.
This is one host-owned vehicle's identity, measured pose metadata and one logical
driver seat. It creates no Cyberpunk entity, mounts nobody, injects no controls,
and does not synchronize physics, passengers, combat, NPC AI or saves.

The policy design adapts the useful ideas in
[Bukczyk/CP2077-Coop SessionRegistry at 20eb125](https://github.com/Bukczyk/CP2077-Coop/blob/20eb125796a4a5e55394cc2c3ed344f9b8456b4a/shared/src/session.cpp):
authenticated connection/member binding, a host committing results, separate
client intent, world epochs, readiness, ordered event IDs and ownership checks.
This is an independent Python implementation. No friend source or CPS1 transport
was copied. The working CB77 v2.1 transport, handshake, clocks and reliability stay
in use. See the root integration provenance for the retained source histories.

## Enablement and trust boundary

Start a separate headless relay with `--entity-authority-test`. Both that flag and
the `C3A1` message namespace are required. With the flag absent, existing routing
is unchanged, including ordinary `SCRIPT_MSG` traffic on channels 21 and 3.

- Existing `SCRIPT_MSG`, protocol minor 1, carries visible ASCII text up to 512
  bytes. No NUL, newlines, Unicode, escaping or embedded `|` in fields.
- Channel **21** carries reliable control. Channel **3** carries unreliable poses.
  Player channels 1/16/30 and controlled-NPC channels 2/20 remain separate.
- Requests address peer 0 (relay) or 255 (broadcast); the relay intercepts this
  namespace before normal forwarding. Other destination IDs are rejected.
- Identity and host role come from the already admitted relay `Peer`, never from
  text. Policy membership is bound to the current connection token internally.
  Tokens and room keys are never echoed in these messages.
- All responses originate from relay peer **0**. A receiver must also verify the
  channel, reliable bit, epoch and its own activation nonce. An `OFFER` must echo
  the nonce from that receiver's current discovery attempt.
- This adds no stronger authentication than the current room-key/cookie/token
  transport. There is no encryption, account identity or hostile-host protection.

The current v37 Lua adapter accepts only its remote peer and the NPC extension's
20/2 channels. It **does not consume relay-source 0 or C3A1 21/3**. Do not install
another `Net_Poll` loop. A later reviewed extension dispatcher must add bounded,
separate authority queues and lifecycle handling under the existing poll owner.
No native DLL or game source changes were needed or deployed for this experiment.

## Discovery and baseline

Nonces and epochs are exactly 32 lowercase hexadecimal characters. A client
generates a fresh activation nonce whenever its local authority coordinator starts
or its game world resets. The relay issues a fresh random 128-bit epoch on BEGIN.
These identifiers are replay/lifecycle context, not extra credentials.

```text
host   -> C3A1|BEGIN|host_nonce
relay  -> C3A1|STATE|epoch|host_nonce|revision|host_peer|member_generation|...
joiner -> C3A1|DISCOVER|joiner_nonce
relay  -> C3A1|OFFER|epoch|joiner_nonce|revision|host_peer
joiner -> C3A1|JOIN|epoch|joiner_nonce
relay  -> C3A1|STATE|epoch|joiner_nonce|revision|host_peer|member_generation|...
joiner -> C3A1|READY|epoch|joiner_nonce|revision
relay  -> C3A1|READY_OK|epoch|joiner_nonce|revision
```

BEGIN is host-only. Repeating the currently active nonce returns STATE without
resetting anything. A previously used host nonce is rejected. Up to 64 host
activation nonces are retained per connection; exhaustion requires reconnect,
not silent replay-history eviction. Host disconnect invalidates the epoch and
clears this experiment. A future host must BEGIN again.

DISCOVER returns public context but admits nobody. JOIN registers only the
authenticated caller, initially not ready. A new JOIN nonce invalidates that
caller's previous pending intent and occupied metadata seat. Each new activation
receives a monotonically increasing **member generation**, even if a reconnect
reuses a peer ID and client nonce. The generation is never a connection token.
Previous JOIN nonces cannot reactivate within the same membership; history is
bounded to 64 activation nonces including the current one, after which reconnect
is required.

READY acknowledges a complete baseline control revision. If another control
change has committed meanwhile, the relay sends a refreshed STATE and does not
admit that synchronizing member. It must acknowledge the new revision. Pose
updates do not change the control revision. This is one bounded metadata baseline,
not a chunked game-world snapshot or an observed engine-application acknowledgment.

## Control commands and results

After admission, each control command has this prefix:

```text
C3A1|VERB|epoch|sender_nonce|event_id|arguments...
```

Event IDs are canonical decimal unsigned 64-bit integers, starting at 1 separately
for each member activation. Gaps are rejected; rejected operations do not advance
the watermark. An already accepted ID returns `duplicate/already_accepted` plus
the **current** STATE without executing again. This bounded experiment does not
retain every original result. Clients must reconcile the current revision rather
than infer an old command's exact result from a duplicate response.

Entity IDs, revisions and member generations are canonical unsigned 32-bit decimal
integers. Entity IDs must be nonzero and strictly increase within an epoch. An ID
cannot be reused after despawn. Only one vehicle metadata record exists at once.

| Verb | Arguments after event ID | Permission and effect |
|---|---|---|
| SPAWN | `entity|expected_revision|record_label|x|y|z|yaw` | Host creates metadata; empty registry required. |
| DESPAWN | `entity|expected_revision` | Host removes metadata; driver seat must be empty. |
| REQUEST | `entity|expected_revision|enter_or_exit` | Ready member requests entry or its own exit; sends INTENT only to host. No seat change. |
| GRANT | `entity|expected_revision|requester_peer|request_event|request_generation` | Host accepts a current pending `enter`; seat must be free. |
| RELEASE | `entity|expected_revision|requester_peer|request_event|request_generation` | Host accepts a current driver's pending `exit`. |
| REVOKE | `entity|expected_revision` | Host explicitly clears an occupied metadata seat. |

`record_label` is 1–64 ASCII letters, digits, underscores, periods or hyphens. It
is an inert label; the relay does not resolve TweakDB or validate a game vehicle
record. A future game bridge needs an exact allowlist for its supported car and
seat, plus actual mounting/dismounting evidence before committing engine outcomes.

An accepted request emits:

```text
C3A1|INTENT|epoch|host_nonce|requester_peer|request_event|request_generation|enter_or_exit|entity|revision
```

Only one pending request per member is retained. It expires after three monotonic
seconds; tick removes expired entries. GRANT/RELEASE must match the pending action,
member identity, activation nonce, generation, request event and current revision.
A committed seat change invalidates other pending requests at the previous
revision. Late, disconnected or superseded requests cannot grant a seat. Both
host and joiner may request the logical seat; only the host commits results.

The relay returns:

```text
C3A1|RESULT|epoch|sender_nonce|event_id|ok_or_duplicate_or_reject|reason|revision
```

Rejected commands use event ID 0; the sender retains its next unaccepted ID and
must correct/reconcile before retrying it. Invalid unregistered clients receive
no experiment response. There is no fallback that treats link acknowledgment as
accepted metadata, and no metadata response proves game mounting completed.

Full STATE layout:

```text
C3A1|STATE|epoch|receiver_nonce|revision|host_peer|receiver_generation|entity|record_label|x|y|z|yaw|driver_peer|seat_generation|pose_sequence|sample_ms
```

An empty registry uses entity 0, record `-` and zero remaining fields. Before the
first POSE, sequence/sample time are 0. SPAWN, seat change and DESPAWN increment the
control revision; each seat change increments the lease's seat generation. State
is sent to all opted-in members, including those waiting to acknowledge a baseline.

## Pose path

Only the host sends channel-3 poses:

```text
C3A1|POSE|epoch|host_nonce|entity|sequence|sample_ms|x|y|z|yaw
```

The relay forwards accepted poses from peer 0, replacing `host_nonce` with each
ready receiver's nonce. Sequence is wrap-aware u32: duplicates, old values and
half-range jumps are rejected. The timestamp is u32 milliseconds on the existing
**relay clock**, not wall time or an unsynchronized sender clock. Reject samples
over 2,000 ms old or more than 250 ms in the future, including across u32 wrap.
At most one pose per 100 ms is accepted. Only the newest unsent experimental pose
per receiver is kept.

Positions must be finite with X/Y in ±20,000 m and Z in ±5,000 m. Yaw is finite
degrees in ±180. No velocity, quaternion, physics input or mounted-character pose
is represented here. A future game sender must read actual state; it must not
publish a requested transform as measured engine state.

## Bounds, teardown and failure

The relay's existing room/member/packet/reliable-rate bounds remain in force. The
policy independently limits membership to eight, holds one vehicle, one pending
intent per member and bounded nonce sets, and retains only one event watermark
per activation. Counter exhaustion requires a new epoch rather than wrap, except
for pose sequences/timestamps explicitly using modular comparisons.

Commands are planned against a small copy. Before committing, the relay verifies
every target still belongs to that membership and all outgoing reliable effects
fit their send windows. On overflow it disconnects the slow consumer and discards
the planned command; it does not queue success or advance that event watermark.
Membership cleanup still occurs. Queue calls execute under optimized Python too.

Joiner removal clears pending requests and revokes its metadata driver lease.
Rejoin requires DISCOVER/JOIN/READY and gets a fresh member generation. Host
removal clears the experiment and sends opted-in survivors:

```text
C3A1|END|old_epoch|receiver_nonce|host_left
```

Host reset sends END with `reset`; revision exhaustion during required cleanup
closes it with `epoch_exhausted`. Metadata lease cleanup does **not** prove a
player physically dismounted. The eventual game bridge must freeze/reconcile
and must never delete a vehicle while a local player is still attached.

## Verification, 2026-10-04

From the repository root:

```powershell
python -m unittest discover -s relay/tests -v
python -O -m unittest discover -s relay/tests -p 'test_authority*.py' -v
```

The full relay suite passed **115 tests**. The new focused suite passed **21 tests**,
including optimized Python. It covers authority/ownership, simultaneous seat
requests, nonmutating rejection, duplicates/gaps, baseline-revision races,
request expiry, membership-generation reuse, epoch/nonce replay, bounds,
timestamp/sequence wrap, reliable overflow and bounded latest-pose buffering.
Default-off passthrough, ordinary player snapshots and NPC channels are verified.

`test_authority_udp.py` uses real localhost UDP sockets and the actual CB77
handshake/codec/reliability path with 12% simulated loss, 10% duplication and
5 ms plus uniform 0–15 ms additional delay in each direction. Both clients learn session context from received
messages; joiner bootstrap uses echoed-nonce OFFER, never server-state reads.
It checks intent before approval, committed occupancy, pose delivery, reconnect
with reused peer ID/client nonce rejecting an old-generation approval, and host
departure. It also asserts actual drops and client reliable retries occurred.
The pose fixture reads the test relay's clock to construct its sample timestamp;
this test does not measure client clock synchronization. Existing native clock
and transport loopback tests cover that separate responsibility.

The full relay suite also passed on Windows and Linux in
[CI run 37259471154 at 856721b](https://github.com/KyleBuildsAI/cp2077-coop/actions/runs/37259471154).
The optimized-Python subset was independently run locally on Windows. These are
headless checks, not a two-PC gameplay test, public-server deployment, shared car
ride or full-physics result. No games were launched for this experiment.
