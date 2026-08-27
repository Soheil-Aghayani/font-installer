import tempfile
import unittest
from pathlib import Path

from font_manager import FontIndex, FontManager


VALID_TTF = b"\x00\x01\x00\x00" + b"font-installer-test".ljust(20, b"\0")


class FontManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.assets = self.root / "assets"
        self.user_fonts = self.root / "user-fonts"
        self.system_fonts = self.root / "system-fonts"
        self.assets.mkdir()
        self.manager = FontManager(
            platform_name="Linux",
            user_font_dir=self.user_fonts,
            system_font_dir=self.system_fonts,
            data_dir=self.root / "data",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_font(self, relative_path, content=VALID_TTF):
        path = self.assets / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_discovery_removes_only_byte_identical_files(self):
        first = self.write_font("one/Regular.ttf")
        self.write_font("two/Regular.ttf")
        second = self.write_font("three/Regular.ttf", VALID_TTF + b"different")

        discovered = self.manager.discover_fonts(self.assets)

        self.assertEqual(len(discovered), 2)
        self.assertIn(first, discovered)
        self.assertIn(second, discovered)

    def test_persistent_index_exposes_duplicate_groups(self):
        first = self.write_font("one/Regular.ttf")
        duplicate = self.write_font("two/Regular.ttf")
        unique = self.write_font("three/Regular.ttf", VALID_TTF + b"different")
        cache_path = self.root / "font-index.sqlite"

        discovered = self.manager.discover_all_fonts(self.assets, cache_path)
        groups = self.manager.duplicate_groups(self.assets, cache_path)

        self.assertTrue(cache_path.exists())
        self.assertEqual(discovered, [first, unique, duplicate])
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[next(iter(groups))], [first, duplicate])

        counting_cache = self.root / "counting-index.sqlite"
        digest_calls = []

        def counting_digest(path):
            digest_calls.append(path)
            return FontManager.sha256(path)

        index = FontIndex(counting_cache)
        index.scan(self.assets, counting_digest)
        first_scan_calls = len(digest_calls)
        index.scan(self.assets, counting_digest)
        self.assertEqual(first_scan_calls, 3)
        self.assertEqual(len(digest_calls), first_scan_calls)

    def test_install_is_idempotent(self):
        source = self.write_font("Vazir-Regular.ttf")

        first = self.manager.install([source], "user")
        second = self.manager.install([source], "user")

        self.assertEqual(first.changed, [self.user_fonts / source.name])
        self.assertEqual(len(second.skipped), 1)
        self.assertFalse(second.failed)

    def test_installing_to_another_scope_is_not_skipped(self):
        source = self.write_font("Vazir-Regular.ttf")

        user_result = self.manager.install([source], "user")
        system_result = self.manager.install([source], "system")

        self.assertEqual(len(user_result.changed), 1)
        self.assertEqual(system_result.changed, [self.system_fonts / source.name])
        self.assertTrue((self.user_fonts / source.name).exists())
        self.assertTrue((self.system_fonts / source.name).exists())

    def test_same_name_different_content_is_not_overwritten(self):
        first = self.write_font("first/Regular.ttf")
        second = self.write_font("second/Regular.ttf", VALID_TTF + b"different")

        self.manager.install([first], "user")
        result = self.manager.install([second], "user")

        self.assertFalse(result.changed)
        self.assertEqual(len(result.failed), 1)
        self.assertIn("different file", result.failed[0][1])

    def test_undo_reverts_install(self):
        source = self.write_font("Vazir-Regular.ttf")

        installed = self.manager.install([source], "user")
        undone = self.manager.undo_last()

        self.assertEqual(len(installed.changed), 1)
        self.assertEqual(undone.changed, [self.user_fonts / source.name])
        self.assertFalse((self.user_fonts / source.name).exists())
        self.assertTrue(self.manager.history_records()[-1]["undone"])

    def test_undo_refuses_to_replace_a_changed_file(self):
        source = self.write_font("Vazir-Regular.ttf")
        self.manager.install([source], "user")
        destination = self.user_fonts / source.name
        changed_content = VALID_TTF + b"changed outside the app"
        destination.write_bytes(changed_content)

        result = self.manager.undo_last()

        self.assertFalse(result.changed)
        self.assertEqual(len(result.failed), 1)
        self.assertIn("changed after installation", result.failed[0][1])
        self.assertEqual(destination.read_bytes(), changed_content)
        self.assertFalse(self.manager.history_records()[-1]["undone"])

    def test_uninstall_backup_can_restore_the_font(self):
        source = self.write_font("Vazir-Regular.ttf")
        self.manager.install([source], "user")

        removed = self.manager.uninstall(source, scope="user")
        restored = self.manager.undo_last()

        self.assertEqual(removed.changed, [self.user_fonts / source.name])
        self.assertTrue(removed.backups)
        self.assertTrue(removed.backups[0].exists())
        self.assertEqual(restored.changed, [self.user_fonts / source.name])
        self.assertEqual((self.user_fonts / source.name).read_bytes(), VALID_TTF)

    def test_asset_removal_backup_can_restore_duplicate_copy(self):
        source = self.write_font("duplicate/Vazir-Regular.ttf")

        removed = self.manager.remove_asset_files([source])

        self.assertEqual(removed.changed, [source])
        self.assertFalse(source.exists())

        restored = self.manager.undo_last()

        self.assertEqual(restored.changed, [source])
        self.assertEqual(source.read_bytes(), VALID_TTF)

    def test_uninstall_refuses_an_unrelated_file(self):
        source = self.write_font("Vazir-Regular.ttf")
        self.user_fonts.mkdir()
        (self.user_fonts / source.name).write_bytes(VALID_TTF + b"unrelated")

        result = self.manager.uninstall(source)

        self.assertFalse(result.changed)
        self.assertEqual(len(result.failed), 1)
        self.assertIn("refused", result.failed[0][1])
        self.assertTrue((self.user_fonts / source.name).exists())

    def test_validation_rejects_invalid_file(self):
        invalid = self.write_font("broken.ttf", b"not-a-font")

        result = self.manager.validate([invalid])

        self.assertFalse(result.changed)
        self.assertEqual(result.failed[0][0], invalid)


if __name__ == "__main__":
    unittest.main()
