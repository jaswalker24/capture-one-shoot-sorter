#!/usr/bin/env python3
"""XMP sidecar writer tests (synthetic files only; no Lightroom, no NAS, no Mac)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import apply_xmp as AX


EXISTING_SIDECAR = """<?xml version="1.0" encoding="UTF-8"?>
<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Adobe XMP Core 7.0">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:xmp="http://ns.adobe.com/xap/1.0/"
    xmlns:lr="http://ns.adobe.com/lightroom/1.0/"
    xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
    xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/"
    xmp:Rating="3"
    xmp:Label="Green"
    xmp:CreateDate="2026-01-15T10:00:00"
    crs:Version="16.3"
    crs:Temperature="5200"
    crs:Tint="+8"
    crs:HasSettings="True"
    crs:Exposure2012="+0.35"
    photoshop:SidecarForExtension="CR3">
   <dc:subject>
    <rdf:Bag>
     <rdf:li>Existing</rdf:li>
     <rdf:li>Keep Me</rdf:li>
    </rdf:Bag>
   </dc:subject>
   <lr:hierarchicalSubject>
    <rdf:Bag>
     <rdf:li>Event|Existing</rdf:li>
    </rdf:Bag>
   </lr:hierarchicalSubject>
   <crs:ToneCurvePV2012>
    <rdf:Seq>
     <rdf:li>0, 0</rdf:li>
     <rdf:li>255, 255</rdf:li>
    </rdf:Seq>
   </crs:ToneCurvePV2012>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>
"""

ODD_NS_SIDECAR = """<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
          xmlns:foo="http://example.com/custom/1.0/">
  <rdf:Description rdf:about=""
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:xap="http://ns.adobe.com/xap/1.0/"
    xap:Rating="5"
    xap:Label="Red"
    foo:CustomField="do-not-touch"
    foo:AlsoKeep="yes">
   <dc:subject>
    <rdf:Bag>
     <rdf:li>Old Leaf</rdf:li>
    </rdf:Bag>
   </dc:subject>
   <foo:ExtraBag>
    <rdf:Bag>
     <rdf:li>custom-value</rdf:li>
    </rdf:Bag>
   </foo:ExtraBag>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
"""

SELF_CLOSING = """<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:Rating="2" xmp:Label="Blue"/>
 </rdf:RDF>
