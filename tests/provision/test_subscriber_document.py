"""Pure-logic tests for the Open5GS subscriber-seeder document mapping."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SEED_DIR = REPO_ROOT / "benchmark" / "provision" / "open5gs-core" / "compose" / "seed"
if str(SEED_DIR) not in sys.path:
    sys.path.insert(0, str(SEED_DIR))

from add_users import subscriber_document  # noqa: E402


class SubscriberDocumentTests(unittest.TestCase):
    def _row(self, **overrides) -> dict[str, str]:
        base = {
            "imsi": "001010000000001",
            "k": "00112233445566778899aabbccddeeff",
            "opc": "63bfa50ee6523365ff14c1f45f88737d",
            "amf": "8000",
            "sqn": "000000000000",
            "plmn": "00101",
            "dnn": "internet",
            "sst": "1",
            "sd": "",
            "auth_profile_id": "ue1_test_profile",
            "ue_id": "ue1",
        }
        base.update(overrides)
        return base

    def test_basic_document_shape(self) -> None:
        doc = subscriber_document(self._row())
        self.assertEqual(doc["imsi"], "001010000000001")
        self.assertEqual(doc["security"]["k"], "00112233445566778899aabbccddeeff")
        self.assertEqual(doc["security"]["opc"], "63bfa50ee6523365ff14c1f45f88737d")
        self.assertEqual(doc["security"]["amf"], "8000")
        self.assertEqual(doc["security"]["sqn"], "000000000000")
        # Default subscribed AMBR (Open5GS schema)
        self.assertIn("ambr", doc)

    def test_slice_with_no_sd(self) -> None:
        doc = subscriber_document(self._row(sd=""))
        self.assertEqual(len(doc["slice"]), 1)
        slice0 = doc["slice"][0]
        self.assertEqual(slice0["sst"], 1)
        self.assertNotIn("sd", slice0)  # absent when empty

    def test_slice_with_sd(self) -> None:
        doc = subscriber_document(self._row(sd="123abc"))
        self.assertEqual(doc["slice"][0]["sd"], "123abc")

    def test_session_dnn(self) -> None:
        doc = subscriber_document(self._row(dnn="ims"))
        self.assertEqual(doc["slice"][0]["session"][0]["name"], "ims")

    def test_plmn_split_into_mcc_mnc(self) -> None:
        # Open5GS subscribers don't carry PLMN directly, but auth_profile_id
        # and the related metadata fields are normalized into the document.
        doc = subscriber_document(self._row(plmn="00101"))
        self.assertEqual(doc["meta"]["plmn"]["mcc"], "001")
        self.assertEqual(doc["meta"]["plmn"]["mnc"], "01")

    def test_auth_profile_id_preserved(self) -> None:
        doc = subscriber_document(self._row(auth_profile_id="ue42_lab"))
        self.assertEqual(doc["meta"]["auth_profile_id"], "ue42_lab")

    def test_ue_id_preserved(self) -> None:
        doc = subscriber_document(self._row(ue_id="ue42"))
        self.assertEqual(doc["meta"]["ue_id"], "ue42")

    def test_sst_coerced_to_int(self) -> None:
        doc = subscriber_document(self._row(sst="3"))
        self.assertEqual(doc["slice"][0]["sst"], 3)

    def test_invalid_imsi_rejected(self) -> None:
        with self.assertRaises(ValueError):
            subscriber_document(self._row(imsi=""))

    def test_invalid_k_length_rejected(self) -> None:
        with self.assertRaises(ValueError):
            subscriber_document(self._row(k="deadbeef"))  # not 32 hex chars

    def test_invalid_plmn_rejected(self) -> None:
        with self.assertRaises(ValueError):
            subscriber_document(self._row(plmn="123"))  # not 5 or 6 digits


if __name__ == "__main__":
    unittest.main()
