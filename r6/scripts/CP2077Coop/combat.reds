// ============================================================
// CP2077 COOP v0.0.25 - Combat Hit Capture
//
// We hook the real DamageSystem pipeline instead of guessing
// mouse/controller bindings.
//
// When the LOCAL player damages an NPC with a ranged weapon,
// we reuse CP2077Coop_PushPlayerState as an internal transport:
//
//   x/y/z      = target NPC world position
//   w          = -777.0  (combat marker)
//   forwardX   = computed health damage
//   forwardY   = 9999.0  (combat marker)
//
// The RED4ext plugin recognizes this marker and queues a combat
// packet without overwriting normal movement state.
// ============================================================

// Opt-in v2 currently transports player snapshots, not authoritative hits.
// Default false preserves the v1 damage path when Lua or the new module is absent.
@addField(PlayerPuppet)
private let CP2077Coop_LegacyCombatSuppressed: Bool;

@addMethod(PlayerPuppet)
public func CP2077Coop_SuppressLegacyCombat(suppressed: Bool) -> Void {
    this.CP2077Coop_LegacyCombatSuppressed = suppressed;
}

@addMethod(PlayerPuppet)
public func CP2077Coop_IsLegacyCombatSuppressed() -> Bool {
    return this.CP2077Coop_LegacyCombatSuppressed;
}

@wrapMethod(DamageSystem)
private func ProcessLocalizedDamage(hitEvent: ref<gameHitEvent>) -> Void {

    // Never replace vanilla damage handling.
    wrappedMethod(hitEvent);

    if !IsDefined(hitEvent) {
        return;
    };

    if !IsDefined(hitEvent.attackData) {
        return;
    };

    if !IsDefined(hitEvent.target) {
        return;
    };

    if !IsDefined(hitEvent.attackData.GetInstigator()) {
        return;
    };

    // Only hits produced by the local player.
    if !hitEvent.attackData.GetInstigator().IsPlayer() {
        return;
    };

    let coopPlayer = hitEvent.attackData.GetInstigator() as PlayerPuppet;
    if IsDefined(coopPlayer) && coopPlayer.CP2077Coop_IsLegacyCombatSuppressed() {
        return;
    };

    // For v0.0.25 we sync ranged weapon hits only.
    let weapon: wref<WeaponObject> =
        hitEvent.attackData.GetWeapon();

    if !IsDefined(weapon) {
        return;
    };

    if !weapon.IsRanged() {
        return;
    };

    // Only NPC targets.
    let npc: wref<NPCPuppet> =
        hitEvent.target as NPCPuppet;

    if !IsDefined(npc) {
        return;
    };

    // Use the damage value already calculated by the real game.
    let damage: Float =
        hitEvent.attackComputed.GetTotalAttackValue(
            gamedataStatPoolType.Health
        );

    if damage <= 0.00 {
        return;
    };

    let position: Vector4 =
        npc.GetWorldPosition();

    // Reuse the existing, already-stable native function.
    CP2077Coop_PushPlayerState(
        position.X,
        position.Y,
        position.Z,
        -777.00,
        damage,
        9999.00
    );
}
