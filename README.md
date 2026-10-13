# Official-44 public Mac dependency cache

Prepared locally only. Nothing has been created, pushed, dispatched or released.

This repository is `filipmansfeld/PST-macOS-public-deps`. It contains
only this README, the generic exporter, the exact public ct2rs MIT notice and its
public URL/hash provenance, and the workflow (installed as
`.github/workflows/public-deps.yml`). The recipe checkout contains no simulator
or private client source. A separate checkout is fixed to **official 0.2.44**:
commit `6c82e0771b0695c038c5278cb21894d2b9776c5a`, tree
`977fdb2987349fb9fca0a87a43e17b77f83f2158`.

Both standard hosts are selected: `macos-15` ARM64 and `macos-15-intel` x86_64,
in parallel on separate VMs, at most 45 minutes per job. The recipe uses fresh scoped Cargo
home/target directories, native Cargo 1.98.1, opt3/debug0/CGU32/LTO **off**.
It builds the official executable only to obtain its external dependencies;
it never runs or exports the simulator. No Tauri, .NET, OAuth, private API,
maps, user profiles or private code are inputs.

The export allowlist joins actual Cargo JSON package IDs to complete metadata.
Every workspace member and every source-null/path package is excluded. External
library artifacts, external build tools/native `OUT_DIR` outputs and proven
external fingerprint directories may be retained. Fingerprint ownership uses
the exact reviewed Cargo-version package-name layout, never output-hash guesses;
names colliding with an excluded package fail closed. Complete fetched external
package sources and actual license/notice texts are included. Unknown packages,
unsupported layout, missing licenses, links and escaped paths fail the export.

The reviewed source-license contract pins the official Cargo.lock and the exact
public ct2rs/CTranslate2/Ruy/cpuinfo notice texts. The actual export still requires
every exported crate's complete source and actual license text, binds native build
outputs to package IDs, and checks the known native notice pins. Unknown or missing
licensing fails the export. The SPDX field alone does not satisfy this check.
`publish_release_assets` defaults to **false**. Publication requires the approved
source-license contract SHA256 and the completed automatic actual inventory;
there is no forced extra human approval for a generated scientific inventory.
There is no Actions artifact or
cache upload. A successful source-only run without publication leaves its output
only on the ephemeral runner.

The delivery is two dependency-only public **Release assets**, each strictly
below 2 GiB, plus SHA256/provenance JSON. Publication uses the token belonging
only to the new public recipe repository. The owner creates one exact approved
public release before dispatch and supplies its tag/ID. Both jobs only verify
that existing release and upload distinct per-architecture names; they never
create a release or replace an asset. Upload uses a pending journal and fresh
GET readback. Uncertain outcomes stop rather than triggering another mutation.

Standard public runner minutes are free/unlimited; larger runners are excluded.
Actions artifact storage has a shared allowance and is not used here. Public
releases have no aggregate release-size/bandwidth cap and a less-than-2-GiB
per-file limit. Sources:

- [GitHub standard runner billing](https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job)
- [Actions storage billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
- [GitHub release limits](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)
- [Reviewed Cargo layout implementation](https://github.com/rust-lang/cargo/blob/797e8a9bc/src/cargo/core/compiler/layout.rs)
- [Cargo profile semantics](https://doc.rust-lang.org/cargo/reference/profiles.html)

A private consumer must verify release asset SHA256, source/lock, architecture,
toolchain, profile, SDK/native dependencies and canonical paths before importing
the seed. Preserve file mtimes. Cargo may rebuild after relocation or incompatible
inputs; never rewrite fingerprints to force reuse. All private workspace crates,
especially the engine, must compile anew with `fresh:false` and current private
source/version evidence. This recipe proves no private-build speedup or runtime.

The next approval must explicitly cover creating the named **public** repository,
publishing the reviewed recipe files and exact public license/provenance, a manual native pair, and dependency
release assets with the reviewed source-license contract and automatic actual
inventory gate.
Earlier approval for a private repository does not cover these public actions.
