"""Verify an offline update candidate; never copy files to a device or reboot it."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile


def stream_hash(stream, algorithm="sha256"):
    value = hashlib.new(algorithm)
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        value.update(block)
    return value.hexdigest()


def require_installable(manifest):
    certification = manifest.get("installation_certification")
    if not isinstance(certification, dict) or certification.get("passed") is not True:
        reasons = certification.get("blockers", []) if isinstance(certification, dict) else []
        raise RuntimeError("INSTALLATION BLOCKED: " + "; ".join(reasons or ["no positive certification"]))


def verify(directory):
    manifest = json.loads((directory / "package-manifest.json").read_text())
    filename = manifest["archive_file"]
    if Path(filename).name != filename or "\\" in filename:
        raise RuntimeError("Unsafe archive filename")
    archive_path = directory / filename
    with archive_path.open("rb") as stream:
        if stream_hash(stream) != manifest["archive_sha256"]:
            raise RuntimeError("Archive SHA256 mismatch")
    if archive_path.stat().st_size != manifest["archive_bytes"]:
        raise RuntimeError("Archive size mismatch")
    root = filename.removesuffix(".tar")
    with tarfile.open(archive_path) as archive:
        members = archive.getmembers()
        if len({item.name for item in members}) != len(members):
            raise RuntimeError("Duplicate archive member")
        for item in members:
            parts = PurePosixPath(item.name).parts
            if (not item.isfile() or not parts or parts[0] != root or
                    ".." in parts or item.name.startswith("/") or "\\" in item.name):
                raise RuntimeError("Unsafe archive member")
        embedded = json.load(archive.extractfile(root + "/SOURCE-MANIFEST.json"))
        for key, value in embedded.items():
            if manifest.get(key) != value:
                raise RuntimeError("Embedded/adjacent manifest mismatch: " + key)
        expected_names = {root + "/SOURCE-MANIFEST.json"}
        for name in ("KERNEL", "SYSTEM"):
            member_name = root + "/target/" + name
            expected_names.update((member_name, member_name + ".md5"))
            member = archive.getmember(member_name)
            if member.size != manifest[name.lower() + "_bytes"]:
                raise RuntimeError("Payload size mismatch: " + name)
            with archive.extractfile(member) as stream:
                if stream_hash(stream) != manifest[name.lower() + "_sha256"]:
                    raise RuntimeError("Payload SHA256 mismatch: " + name)
            with archive.extractfile(member) as stream:
                checksum = stream_hash(stream, "md5") + "  target/" + name + "\n"
            if archive.extractfile(member_name + ".md5").read().decode() != checksum:
                raise RuntimeError("Native MD5 path mismatch: " + name)
        for name, expected in manifest["archive_files_sha256"].items():
            member_name = root + "/" + name
            expected_names.add(member_name)
            with archive.extractfile(member_name) as stream:
                if stream_hash(stream) != expected:
                    raise RuntimeError("Packaged source/document mismatch: " + name)
        if {item.name for item in members} != expected_names:
            raise RuntimeError("Unexpected archive member")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--require-installable", action="store_true")
    args = parser.parse_args()
    manifest = verify(args.directory)
    print("Archive, payloads, native MD5 paths and source/document hashes: PASS")
    print("Installation certification: " +
          json.dumps(manifest["installation_certification"], sort_keys=True))
    if args.require_installable:
        require_installable(manifest)


if __name__ == "__main__":
    main()
