// CP2077Coop NPC sync - identity probe (test bench).
//
// Run on BOTH games standing at the same spot after loading the same save:
//   for _, line in ipairs(Game.GetPlayer():CP2077Coop_NpcProbe(80.0)) do log(line) end
// then compare the two dumps with npcsync/tools/npc_probe_diff.py. It measures the
// assumption the whole mirror design rests on: placed/community NPCs have the same
// static EntityID (and usually the same appearance) in both games, while crowd and
// runtime spawns have dynamic IDs that never match.
//
// Line format (CSV, one NPC or vehicle per line):
//   PROBE,<type>,<entityHash>,<static 0/1>,<crowd 0/1>,<recHash>,<recLen>,<appearanceHash>,<xCm>,<yCm>,<zCm>,<dead 0/1>
//   type: N = NPC, V = vehicle (vehicles from the Codeware registry are not included here;
//   only puppets found by the targeting query)

@addMethod(PlayerPuppet)
public func CP2077Coop_NpcProbe(radius: Float) -> array<String> {
    let lines: array<String>;
    let npcs: array<ref<NPCPuppet>> = this.GetNPCsAroundObject(radius);
    let i: Int32 = 0;

    ArrayPush(lines, "PROBE_BEGIN," + IntToString(ArraySize(npcs))
        + "," + IntToString(CP2077CoopNpc_MetresToCm(this.GetWorldPosition().X))
        + "," + IntToString(CP2077CoopNpc_MetresToCm(this.GetWorldPosition().Y))
        + "," + IntToString(CP2077CoopNpc_MetresToCm(this.GetWorldPosition().Z)));

    while i < ArraySize(npcs) {
        let npc: ref<NPCPuppet> = npcs[i];
        if IsDefined(npc) {
            let id: EntityID = npc.GetEntityID();
            let position: Vector4 = npc.GetWorldPosition();
            ArrayPush(lines, "PROBE,N," + ToString(EntityID.ToHash(id))
                + "," + (CP2077CoopNpc_IsStaticId(id) ? "1" : "0")
                + "," + (npc.IsCrowd() ? "1" : "0")
                + "," + ToString(CP2077CoopNpc_RecordHash(npc.GetRecordID()))
                + "," + IntToString(CP2077CoopNpc_RecordLength(npc.GetRecordID()))
                + "," + ToString(NameToHash(npc.GetCurrentAppearanceName()))
                + "," + IntToString(CP2077CoopNpc_MetresToCm(position.X))
                + "," + IntToString(CP2077CoopNpc_MetresToCm(position.Y))
                + "," + IntToString(CP2077CoopNpc_MetresToCm(position.Z))
                + "," + (npc.IsDead() ? "1" : "0"));
        }
        i += 1;
    }

    ArrayPush(lines, "PROBE_END");
    return lines;
}
