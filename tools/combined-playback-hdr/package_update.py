"""Repack a pinned CoreELEC SYSTEM without changing unrelated filesystem metadata."""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import shutil
import stat
import struct
import subprocess
import sys
import tarfile


def digest(path, algorithm="sha256"):
    value = hashlib.new(algorithm)
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def run(args, **kwargs):
    subprocess.run(args, check=True, **kwargs)


def locked_file(entry):
    path = Path(entry["path"]).resolve(strict=True)
    if not path.is_file() or digest(path) != entry["sha256"]:
        raise RuntimeError("Pinned input mismatch: " + str(path))
    return path


def native_init(kernel):
    data = kernel.read_bytes()
    header = struct.unpack_from("<8s8I", data)
    magic, kernel_size, _, ramdisk_size, _, _, _, _, page = header
    if magic != b"ANDROID!" or page != 2048:
        raise RuntimeError("Unsupported Android boot image")
    start = page + (kernel_size + page - 1) // page * page
    archive = data[start:start + ramdisk_size]
    if archive.startswith(b"\x1f\x8b"):
        archive = gzip.decompress(archive)
    offset, entries = 0, {}
    while offset + 110 <= len(archive):
        header = archive[offset:offset + 110]
        if header[:6] not in (b"070701", b"070702"):
            raise RuntimeError("Unsupported initramfs format")
        values = [int(header[i:i + 8], 16) for i in range(6, 110, 8)]
        size, name_size = values[6], values[11]
        name = archive[offset + 110:offset + 110 + name_size - 1].decode()
        offset = (offset + 110 + name_size + 3) & ~3
        content = archive[offset:offset + size]
        if len(content) != size:
            raise RuntimeError("Truncated initramfs entry")
        offset = (offset + size + 3) & ~3
        if name == "TRAILER!!!":
            break
        entries[name.removeprefix("./")] = content
    else:
        raise RuntimeError("Missing initramfs trailer")
    init = entries["init"]
    for required in (
        'tar -xf "$UPDATE_TAR" -C $UPDATE_DIR/.tmp',
        'mv $UPDATE_DIR/.tmp/*/target/* $UPDATE_DIR',
        'sed "s#target/KERNEL#$UPDATE_DIR/$UPDATE_KERNEL#g"',
        'sed "s#target#$UPDATE_DIR#g"',
        'if [ "${1}" = "${2}" ]; then',
        'if [ ! -f "$UPDATE_ROOT/.nocompat" ]; then',
        'if ! check_is_compatible "$UPDATE_FILENAME"; then',
        'TMP_SIZE=$((OLD_KERNEL + OLD_SYSTEM + FLASH_FREE - NEW_KERNEL - NEW_SYSTEM))',
        'update_file "System" "$UPDATE_SYSTEM" "/flash/$IMAGE_SYSTEM"',
        "update_bootloader",
    ):
        if required.encode() not in init:
            raise RuntimeError("Native updater semantics changed: " + required)
    return init, len(entries)


def tree(root):
    result, links = {}, {}
    for path in [root, *sorted(root.rglob("*"))]:
        metadata = path.lstat()
        name = path.relative_to(root).as_posix()
        item = {
            "mode": stat.S_IMODE(metadata.st_mode),
            "type": stat.S_IFMT(metadata.st_mode),
            "uid": metadata.st_uid, "gid": metadata.st_gid,
            "mtime": int(metadata.st_mtime),
        }
        if stat.S_ISREG(metadata.st_mode):
            item.update(size=metadata.st_size, sha256=digest(path))
            if metadata.st_nlink > 1:
                links.setdefault((metadata.st_dev, metadata.st_ino), []).append(name)
        elif stat.S_ISLNK(metadata.st_mode):
            item["target"] = os.readlink(path)
        elif stat.S_ISCHR(metadata.st_mode) or stat.S_ISBLK(metadata.st_mode):
            item["rdev"] = metadata.st_rdev
        result[name] = item
    return result, sorted(sorted(group) for group in links.values())


