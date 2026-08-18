import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from .shipping_defaults import (
    MANIFEST_RELATIVE_PATH,
    ShippingDefaultsError,
    load_strict_json_bytes,
    validate_manifest,
    verify_repository,
)


ROOT = Path(__file__).resolve().parents[2]


class ShippingDefaultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = (ROOT / MANIFEST_RELATIVE_PATH).read_bytes()
        cls.document = load_strict_json_bytes(cls.payload)
        cls.receipt_source = (ROOT / "platform/qt/ReceiptSettings.cpp").read_text(encoding="utf-8")

    def assert_invalid(self, document: dict) -> None:
        with self.assertRaises(ShippingDefaultsError):
            validate_manifest(document, self.receipt_source)

    def test_repository_snapshot_is_valid(self) -> None:
        verify_repository(ROOT)

    def test_duplicate_key_is_rejected(self) -> None:
        payload = self.payload.replace(
            b'{\n  "schema":', b'{\n  "schema": "mlvapp.shipping-defaults.v1",\n  "schema":', 1
        )
        with self.assertRaises(ShippingDefaultsError):
            load_strict_json_bytes(payload)

    def test_invalid_utf8_is_rejected(self) -> None:
        with self.assertRaises(ShippingDefaultsError):
            load_strict_json_bytes(self.payload + b"\xff")

    def test_nonfinite_number_is_rejected(self) -> None:
        with self.assertRaises(ShippingDefaultsError):
            load_strict_json_bytes(b'{"value": NaN}')

    def test_unknown_schema_is_rejected(self) -> None:
        document = copy.deepcopy(self.document)
        document["schema"] = "mlvapp.shipping-defaults.v2"
        self.assert_invalid(document)

    def test_extra_and_missing_keys_are_rejected(self) -> None:
        extra = copy.deepcopy(self.document)
        extra["unexpected"] = True
        self.assert_invalid(extra)
        missing = copy.deepcopy(self.document)
        del missing["processing"]["freshReceipt"]["m_exposure"]
        self.assert_invalid(missing)

    def test_boolean_cannot_masquerade_as_integer(self) -> None:
        document = copy.deepcopy(self.document)
        document["processing"]["freshReceipt"]["m_exposure"] = False
        self.assert_invalid(document)

    def test_playback_boolean_integer_substitutions_are_rejected(self) -> None:
        mutations = (
            ("qualityMode", "value", True),
            ("previewMode", "value", False),
            ("showQualityIndicator", None, 1),
            ("preferHqMean23", "derived", 1),
        )
        for key, container, value in mutations:
            with self.subTest(key=key):
                document = copy.deepcopy(self.document)
                if container == "value":
                    document["playback"][key]["value"] = value
                elif container == "derived":
                    document["playback"]["derived"][key] = value
                else:
                    document["playback"][key] = value
                self.assert_invalid(document)

    def test_enum_name_value_disagreement_is_rejected(self) -> None:
        document = copy.deepcopy(self.document)
        document["processing"]["freshReceipt"]["m_debayer"] = {"name": "amaze", "value": 4}
        self.assert_invalid(document)

    def test_new_constructor_default_requires_manifest_entry(self) -> None:
        changed_source = self.receipt_source.replace(
            "    m_mark = 0;", "    m_newOutputDefault = 1;\n    m_mark = 0;"
        )
        with self.assertRaises(ShippingDefaultsError):
            validate_manifest(copy.deepcopy(self.document), changed_source)

    def test_source_mirror_drift_is_rejected(self) -> None:
        temp = Path(tempfile.mkdtemp(prefix="shipping-defaults-test-"))
        try:
            for relative in (
                MANIFEST_RELATIVE_PATH,
                Path("platform/qt/ReceiptSettings.cpp"),
                Path("platform/qt/MainWindow.cpp"),
                Path("platform/qt/MainWindow.ui"),
            ):
                target = temp / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / relative, target)
            ui_path = temp / "platform/qt/MainWindow.ui"
            ui_text = ui_path.read_text(encoding="utf-8")
            widget_offset = ui_text.index('name="checkBoxLookAssistEnable"')
            checked_offset = ui_text.index("<bool>true</bool>", widget_offset)
            ui_text = (
                ui_text[:checked_offset]
                + "<bool>false</bool>"
                + ui_text[checked_offset + len("<bool>true</bool>") :]
            )
            ui_path.write_text(ui_text, encoding="utf-8")
            with self.assertRaises(ShippingDefaultsError):
                verify_repository(temp)

            shutil.copy2(ROOT / "platform/qt/MainWindow.ui", ui_path)
            source_path = temp / "platform/qt/MainWindow.cpp"
            source_path.write_text(
                source_path.read_text(encoding="utf-8").replace(
                    "PlaybackQualitySettings::kDefaultScaleFactorOverride() ).toInt()",
                    "4 ).toInt()",
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ShippingDefaultsError):
                verify_repository(temp)
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    def test_serialized_manifest_remains_strictly_parseable(self) -> None:
        round_trip = json.dumps(self.document, allow_nan=False).encode("utf-8")
        self.assertEqual(self.document, load_strict_json_bytes(round_trip))


if __name__ == "__main__":
    unittest.main()