</x:xmpmeta>
"""

CRS_BLOCK = 'crs:Version="16.3"'
CRS_TEMP = 'crs:Temperature="5200"'
CRS_CURVE = "<crs:ToneCurvePV2012>"


class KeywordPaths(unittest.TestCase):
    def test_slash_becomes_pipe(self):
        self.assertEqual(AX.hier_from_keyword("General/Rank/Best"), "General|Rank|Best")
        self.assertEqual(AX.subject_terms("General|Rank|Best"), ["General", "Rank", "Best"])

    def test_already_piped(self):
        self.assertEqual(AX.hier_from_keyword("Cull|Rank Best"), "Cull|Rank Best")


class MergePreservesProtected(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="xmp-"))
        self.img = self.tmp / "IMG_0001.CR3"
        self.img.write_bytes(b"fake-raw-bytes")
        self.xmp = self.tmp / "IMG_0001.xmp"
        self.xmp.write_text(EXISTING_SIDECAR, encoding="utf-8")
        self.original = EXISTING_SIDECAR

    def test_merge_preserves_rating_label_and_crs(self):
        r = AX.apply_to_sidecar(self.img, ["General/Rank/Best", "General/Quality/Sharp"], apply=True, backup=True)
        self.assertFalse(r["failures"], r["failures"])
        self.assertEqual({a["keyword"] for a in r["applied"]}, {"General/Rank/Best", "General/Quality/Sharp"})
        text = self.xmp.read_text(encoding="utf-8")
        after = AX.read_sidecar(text)
        self.assertEqual(after["rating"], "3")
        self.assertEqual(after["label"], "Green")
        self.assertEqual(after["crs"], AX.read_sidecar(self.original)["crs"])
        # Byte-level: protected attributes and the crs curve block are unchanged
        self.assertIn('xmp:Rating="3"', text)
        self.assertIn('xmp:Label="Green"', text)
        self.assertIn('xmp:CreateDate="2026-01-15T10:00:00"', text)
        self.assertIn(CRS_BLOCK, text)
        self.assertIn(CRS_TEMP, text)
        self.assertIn('crs:Tint="+8"', text)
        self.assertIn('crs:Exposure2012="+0.35"', text)
        self.assertIn(CRS_CURVE, text)
        self.assertIn("<rdf:li>0, 0</rdf:li>", text)
        self.assertIn("<rdf:li>255, 255</rdf:li>", text)
        self.assertIn('photoshop:SidecarForExtension="CR3"', text)
        self.assertIn('<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>', text)
        self.assertIn('<?xpacket end="w"?>', text)
        # Existing keywords still there
        self.assertIn("Event|Existing", after["hierarchical"])
        self.assertIn("Existing", after["subjects"])
        self.assertIn("Keep Me", after["subjects"])
        # New hierarchy
        self.assertIn("General|Rank|Best", after["hierarchical"])
        self.assertIn("General|Quality|Sharp", after["hierarchical"])
        self.assertIn("Best", after["subjects"])
        self.assertIn("Sharp", after["subjects"])
        self.assertIn("General", after["subjects"])
        # Image bytes untouched
        self.assertEqual(self.img.read_bytes(), b"fake-raw-bytes")
        self.assertTrue(r["image_untouched"])
        # Backup written
        self.assertTrue((self.tmp / "IMG_0001.xmp.bak").is_file())
        self.assertEqual((self.tmp / "IMG_0001.xmp.bak").read_text(encoding="utf-8"), self.original)

    def test_repeat_run_adds_no_duplicates(self):
        AX.apply_to_sidecar(self.img, ["General/Rank/Best"], apply=True, backup=True)
        first = self.xmp.read_text(encoding="utf-8")
        r2 = AX.apply_to_sidecar(self.img, ["General/Rank/Best"], apply=True, backup=True)
        self.assertEqual(r2["applied"], [])
        self.assertTrue(any(s["reason"] == "already applied" for s in r2["skipped"]))
        second = self.xmp.read_text(encoding="utf-8")
        self.assertEqual(first.count("General|Rank|Best"), 1)
        self.assertEqual(second.count("General|Rank|Best"), 1)
        self.assertEqual(second.count("<rdf:li>Best</rdf:li>"), first.count("<rdf:li>Best</rdf:li>"))

    def test_dry_run_writes_nothing(self):
        r = AX.apply_to_sidecar(self.img, ["General/Rank/Best"], apply=False, backup=True)
        self.assertTrue(all(a.get("dry") for a in r["applied"]))
        self.assertEqual(self.xmp.read_text(encoding="utf-8"), self.original)
        self.assertFalse((self.tmp / "IMG_0001.xmp.bak").exists())


class HierarchyAndCreate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="xmp-new-"))
        self.img = self.tmp / "SHOT.CR3"
        self.img.write_bytes(b"raw")

    def test_creates_minimal_sidecar(self):
        r = AX.apply_to_sidecar(self.img, ["General/Rank/Best", "Cull/Shortlist"], apply=True, backup=True)
        self.assertTrue(r["created"])
        sc = self.tmp / "SHOT.xmp"
        self.assertTrue(sc.is_file())
        info = AX.read_sidecar(sc.read_text(encoding="utf-8"))
        self.assertIn("General|Rank|Best", info["hierarchical"])
        self.assertIn("Cull|Shortlist", info["hierarchical"])
        self.assertIn("Best", info["subjects"])
        self.assertIn("Shortlist", info["subjects"])
        self.assertIsNone(info["rating"])
        self.assertIsNone(info["label"])
        # No backup when there was no previous sidecar
        self.assertFalse(Path(str(sc) + ".bak").exists())

    def test_cull_keyword_hierarchy(self):
        AX.apply_to_sidecar(self.img, ["Cull/Rank Best", "Cull/Sharp + Burst Best"], apply=True)
        info = AX.read_sidecar((self.tmp / "SHOT.xmp").read_text(encoding="utf-8"))
        self.assertIn("Cull|Rank Best", info["hierarchical"])
        self.assertIn("Cull|Sharp + Burst Best", info["hierarchical"])


class OddNamespaces(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="xmp-odd-"))
        self.img = self.tmp / "ODD.CR3"
        self.img.write_bytes(b"raw")
        (self.tmp / "ODD.xmp").write_text(ODD_NS_SIDECAR, encoding="utf-8")

    def test_custom_namespace_and_xap_prefix_preserved(self):
        r = AX.apply_to_sidecar(self.img, ["General/Scene/Street"], apply=True, backup=False)
        self.assertFalse(r["failures"], r["failures"])
        text = (self.tmp / "ODD.xmp").read_text(encoding="utf-8")
        after = AX.read_sidecar(text)
        self.assertEqual(after["rating"], "5")
        self.assertEqual(after["label"], "Red")
        self.assertIn('foo:CustomField="do-not-touch"', text)
        self.assertIn('foo:AlsoKeep="yes"', text)
        self.assertIn("<foo:ExtraBag>", text)
        self.assertIn("<rdf:li>custom-value</rdf:li>", text)
        self.assertIn('xmlns:foo="http://example.com/custom/1.0/"', text)
        self.assertIn("Old Leaf", after["subjects"])
        self.assertIn("General|Scene|Street", after["hierarchical"])
        # xmlns:lr added so hierarchicalSubject is valid
        self.assertIn("xmlns:lr=", text)

    def test_self_closing_description(self):
        img = self.tmp / "FLAT.CR3"
        img.write_bytes(b"raw")
        (self.tmp / "FLAT.xmp").write_text(SELF_CLOSING, encoding="utf-8")
        r = AX.apply_to_sidecar(img, ["General/Quality/Sharp"], apply=True, backup=False)
        self.assertFalse(r["failures"], r["failures"])
        text = (self.tmp / "FLAT.xmp").read_text(encoding="utf-8")
        after = AX.read_sidecar(text)
        self.assertEqual(after["rating"], "2")
        self.assertEqual(after["label"], "Blue")
        self.assertIn('xmp:Rating="2"', text)
        self.assertIn('xmp:Label="Blue"', text)
        self.assertIn("General|Quality|Sharp", after["hierarchical"])


class EmbeddedFormats(unittest.TestCase):
    def test_jpeg_sidecar_written_image_untouched(self):
        tmp = Path(tempfile.mkdtemp(prefix="xmp-jpg-"))
        img = tmp / "frame.jpg"
        payload = b"\xff\xd8\xfffake-jpeg"
        img.write_bytes(payload)
        r = AX.apply_to_sidecar(img, ["General/Rank/Best"], apply=True)
        self.assertTrue(r["embedded_format"])
        self.assertIn("Read Metadata from Files", r["note"])
        self.assertEqual(img.read_bytes(), payload)
        self.assertTrue(r["image_untouched"])
        self.assertTrue((tmp / "frame.xmp").is_file())

    def test_dng_reported_and_not_embedded(self):
        tmp = Path(tempfile.mkdtemp(prefix="xmp-dng-"))
        img = tmp / "scan.dng"
        payload = b"DNG-BYTES-000"
        img.write_bytes(payload)
        r = AX.apply_to_sidecar(img, ["General/Scene/Architecture"], apply=True)
        self.assertTrue(r["embedded_format"])
        self.assertEqual(img.read_bytes(), payload)
        self.assertTrue((tmp / "scan.xmp").is_file())


class ApplyProposals(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="xmp-prop-"))
        self.folder = self.tmp / "shoot"
        self.folder.mkdir()
        self.img = self.folder / "IMG_0001.CR3"
        self.img.write_bytes(b"the-original")
        import hashlib
        self.digest = hashlib.sha256(b"the-original").hexdigest()
        self.out = self.tmp / "out"
        self.out.mkdir()

    def _proposals(self, images):
        p = self.out / "proposals.json"
        json.dump({"folder": str(self.folder), "images": images}, open(p, "w"))
        return p

    def test_policy_skips_and_sha(self):
        props = self._proposals([
            {"filename": "IMG_0001.CR3", "sha256": "deadbeef", "proposed_keywords": ["General/Rank/Best"]},
            {"filename": "IMG_0002.CR3", "sha256": self.digest, "needs_review": True,
             "review_reasons": ["low confidence"], "proposed_keywords": ["X"]},
            {"filename": "_schedule.jpg", "proposed_keywords": ["Ignore/Me"]},
            {"filename": "IMG_0003.CR3", "error": "boom", "proposed_keywords": ["X"]},
        ])
        res = AX.apply_proposals(str(props), apply=True, prefix=str(self.out / "run"), backup=False)
        reasons = {s["filename"]: s["reason"] for s in res["skipped"]}
        self.assertIn("sha256 mismatch", reasons["IMG_0001.CR3"])
        self.assertTrue(reasons["IMG_0002.CR3"].startswith("needs_review"))
        self.assertEqual(reasons["_schedule.jpg"], "schedule file")
        self.assertEqual(reasons["IMG_0003.CR3"], "tagger error")
        self.assertEqual(res["applied"], [])
        self.assertFalse((self.folder / "IMG_0001.xmp").exists())

    def test_apply_then_idempotent_via_proposals(self):
        props = self._proposals([
            {"filename": "IMG_0001.CR3", "sha256": self.digest,
             "proposed_keywords": ["General/Rank/Best", "General/Quality/Sharp"]},
        ])
        r1 = AX.apply_proposals(str(props), apply=True, prefix=str(self.out / "a"), backup=True)
        self.assertEqual(len(r1["applied"]), 2)
        r2 = AX.apply_proposals(str(props), apply=True, prefix=str(self.out / "b"), backup=True)
        self.assertEqual(r2["applied"], [])
        self.assertEqual({s["reason"] for s in r2["skipped"] if s.get("keyword")}, {"already applied"})
        text = (self.folder / "IMG_0001.xmp").read_text(encoding="utf-8")
        self.assertEqual(text.count("General|Rank|Best"), 1)
        self.assertEqual(text.count("General|Quality|Sharp"), 1)

    def test_undo_removes_only_added_keywords(self):
        (self.folder / "IMG_0001.xmp").write_text(EXISTING_SIDECAR, encoding="utf-8")
        props = self._proposals([
            {"filename": "IMG_0001.CR3", "sha256": self.digest,
             "proposed_keywords": ["General/Rank/Best"]},
        ])
        AX.apply_proposals(str(props), apply=True, prefix=str(self.out / "c"), backup=True)
        plan = {
            "folder": str(self.folder),
            "items": [{"filename": "IMG_0001.CR3", "keywords": ["General/Rank/Best"]}],
        }
        res = AX.undo_plan(plan, apply=True, prefix=str(self.out / "undo"), backup=True)
        self.assertEqual(len(res["removed"]), 1)
        after = AX.read_sidecar((self.folder / "IMG_0001.xmp").read_text(encoding="utf-8"))
        self.assertNotIn("General|Rank|Best", after["hierarchical"])
        self.assertNotIn("Best", after["subjects"])
        # Pre-existing kept
        self.assertIn("Event|Existing", after["hierarchical"])
        self.assertIn("Keep Me", after["subjects"])
        self.assertEqual(after["rating"], "3")
        self.assertEqual(after["label"], "Green")


class SnapFolder(unittest.TestCase):
    def test_reads_rating_and_label(self):
        tmp = Path(tempfile.mkdtemp(prefix="xmp-snap-"))
        img = tmp / "A.CR3"
        img.write_bytes(b"raw")
        (tmp / "A.xmp").write_text(EXISTING_SIDECAR, encoding="utf-8")
        (tmp / "clip.MP4").write_bytes(b"mov")
        (tmp / "_schedule.jpg").write_bytes(b"poster")
        snap = AX.snap_folder(tmp)
        self.assertIn("A.CR3", snap)
        self.assertEqual(snap["A.CR3"]["rating"], "3")
        self.assertEqual(snap["A.CR3"]["color"], "Green")
        self.assertNotIn("clip.MP4", snap)
        self.assertNotIn("_schedule.jpg", snap)



class CullKeywords(unittest.TestCase):
    def test_writes_cull_keywords_from_sidecar_tags(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import cull_album as CA
        tmp = Path(tempfile.mkdtemp(prefix="xmp-cull-"))
        # Sharp + Burst Best
        a = tmp / "A.CR3"; a.write_bytes(b"a")
        AX.apply_to_sidecar(a, ["General/Quality/Sharp", "General/Burst Best"], apply=True, backup=False)
        # Rank Best only
        b = tmp / "B.CR3"; b.write_bytes(b"b")
        AX.apply_to_sidecar(b, ["General/Rank/Best"], apply=True, backup=False)
        # Sharp but blurred — excluded
        c = tmp / "C.CR3"; c.write_bytes(b"c")
        AX.apply_to_sidecar(c, ["General/Quality/Sharp", "General/Burst Best", "General/Quality/Motion Blur"], apply=True, backup=False)
        plan = CA.proposals_for(tmp, CA.frames_from_folder(tmp))
        by = {im["filename"]: im["proposed_keywords"] for im in plan["images"]}
        self.assertIn("Cull/Sharp + Burst Best", by["A.CR3"])
        self.assertIn("Cull/Shortlist", by["A.CR3"])
        self.assertIn("Cull/Rank Best", by["B.CR3"])
        self.assertIn("Cull/Shortlist", by["B.CR3"])
        self.assertNotIn("C.CR3", by)
        # apply and confirm hierarchy in the sidecar
        prop = tmp / "plan.json"
        prop.write_text(json.dumps({"folder": str(tmp), "images": plan["images"]}))
        res = AX.apply_proposals(str(prop), apply=True, prefix=str(tmp / "cull"), backup=False)
        self.assertFalse(res["failures"], res["failures"])
        info_a = AX.read_sidecar((tmp / "A.xmp").read_text(encoding="utf-8"))
        self.assertIn("Cull|Sharp + Burst Best", info_a["hierarchical"])
        self.assertIn("Cull|Shortlist", info_a["hierarchical"])
        # original keywords still present
        self.assertIn("General|Quality|Sharp", info_a["hierarchical"])
        info_c = AX.read_sidecar((tmp / "C.xmp").read_text(encoding="utf-8"))
        self.assertNotIn("Cull|Shortlist", info_c["hierarchical"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