def mutation_name(name):
    path = PurePosixPath(name)
    if (not name or name == "." or name != path.as_posix() or
            path.is_absolute() or ".." in path.parts or
            any(character.isspace() for character in name) or "\\" in name):
        raise RuntimeError("Unsafe SYSTEM mutation path: " + name)


def repack(base, replacements, image, work, output, epoch, *,
           additions=None, removals=(), symlinks=None, replacement_mtimes=None):
    additions, removals, symlinks = additions or {}, set(removals), symlinks or {}
    replacement_mtimes = replacement_mtimes or {}
    if (not set(replacement_mtimes).issubset(replacements) or
            any(type(value) is not int or not 0 <= value <= 0xffffffff
                for value in replacement_mtimes.values())):
        raise RuntimeError("Invalid replacement-mtime declaration")
    groups = (set(replacements), set(additions), removals, set(symlinks))
    for index, group in enumerate(groups):
        for other in groups[index + 1:]:
            if group & other:
                raise RuntimeError("Overlapping SYSTEM mutations")
        for name in group:
            mutation_name(name)
    roots = [work / "base-root", work / "combined-root"]
    run(["unsquashfs", "-no-progress", "-strict-errors", "-d", str(roots[0]), str(base)],
        stdout=subprocess.DEVNULL)
    before, before_links = tree(roots[0])
    for name in set(replacements) | removals | set(symlinks):
        if name not in before:
            raise RuntimeError("Missing existing mutation path: " + name)
    for name, entry in additions.items():
        if name in before:
            raise RuntimeError("Addition already exists: " + name)
        if entry["type"] not in ("file", "directory", "symlink"):
            raise RuntimeError("Unsupported added inode type: " + name)
        parent = PurePosixPath(name).parent.as_posix()
        if parent in additions:
            parent_type = additions[parent]["type"] == "directory"
        else:
            parent_type = parent in before and before[parent]["type"] == stat.S_IFDIR
        if not parent_type or parent in removals:
            raise RuntimeError("Missing/non-directory addition parent: " + name)
        if entry["mode"] not in (0o644, 0o755, 0o777):
            raise RuntimeError("Unreviewed added inode mode: " + name)
    for name in removals:
        if before[name]["type"] == stat.S_IFDIR:
            descendants = {value for value in before if value.startswith(name + "/")}
            if not descendants.issubset(removals):
                raise RuntimeError("Directory removal omits existing children: " + name)
    for group in before_links:
        if set(group) & (set(replacements) | set(symlinks)):
            raise RuntimeError("Mutation would break an existing hardlink group")
        if set(group) & removals and not set(group).issubset(removals):
            raise RuntimeError("Partial hardlink-group removal is not supported")
    for target in [*symlinks.values(),
                   *(entry["target"] for entry in additions.values()
                     if entry["type"] == "symlink")]:
        if not target or "\n" in target or "\0" in target:
            raise RuntimeError("Invalid symlink target")
    for name in symlinks:
        if before[name]["type"] != stat.S_IFLNK:
            raise RuntimeError("Symlink update is not a symlink: " + name)
    pseudo, modified = work / "original.pseudo", work / "modified.pseudo"
    run(["unsquashfs", "-pf", str(pseudo), str(base)], stdout=subprocess.DEVNULL)
    changed = set()
    with pseudo.open("rb") as source, modified.open("wb") as target:
        for line in source:
            if line.startswith(b"# START OF DATA"):
                for name, entry in sorted(additions.items()):
                    kind = {"file": "F", "directory": "D", "symlink": "S"}[entry["type"]]
                    text = f'{name} {kind} {epoch} {entry["mode"]:o} 0 0'
                    if kind == "F":
                        text += " cat " + shlex.quote(str(entry["source"]))
                    elif kind == "S":
                        text += " " + entry["target"]
                    target.write((text + "\n").encode())
                target.write(line)
                shutil.copyfileobj(source, target, 1024 * 1024)
                break
            fields = line.split()
            name = fields[0].decode() if fields else ""
            if name in removals:
                changed.add(name)
            elif name in replacements:
                if fields[1] != b"R":
                    raise RuntimeError("Replacement is not a regular file: " + name)
                fields = [fields[0], b"F", *fields[2:6],
                          b"cat", shlex.quote(str(replacements[name])).encode()]
                if name in replacement_mtimes:
                    fields[2] = str(replacement_mtimes[name]).encode()
                target.write(b" ".join(fields) + b"\n")
                changed.add(name)
            elif name in symlinks:
                if fields[1] != b"S":
                    raise RuntimeError("Unexpected pseudo symlink type: " + name)
                target.write(b" ".join(fields[:6]) + b" " + symlinks[name].encode() + b"\n")
                changed.add(name)
            else:
                target.write(line)
        else:
            raise RuntimeError("Missing squashfs pseudo data marker")
    if changed != set(replacements) | removals | set(symlinks):
        raise RuntimeError("Missing existing mutation paths")
    empty = work / "empty-root"
    empty.mkdir()
    with (output / "system-repack.log").open("xb") as log:
        run(["mksquashfs", str(empty), str(image), "-pf", str(modified),
             "-noappend", "-comp", "lzo", "-Xalgorithm", "lzo1x_999",
             "-Xcompression-level", "9", "-b", "524288", "-no-xattrs",
             "-exports", "-processors", "2", "-no-progress",
             "-mkfs-time", str(epoch),
             "-root-time", str(before["."]["mtime"]),
             "-root-mode", f'{before["."]["mode"]:o}',
             "-root-uid", str(before["."]["uid"]),
             "-root-gid", str(before["."]["gid"])], stdout=log, stderr=subprocess.STDOUT)
    run(["unsquashfs", "-no-progress", "-strict-errors", "-d", str(roots[1]), str(image)],
        stdout=subprocess.DEVNULL)
    after, after_links = tree(roots[1])
    expected_links = sorted(sorted(name for name in group if name not in removals)
                            for group in before_links
                            if len(set(group) - removals) > 1)
    if set(after) != ((set(before) - removals) | set(additions)) or expected_links != after_links:
        raise RuntimeError("SYSTEM namespace or hardlink groups changed")
    actual_changes = []
    for name in before:
        if name in removals:
            continue
        if before[name] == after[name]:
            continue
        if name not in replacements and name not in symlinks:
            raise RuntimeError("Unexpected SYSTEM change: " + name)
        for key in ("mode", "type", "uid", "gid", "mtime"):
            expected = (replacement_mtimes.get(name, before[name][key])
                        if key == "mtime" else before[name][key])
            if expected != after[name][key]:
                raise RuntimeError("Metadata changed: " + name + ":" + key)
        if name in replacements and after[name]["sha256"] != digest(replacements[name]):
            raise RuntimeError("Replacement hash mismatch: " + name)
        if name in symlinks and after[name]["target"] != symlinks[name]:
            raise RuntimeError("Symlink update mismatch: " + name)
        actual_changes.append(name)
    if set(actual_changes) != set(replacements) | set(symlinks):
        raise RuntimeError("Replacement did not change its intended path")
    for name, entry in additions.items():
        expected = {
            "type": {"file": stat.S_IFREG, "directory": stat.S_IFDIR,
                     "symlink": stat.S_IFLNK}[entry["type"]],
            "mode": entry["mode"], "uid": 0, "gid": 0, "mtime": epoch,
        }
        if entry["type"] == "file":
            expected.update(size=entry["source"].stat().st_size,
                            sha256=digest(entry["source"]))
        elif entry["type"] == "symlink":
            expected["target"] = entry["target"]
        if after[name] != expected:
            raise RuntimeError("Added inode metadata/content mismatch: " + name)
    report = {
        "base_paths": len(before), "paths": len(after),
        "changed_paths": sorted(actual_changes),
        "added_paths": sorted(additions), "removed_paths": sorted(removals),
        "inode_metadata_preserved": not replacement_mtimes,
        "unlisted_inode_metadata_preserved": True,
        "declared_replacement_mtimes": replacement_mtimes,
        "symlinks": sum("target" in item for item in after.values()),
        "hardlink_groups": len(after_links), "hardlink_groups_preserved": True,
        "modules": {name: item for name, item in after.items()
                    if name.startswith(("usr/lib/modules/", "usr/lib/kernel-overlays/")) or
                    name.endswith((".ko", ".ko.xz", ".ko.gz"))},
    }
    return report


