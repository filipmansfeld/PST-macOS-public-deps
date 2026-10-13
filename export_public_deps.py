#!/usr/bin/env python3
"""Export an external dependency seed from official public 0.2.44 only.

This file is a standalone public recipe. It imports no private repository module,
reads no credential store, and does not upload anything or execute the engine.
"""
import argparse
import datetime as dt
import hashlib
import http.client
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import urllib.parse
import uuid

OFFICIAL_COMMIT = "6c82e0771b0695c038c5278cb21894d2b9776c5a"
OFFICIAL_TREE = "977fdb2987349fb9fca0a87a43e17b77f83f2158"
OFFICIAL_LOCK_SHA = "0c5f7b13f20c89c8874611b5ae13663b0e7c093205b6a769b24b86cc36ebea73"
CARGO_VERSIONS = {"arm64": "cargo 1.98.1 (797e8a9bc 2026-08-05)",
                  "x86_64": "cargo 1.98.0 (797e8a9bc 2026-08-05)"}
RUST_RELEASES = {"arm64": "1.98.1", "x86_64": "1.98.0"}
RUST_COMMITS = {"arm64": "48a229ceaefd4985c50990b14116b6d856af0985",
                "x86_64": "88d9e12ae178fab0fb5cc050a94da85685d449ea"}
CARGO_LAYOUT_SOURCE = "https://github.com/rust-lang/cargo/blob/797e8a9bc/src/cargo/core/compiler/layout.rs"
PROFILE = {"CARGO_PROFILE_RELEASE_OPT_LEVEL": "3", "CARGO_PROFILE_RELEASE_DEBUG": "0",
           "CARGO_PROFILE_RELEASE_CODEGEN_UNITS": "32", "CARGO_PROFILE_RELEASE_LTO": "off"}
TARGETS = {"arm64": "aarch64-apple-darwin", "x86_64": "x86_64-apple-darwin"}
MAX_ARCHIVE = 2 * 1024 * 1024 * 1024 - 1


def need(value, message):
    if not value:
        raise RuntimeError(message)


def pin(path):
    need(path.is_absolute() and path.resolve(strict=True) == path, "Noncanonical or linked input/output.")
    before = path.lstat()
    need(stat.S_ISREG(before.st_mode), "Nonregular export input.")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    after = path.lstat()
    need((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
         == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns), "Input changed while reading.")
    return {"bytes": before.st_size, "sha256": digest.hexdigest(), "mtime_ns": before.st_mtime_ns}


def files(root):
    need(root.is_dir() and root.resolve(strict=True) == root, "Noncanonical input directory.")
    for base, dirs, names in os.walk(root, followlinks=False):
        # Cargo's fetched git control data is not corresponding source and is
        # never an input to the published dependency source/notice collection.
        dirs[:] = sorted(name for name in dirs if name != ".git")
        for name in dirs:
            need(not (Path(base) / name).is_symlink(), "Refuse a linked export directory.")
        for name in sorted(names):
            path = Path(base) / name
            if name == ".git":
                continue
            need(stat.S_ISREG(path.lstat().st_mode), "Refuse a symlink/special export file.")
            yield path


def save(path, data):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, sort_keys=True, indent=2)
        stream.write("\n")


