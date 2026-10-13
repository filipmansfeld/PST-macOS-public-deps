#!/usr/bin/env python3
"""Export an external dependency seed from official public 0.2.44 only.

This file is a standalone public recipe. It imports no private repository module,
reads no credential store, and does not upload anything or execute the engine.
"""
import argparse
import base64
import datetime as dt
import hashlib
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tomllib
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
        def public_source_owner(path):
            matches = [value for value in getattr(self, "native_public_source_owners", [])
                       if path == Path(value["selected_path"]) or value["allow_descendants"] and Path(value["selected_path"]) in path.parents]
            if len(matches) != 1:
                return None
            selected = matches[0]
            roots = [value for value in getattr(self, "native_all_package_roots", [])
                     if path == Path(value["source_root"]) or Path(value["source_root"]) in path.parents]
            if not roots:
                return None
            deepest = max(len(Path(value["source_root"]).parts) for value in roots)
            exact = [value for value in roots if len(Path(value["source_root"]).parts) == deepest]
            return selected if len(exact) == 1 and exact[0]["package_id"] == selected["package_id"] else None
        def physical_owned(path):
            return inside(path) or public_source_owner(path) is not None
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
                        # One direct transition to a reviewed public package path;
                        # package source itself remains strict no-links throughout.
                        candidate = lexical.joinpath(*pending)
                        source_owner = public_source_owner(candidate)
                        if source_owner is None:
                            return None, hops, "outside_owner_unlicensed_or_unknown_public_source", str(candidate)
                        try:
                            resolved_candidate = candidate.resolve(strict=True)
                            candidate_stat = candidate.lstat()
                        except (OSError, RuntimeError):
                            return None, hops, "unresolved_public_source_target", str(candidate)
                        if ".." in candidate.parts or resolved_candidate != candidate:
                            return None, hops, "noncanonical_or_linked_public_source_target", str(candidate)
                        if not pending and (raw.endswith("/") or raw.endswith("/.") or raw == ".") and not stat.S_ISDIR(candidate_stat.st_mode):
                            return None, hops, "non_directory_public_source_component", str(candidate)
                        hop["public_source_owner"] = source_owner
                        return candidate, hops, None, str(candidate)
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
                need(inside(actual), "Public package source traversal must not follow source-side links.")
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
                physical_root = directory if inside(resolved) else Path(public_source_owner(resolved)["source_root"])
                if ".git" in resolved.relative_to(physical_root).parts:
                    event["omitted_reason"] = "git_control_target"
                    plan["omitted_links"].append(event)
                    return
                need(physical_owned(resolved) and resolved.resolve(strict=True) == resolved,
                     "Resolved native target escaped its exact output or licensed public package owner.")
                event["physical_source_owner"] = public_source_owner(resolved)
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
            need(actual.resolve(strict=True) == actual and physical_owned(actual), "Native physical source escaped its exact verified owner.")
            if stat.S_ISDIR(value.st_mode):
                key = (value.st_dev, value.st_ino)
                need(key not in ancestors, "Unexpected native directory cycle.")
                snapshot = {"path": str(actual), "identity": identity(actual), "members": directory_members(actual),
                            "physical_source_owner": public_source_owner(actual)}
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
                                      "source_pin": actual_pin, "followed_link_indexes": refs,
                                      "physical_source_owner": public_source_owner(actual)})
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
        def physical_owned(path, source_owner):
            if source_owner is None:
                return path == owner_root or owner_root in path.parents
            need(source_owner in getattr(self, "native_public_source_owners", []), "Native public source owner proof changed.")
            selected = Path(source_owner["selected_path"])
            return path == selected or source_owner["allow_descendants"] and selected in path.parents
        def verify_directories():
            for snapshot in plan["directories"]:
                directory = Path(snapshot["path"])
                need(physical_owned(directory, snapshot["physical_source_owner"]) and directory.resolve(strict=True) == directory,
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
            need(owner_root in logical.parents and physical_owned(actual, item["physical_source_owner"])
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
            if item["physical_source_owner"] and item["physical_source_owner"]["package_id"] not in row["owners"]:
                row["owners"].append(item["physical_source_owner"]["package_id"])
            row.setdefault("native_output_derivatives", []).append({"owner_output_parent": str(owner_root),
                       "physical_source": str(actual), "physical_source_owner": item["physical_source_owner"],
                       "corresponding_source_path": ("cargo-home/" + actual.relative_to(self.home).as_posix() if item["physical_source_owner"] else None),
                       "followed_link_indexes": item["followed_link_indexes"],
                       "ordinary_copy": True, "cache_import_eligible": category == "target"})
        for event in plan["links"]:
            for hop in event["hops"]:
                path = Path(hop["path"])
                value = path.lstat()
                need({key: getattr(value, key) for key in hop["identity"]} == hop["identity"]
                     and os.readlink(path) == hop["original_target"], "Native source link changed during its derivative copies.")
        verify_directories()

    def app_non_dev_closure(self, meta, packages):
        roots = [key for key, package in packages.items() if key in meta["workspace_members"]
                 and package["name"] == "omsi-app"
                 and Path(package["manifest_path"]) == self.official / "crates/omsi-app/Cargo.toml"]
        need(len(roots) == 1, "Require exact official workspace omsi-app metadata identity.")
        nodes = {node["id"]: node for node in meta["resolve"]["nodes"]}
        reached = set()
        pending = [roots[0]]
        while pending:
            key = pending.pop()
            if key in reached:
                continue
            need(key in packages and key in nodes, "Unresolved exact metadata dependency identity: " + key)
            reached.add(key)
            for edge in nodes[key]["deps"]:
                kinds = edge["dep_kinds"]
                need(kinds and all(item["kind"] in {None, "build", "dev"} for item in kinds), "Unknown metadata dependency kind.")
                if any(item["kind"] in {None, "build"} for item in kinds):
                    pending.append(edge["pkg"])
        return roots[0], reached

    def public_source_get(self, host, path, metadata_redirect=False):
        """Bounded public GET only; GitHub auth never crosses to GitLab/other hosts."""
        need(host in {"api.github.com", "gitlab.com"} and path.startswith("/") and "\0" not in path,
             "Unapproved original-public-source API host/path.")
        if not hasattr(self, "license_api_cache"):
            self.license_api_cache = {}
        cache_key = (host, path)
        if cache_key in self.license_api_cache:
            return self.license_api_cache[cache_key]
        redirects = []
        for attempt in range(5):
            connection = http.client.HTTPSConnection(host, timeout=30)
            try:
                headers = {"Accept": "application/json", "User-Agent": "official44-original-source-license-preflight", "Cache-Control": "no-cache"}
                if host == "api.github.com":
                    headers.update({"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10"})
                    token = os.environ.get("LICENSE_GITHUB_TOKEN")
                    if token:
                        headers["Authorization"] = "Bearer " + token
                observed_nonce = uuid.uuid4().hex
                connection.request("GET", path + ("&" if "?" in path else "?") + "observed=" + observed_nonce, headers=headers)
                response = connection.getresponse()
                body = response.read(8 * 1024 * 1024 + 1)
                need(len(body) <= 8 * 1024 * 1024, "Original public-source API response exceeds bounded gate.")
                if response.status in {301, 302, 307, 308}:
                    need(metadata_redirect, "Redirect on immutable original source object refused.")
                    location = urllib.parse.urlsplit(urllib.parse.urljoin("https://" + host + path, response.getheader("Location") or ""))
                    need(location.scheme == "https" and location.hostname == host and location.port is None
                         and not location.username and not location.password and not location.fragment,
                         "Original repository metadata redirect leaves the same exact public provider.")
                    redirect_query = urllib.parse.parse_qsl(location.query, keep_blank_values=True, strict_parsing=True)
                    need(not redirect_query or redirect_query == [("observed", observed_nonce)],
                         "Original metadata redirect query is not our one exact observed nonce.")
                    predicate = (r"/(?:repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+|repositories/[0-9]+)" if host == "api.github.com"
                                 else r"/api/v4/projects/(?:[0-9]+|[A-Za-z0-9_.%/-]+)")
                    need(re.fullmatch(predicate, location.path), "Unexpected repository metadata redirect route.")
                    redirects.append({"from_path": path, "to_path": location.path, "status": response.status})
                    path = location.path
                    continue
                need(response.status == 200, "Original public-source API read unconfirmed (HTTP " + str(response.status) + ").")
                result = (json.loads(body), {"final_path": path, "redirects": redirects, "next_page": response.getheader("X-Next-Page") or ""})
                self.license_api_cache[cache_key] = result
                return result
            finally:
                connection.close()
        raise RuntimeError("Original public repository metadata redirect limit exceeded.")

    def original_license_fallback(self, package_id, package, root):
        """Actual original texts bound to the exact authored manifest and public VCS commit."""
        vcs_path = root / ".cargo_vcs_info.json"
        vcs = json.loads(vcs_path.read_text(encoding="utf-8"))
        commit = vcs["git"]["sha1"]
        need(re.fullmatch(r"[0-9a-f]{40}", commit) and vcs["git"].get("dirty", False) is False,
             "Original licence fallback needs an exact non-dirty reported VCS commit.")
        authored_manifest = root / "Cargo.toml.orig"
        authored_pin = pin(authored_manifest)
        authored_bytes = authored_manifest.read_bytes()
        authored = tomllib.loads(authored_bytes.decode())["package"]
        # Repository/homepage is an authored locator, never a replacement for VCS/manifest identity.
        locator_role = "repository" if package.get("repository") else "homepage"
        locator = package.get(locator_role) or ""
        locator_declaration = authored.get(locator_role)
        locator_inherited = isinstance(locator_declaration, dict) and locator_declaration == {"workspace": True}
        need(locator_declaration == locator or locator_inherited,
             "Original source locator is not an authored scalar or explicit workspace inheritance declaration.")
        parsed = urllib.parse.urlsplit(locator)
        need(parsed.scheme == "https" and parsed.hostname in {"github.com", "gitlab.com"} and parsed.port is None
             and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment,
             "Original source requires an authored canonical public GitHub/GitLab locator.")
        parts = parsed.path.strip("/").split("/")
        need(all(re.fullmatch(r"[A-Za-z0-9_.-]+", value) and value not in {".", ".."} for value in parts), "Unsafe original repository path.")
        if parsed.hostname == "github.com":
            need(len(parts) == 2 or len(parts) >= 4 and parts[2] == "tree", "Unrecognized authored GitHub source locator route.")
            repo_parts = parts[:2]
        else:
            route = parts.index("-") if "-" in parts else len(parts)
            need(route == len(parts) or len(parts) >= route + 3 and parts[route + 1] == "tree", "Unrecognized authored GitLab source locator route.")
            repo_parts = parts[:route]
            need(len(repo_parts) >= 2, "GitLab project namespace is missing.")
        repo_parts[-1] = repo_parts[-1][:-4] if repo_parts[-1].endswith(".git") else repo_parts[-1]
        repo_locator = "/".join(repo_parts)
        provider = parsed.hostname
        host = "api.github.com" if provider == "github.com" else "gitlab.com"
        metadata_path = "/repos/" + repo_locator if provider == "github.com" else "/api/v4/projects/" + urllib.parse.quote(repo_locator, safe="")
        metadata, metadata_read = self.public_source_get(host, metadata_path, True)
        if provider == "github.com":
            need(metadata["private"] is False and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", metadata["full_name"]), "Refuse private/invalid canonical original repository.")
            repo = metadata["full_name"]
            prefix = "/repos/" + repo
            actual_commit, _ = self.public_source_get(host, prefix + "/git/commits/" + commit)
            need(actual_commit["sha"] == commit and re.fullmatch(r"[0-9a-f]{40}", actual_commit["tree"]["sha"]), "Exact original commit/tree identity mismatch.")
            tree_sha = actual_commit["tree"]["sha"]
            recursive, _ = self.public_source_get(host, prefix + "/git/trees/" + tree_sha + "?recursive=1")
            need(recursive["sha"] == tree_sha and recursive.get("truncated") is False, "Exact original tree truncated/unresolved.")
            entries = recursive["tree"]
            tree_basis = "authoritative_commit_tree_sha"
            blob_route = prefix + "/git/blobs/"
        else:
            need(metadata["visibility"] == "public" and isinstance(metadata["id"], int)
                 and metadata["id"] > 0 and metadata["path_with_namespace"], "Refuse nonpublic or unidentified GitLab project.")
            repo = metadata["path_with_namespace"]
            prefix = "/api/v4/projects/" + str(metadata["id"])
            actual_commit, _ = self.public_source_get(host, prefix + "/repository/commits/" + commit)
            need(actual_commit["id"] == commit, "Exact original GitLab commit identity mismatch.")
            entries = []
            page = "1"
            for count in range(1000):
                batch, info = self.public_source_get(host, prefix + "/repository/tree?ref=" + commit + "&recursive=true&per_page=100&page=" + page)
                need(isinstance(batch, list) and len(batch) <= 100, "Invalid bounded GitLab source tree page.")
                entries.extend({**item, "sha": item["id"]} for item in batch)
                next_page = info["next_page"]
                if not next_page:
                    break
                need(next_page.isdecimal() and int(next_page) == int(page) + 1, "Unconfirmed GitLab source tree pagination.")
                page = next_page
            else:
                raise RuntimeError("GitLab original source tree exceeds bounded pagination.")
            # GitLab returns exact-ref ls-tree entries, not an API commit tree field.
            # Reconstruct and verify every included child tree; record this root as derived.
            tree_sha = None
            tree_basis = "derived_git_tree_from_complete_exact_commit_paginated_entries"
            blob_route = prefix + "/repository/blobs/"
        need(len(entries) <= 100000, "Original source tree exceeds bounded entries.")
        by_path = {}
        for entry in entries:
            path = entry["path"]
            need(isinstance(path, str) and not path.startswith("/") and "\\" not in path and "\0" not in path
                 and all(component not in {"", ".", ".."} for component in path.split("/"))
                 and path not in by_path and re.fullmatch(r"[0-9a-f]{40}", entry["sha"]), "Unsafe/duplicate original Git tree identity.")
            by_path[path] = entry
        if provider == "gitlab.com":
            def tree_hash(prefix_path):
                direct = [item for path, item in by_path.items() if str(PurePosixPath(path).parent) in {prefix_path or "."}]
                data = b""
                for item in sorted(direct, key=lambda value: (PurePosixPath(value["path"]).name + ("/" if value["type"] == "tree" else "")).encode()):
                    if item["type"] == "tree":
                        need(tree_hash(item["path"]) == item["sha"], "GitLab exact-ref child tree bytes/SHA mismatch.")
                    data += item["mode"].lstrip("0").encode() + b" " + PurePosixPath(item["path"]).name.encode() + b"\0" + bytes.fromhex(item["sha"])
                return hashlib.sha1(b"tree " + str(len(data)).encode() + b"\0" + data).hexdigest()
            tree_sha = tree_hash("")
        def blob(entry):
            need(entry["type"] == "blob" and entry["mode"] in {"100644", "100755"}, "Original manifest/notice is not a regular Git blob.")
            value, _ = self.public_source_get(host, blob_route + entry["sha"])
            need(value["sha"] == entry["sha"] and value["encoding"] == "base64" and 0 <= value["size"] <= 1024 * 1024, "Original blob encoding/size/identity unresolved.")
            content = base64.b64decode("".join(value["content"].splitlines()), validate=True)
            need(len(content) == value["size"] and hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest() == entry["sha"], "Original Git blob SHA/bytes mismatch.")
            content.decode("utf-8")
            return content
        reported_path = vcs.get("path_in_vcs")
        if reported_path is None:
            canonical_authored_bytes = authored_bytes.replace(b"\r\n", b"\n")
            authored_git_shas = {hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest()
                                 for body in (authored_bytes, canonical_authored_bytes)}
            matches = [path for path, item in by_path.items() if PurePosixPath(path).name == "Cargo.toml"
                       and item["type"] == "blob" and item["mode"] in {"100644", "100755"} and item["sha"] in authored_git_shas]
            need(len(matches) == 1, "Absent path_in_vcs requires one unique exact authored manifest blob in the authoritative commit tree.")
            manifest_path = matches[0]
            path_in_vcs = str(PurePosixPath(manifest_path).parent)
            path_in_vcs = "" if path_in_vcs == "." else path_in_vcs
            path_basis = "unique_original_manifest_blob_raw_or_exact_CRLF_to_LF_discovery"
        else:
            need(isinstance(reported_path, str) and "\\" not in reported_path and "\0" not in reported_path
                 and not reported_path.startswith("/") and all(value not in {"", ".", ".."} for value in reported_path.split("/") if reported_path), "Unsafe reported original VCS path.")
            path_in_vcs = reported_path
            manifest_path = (path_in_vcs + "/" if path_in_vcs else "") + "Cargo.toml"
            path_basis = "reported_path_in_vcs_verified"
        ancestor_paths = [""]
        current = ""
        for component in PurePosixPath(path_in_vcs).parts if path_in_vcs else ():
            current = (current + "/" if current else "") + component
            need(current in by_path and by_path[current]["type"] == "tree" and by_path[current]["mode"] == "040000", "Original package path contains a link/gitlink/unresolved directory.")
            ancestor_paths.append(current)
        need(manifest_path in by_path, "Exact original VCS manifest is absent.")
        original_manifest_bytes = blob(by_path[manifest_path])
        raw_byte_identical = original_manifest_bytes == authored_bytes
        exact_crlf_to_lf_equal = original_manifest_bytes.replace(b"\r\n", b"\n") == authored_bytes.replace(b"\r\n", b"\n")
        need(exact_crlf_to_lf_equal and pin(authored_manifest) == authored_pin,
             "Packaged authored manifest differs beyond the exact CRLF-to-LF transform from original VCS manifest.")
        original_package = tomllib.loads(original_manifest_bytes.decode())["package"]
        original_workspace = None
        explicit_workspace = original_package.get("workspace")
        if explicit_workspace is not None:
            need(isinstance(explicit_workspace, str) and explicit_workspace and not explicit_workspace.startswith("/")
                 and "\\" not in explicit_workspace and "\0" not in explicit_workspace,
                 "Original package.workspace is not bounded repository-relative text.")
            workspace_parts = list(PurePosixPath(path_in_vcs).parts) if path_in_vcs else []
            for component in explicit_workspace.split("/"):
                if component in {"", "."}:
                    continue
                if component == "..":
                    need(workspace_parts, "Explicit original workspace escapes the exact public repository.")
                    workspace_parts.pop()
                else:
                    workspace_parts.append(component)
            workspace_path = "/".join(workspace_parts)
            prefix_path = ""
            for component in workspace_parts:
                prefix_path = (prefix_path + "/" if prefix_path else "") + component
                need(prefix_path in by_path and by_path[prefix_path]["type"] == "tree" and by_path[prefix_path]["mode"] == "040000",
                     "Explicit original workspace path contains links/gitlinks/unresolved directories.")
            workspace_manifest_path = (workspace_path + "/" if workspace_path else "") + "Cargo.toml"
            need(workspace_manifest_path in by_path, "Explicit original workspace manifest is absent.")
            workspace_document = tomllib.loads(blob(by_path[workspace_manifest_path]).decode())
            need(isinstance(workspace_document.get("workspace"), dict), "Explicit original owning manifest does not declare [workspace].")
            original_workspace = {"path": workspace_path, "manifest_blob": by_path[workspace_manifest_path]["sha"],
                                  "selection": "explicit_package_workspace", "document": workspace_document["workspace"]}
        else:
            # Stop at the closest actual workspace boundary even when a field is absent.
            for ancestor in reversed(ancestor_paths):
                workspace_manifest_path = (ancestor + "/" if ancestor else "") + "Cargo.toml"
                if workspace_manifest_path not in by_path:
                    continue
                workspace_document = tomllib.loads(blob(by_path[workspace_manifest_path]).decode())
                if isinstance(workspace_document.get("workspace"), dict):
                    original_workspace = {"path": ancestor, "manifest_blob": by_path[workspace_manifest_path]["sha"],
                                          "selection": "closest_actual_workspace_boundary", "document": workspace_document["workspace"]}
                    break
        def inherited_field(field):
            need(original_workspace is not None, "Original owning workspace identity is unresolved.")
            value = original_workspace["document"].get("package", {}).get(field)
            need(value is not None, "Original selected workspace.package field absent: " + field)
            return value
        if locator_inherited:
            inherited_locator = inherited_field(locator_role)
            need(isinstance(inherited_locator, str) and inherited_locator == locator,
                 "Original selected workspace repository/homepage differs from normalized Cargo metadata.")
        need(original_package["name"] == package["name"], "Original VCS package name mismatch.")
        for field in ("version", "license"):
            expected = original_package.get(field)
            if isinstance(expected, dict) and expected.get("workspace") is True:
                need(expected == {"workspace": True}, "Unknown authored workspace inheritance form.")
                expected = inherited_field(field)
            need(expected == package.get(field), "Original package version/licence metadata mismatch.")
        candidates = []
        notice_total_bytes = 0
        selected_notices = {}
        for ancestor in reversed(ancestor_paths):
            for relative, entry in sorted(by_path.items()):
                parent = str(PurePosixPath(relative).parent)
                if parent != (ancestor or ".") or not re.search(r"(^|[-_.])(licen[cs]e|copying|notice|unlicense)([-_.]|$)", PurePosixPath(relative).name, re.I):
                    continue
                if entry["type"] == "tree":
                    need(entry["mode"] == "040000", "Original matched notice directory is not an ordinary exact Git tree.")
                    descendants = [(path, child) for path, child in sorted(by_path.items()) if path.startswith(relative + "/")]
                    need(len(descendants) <= 1024, "Original notice directory exceeds bounded descendant gate.")
                    for path, child in descendants:
                        need(len(PurePosixPath(path).relative_to(PurePosixPath(relative)).parts) <= 32,
                             "Original notice directory exceeds bounded depth.")
                        if child["type"] == "tree":
                            need(child["mode"] == "040000", "Original notice descendant directory is nonordinary.")
                            continue
                        need(child["type"] == "blob" and child["mode"] in {"100644", "100755"},
                             "Original notice directory includes a link/gitlink/special entry.")
                        selected_notices[path] = (child, "original_actual_notice_directory_regular_descendant_text", relative)
                else:
                    need(entry["type"] == "blob" and entry["mode"] in {"100644", "100755"},
                         "Original ancestor notice is a link/gitlink/nonregular entry.")
                    selected_notices[relative] = (entry, "original_package_or_ancestor_notice_text", None)
        need(len(selected_notices) <= 1024, "Original selected licence text inventory exceeds bounded count.")
        for relative, (entry, notice_scope, notice_directory) in sorted(selected_notices.items()):
            content = blob(entry)
            need(content.strip() and b"\0" not in content, "Original notice text is empty/nontext.")
            notice_total_bytes += len(content)
            need(notice_total_bytes <= 8 * 1024 * 1024, "Original notice text collection exceeds bounded bytes.")
            destination = self.root / "license-preflight-refs" / hashlib.sha256(package_id.encode()).hexdigest()[:20] / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                destination.write_bytes(content)
            need(destination.read_bytes() == content, "Conflicting original public notice bytes.")
            url = ("https://github.com/" + repo + "/blob/" if provider == "github.com" else "https://gitlab.com/" + repo + "/-/blob/") + commit + "/" + urllib.parse.quote(relative, safe="/")
            candidates.append({"path": str(destination), "pin": pin(destination), "repo_relative_path": relative,
                               "url": url, "commit": commit, "tree": tree_sha, "tree_identity_basis": tree_basis,
                               "blob": entry["sha"], "repository": repo, "provider": provider,
                               "scope": notice_scope, "notice_directory_relative_path": notice_directory})
        if not candidates and package.get("license") == "CC0-1.0" and original_package.get("license") == "CC0-1.0":
            standard = Path(__file__).resolve().parent / "public-licenses/standard/CC0-1.0.txt"
            actual = pin(standard)
            need(actual["bytes"] == 7048 and actual["sha256"] == "a2010f343487d3f7618affe54f789f5487602331c0a8d03f49e9a7c547cf0499", "Reviewed actual standardized CC0 text changed.")
            destination = self.root / "license-preflight-refs" / hashlib.sha256(package_id.encode()).hexdigest()[:20] / "text/CC0-1.0.txt"
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                shutil.copy2(standard, destination)
            need(pin(destination) == actual, "Standardized CC0 derivative not byte/mtime identical.")
            candidates.append({"path": str(destination), "pin": actual, "repo_relative_path": "text/CC0-1.0.txt",
                               "url": "https://github.com/spdx/license-list-data/blob/31ba1a50e5397e00a304dbadc76531740e89ee48/text/CC0-1.0.txt",
                               "repository": "spdx/license-list-data", "commit": "31ba1a50e5397e00a304dbadc76531740e89ee48",
                               "tree": "dc9f030bcb3744a0a351fad8eafb94015f4305fa", "blob": "0e259d42c996742e9e3cba14c677129b2c1b6311",
                               "scope": "standardized_CC0_terms_bound_to_byte_identical_original_authored_CC0_declaration",
                               "author_repository_notice_claimed": False, "grant_manifest_blob": by_path[manifest_path]["sha"]})
        need(candidates, "No actual original or explicitly declaration-bound standardized licence text resolved.")
        return {"package_id": package_id, "vcs": {"pin": pin(vcs_path), "commit": commit, "reported_path_in_vcs": reported_path,
                "path_in_vcs": path_in_vcs, "path_identity_basis": path_basis}, "provider": provider,
                "authored_locator_role": locator_role, "authored_locator": locator, "canonical_repository": repo,
                "repository_metadata_read": metadata_read, "authored_manifest_pin": authored_pin,
                "original_manifest_blob": by_path[manifest_path]["sha"], "original_manifest_byte_identical": raw_byte_identical,
                "exact_crlf_to_lf_equal": exact_crlf_to_lf_equal,
                "original_manifest_bytes": len(original_manifest_bytes),
                "original_manifest_sha256": hashlib.sha256(original_manifest_bytes).hexdigest(),
                "authored_locator_inherited_from_exact_original_workspace": locator_inherited,
                "original_workspace": ({key: value for key, value in original_workspace.items() if key != "document"} if original_workspace else None),
                "whole_crate_source_equivalence_claimed": False, "notices": candidates}

    def license_omission_closure(self, meta, unresolved, actual_compiled_ids):
        """Omit every actual external dependent, through workspace nodes too."""
        reverse = {}
        for node in meta["resolve"]["nodes"]:
            for edge in node["deps"]:
                need(edge["dep_kinds"] and all(value["kind"] in {None, "build", "dev"} for value in edge["dep_kinds"]), "Unknown omission dependency kind.")
                if any(value["kind"] in {None, "build"} for value in edge["dep_kinds"]):
                    reverse.setdefault(edge["pkg"], set()).add(node["id"])
        omitted = set(unresolved)
        pending = list(omitted)
        while pending:
            for dependent in reverse.get(pending.pop(), ()):
                if dependent not in omitted:
                    omitted.add(dependent)
                    pending.append(dependent)
        return omitted.intersection(actual_compiled_ids)

    def license_preflight_package(self, package_id, package, contract):
        root = Path(package["manifest_path"]).parent
        need(self.home in root.parents and root.resolve(strict=True) == root, "Preflight source must be the exact fetched external package.")
        source_root = root
        if package["source"].startswith("git+"):
            candidates = [ancestor for ancestor in (root, *root.parents) if self.home in ancestor.parents and (ancestor / ".git").exists()]
            need(candidates, "Fetched original external Git source root unresolved.")
            source_root = candidates[0]
        license_files = [path for path in files(source_root) if re.search(r"(^|[-_.])(licen[cs]e|copying|notice|unlicense)([-_.]|$)", path.name, re.I)]
        need(package.get("license") or package.get("license_file"), "Package licensing metadata unresolved.")
        fallback = None
        if package["name"] == "ct2rs":
            need(package["version"] == "0.10.1", "Unreviewed ct2rs original licence contract version.")
            vcs = json.loads((root / ".cargo_vcs_info.json").read_text())
            need(vcs["git"]["sha1"] == "2d2bc51c5c3f10ba91ade120414c0fb4ed196051", "ct2rs original source notice contract mismatch.")
            for notice in contract["licenses"]:
                actual = pin(root / notice["crate_relative_path"]) if notice.get("crate_relative_path") else pin(Path(__file__).resolve().parent / notice["path"])
                need(actual["bytes"] == notice["bytes"] and actual["sha256"] == notice["sha256"], "ct2rs original/vendor notice pin mismatch during preflight.")
        elif not license_files:
            fallback = self.original_license_fallback(package_id, package, root)
        need(license_files or fallback or package["name"] == "ct2rs", "Original actual licence text remains unresolved.")
        return {"source_root": str(source_root), "license_files": [str(path) for path in license_files], "fallback": fallback}

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
        official_lock = tomllib.loads((self.official / "Cargo.lock").read_text())
        registry_lock_checksums = {}
        for entry in official_lock["package"]:
            if not (entry.get("source") or "").startswith(("registry+", "sparse+")):
                continue
            key = (entry["name"], entry["version"], entry["source"])
            need(key not in registry_lock_checksums and re.fullmatch(r"[0-9a-f]{64}", entry.get("checksum") or ""),
                 "Exact official registry lock owner/checksum ambiguous or absent.")
            registry_lock_checksums[key] = entry["checksum"]
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
        metadata_raw = self.run("cargo-metadata", ["cargo", "metadata", "--locked", "--format-version", "1", "--filter-platform", self.args.target]).read_text()
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
        app_root, reachable = self.app_non_dev_closure(meta, packages)
        preflight_ids = sorted(reachable.intersection(allowed))
        license_infos = {}
        unresolved_licenses = {}
        for package_id in preflight_ids:
            package = allowed[package_id]
            try:
                license_infos[package_id] = self.license_preflight_package(package_id, package, contract)
            except Exception as error:
                vcs_path = Path(package["manifest_path"]).parent / ".cargo_vcs_info.json"
                vcs = None
                if vcs_path.is_file():
                    try:
                        actual_vcs = json.loads(vcs_path.read_text())
                        vcs = {"git": {key: actual_vcs.get("git", {}).get(key) for key in ("sha1", "dirty")},
                               "path_in_vcs": actual_vcs.get("path_in_vcs"), "pin": pin(vcs_path)}
                    except Exception as vcs_error:
                        vcs = {"parse_error_type": type(vcs_error).__name__}
                repository = urllib.parse.urlsplit(package.get("repository") or "")
                safe_repository = package.get("repository") if not (repository.username or repository.password or repository.query or repository.fragment) else None
                print(json.dumps({"phase": "license-preflight-unresolved", "package_id": package_id,
                                  "name": package["name"], "version": package["version"], "source": package["source"],
                                  "repository": safe_repository, "manifest_path": package["manifest_path"],
                                  "vcs": vcs, "reason": str(error), "Cargo_build_started": False,
                                  "publication_policy": "omit_owner_and_actual_compiled_dependents"}, sort_keys=True), flush=True)
                unresolved_licenses[package_id] = {"name": package["name"], "version": package["version"],
                                                   "source": package["source"], "reason": str(error)}
        print(json.dumps({"phase": "license-preflight-complete", "root_package_id": app_root, "target": self.args.target,
                          "conservative_external_package_count": len(preflight_ids), "compiled_scope_claimed": False,
                          "unresolved_package_ids": sorted(unresolved_licenses),
                          "publication_policy": "omit_unresolved_and_all_actual_compiled_dependents"}, sort_keys=True), flush=True)
        raw = self.run("cargo-official-release", ["cargo", "build", "--locked", "--release", "-p", "omsi-app", "--bin", "openomsi",
                                                  "--target", self.args.target, "--message-format=json-render-diagnostics"])
        messages = [json.loads(line) for line in raw.read_text().splitlines() if line.startswith("{")]
        need(any(item.get("reason") == "build-finished" and item.get("success") is True for item in messages), "Natural build completion missing.")
        actual_compiled_ids = {message["package_id"] for message in messages
                               if message.get("reason") in {"compiler-artifact", "build-script-executed"} and message["package_id"] in allowed}
        for package_id in sorted(actual_compiled_ids - license_infos.keys() - unresolved_licenses.keys()):
            try:
                license_infos[package_id] = self.license_preflight_package(package_id, allowed[package_id], contract)
            except Exception as error:
                package = allowed[package_id]
                print(json.dumps({"phase": "compiled-owner-license-unresolved", "package_id": package_id,
                                  "name": package["name"], "version": package["version"], "source": package["source"],
                                  "reason": str(error), "Cargo_build_started": True, "export_started": False,
                                  "publication_policy": "omit_owner_and_actual_compiled_dependents"}, sort_keys=True), flush=True)
                unresolved_licenses[package_id] = {"name": package["name"], "version": package["version"],
                                                   "source": package["source"], "reason": str(error)}
        license_omitted = self.license_omission_closure(meta, unresolved_licenses, actual_compiled_ids)
        self.native_public_source_owners = []
        for key in sorted(actual_compiled_ids - license_omitted):
            package = allowed[key]
            root = Path(package["manifest_path"]).parent
            if package["name"] not in {"ct2rs", "cxx"}:
                continue
            need(root.resolve(strict=True) == root and self.home in root.parents and key in license_infos,
                 "Native source alias owner must be an actual licensed canonical external package.")
            selected = root if package["name"] == "ct2rs" else root / "include/cxx.h"
            need(selected.resolve(strict=True) == selected, "Native public alias destination must be canonical/no-link.")
            self.native_public_source_owners.append({"package_id": key, "source_root": str(root),
                 "selected_path": str(selected), "allow_descendants": package["name"] == "ct2rs",
                 "license_info_sha256": hashlib.sha256(json.dumps(license_infos[key], sort_keys=True, separators=(",", ":")).encode()).hexdigest()})
        self.native_all_package_roots = [{"package_id": key, "source_root": str(Path(value["manifest_path"]).parent)} for key, value in packages.items()]
        native_plans = {}
        incomplete = set()
        for message in messages:
            if message.get("reason") != "build-script-executed" or message["package_id"] not in allowed or message["package_id"] in license_omitted:
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
            if package_id in license_omitted:
                omitted_artifacts.append({"package_id": package_id, "filenames": message.get("filenames", []),
                                          "reason": "unresolved_license_owner_or_transitive_dependent_no_bytes_exported"})
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
            need(any(allowed[key]["name"] == "ct2rs" for key in actual_compiled_ids), "Expected real ARM ct2rs dependency closure missing.")
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
                if any(key in incomplete or key in license_omitted or key in unresolved_licenses or key not in actual_compiled_ids or key not in license_infos for key in candidates):
                    omitted_fingerprints.append({"path": str(directory), "external_package_candidates": candidates,
                                                 "reason": "native_or_licence_owner_unavailable_normal_rebuild_required"})
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
            license_files = [path for path in files(source_root) if re.search(r"(^|[-_.])(licen[cs]e|copying|notice|unlicense)([-_.]|$)", path.name, re.I)]
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
            original_fallback = license_infos[package_id]["fallback"]
            need(license_files or original_fallback or package["name"] == "ct2rs", "No actual license/notice text found for an exported package; manual resolution required.")
            if original_fallback:
                for notice in original_fallback["notices"]:
                    actual = Path(notice["path"])
                    need(pin(actual) == notice["pin"], "Original preflight notice bytes changed before compiled-source export.")
                    self.copy(actual, "original-license-texts", package_id, self.root / "license-preflight-refs")
            # A shared Git checkout may contain another omitted package; do not
            # publish its bytes through a licensed sibling owner.
            blocked_roots = [Path(packages[key]["manifest_path"]).parent for key in license_omitted | unresolved_licenses.keys()]
            for source_file in files(source_root):
                if any(blocked == source_file or blocked in source_file.parents for blocked in blocked_roots):
                    continue
                self.copy(source_file, "cargo-home", package_id, self.home)
            registry_archive = None
            if package["source"].startswith(("registry+", "sparse+")):
                lock_key = (package["name"], package["version"], package["source"])
                need(lock_key in registry_lock_checksums and root.parent.parent == self.home / "registry/src",
                     "Exported registry owner is not an exact official lock/source-cache identity.")
                archive = self.home / "registry/cache" / root.parent.name / (package["name"] + "-" + package["version"] + ".crate")
                archive_pin = pin(archive)
                need(archive_pin["sha256"] == registry_lock_checksums[lock_key],
                     "Actual public registry archive SHA does not match the exact official lock owner checksum.")
                self.copy(archive, "cargo-home", package_id, self.home)
                archive_key = "cargo-home/" + archive.relative_to(self.home).as_posix()
                need(self.inventory[archive_key]["sha256"] == archive_pin["sha256"] and pin(archive) == archive_pin,
                     "Corresponding public registry archive changed during export.")
                registry_archive = {"path": archive_key, "origin_path": str(archive), "pin": archive_pin,
                                    "lock_identity": {"name": lock_key[0], "version": lock_key[1], "source": lock_key[2],
                                                      "checksum": registry_lock_checksums[lock_key]},
                                    "checksum_verified_against_exact_official_lock": True}
            source_rows.append({"package_id": package_id, "name": package["name"], "version": package["version"],
                                "source": package["source"], "license": package.get("license"),
                                "original_source_root": str(source_root), "registry_archive": registry_archive,
                                "license_files": [{"path": path.relative_to(source_root).as_posix(),
                                                   "sha256": self.inventory["cargo-home/" + path.relative_to(self.home).as_posix()]["sha256"]}
                                                  for path in license_files
                                                  if "cargo-home/" + path.relative_to(self.home).as_posix() in self.inventory
                                                  and package_id in self.inventory["cargo-home/" + path.relative_to(self.home).as_posix()]["owners"]],
                                "original_source_license_fallback": original_fallback,
                                "additional_workspace_license": ({"path": "public-licenses/ct2rs-0.10.1/WORKSPACE-LICENSE",
                                    "sha256": "ed39d4eb1b651d31041f1dde23f4fa4a19a7ab87ebfc6858b3abfd47f66cb557",
                                    "source_url": "https://raw.githubusercontent.com/jkawamoto/ctranslate2-rs/2d2bc51c5c3f10ba91ade120414c0fb4ed196051/LICENSE"}
                                    if package["name"] == "ct2rs" else None)})
        for item in self.inventory.values():
            for derivative in item.get("native_output_derivatives", []):
                source_key = derivative["corresponding_source_path"]
                if source_key:
                    need(source_key in self.inventory and self.inventory[source_key]["sha256"] == item["sha256"]
                         and self.inventory[source_key]["bytes"] == item["bytes"],
                         "Materialized native public alias lacks byte-identical included corresponding source.")
        license_inventory = {"official_commit": OFFICIAL_COMMIT, "official_tree": OFFICIAL_TREE,
                             "cargo_lock_sha256": lock_before["sha256"],
                             "packages": [{key: row[key] for key in ("package_id", "name", "version", "source", "license", "license_files", "additional_workspace_license", "original_source_license_fallback", "registry_archive")}
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
                   "license_preflight": {"root_package_id": app_root, "target": self.args.target, "package_ids": preflight_ids,
                                         "scope": "conservative_target_filtered_non_dev_reachable_not_compiled_scope"},
                   "original_source_license_fallbacks": [license_infos[key]["fallback"] for key in sorted(compiled) if license_infos[key]["fallback"]],
                   "non_cache_license_prefix": "original-license-texts/",
                   "unresolved_license_packages": [{"package_id": key, **value} for key, value in sorted(unresolved_licenses.items())],
                   "license_omitted_actual_compiled_package_ids": sorted(license_omitted),
                   "license_omission_policy": "no_source_native_fingerprint_or_artifact_bytes_for_unresolved_owners_and_all_actual_non_dev_compiled_dependents",
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
                   "license_preflight": {"root_package_id": app_root, "target": self.args.target, "package_ids": preflight_ids,
                                         "scope": "conservative_target_filtered_non_dev_reachable_not_compiled_scope"},
                   "original_source_license_fallbacks": [license_infos[key]["fallback"] for key in sorted(compiled) if license_infos[key]["fallback"]],
                   "non_cache_license_prefix": "original-license-texts/",
                   "unresolved_license_packages": [{"package_id": key, **value} for key, value in sorted(unresolved_licenses.items())],
                   "license_omitted_actual_compiled_package_ids": sorted(license_omitted),
                   "license_omission_policy": "no_source_native_fingerprint_or_artifact_bytes_for_unresolved_owners_and_all_actual_non_dev_compiled_dependents",
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
