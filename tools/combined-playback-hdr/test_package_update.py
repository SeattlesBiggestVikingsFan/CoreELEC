import hashlib
import io
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

from package_update import add, inspect_archive, mutation_name, repack, tree


class PackageTests(unittest.TestCase):
    def test_native_checksum_paths_and_extracted_payloads(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payloads = {}
            for name in ("KERNEL", "SYSTEM"):
                payloads[name] = root / name
                payloads[name].write_bytes((name + "-new-payload").encode())
            archive_path = root / "update.tar"
            with tarfile.open(archive_path, "w") as archive:
                for name, path in payloads.items():
                    add(archive, "update", "target/" + name, path.read_bytes(), 0)
                    checksum = hashlib.md5(path.read_bytes()).hexdigest()
                    add(archive, "update", "target/" + name + ".md5",
                        (checksum + "  target/" + name + "\n").encode(), 0)
            self.assertEqual(inspect_archive(archive_path, "update", payloads, root), 4)

    def test_bare_checksum_target_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "SYSTEM"
            payload.write_bytes(b"system")
            archive_path = root / "update.tar"
            with tarfile.open(archive_path, "w") as archive:
                add(archive, "update", "target/SYSTEM", payload.read_bytes(), 0)
                checksum = hashlib.md5(payload.read_bytes()).hexdigest()
                add(archive, "update", "target/SYSTEM.md5",
                    (checksum + "  SYSTEM\n").encode(), 0)
            with self.assertRaisesRegex(RuntimeError, "Incorrect native MD5"):
                inspect_archive(archive_path, "update", {"SYSTEM": payload}, root)

    def test_unsafe_duplicate_and_link_members_are_rejected(self):
        for kind in ("traversal", "duplicate", "link"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive_path = root / "update.tar"
                with tarfile.open(archive_path, "w") as archive:
                    item = tarfile.TarInfo("update/../escape" if kind == "traversal" else "update/file")
                    if kind == "link":
                        item.type, item.linkname = tarfile.SYMTYPE, "/etc/passwd"
                        archive.addfile(item)
                    else:
                        item.size = 1
                        archive.addfile(item, io.BytesIO(b"x"))
                        if kind == "duplicate":
                            archive.addfile(item, io.BytesIO(b"y"))
                with self.assertRaises(RuntimeError):
                    inspect_archive(archive_path, "update", {}, root)

    def test_pseudo_repack_preserves_links_namespace_and_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed, output, work = root / "seed", root / "output", root / "work"
            seed.mkdir()
            output.mkdir()
            work.mkdir()
            (seed / "replace").write_bytes(b"old")
            (seed / "keep").write_bytes(b"unchanged")
            (seed / "keep").chmod(0o640)
            (seed / "hardlink").hardlink_to(seed / "keep")
            (seed / "symlink").symlink_to("keep")
            base, image = root / "base.img", output / "SYSTEM.img"
            subprocess.run(
                ["mksquashfs", str(seed), str(base), "-noappend", "-comp", "lzo",
                 "-no-xattrs", "-no-progress", "-processors", "2",
                 "-root-time", "1794629968", "-root-mode", "755",
                 "-root-uid", "0", "-root-gid", "0"],
                check=True, stdout=subprocess.DEVNULL,
            )
            replacement = root / "new"
            replacement.write_bytes(b"new")
            report = repack(base, {"replace": replacement}, image, work, output, 1794873600)
            self.assertEqual(report["changed_paths"], ["replace"])
            self.assertEqual(report["hardlink_groups"], 1)
            self.assertEqual(report["symlinks"], 1)
            before, _ = tree(work / "base-root")
            after, _ = tree(work / "combined-root")
            self.assertEqual(before["keep"], after["keep"])
            self.assertEqual(before["replace"]["mtime"], after["replace"]["mtime"])
            second_work, second_output = root / "second-work", root / "second-output"
            second_work.mkdir()
            second_output.mkdir()
            second_image = second_output / "SYSTEM.img"
            repack(base, {"replace": replacement}, second_image, second_work,
                   second_output, 1794873600)
            self.assertEqual(image.read_bytes(), second_image.read_bytes())

    def test_explicit_namespace_delta_and_symlink_update(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed, output, work = root / "seed", root / "output", root / "work"
            for path in (seed, output, work):
                path.mkdir()
            (seed / "replace").write_bytes(b"old")
            (seed / "remove").write_bytes(b"obsolete")
            (seed / "keep").write_bytes(b"retained")
            (seed / "hardlink").hardlink_to(seed / "keep")
            (seed / "symlink").symlink_to("keep")
            base = root / "base.img"
            subprocess.run(
                ["mksquashfs", str(seed), str(base), "-noappend", "-no-xattrs",
                 "-no-progress", "-processors", "2"],
                check=True, stdout=subprocess.DEVNULL,
            )
            replacement, added = root / "replacement", root / "added"
            replacement.write_bytes(b"updated")
            added.write_bytes(b"new module bytes")
            additions = {
                "new": {"type": "directory", "mode": 0o755},
                "new/module.ko": {"type": "file", "mode": 0o644, "source": added},
                "new/link": {"type": "symlink", "mode": 0o777, "target": "module.ko"},
            }
            report = repack(
                base, {"replace": replacement}, output / "SYSTEM.img", work,
                output, 1795046400, additions=additions, removals=["remove"],
                symlinks={"symlink": "new/module.ko"},
                replacement_mtimes={"replace": 1795065895},
            )
            self.assertEqual(report["added_paths"], sorted(additions))
            self.assertEqual(report["removed_paths"], ["remove"])
            self.assertEqual(report["changed_paths"], ["replace", "symlink"])
            self.assertEqual(report["hardlink_groups"], 1)
            self.assertEqual(report["paths"], report["base_paths"] + 2)
            before, _ = tree(work / "base-root")
            after, _ = tree(work / "combined-root")
            self.assertEqual(before["."], after["."])
            self.assertEqual(before["keep"], after["keep"])
            self.assertEqual(after["replace"]["mtime"], 1795065895)
            for key in ("mode", "type", "uid", "gid"):
                self.assertEqual(before["replace"][key], after["replace"][key])
            self.assertEqual(after["new/module.ko"]["sha256"],
                             hashlib.sha256(added.read_bytes()).hexdigest())

    def test_noncanonical_mutation_paths_are_rejected(self):
        for value in ("", ".", "../file", "/file", "a//b", "a/./b", "a\\b", "a b"):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                mutation_name(value)

    def test_replacement_mtime_requires_a_locked_replacement_and_valid_time(self):
        for value in ({"keep": 1}, {"replace": True}, {"replace": -1},
                      {"replace": 0x100000000}):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                with self.assertRaisesRegex(RuntimeError, "Invalid replacement-mtime"):
                    repack(root / "base", {"replace": root / "new"}, root / "image",
                           root / "work", root / "output", 0,
                           replacement_mtimes=value)


if __name__ == "__main__":
    unittest.main()