def add(archive, root, name, data, epoch):
    parts = PurePosixPath(name).parts
    if not parts or ".." in parts or name.startswith("/") or "\\" in name:
        raise RuntimeError("Unsafe output member")
    item = tarfile.TarInfo(root + "/" + name)
    item.size, item.mode = len(data), 0o644
    item.uid, item.gid, item.mtime = 0, 0, epoch
    archive.addfile(item, io.BytesIO(data))


def inspect_archive(path, root, payloads, work):
    rehearsal = work / "tar-verification"
    rehearsal.mkdir()
    with tarfile.open(path) as archive:
        members = archive.getmembers()
        if len({item.name for item in members}) != len(members):
            raise RuntimeError("Duplicate archive member")
        for item in members:
            parts = PurePosixPath(item.name).parts
            if (not item.isfile() or not parts or parts[0] != root or
                    ".." in parts or item.name.startswith("/") or "\\" in item.name):
                raise RuntimeError("Unsafe archive member")
            destination = rehearsal.joinpath(*parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(item) as source, destination.open("xb") as target:
                shutil.copyfileobj(source, target)
        for name, payload in payloads.items():
            target = rehearsal / root / "target" / name
            if digest(target) != digest(payload):
                raise RuntimeError("Extracted payload mismatch: " + name)
            text = (target.parent / (name + ".md5")).read_text()
            if text != digest(payload, "md5") + "  target/" + name + "\n":
                raise RuntimeError("Incorrect native MD5 format")
            rewritten = (text.replace("target/KERNEL", str(target)) if name == "KERNEL"
                         else text.replace("target", str(target.parent)))
            checksum = rehearsal / (name + ".rewritten.md5")
            checksum.write_text(rewritten)
            run(["md5sum", "-c", str(checksum)], cwd="/")
    return len(members)


def validate_identity(root, manifest):
    release = {}
    for line in (root / "etc/os-release").read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            release[key] = value.strip('"')
    expected = {
        "VERSION": manifest["version"], "LIBREELEC_ARCH": "Amlogic-ng.arm",
        "COREELEC_ARCH": "Amlogic-ng.arm", "COREELEC_PROJECT": "Amlogic-ce",
        "COREELEC_DEVICE": "Amlogic-ng", "COREELEC_BUILD": "p3i",
    }
    for key, value in expected.items():
        if release.get(key) != value:
            raise RuntimeError("Packaged release identity mismatch: " + key)
    if (root / "etc/release").read_text() != "Amlogic-ng.arm-" + manifest["version"] + "\n":
        raise RuntimeError("Packaged etc/release mismatch")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("Root is required for strict rootfs metadata verification")
    if args.work.exists() or args.output.exists():
        raise RuntimeError("Work and output paths must both be new")
    config = json.loads(args.inputs.read_text())
    base, kernel = locked_file(config["base_system"]), locked_file(config["kernel"])
    replacements = {name: locked_file(entry) for name, entry in config["replacements"].items()}
    additions = {}
    for name, entry in config.get("additions", {}).items():
        additions[name] = dict(entry)
        if entry["type"] == "file":
            additions[name]["source"] = locked_file(entry)
    removals, symlinks = config.get("removals", []), config.get("symlinks", {})
    extras = {name: locked_file(entry) for name, entry in config["archive_files"].items()}
    required = {
        "usr/lib/kodi/kodi.bin", "usr/share/kodi/system/settings/settings.xml",
        "usr/share/kodi/addons/resource.language.en_gb/resources/strings.po",
        "etc/os-release", "etc/release", "usr/share/bootloader/canupdate.sh",
    }
    if not required.issubset(replacements):
        raise RuntimeError("Missing coherent Kodi/release/fallback replacement")
    for name in set(replacements) | set(additions) | set(removals) | set(symlinks):
        mutation_name(name)
        if name not in required and not name.startswith(("usr/lib/", "usr/share/", "usr/bin/")):
            raise RuntimeError("Mutation outside reviewed runtime namespace: " + name)
        if (name.startswith("usr/share/bootloader/") and
                name != "usr/share/bootloader/canupdate.sh" and
                not (name.startswith("usr/share/bootloader/device_trees/") and
                     name.endswith(".dtb"))):
            raise RuntimeError("Boot payload mutation is not permitted")
        if name in ("usr/bin/kodi", "usr/bin/kodi-standalone", "usr/bin/xbmc",
                    "usr/bin/xbmc-standalone", "usr/lib/kodi/kodi.sh"):
            raise RuntimeError("Distribution wrapper mutation is not permitted")
    args.work.mkdir(parents=True)
    args.output.mkdir(parents=True)
    output = args.output.resolve()
    image = output / "SYSTEM.img"
    init, count = native_init(kernel)
    if hashlib.sha256(init).hexdigest() != config["native_init_sha256"]:
        raise RuntimeError("Native boot/update init mismatch")
    report = repack(base, replacements, image, args.work.resolve(), output, config["epoch"],
                    additions=additions, removals=removals, symlinks=symlinks,
                    replacement_mtimes=config.get("replacement_mtimes", {}))
    manifest = dict(config["manifest"])
    validate_identity(args.work.resolve() / "combined-root", manifest)
    manifest.update(
        archive_file=config["name"] + ".tar",
        base_system_sha256=digest(base), kernel_sha256=digest(kernel),
        system_sha256=digest(image), system_bytes=image.stat().st_size,
        kernel_bytes=kernel.stat().st_size,
        replacements_sha256={name: digest(path) for name, path in replacements.items()},
        additions={name: {key: value for key, value in entry.items() if key != "path"}
                   for name, entry in config.get("additions", {}).items()},
        removals=sorted(removals), symlink_updates=symlinks,
        replacement_mtimes=config.get("replacement_mtimes", {}),
        native_init_sha256=hashlib.sha256(init).hexdigest(), ramdisk_files=count,
        system_verification=report, device_install_reboot_verified=False,
        archive_files_sha256={name: digest(path) for name, path in extras.items()},
        package_recipe_sha256=digest(Path(__file__)),
        packaging_python=sys.version.splitlines()[0],
        packaging_tools_sha256={
            name: digest(Path(shutil.which(name)))
            for name in ("unsquashfs", "mksquashfs", "md5sum")
        },
    )
    (output / "system-verification.json").write_text(json.dumps(report, indent=2) + "\n")
    archive_path = output / manifest["archive_file"]
    payloads = {"KERNEL": kernel, "SYSTEM": image}
    with tarfile.open(archive_path, "x", format=tarfile.USTAR_FORMAT) as archive:
        for name, path in payloads.items():
            add(archive, config["name"], "target/" + name, path.read_bytes(), config["epoch"])
            add(archive, config["name"], "target/" + name + ".md5",
                (digest(path, "md5") + "  target/" + name + "\n").encode(), config["epoch"])
        add(archive, config["name"], "SOURCE-MANIFEST.json",
            (json.dumps(manifest, indent=2) + "\n").encode(), config["epoch"])
        for name, path in extras.items():
            add(archive, config["name"], name, path.read_bytes(), config["epoch"])
    members = inspect_archive(archive_path, config["name"], payloads, args.work.resolve())
    manifest.update(archive_sha256=digest(archive_path),
                    archive_bytes=archive_path.stat().st_size,
                    archive_members=members, native_checksum_rehearsal_passed=True)
    (output / "package-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (output / (archive_path.name + ".sha256")).write_text(
        digest(archive_path) + "  " + archive_path.name + "\n")
    print(json.dumps({key: manifest[key] for key in
                     ("archive_file", "archive_sha256", "archive_bytes", "system_sha256")}, indent=2))


if __name__ == "__main__":
    main()
