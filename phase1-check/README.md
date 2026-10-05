# Standalone native probe

`CoopNetCheck/init.lua` is a CET bench mod for CP2077CoopNet. The current local
profile uses v2 room `codex-bench` at `127.0.0.1:11778`. It logs native/redscript
selftests, accepted unreliable/reliable sends, ordering/duplicate counters,
precise poll timing, native JSON stats and real-player snapshot sampling.

## One native inbox owner

The plugin has one connection and one `Net_Poll` inbox per game. Before using
CP2077Coop native gameplay, create `disabled.txt` beside this probe's `init.lua`
while the game is closed. Its next startup must log:

`[CoopNetCheck] disabled.txt present: gameplay owns Net_Poll`

In that state the probe does not connect, poll, push snapshots or disconnect the
gameplay connection. Its old log remains on disk and is **not** evidence of the
current run. To resume standalone probing, first disable native gameplay and
then remove the probe switch while the game is closed.

## Measurement limits

This is a diagnostic, not the formal Phase 1 soak scorer. Unreliable loss includes
application newest-only/reorder effects. Snapshot samples demonstrate native
buffer output, not an avatar drawn at that location. Reliable counters are scoped
to the current probe process; logs are replaced on enabled startup. Archive logs
before relaunching, record actual relay settings, and never call a short capture a
30-minute soak. The repaired `coopnet/phase1` tools provide formal epoch/duration
scoring when required.
