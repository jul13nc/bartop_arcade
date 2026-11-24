import struct
import json
import shutil
import tempfile
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

from build_image import customize, partition_layout, run, safe_path, set_conf, set_es, squash_options


class ImageConfigurationTests(unittest.TestCase):
    def test_customization_updates_seed_and_share_without_overwriting_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            root, share = base / "root", base / "share"
            version = root / "usr/share/batocera/batocera.version"
            version.parent.mkdir(parents=True)
            version.write_text("44 2026/11/12")
            theme = root / "usr/share/emulationstation/themes/es-theme-carbon/theme.xml"
            theme.parent.mkdir(parents=True)
            theme.write_text('<theme><include name="orange" /></theme>')
            seed = root / "usr/share/batocera/datainit"
            (seed / "system").mkdir(parents=True)
            (seed / "system/batocera.conf").write_text("audio.volume=90\n")
            share.mkdir()
            paths = {}
            for key in ("boot_logo", "es_splash", "splash_video"):
                paths[key] = base / key
                paths[key].write_bytes(b"fixture media")
            for key in ("roms", "bios"):
                paths[key] = base / key
                paths[key].mkdir()
                (paths[key] / "example").write_text(key)
            profile = {"version": "44", "language": "fr_FR", "theme_colorset": "orange"}
            customize(root, share, profile, paths)
            customize(root, share, profile, paths)
            for destination in (seed, share):
                conf = (destination / "system/batocera.conf").read_text()
                self.assertIn("audio.volume=90\n", conf)
                self.assertEqual(conf.count("system.language=fr_FR"), 1)
                self.assertIn("es.customsargs=--splash-image ", conf)
                self.assertEqual((destination / "splash/intro.mp4").read_bytes(), b"fixture media")
            self.assertEqual((share / "roms/example").read_text(), "roms")
            self.assertEqual((share / "bios/example").read_text(), "bios")

    @unittest.skipUnless(shutil.which("sfdisk"), "sfdisk requis")
    def test_partition_growth_preserves_start_type_uuid_and_boot_code(self):
        for label in ("gpt", "dos"):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as folder:
                image = Path(folder) / "test.img"
                with image.open("wb") as stream:
                    stream.truncate(64 * 1024 * 1024)
                run("sfdisk", image, input=f"label: {label}\nsize=16M\nsize=16M\n")
                with image.open("r+b") as stream:
                    stream.write(b"BOOT-CODE")
                before = json.loads(run("sfdisk", "--json", image))["partitiontable"]
                run("sfdisk", "--no-reread", "--no-tell-kernel", "--wipe", "never",
                    "--wipe-partitions", "never", "-N", "2", image, input=", +\n")
                after = json.loads(run("sfdisk", "--json", image))["partitiontable"]
                self.assertEqual(before["partitions"][0], after["partitions"][0])
                for key in ("start", "type", "uuid"):
                    self.assertEqual(before["partitions"][1].get(key), after["partitions"][1].get(key))
                self.assertGreater(after["partitions"][1]["size"], before["partitions"][1]["size"])
                with image.open("rb") as stream:
                    self.assertEqual(stream.read(9), b"BOOT-CODE")

    def test_conf_removes_duplicate_values_and_preserves_unrelated_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "batocera.conf"
            path.write_text("#system.language=en_US\naudio.volume=90\n"
                            "system.language=de_DE\n# explanation\n")
            set_conf(path, {"system.language": "fr_FR"})
            self.assertEqual(path.read_text(), "system.language=fr_FR\naudio.volume=90\n# explanation\n")
            first = path.read_bytes()
            set_conf(path, {"system.language": "fr_FR"})
            self.assertEqual(path.read_bytes(), first)

    def test_es_keeps_other_preferences_and_removes_transient_splash(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "es_settings.cfg"
            path.write_text('<config><bool name="DrawClock" value="false" />'
                            '<string name="ThemeColorSet" value="blue" />'
                            '<string name="AlternateSplashScreen" value="old.png" /></config>')
            set_es(path, "orange")
            root = ET.parse(path).getroot()
            self.assertEqual(root.find('./bool[@name="DrawClock"]').get("value"), "false")
            self.assertEqual(root.find('./string[@name="ThemeColorSet"]').get("value"), "orange")
            self.assertIsNone(root.find('./string[@name="AlternateSplashScreen"]'))

    def test_partition_layout_rejects_extra_or_overlapping_partitions(self):
        table = {"label": "gpt", "sectorsize": 512, "partitions": [
            {"start": 2048, "size": 4096}, {"start": 6144, "size": 1024}]}
        self.assertEqual(len(partition_layout(table)), 2)
        table["partitions"][1]["start"] = 4096
        with self.assertRaises(ValueError):
            partition_layout(table)
        table["partitions"].append({"start": 8192, "size": 1024})
        with self.assertRaises(ValueError):
            partition_layout(table)

    def test_extracted_root_cannot_escape_via_symlink(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "escape").symlink_to("/tmp")
            with self.assertRaises(ValueError):
                safe_path(root, "escape/logo.png")
            self.assertEqual(safe_path(root, "local/logo.png"), root / "local/logo.png")

    def test_squashfs_compression_and_blocksize_detection(self):
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "batocera"
            header = bytearray(96)
            header[:4] = b"hsqs"
            struct.pack_into("<I", header, 12, 131072)
            struct.pack_into("<H", header, 20, 6)
            image.write_bytes(header)
            self.assertEqual(squash_options(image), ("zstd", 131072))
            image.write_bytes(b"not squashfs")
            with self.assertRaises(ValueError):
                squash_options(image)
