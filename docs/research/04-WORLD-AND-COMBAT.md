# 4. Combat, NPCs and world consistency

[Atlas](README.md) · V37-08 and later multiplayer gates. None of the reviewed sources establishes complete synchronized story co-op for our project.

## WORLD-01: immediate reaction disagrees with accepted damage

**Their result — documented contract.** OPEN//77's [prediction guide](https://open2077.net/docs/prediction) describes local visual anticipation followed by adoption or rollback after the server verdict. Families are independently switchable, with a documented default 250 ms RTT ceiling and counters for accepted/refused/skipped prediction. Prediction itself does not award damage or authority. The public policy resource is not the native predictor.

**Our proposal.** Keep authoritative outcomes and optional presentation separate. Before prediction, establish event identity, bounded lifetime, duplicate rejection and canonical health. Later count prediction adoption/refusal/expiry and cleanup duration. Treat their latency threshold as a reported setting, not our tuned value. Gate: denied, duplicate or timed-out actions cannot inflict damage, spend inventory twice or leave a permanent visual reaction.

## WORLD-02: wrong shot gets credit; NPC dead on only one client

**Their result — reported fixes.** The [September 30 update](https://open2077.net/devblog/2026-09-30-combat-npcs-prediction-pass) reports matching damage evidence to the actual shot, canonical NPC life-state fixes and improved environmental/contact damage handling. Full native hit validation and wire details are not public in this material.

**Our proposal.** Specify event IDs linking discharge, target, weapon context, membership/epoch and accepted result. Include environmental damage without inventing a shooter. Reject repeats and late events against a replacement entity. Test simultaneous lethal hits, death during restream and a delayed hit after respawn. Gate: one accepted outcome converges on both clients and feedback does not recursively generate another network hit.

**Unresolved comparison.** [Cyberverse #5](https://github.com/TDUniverse/Cyberverse/issues/5) remains an open health/damage design issue. CyberMP Freeroam [#84](https://github.com/Cyber-MP/CyberMP-Freeroam/issues/84), closed September 5, proposes server-side immunity rather than relying only on huge health. Its issue closure is not an inspected implementation or test.

## WORLD-03: traffic differs, dead driver slides with the car

**Their result — documented model and reported fixes.** The [vehicle AI guide](https://open2077.net/docs/vehicle-ai) assigns native driving to a nearby ready client while the server owns task lifecycle and seat reservations. It explicitly says there is no headless server physics. A reserved AI driver's seat must be released before a human takes over. The [September 28 update](https://open2077.net/devblog/2026-09-28-ai-traffic-stability-pass) reports preserving dead seated drivers for late join, detaching bodies after completed theft, avoiding repeatedly failed spawn corridors and retaining nearby traffic more reliably.

**Our proposal.** After owned-car success, investigate one controlled NPC driver, task ownership, actual detach and late-join reconstruction. Do not enable broad population suppression or attempt whole-city traffic first. Gate: one canonical driver/car pair survives ownership change, death and despawn without duplicates. A server-owned task is not evidence that the relay simulates driving.

## WORLD-04: doors disagree, close through players or expose empty lift shafts

**Their result — documented contract.** [Networked doors](https://open2077.net/docs/doors) uses a canonical registry, validated discovery, proximity hysteresis and elevator linkage. Late streaming applies state without replaying old animation; unresolved elevator links stay closed. Discovery does not independently attest arbitrary client-proposed IDs. Its default discovery policy does not import save-specific quest locks.

**Our proposal.** Our host-world design must preserve supported host locks rather than inherit a freeroam default. Begin with an explicitly identified ordinary door, then test a locked door and an elevator separately. Gate: both views and collision agree, old changes cannot reopen a new-generation door, and blocked geometry remains safe. This does not establish quest synchronization.

## WORLD-05: destroyed object resurrects or weather never applies

**Their result — mixed detail.** The [August 25 update](https://open2077.net/devblog/2026-08-25-server-banners-and-world-sync) reports five interacting causes behind resurrecting props, but does not name them. Do not invent a five-step implementation. It specifically attributes stuck weather to a failed first application without retries and reports retrying until successful application.

**Our proposal.** Use persistent session identity/tombstones and observed apply completion for supported world entities. Bound retries by session generation and time; expire/recover visibly rather than retry forever against a deleted world. Gate: leaving range and late joining preserve destruction, while a deliberate new session initializes correctly. Test weather application after readiness rather than treating packet receipt as engine success.

## Campaign and persistence boundary

Sharing doors, weather, damage or multiworld item events does not synchronize quest graphs, scenes, dialogue choices, rewards or saves. Before a campaign milestone, define one bounded quest with host-owned decisions, late-join policy, participant restrictions, exactly-once rewards and failure recovery. No full save merge or campaign completion is established by this survey. See the [project directory](06-PROJECTS-AND-OPEN-QUESTIONS.md) for what each project actually claims.
