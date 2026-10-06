# Source foundation versus the playable package

The repository now contains Bukczyk's typed session foundation for development
toward large multiplayer co-op. See
[the integration and build guide](../../docs/FOUNDATION_INTEGRATION.md).

This folder's complete ZIP, manifest and `game-root` remain the **v0.0.37 / alpha.5**
game-tested reference. A source merge does not make the old scripts compatible
with the new session plugin. This note is package-folder guidance, not a new
installation payload or a changed release archive.

Build and prepare matching development profiles using the guide. Keep them separate
from this package and from game installations until the typed bridge has passed
its game/rollback gates. The session package generator uses fresh private keys;
do not publish its generated private profiles as public releases.

New playable versions must refresh this complete package, GitHub release assets,
verified hashes and the vault together. Preserve this reference until then.
