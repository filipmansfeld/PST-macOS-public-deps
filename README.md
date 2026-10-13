# Official-44 public Mac dependency cache

The recipe is versioned with its workflow and exact public notice contract. Publication is an explicit repository-owner action.

This repository is `filipmansfeld/PST-macOS-public-deps`. It contains
only this README, the generic exporter, the exact public ct2rs MIT notice and its
public URL/hash provenance, and the workflow (installed as
`.github/workflows/public-deps.yml`). The recipe checkout contains no simulator
or private client source. A separate checkout is fixed to **official 0.2.44**:
commit `6c82e0771b0695c038c5278cb21894d2b9776c5a`, tree
`977fdb2987349fb9fca0a87a43e17b77f83f2158`.

The manual `architectures` choice selects `both` (default), `arm64`, or `x86_64`.
The fixed standard hosts are `macos-15` ARM64 and `macos-15-intel` x86_64,
in parallel on separate VMs when both are selected, at most 45 minutes per job.
Runs are isolated by the supplied existing release ID and architecture selection,
without cancelling another run. The recipe uses fresh scoped Cargo home/target
directories and opt3/debug0/CGU32/LTO **off**. The exact native toolchains are:

- ARM64: Cargo 1.98.1 (`797e8a9bc`), Rust 1.98.1 (`48a229ceaefd4985c50990b14116b6d856af0985`).
- x86_64: Cargo 1.98.0 (`797e8a9bc`), Rust 1.98.0 (`88d9e12ae178fab0fb5cc050a94da85685d449ea`).

Both Cargo versions use the same reviewed Cargo source commit/layout. The worker
checks exact per-architecture versions and Rust commits, records the actual
version output, and does not install or silently accept another toolchain.
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
unsupported layout, missing licenses and escaped source paths fail the export.
Corresponding-source, fingerprint and library traversal rejects links. Native
build-output links are handled only within the same exact output owner: safe
inside links become verified ordinary copies at their original logical paths.
Outside, cyclic or unrepresentable empty-directory links are omitted and recorded;
that package's library/build-tool artifacts and fingerprints are excluded from
Cargo-ready material. Its remaining safe native files are inspection-only under
`inspection-native-output/`, never a Cargo import. The receipt explicitly reports
incomplete package IDs and normal rebuild requirements; it claims no complete
standalone reusable native closure.

License preflight runs before the expensive release build. Cargo metadata is
filtered to the exact native target; traversal starts at the official `omsi-app`
workspace identity and follows normal/build edges, excluding dev-only edges.
This is a conservative preflight set, never an asserted compiled package set.
A missing packaged text may be resolved only from the package's actual recorded
VCS commit, canonical public GitHub repository and VCS package path. The original
regular manifest blob must match `Cargo.toml.orig`; package version/license and
workspace inheritance must match Cargo metadata. Original ancestor/root license
blobs are bound by Git object SHA and content SHA256; no template text is inferred.
An unresolved package stops before Cargo with its exact ID/name/version/source/VCS.
Only actually compiled package IDs contribute fallback texts to the export.
The ephemeral recipe token is used only for bounded GETs to the verified public
original GitHub repositories, never forwarded to redirects or Cargo subprocesses.
Original notice texts are data-only under `original-license-texts/`. VCS metadata
is a locator and matching authored manifests do not claim whole-crate equivalence.

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

Publishing is limited to this named **public** recipe repository, the exact
public license/provenance files, the selected standard native runners, and
external-dependency release assets after the source-license inventory gate.
Supply `publish_release_assets=true`, the exact reviewed source-license contract
SHA256, and the actual pre-created release tag/ID. The release must target the
same recipe commit as the dispatched workflow (`GITHUB_SHA`).


R6 original licence preflight and partial-seed policy