class Exporter:
    def __init__(self, args):
        self.args = args
        self.official = Path(args.official).resolve(strict=True)
        self.root = Path(args.output).absolute()
        need(not self.root.exists() and self.root.parent.resolve(strict=True) == self.root.parent, "Preserve an existing/noncanonical seed root.")
        need(self.official not in self.root.parents and self.root not in self.official.parents, "Source and cache roots must be separate.")
        self.root.mkdir(mode=0o700)
        self.target = self.root / "target"
        self.home = self.root / "cargo-home"
        self.staging = self.root / "export"
        self.staging.mkdir()
        self.phases = []
        self.env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "RUSTUP_HOME", "SDKROOT", "DEVELOPER_DIR", "LANG") if key in os.environ}
        self.env.update(PROFILE)
        self.env.update({"CARGO_HOME": str(self.home), "CARGO_TARGET_DIR": str(self.target),
                         "CARGO_INCREMENTAL": "0", "CARGO_TERM_COLOR": "never", "GIT_TERMINAL_PROMPT": "0"})
        self.inventory = {}

    def run(self, name, argv):
        raw = self.root / (name + ".raw.log")
        phase = {"name": name, "argv": list(map(str, argv)), "cwd": str(self.official),
                 "started_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
        self.phases.append(phase)
        with raw.open("xb") as stream:
            child = subprocess.Popen(list(map(str, argv)), cwd=self.official, env=self.env,
                                     stdout=stream, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
            phase.update(pid=child.pid, pgid=child.pid)
            try:
                result = child.wait(timeout=2100)
            except BaseException:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=5)
                raise
        phase.update(exit_code=result, natural_exit=True, ended_utc=dt.datetime.now(dt.timezone.utc).isoformat(), raw=pin(raw))
        need(result == 0, "Public dependency build/read phase failed: " + name)
        return raw

    def text(self, name, argv):
        return self.run(name, argv).read_text(encoding="utf-8").strip()

    def copy(self, source, category, owner, base):
        need(source == source.resolve(strict=True) and base in source.parents, "Export leaf must stay inside its declared canonical root.")
        relative = source.relative_to(base)
        destination = self.staging / category / relative
        before = pin(source)
        if destination.exists():
            need(pin(destination)["sha256"] == before["sha256"], "Conflicting export owners/bytes.")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        actual = pin(destination)
        need(actual["sha256"] == before["sha256"] and actual["bytes"] == before["bytes"] and pin(source) == before,
             "Export copy is not byte-identical.")
        key = destination.relative_to(self.staging).as_posix()
        row = self.inventory.setdefault(key, {"path": key, **actual, "owners": []})
        if owner not in row["owners"]:
            row["owners"].append(owner)

    def tree(self, directory, category, owner, base):
        for path in files(directory):
            self.copy(path, category, owner, base)

    def native_output_plan(self, directory, owner):
        """Inspect only one exact external build output owner; never broaden it."""
        need(directory.resolve(strict=True) == directory and self.target in directory.parents,
             "Native output owner must be a canonical exact subtree of the fresh target.")
        plan = {"package_id": owner, "output_parent": str(directory), "files": [], "directories": [], "links": [], "omitted_links": []}
        directory_snapshots = {}
        entries = 0
        expanded_bytes = 0
        def identity(path):
            value = path.lstat()
            return {key: getattr(value, key) for key in ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")}
        def directory_members(path):
            return [{"name": child.name, "file_type": stat.S_IFMT(child.lstat().st_mode),
                     "identity": identity(child)} for child in sorted(path.iterdir())]
        def inside(path):
            return path == directory or directory in path.parents
        def resolve_owned(path):
            # Inspect every link hop inside this owner before following it. A
            # link may not escape and then re-enter via another outside link.
            pending = list(path.relative_to(directory).parts)
            current = directory
            hops = []
            seen = set()
            while pending:
                component = pending.pop(0)
                if component == "..":
                    current = current.parent
                    if not inside(current):
                        return None, hops, "outside_owner_parent_component", str(current)
                    continue
                if component == ".":
                    if not current.is_dir():
                        return None, hops, "non_directory_component", str(current)
                    continue
                current = current / component
                try:
                    value = current.lstat()
                except OSError:
                    return None, hops, "unresolved_target", str(current)
                if stat.S_ISLNK(value.st_mode):
                    raw = os.readlink(current)
                    # Preserve '..' until earlier link components are actually
                    # resolved; collapsing them first changes symlink semantics.
                    lexical = Path(raw) if Path(raw).is_absolute() else current.parent / raw
                    hop = {"path": str(current), "original_target": raw, "lexical_target": str(lexical), "identity": identity(current)}
                    hops.append(hop)
                    if not inside(lexical):
                        return None, hops, "outside_owner_link_hop", str(lexical)
                    state = (current, tuple(pending))
                    if state in seen or len(hops) > 64:
                        return None, hops, "link_chain_cycle_or_limit", str(lexical)
                    seen.add(state)
                    target_parts = list(lexical.relative_to(directory).parts)
                    if raw.endswith("/") or raw.endswith("/.") or raw == ".":
                        target_parts.append(".")
                    pending = target_parts + pending
                    current = directory
                elif pending and not stat.S_ISDIR(value.st_mode):
                    return None, hops, "non_directory_component", str(current)
            return current, hops, None, str(current)
        def walk(logical, actual, ancestors, refs, depth):
            nonlocal entries, expanded_bytes
            entries += 1
            need(entries <= 100000 and depth <= 64, "Native alias expansion exceeds its bounded entry/depth gate.")
            value = actual.lstat()
            if stat.S_ISLNK(value.st_mode):
                need(len(plan["links"]) < 1024, "Native link diagnostic inventory exceeds its bounded gate.")
                resolved, hops, reason, target = resolve_owned(actual)
                event = {"source_link_path": str(actual), "logical_link_path": str(logical),
                         "original_target": os.readlink(actual), "resolved_target": str(resolved) if resolved else None,
                         "diagnostic_target": target, "hops": hops, "target_type": None,
                         "omitted_reason": reason, "target_content_read": False}
                index = len(plan["links"])
                plan["links"].append(event)
                if reason:
                    plan["omitted_links"].append(event)
                    return
                if ".git" in resolved.relative_to(directory).parts:
                    event["omitted_reason"] = "git_control_target"
                    plan["omitted_links"].append(event)
                    return
                need(inside(resolved) and resolved.resolve(strict=True) == resolved,
                     "Resolved native link target is not inside the same canonical owner.")
                target_stat = resolved.lstat()
                if stat.S_ISDIR(target_stat.st_mode) and (target_stat.st_dev, target_stat.st_ino) in ancestors:
                    event["target_type"] = "directory"
                    event["omitted_reason"] = "directory_cycle_or_ancestor"
                    plan["omitted_links"].append(event)
                    return
                event["target_type"] = "directory" if stat.S_ISDIR(target_stat.st_mode) else "regular_file" if stat.S_ISREG(target_stat.st_mode) else "special"
                if event["target_type"] == "special":
                    event["omitted_reason"] = "special_link_target"
                    plan["omitted_links"].append(event)
                    return
                walk(logical, resolved, ancestors, refs + [index], depth + 1)
                event["target_content_read"] = True
                return
            need(actual.resolve(strict=True) == actual and inside(actual), "Native physical source escaped its exact owner.")
            if stat.S_ISDIR(value.st_mode):
                key = (value.st_dev, value.st_ino)
                need(key not in ancestors, "Unexpected native directory cycle.")
                snapshot = {"path": str(actual), "identity": identity(actual), "members": directory_members(actual)}
                if str(actual) in directory_snapshots:
                    need(directory_snapshots[str(actual)] == snapshot, "Native alias source directory changed between scans.")
                else:
                    directory_snapshots[str(actual)] = snapshot
                for child in sorted(actual.iterdir()):
                    if child.name == ".git":
                        continue
                    walk(logical / child.name, child, ancestors | {key}, refs, depth + 1)
                need(identity(actual) == snapshot["identity"] and directory_members(actual) == snapshot["members"],
                     "Native source directory membership changed during inventory.")
            else:
                need(stat.S_ISREG(value.st_mode), "Refuse a special native output file.")
                actual_pin = pin(actual)
                expanded_bytes += actual_pin["bytes"]
                need(expanded_bytes <= 4 * 1024 * 1024 * 1024, "Native alias expansion exceeds its bounded byte gate.")
                plan["files"].append({"logical_path": str(logical), "physical_source": str(actual),
                                      "source_pin": actual_pin, "followed_link_indexes": refs})
        walk(directory, directory, set(), [], 0)
        plan["directories"] = list(directory_snapshots.values())
        plan["native_directory_count"] = len(plan["directories"])
        plan["directory_membership_sha256"] = hashlib.sha256(json.dumps(plan["directories"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        for index, event in enumerate(plan["links"]):
            if event["omitted_reason"]:
                continue
            leaves = [{"logical_relative_path": Path(item["logical_path"]).relative_to(Path(event["logical_link_path"])).as_posix(),
                       "bytes": item["source_pin"]["bytes"], "sha256": item["source_pin"]["sha256"]}
                      for item in plan["files"] if index in item["followed_link_indexes"]]
            if event["target_type"] == "directory" and not leaves:
                # The file-only archive cannot represent an empty directory
                # alias; do not label that missing logical alias cache-ready.
                event["omitted_reason"] = "empty_directory_link_not_materialized"
                plan["omitted_links"].append(event)
            event["copied_target_content_scope"] = "selected_safe_regular_descendants_only"
            event["copied_target_file_count"] = len(leaves)
            event["copied_target_bytes"] = sum(item["bytes"] for item in leaves)
            event["copied_target_content_sha256"] = hashlib.sha256(json.dumps(leaves, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            if event["target_type"] == "regular_file":
                need(len(leaves) == 1, "A native regular-file link must bind one exact content pin.")
                event["resolved_target_pin"] = {key: leaves[0][key] for key in ("bytes", "sha256")}
        plan["cache_complete"] = not plan["omitted_links"]
        plan["native_regular_file_count"] = len(plan["files"])
        if plan["links"]:
            print(json.dumps({"phase": "native-output-scan", "package_id": owner, "output_parent": str(directory),
                              "cache_complete": plan["cache_complete"], "link_count": len(plan["links"]),
                              "omitted_link_count": len(plan["omitted_links"]),
                              "first_links": [{key: event[key] for key in ("source_link_path", "logical_link_path", "original_target",
                                                "resolved_target", "diagnostic_target", "target_type", "omitted_reason")}
                                              for event in plan["links"][:16]]}, sort_keys=True), flush=True)
        return plan

    def copy_native_output_plan(self, plan, category):
        owner_root = Path(plan["output_parent"])
        owner = plan["package_id"]
        need(category in {"target", "inspection-native-output"}, "Unknown native export category.")
        def verify_directories():
            for snapshot in plan["directories"]:
                directory = Path(snapshot["path"])
                need((directory == owner_root or owner_root in directory.parents) and directory.resolve(strict=True) == directory,
                     "Native source directory escaped its exact owner after inventory.")
                value = directory.lstat()
                members = [{"name": child.name, "file_type": stat.S_IFMT(child.lstat().st_mode),
                            "identity": {key: getattr(child.lstat(), key) for key in snapshot["identity"]}}
                           for child in sorted(directory.iterdir())]
                need({key: getattr(value, key) for key in snapshot["identity"]} == snapshot["identity"] and members == snapshot["members"],
                     "Native source directory identity/membership changed after inventory.")
        verify_directories()
        for event in plan["links"]:
            for hop in event["hops"]:
                path = Path(hop["path"])
                value = path.lstat()
                need({key: getattr(value, key) for key in hop["identity"]} == hop["identity"]
                     and os.readlink(path) == hop["original_target"], "Native source link changed after inventory.")
        for item in plan["files"]:
            logical = Path(item["logical_path"])
            actual = Path(item["physical_source"])
            need(owner_root in logical.parents and owner_root in actual.parents
                 and actual.resolve(strict=True) == actual and pin(actual) == item["source_pin"],
                 "Native derivative source changed or escaped its exact owner.")
            destination = self.staging / category / logical.relative_to(self.target)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                need(pin(destination)["sha256"] == item["source_pin"]["sha256"], "Conflicting native derivative bytes.")
            else:
                shutil.copy2(actual, destination)
            copied = pin(destination)
            need(copied == item["source_pin"] and pin(actual) == item["source_pin"],
                 "Native ordinary derivative is not an unchanged byte/mtime copy.")
            key = destination.relative_to(self.staging).as_posix()
            row = self.inventory.setdefault(key, {"path": key, **copied, "owners": []})
            if owner not in row["owners"]:
                row["owners"].append(owner)
            row.setdefault("native_output_derivatives", []).append({"owner_output_parent": str(owner_root),
                       "physical_source": str(actual), "followed_link_indexes": item["followed_link_indexes"],
                       "ordinary_copy": True, "cache_import_eligible": category == "target"})
        for event in plan["links"]:
            for hop in event["hops"]:
                path = Path(hop["path"])
                value = path.lstat()
                need({key: getattr(value, key) for key in hop["identity"]} == hop["identity"]
                     and os.readlink(path) == hop["original_target"], "Native source link changed during its derivative copies.")
        verify_directories()

    def build(self):
        need(self.text("official-commit", ["git", "rev-parse", "HEAD"]) == OFFICIAL_COMMIT, "Official source commit mismatch.")
        need(self.text("official-tree", ["git", "rev-parse", "HEAD^{tree}"]) == OFFICIAL_TREE, "Official source tree mismatch.")
        need(not self.text("official-clean", ["git", "status", "--porcelain", "--untracked-files=no"]), "Official source is modified.")
        cargo = self.text("cargo-version", ["cargo", "--version"])
        need(cargo == CARGO_VERSIONS[self.args.architecture], "Refuse an unreviewed Cargo internal layout version for this architecture.")
        rustc = self.text("rustc-version", ["rustc", "-vV"])
        rust_fields = dict(line.split(": ", 1) for line in rustc.splitlines()[1:] if ": " in line)
        need(rust_fields.get("release") == RUST_RELEASES[self.args.architecture]
             and rust_fields.get("commit-hash") == RUST_COMMITS[self.args.architecture]
             and rust_fields.get("host") == self.args.target,
             "Native Rust toolchain differs from the exact reviewed architecture contract.")
        os_info = self.text("os-version", ["/usr/bin/sw_vers"])
        sdk = self.text("sdk-version", ["/usr/bin/xcrun", "--show-sdk-version"])
        clang = self.text("clang-version", ["/usr/bin/clang", "--version"])
        cmake = self.text("cmake-version", ["cmake", "--version"])
        lock_before = pin(self.official / "Cargo.lock")
        need(lock_before["sha256"] == OFFICIAL_LOCK_SHA, "Exact reviewed official Cargo.lock mismatch.")
        contract_path = Path(__file__).resolve().parent / "public-license-provenance.json"
        contract_pin = pin(contract_path)
        contract = json.loads(contract_path.read_text())
        need(contract["official"]["commit"] == OFFICIAL_COMMIT and contract["official"]["tree"] == OFFICIAL_TREE
             and contract["official"]["cargo_lock_sha256"] == OFFICIAL_LOCK_SHA, "Public source/notice contract mismatch.")
        if self.args.publish_requested == "true":
            need(self.args.approved_source_license_contract == contract_pin["sha256"], "Publishing needs the reviewed exact source/notice contract pin.")
        for notice in contract["licenses"]:
            path = Path(__file__).resolve().parent / notice["path"]
            actual = pin(path)
            need(actual["bytes"] == notice["bytes"] and actual["sha256"] == notice["sha256"], "A staged exact public notice changed.")
        metadata_raw = self.run("cargo-metadata", ["cargo", "metadata", "--locked", "--format-version", "1"]).read_text()
        metadata_values = [json.loads(line) for line in metadata_raw.splitlines() if line.startswith("{")]
        need(len(metadata_values) == 1, "Require one actual metadata document, separate from registry diagnostics.")
        meta = metadata_values[0]
        packages = {item["id"]: item for item in meta["packages"]}
        workspace = set(meta["workspace_members"])
        allowed = {key: item for key, item in packages.items() if key not in workspace and item["source"] is not None
                   and item["source"].startswith(("registry+", "sparse+", "git+"))}
        by_name = {}
        for key, item in packages.items():
            by_name.setdefault(item["name"], []).append(key)
        raw = self.run("cargo-official-release", ["cargo", "build", "--locked", "--release", "-p", "omsi-app", "--bin", "openomsi",
                                                  "--target", self.args.target, "--message-format=json-render-diagnostics"])
        messages = [json.loads(line) for line in raw.read_text().splitlines() if line.startswith("{")]
        need(any(item.get("reason") == "build-finished" and item.get("success") is True for item in messages), "Natural build completion missing.")
        native_plans = {}
        incomplete = set()
        for message in messages:
            if message.get("reason") != "build-script-executed" or message["package_id"] not in allowed:
                continue
            package_id = message["package_id"]
            package = allowed[package_id]
            output = Path(message["out_dir"])
            need(self.target in output.parents and output.name == "out"
                 and re.fullmatch(re.escape(package["name"]) + r"-[0-9a-f]{16}", output.parent.name), "Unproven external build OUT_DIR.")
            key = (package_id, str(output.parent))
            if key not in native_plans:
                native_plans[key] = self.native_output_plan(output.parent, package_id)
            if not native_plans[key]["cache_complete"]:
                incomplete.add(package_id)
        compiled = set()
        excluded = []
        omitted_artifacts = []
        omitted_fingerprints = []
        for message in messages:
            if message.get("reason") not in {"compiler-artifact", "build-script-executed"}:
                continue
            package_id = message["package_id"]
            need(package_id in packages, "Unresolved actual compiler package identity.")
            if package_id not in allowed:
                excluded.append(package_id)
                continue
            compiled.add(package_id)
            package = allowed[package_id]
            if message["reason"] == "build-script-executed":
                continue
            if package_id in incomplete:
                omitted_artifacts.append({"package_id": package_id, "filenames": message["filenames"],
                                          "reason": "native_owner_cache_incomplete_normal_rebuild_required"})
                continue
            kind = set(message["target"]["kind"])
            need(message["profile"].get("test") is False and not kind.intersection({"bin", "example", "test", "bench"}), "Do not export executable/test workspace products.")
            need(kind <= {"lib", "rlib", "staticlib", "cdylib", "dylib", "proc-macro", "custom-build"}, "Unknown external target kind.")
            for filename in message["filenames"]:
                path = Path(filename)
                need(self.target in path.parents, "Compiler output escaped the fresh target.")
                if "custom-build" in kind:
                    need(path.parent.parent.name == "build" and re.fullmatch(re.escape(package["name"]) + r"-[0-9a-f]{16}", path.parent.name), "Unknown dependency build-tool directory.")
                    if (package_id, str(path.parent)) not in native_plans:
                        self.tree(path.parent, "target", package_id, self.target)
                else:
                    need(path.parent.name == "deps", "External library must be an exact reported deps leaf.")
                    self.copy(path, "target", package_id, self.target)
                    match = re.fullmatch(r"lib" + re.escape(message["target"]["name"]) + r"-([0-9a-f]{16})\.(rlib|rmeta|dylib|so|a)", path.name)
                    if match:
                        # Output companions share rustc's exact extra-filename;
                        # this hash is NOT used to infer fingerprint ownership.
                        for companion in (path.parent / (message["target"]["name"] + "-" + match[1] + ".d"),
                                          path.parent / ("lib" + message["target"]["name"] + "-" + match[1] + ".rmeta")):
                            if companion.exists():
                                self.copy(companion, "target", package_id, self.target)
        native_owners = []
        for plan in native_plans.values():
            plan["cache_complete"] = plan["package_id"] not in incomplete
            plan["export_category"] = "target" if plan["cache_complete"] else "inspection-native-output"
            self.copy_native_output_plan(plan, plan["export_category"])
            native_owners.append({key: value for key, value in plan.items() if key not in {"files", "directories"}})
        need(compiled, "Expected real external Rust dependency closure missing.")
        if self.args.architecture == "arm64":
            need(any(allowed[key]["name"] == "ct2rs" for key in compiled), "Expected real ARM ct2rs dependency closure missing.")
        # Cargo797e8a9bc's legacy layout explicitly uses $pkgname-$META. Do not
        # equate META with artifact hashes. Exact-name candidates are exhaustive;
        # reject ANY name shared with a workspace/path package.
        names = {allowed[key]["name"] for key in compiled}
        for release in (self.target / "release", self.target / self.args.target / "release"):
            fingerprints = release / ".fingerprint"
            need(fingerprints.is_dir(), "Unsupported actual Cargo fingerprint layout.")
            for directory in sorted(fingerprints.iterdir()):
                match = re.fullmatch(r"(.+)-([0-9a-f]{16})", directory.name)
                if not match or match[1] not in names:
                    continue
                candidates = by_name[match[1]]
                need(all(key in allowed for key in candidates), "Fingerprint name collides with an excluded workspace/path package.")
                if any(key in incomplete for key in candidates):
                    omitted_fingerprints.append({"path": str(directory), "external_package_candidates": candidates,
                                                 "reason": "native_owner_cache_incomplete_normal_rebuild_required"})
                    continue
                self.tree(directory, "target", {"external_package_candidates": candidates, "ownership": "verified Cargo-version exact package-name directory"}, self.target)
        source_rows = []
        native_builds = [{"package_id": item["package_id"], "out_dir": item["out_dir"],
                          "linked_libs": item.get("linked_libs", []), "linked_paths": item.get("linked_paths", []),
                          "cache_import_eligible": item["package_id"] not in incomplete}
                         for item in messages if item.get("reason") == "build-script-executed" and item["package_id"] in compiled]
        for package_id in sorted(compiled):
            package = allowed[package_id]
            root = Path(package["manifest_path"]).parent
            need(self.home in root.parents, "External package source must originate in the fresh scoped Cargo home.")
            source_root = root
            if package["source"].startswith("git+"):
                # Inherited Git-workspace licenses/build inputs can live above
                # an individual dependency crate. Keep the whole PUBLIC fetched
                # checkout's corresponding source, excluding its Git control data.
                candidates = [ancestor for ancestor in (root, *root.parents)
                              if self.home in ancestor.parents and (ancestor / ".git").exists()]
                need(candidates, "Fetched external Git source root is unresolved.")
                source_root = candidates[0]
            license_files = [path for path in files(source_root) if re.search(r"(^|[-_.])(licen[cs]e|copying|notice)([-_.]|$)", path.name, re.I)]
            if package["name"] == "ct2rs":
                need(package["version"] == "0.10.1", "Unreviewed ct2rs license fallback version.")
                vcs = json.loads((root / ".cargo_vcs_info.json").read_text())
                need(vcs["git"]["sha1"] == "2d2bc51c5c3f10ba91ade120414c0fb4ed196051", "ct2rs source/license origin changed.")
                fallback = Path(__file__).resolve().parent / "public-licenses/ct2rs-0.10.1/WORKSPACE-LICENSE"
                fallback_pin = pin(fallback)
                need(fallback_pin["bytes"] == 1087 and fallback_pin["sha256"] == "ed39d4eb1b651d31041f1dde23f4fa4a19a7ab87ebfc6858b3abfd47f66cb557",
                     "The exact original public ct2rs workspace MIT text differs.")
                self.copy(fallback, "", package_id, Path(__file__).resolve().parent)
                for notice in contract["licenses"]:
                    if notice.get("crate_relative_path"):
                        actual_vendor = pin(root / notice["crate_relative_path"])
                        need(actual_vendor["sha256"] == notice["sha256"] and actual_vendor["bytes"] == notice["bytes"],
                             "ct2rs native vendor notice differs from its exact reviewed public source pin.")
            need(package.get("license") or package.get("license_file"), "Package licensing metadata is unresolved.")
            # Copy complete fetched package source, including bundled native
            # notices/corresponding source, not merely the crate SPDX string.
            need(license_files or package["name"] == "ct2rs", "No actual license/notice text found for an exported package; manual resolution required.")
            self.tree(source_root, "cargo-home", package_id, self.home)
            source_rows.append({"package_id": package_id, "name": package["name"], "version": package["version"],
                                "source": package["source"], "license": package.get("license"),
                                "original_source_root": str(source_root),
                                "license_files": [{"path": path.relative_to(source_root).as_posix(),
                                                   "sha256": pin(path)["sha256"]} for path in license_files],
                                "additional_workspace_license": ({"path": "public-licenses/ct2rs-0.10.1/WORKSPACE-LICENSE",
                                    "sha256": "ed39d4eb1b651d31041f1dde23f4fa4a19a7ab87ebfc6858b3abfd47f66cb557",
                                    "source_url": "https://raw.githubusercontent.com/jkawamoto/ctranslate2-rs/2d2bc51c5c3f10ba91ade120414c0fb4ed196051/LICENSE"}
                                    if package["name"] == "ct2rs" else None)})
        license_inventory = {"official_commit": OFFICIAL_COMMIT, "official_tree": OFFICIAL_TREE,
                             "cargo_lock_sha256": lock_before["sha256"],
                             "packages": [{key: row[key] for key in ("package_id", "name", "version", "source", "license", "license_files", "additional_workspace_license")}
                                          for row in source_rows]}
        license_sha = hashlib.sha256(json.dumps(license_inventory, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        need(pin(self.official / "Cargo.lock") == lock_before
             and not self.text("official-still-clean", ["git", "status", "--porcelain", "--untracked-files=no"]), "Official source changed.")
        receipt = {"schema": "official44-external-macos-dependency-seed-v1", "status": "actual_external_dependency_export_completed",
                   "official": {"commit": OFFICIAL_COMMIT, "tree": OFFICIAL_TREE, "cargo_lock": lock_before},
                   "toolchain": {"cargo": cargo, "rustc": rustc, "sdk": sdk, "clang": clang, "cmake": cmake, "os": os_info},
                   "architecture": self.args.architecture, "target": self.args.target, "profile_env": PROFILE,
                   "canonical_origin": {"workspace": str(self.official), "cargo_home": str(self.home), "target": str(self.target)},
                   "layout_ownership_primary_source": CARGO_LAYOUT_SOURCE, "packages": source_rows,
                   "workspace_and_path_package_ids_excluded": sorted(set(excluded)), "inventory": list(self.inventory.values()),
                   "phases": self.phases, "engine_exported": False, "engine_executed": False, "private_source_used": False,
                   "dotnet_used": False, "runtime_verified": False, "speedup_verified": False,
                   "consumer_must_preserve_mtimes_and_check_source_toolchain_profile_paths": True,
                   "consumer_must_not_rewrite_fingerprints_or_force_workspace_freshness": True,
                   "license_inventory": license_inventory, "license_inventory_sha256": license_sha,
                   "source_license_contract_sha256": contract_pin["sha256"], "actual_external_native_builds": native_builds,
                   "native_cache_complete": not incomplete, "native_cache_incomplete_package_ids": sorted(incomplete),
                   "native_output_owners": native_owners, "non_cache_inspection_prefix": "inspection-native-output/",
                   "incomplete_package_target_artifacts_omitted": omitted_artifacts,
                   "incomplete_package_fingerprints_omitted": omitted_fingerprints,
                   "seed_scope": "partial_external_dependency_material_normal_Cargo_freshness_rules_apply",
                   "notice_verification_scope": "actual_exported_sources_and_notice_texts_not_omitted_links_or_cache_completeness",
                   "automatic_source_and_native_notice_inventory_verified": True,
                   "source_license_contract_approved_for_publication": self.args.publish_requested == "true"}
        save(self.staging / "provenance.json", receipt)
        delivery = self.root / "delivery"
        delivery.mkdir()
        archive = delivery / ("deps-" + self.args.architecture + ".tar.gz")
        pending = archive.with_suffix(".gz.pending")
        with tarfile.open(pending, "x:gz", compresslevel=6, format=tarfile.PAX_FORMAT) as tar:
            for path in files(self.staging):
                tar.add(path, arcname=path.relative_to(self.staging).as_posix(), recursive=False)
        need(pending.stat().st_size <= MAX_ARCHIVE, "Archive is not strictly below the public release 2GiB per-file limit.")
        pending.rename(archive)
        actual = pin(archive)
        summary = {"schema": "official44-public-dependency-delivery-v1", "archive": actual, "archive_name": archive.name,
                   "official_commit": OFFICIAL_COMMIT, "official_tree": OFFICIAL_TREE,
                   "target": self.args.target, "profile_env": PROFILE, "toolchain": receipt["toolchain"],
                   "exported_file_count": len(self.inventory), "workspace_artifacts_exported": False,
                   "runtime_verified": False, "license_inventory_sha256": license_sha,
                   "license_inventory": license_inventory,
                   "source_license_contract_sha256": contract_pin["sha256"],
                   "native_cache_complete": not incomplete, "native_cache_incomplete_package_ids": sorted(incomplete),
                   "native_output_owners": native_owners, "non_cache_inspection_prefix": "inspection-native-output/",
                   "seed_scope": "partial_external_dependency_material_normal_Cargo_freshness_rules_apply",
                   "notice_verification_scope": "actual_exported_sources_and_notice_texts_not_omitted_links_or_cache_completeness",
                   "automatic_source_and_native_notice_inventory_verified": True,
                   "source_license_contract_approved_for_publication": self.args.publish_requested == "true"}
        save(delivery / ("deps-" + self.args.architecture + ".provenance.json"), summary)
        (delivery / ("deps-" + self.args.architecture + ".sha256")).write_text(actual["sha256"] + "  " + archive.name + "\n")
        print(json.dumps(summary, sort_keys=True))


def publish_release(args):
    """Explicit future asset upload only: pending -> one mutation -> GET readback.

    Never create releases, repeat uncertain uploads, or overwrite an asset.
    Authorization is sent only to GitHub's API/upload hosts for this exact repo.
    """
    repo = "filipmansfeld/PST-macOS-public-deps"
    need(os.environ.get("GITHUB_REPOSITORY") == repo and re.fullmatch(r"[0-9a-f]{40}", args.recipe_commit)
         and re.fullmatch(r"official44-deps-opt3-debug0-cgu32-lto-off-[a-z0-9-]+", args.release_tag)
         and args.release_id.isdecimal() and int(args.release_id) > 0, "Publication scope mismatch.")
    directory = Path(args.delivery).resolve(strict=True)
    name = "deps-" + args.architecture
    asset_paths = [directory / (name + suffix) for suffix in (".tar.gz", ".provenance.json", ".sha256")]
    summary = json.loads(asset_paths[1].read_text())
    actual_archive = pin(asset_paths[0])
    need(summary["source_license_contract_approved_for_publication"] is True
         and summary["automatic_source_and_native_notice_inventory_verified"] is True
         and summary["official_commit"] == OFFICIAL_COMMIT and summary["official_tree"] == OFFICIAL_TREE
         and summary["archive"]["bytes"] == actual_archive["bytes"]
         and summary["archive"]["sha256"] == actual_archive["sha256"]
         and actual_archive["bytes"] <= MAX_ARCHIVE, "Only actual reviewed bounded dependency archives may publish.")
    token = os.environ["GH_TOKEN"]
    def request(method, path, payload=None, upload=None):
        host = "uploads.github.com" if upload else "api.github.com"
        connection = http.client.HTTPSConnection(host, timeout=180)
        try:
            headers = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                       "X-GitHub-Api-Version": "2026-03-10", "User-Agent": "official44-public-deps-recipe", "Cache-Control": "no-cache"}
            if upload:
                connection.putrequest(method, path)
                for key, value in headers.items(): connection.putheader(key, value)
                connection.putheader("Content-Type", "application/octet-stream")
                connection.putheader("Content-Length", str(upload.stat().st_size))
                connection.endheaders()
                with upload.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""): connection.send(block)
            else:
                data = json.dumps(payload).encode() if payload is not None else None
                if data is not None: headers["Content-Type"] = "application/json"
                connection.request(method, path, body=data, headers=headers)
            response = connection.getresponse()
            body = response.read(4 * 1024 * 1024)
            return response.status, json.loads(body) if body else None
        finally:
            connection.close()
    prefix = "/repos/" + repo
    status, repository = request("GET", prefix + "?observed=" + uuid.uuid4().hex)
    need(status == 200 and repository["private"] is False and repository["full_name"] == repo, "Confirm the exact public repository before writing.")
    status, release = request("GET", prefix + "/releases/" + args.release_id + "?observed=" + uuid.uuid4().hex)
    need(status == 200 and release["id"] == int(args.release_id) and release["tag_name"] == args.release_tag
         and release["target_commitish"] == args.recipe_commit and release["draft"] is False,
         "Exact pre-created public release readback unconfirmed; do not create any release.")
    release_id = release["id"]
    for asset in asset_paths:
        current = pin(asset)
        status, listing = request("GET", prefix + f"/releases/{release_id}/assets?per_page=100&observed=" + uuid.uuid4().hex)
        need(status == 200 and len(listing) < 100, "Complete bounded asset readback required.")
        existing = [item for item in listing if item["name"] == asset.name]
        if existing:
            need(len(existing) == 1 and existing[0].get("digest") == "sha256:" + current["sha256"]
                 and existing[0]["size"] == current["bytes"], "Existing asset differs or digest unavailable; never overwrite it.")
            continue
        pending = directory / (asset.name + ".upload.pending.json")
        need(not pending.exists(), "Reconcile a pending upload; do not repeat it blindly.")
        save(pending, {"release_id": release_id, "asset": asset.name, "pin": current, "upload_pending": True})
        try:
            request("POST", prefix + f"/releases/{release_id}/assets?name=" + urllib.parse.quote(asset.name, safe=""), upload=asset)
        except (OSError, http.client.HTTPException):
            pass
        status, listing = request("GET", prefix + f"/releases/{release_id}/assets?per_page=100&observed=" + uuid.uuid4().hex)
        matches = [item for item in listing if item["name"] == asset.name] if status == 200 else []
        need(len(matches) == 1 and matches[0].get("digest") == "sha256:" + current["sha256"]
             and matches[0]["size"] == current["bytes"], "Actual upload outcome unconfirmed; stop without another upload.")
    print(json.dumps({"status": "actual_dependency_assets_readback_confirmed", "repository": repo,
                      "tag": args.release_tag, "engine_or_private_source_published": False}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--official")
    parser.add_argument("--output")
    parser.add_argument("--architecture", choices=sorted(TARGETS), required=True)
    parser.add_argument("--target")
    parser.add_argument("--publish-requested", choices=("false", "true"), default="false")
    parser.add_argument("--approved-source-license-contract", default="")
    parser.add_argument("--publish-release", action="store_true")
    parser.add_argument("--delivery")
    parser.add_argument("--release-tag")
    parser.add_argument("--release-id", default="")
    parser.add_argument("--recipe-commit")
    args = parser.parse_args()
    if args.publish_release:
        publish_release(args)
        return
    need(args.official and args.output and args.target, "Explicit official source/output/native target required.")
    need(sys.platform == "darwin" and platform.machine() == args.architecture and TARGETS[args.architecture] == args.target,
         "Native standard Mac target only.")
    need(os.environ.get("GITHUB_REPOSITORY") == "filipmansfeld/PST-macOS-public-deps", "Only the proposed public recipe repository is allowed.")
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    need(event["repository"]["private"] is False and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch", "Manual public-repository execution only.")
    Exporter(args).build()


if __name__ == "__main__":
    main()
