import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_resources import check_file, extract, install, relative


def record(payload):
    return {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


class ResourceTests(unittest.TestCase):
    def test_paths(self):
        for name in ("../secret", "/etc/passwd", "a/../../x", "a\\b", "C:/temp", ""):
            with self.assertRaises(ValueError):
                relative(name)
        self.assertEqual(relative("weights/model.pt").as_posix(), "weights/model.pt")

    def test_check_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test"
            path.write_bytes(b"hello")
            self.assertEqual(check_file(path, record(b"hello")), "ok")
            self.assertEqual(check_file(path, record(b"other")), "sha256 mismatch")
            self.assertEqual(check_file(path, record(b"hi")), "size mismatch")

    def test_archive_rejects_symlinks_and_extra_files(self):
        for kind in ("link", "extra", "escape", "duplicate"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); archive = root / "asset.tar.gz"
                with tarfile.open(archive, "w:gz") as stream:
                    member = tarfile.TarInfo("good.txt")
                    member.size = 2
                    stream.addfile(member, io.BytesIO(b"hi"))
                    other = tarfile.TarInfo({"link": "link.txt", "extra": "extra.txt", "escape": "../escape", "duplicate": "good.txt"}[kind])
                    if kind == "link":
                        other.type = tarfile.SYMTYPE; other.linkname = "../secret"
                        stream.addfile(other)
                    else:
                        other.size = 2; stream.addfile(other, io.BytesIO(b"hi"))
                target = root / "out"; target.mkdir()
                with self.assertRaises(ValueError):
                    extract(archive, target, {"good.txt": record(b"hi")})

    def test_install_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); cache = root / "cache"; cache.mkdir()
            archive = cache / "test.tar.gz"
            with tarfile.open(archive, "w:gz") as stream:
                member = tarfile.TarInfo("weights/test.pt"); member.size = 2
                stream.addfile(member, io.BytesIO(b"hi"))
            manifest = {"files": [{"path": "weights/test.pt", "group": "test", "in_git": False, **record(b"hi")}],
                        "assets": [{"name": archive.name, "group": "test", "files": ["weights/test.pt"], **record(archive.read_bytes())}]}
            destination = root / "resources"
            install(manifest, {"test"}, cache, destination)
            self.assertEqual((destination / "weights/test.pt").read_bytes(), b"hi")
            with self.assertRaises(ValueError):
                install(manifest, {"test"}, cache, destination)

    def test_split_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); cache = root / "cache"; cache.mkdir()
            parts = []
            for name, payload in (("part01", b"he"), ("part02", b"llo")):
                (cache / name).write_bytes(payload)
                parts.append({"name": name, "group": "test", "files": [], **record(payload)})
            manifest = {"files": [{"path": "reference/test", "group": "test", "in_git": False,
                                   "parts": ["part01", "part02"], **record(b"hello")}], "assets": parts}
            install(manifest, {"test"}, cache, root / "out")
            self.assertEqual((root / "out/reference/test").read_bytes(), b"hello")


if __name__ == "__main__":
    unittest.main()