Before Cargo compilation, target-filtered metadata enumerates the conservative non-dev omsi-app dependency closure. Missing original texts are resolved only from the actual authored repository (or authored homepage when repository is absent), reported exact clean VCS commit, and an original Cargo.toml bound by raw byte identity or the exact CRLF-to-LF transform recorded below. GitHub metadata redirects stay on api.github.com and the final repository must be public. GitLab reads are public unauthenticated exact-commit tree/blob GETs; GitHub authorization is never forwarded. Missing path_in_vcs requires one unique matching authored-manifest Git blob in the complete original tree. Tree-URL suffixes are locator metadata, never branch-build inputs.

Actual standardized CC0 terms are included only when the verified original authored package and Cargo metadata both declare CC0-1.0. The staged SPDX text is 7,048 bytes/SHA256 a2010f343487d3f7618affe54f789f5487602331c0a8d03f49e9a7c547cf0499, from spdx/license-list-data commit 31ba1a50e5397e00a304dbadc76531740e89ee48, tree dc9f030bcb3744a0a351fad8eafb94015f4305fa, blob 0e259d42c996742e9e3cba14c677129b2c1b6311. It is explicitly standardized terms under the original declaration, not a claimed author-repository licence file. CT2 original and vendor notice contract is unchanged.

Unresolved packages are named before compilation and omitted from public export, together with every actual compiled non-dev transitive dependent. No corresponding source, native output, inspection files, target library/build executable, or fingerprint owned by an omitted package is exported. Ambiguous fingerprint candidates also require actual compilation and resolved licensing. The provenance fields unresolved_license_packages and license_omitted_actual_compiled_package_ids identify omissions; license_omission_policy requires normal private Cargo rebuilds. This is a partial dependency seed, not evidence that every dependency can be reused.

Native generated aliases may transition directly only to the actual compiled and licence-resolved ct2rs package root or cxx include/cxx.h. All physical package roots come from exact Cargo metadata manifest_path parents. Workspace, unknown, ambiguous nested owners and package-source symlinks remain refused. Original output logical paths remain unchanged; separate physical_source_owner records bind source owner/package root/selection/licence-info digest. Derivative inventory binds the identical included cargo-home corresponding-source path. Every directory membership/link identity and copied content is checked again. If any other alias remains unresolved, the native owner stays incomplete/data-only and its ready cache files/fingerprints are omitted.


Registry archive custody

Each exported licensed actual compiled registry owner includes exactly its corresponding cargo-home/registry/cache/<same source index>/<name>-<version>.crate. Original archive SHA256 must equal the exact reviewed official Cargo.lock checksum for the package name/version/source tuple. Package and licence inventory records expose registry_archive with its included path, actual pin, lock identity and checksum confirmation. No unrelated registry archives, global-cache database, Cargo settings, credentials, or workspace source are copied. Consumers validate the archive against their actual lock and may derive checksum/source integrity without assuming a legacy .cargo-checksum.json exists.

Metadata redirects may retain only the exact worker-generated observed nonce query. It is checked and removed before the next GET; that GET creates a fresh nonce. Other query keys/duplicate values, credentials, changed provider and non-metadata routes are refused.


Authored manifests may differ from the exact original Git blob only by the explicitly verified CRLF-to-LF byte transform. Both original Git blob/SHA256/bytes and unchanged archive authored-manifest SHA remain recorded; original_manifest_byte_identical stays false when raw bytes differ, with exact_crlf_to_lf_equal true. No general TOML, whitespace, name, version or licence relaxation applies. Explicit workspace-inherited repository/homepage declarations must resolve to an actual scalar from the closest original exact-commit workspace.package manifest and equal normalized Cargo metadata.


Matched original notice directories are read as bounded exact-commit ordinary trees, with at most 1,024 selected regular UTF-8 text descendants, depth32 and8MiB aggregate. Links, gitlinks and special entries remain refused; directory-scoped notice records identify the actual parent. Workspace inheritance selects one actual owning workspace from explicit package.workspace or the closest [workspace] boundary. Repository/homepage, version and licence all resolve from that same root; a missing field is unresolved rather than borrowed from a farther workspace.
