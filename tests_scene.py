#!/usr/bin/env python3
"""Unit tests for scene_pass selection (no model, no Capture One)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import scene_pass as S

TH = {n: 0.88 for n in S.VOCAB}

def test_vocab_unique_and_paths():
    assert len(S.VOCAB) == len(set(S.VOCAB))
    assert 30 <= len(S.VOCAB) <= 50
    for name in S.VOCAB:
        assert "/" not in name
        assert S.keyword_path(name).startswith("General/Scene/")
        pos, neg, th = S.VOCAB[name]
        assert pos and neg and pos != neg
        assert 0.5 <= th <= 0.99
    for a, b in S.EXCLUSIVE:
        assert a in S.VOCAB and b in S.VOCAB
    for c, parents in S.IMPLIES.items():
        assert c in S.VOCAB
        for p in parents:
            assert p in S.VOCAB

def test_below_threshold_empty():
    scores = {n: 0.2 for n in S.VOCAB}
    assert S.select_labels(scores, TH, 6) == []

def test_cap_and_order():
    scores = {n: 0.2 for n in S.VOCAB}
    scores["Architecture"] = 0.99
    scores["Street"] = 0.97
    scores["Night"] = 0.95
    scores["Neon"] = 0.94
    scores["Food"] = 0.93
    scores["Market"] = 0.92
    scores["Signage"] = 0.91
    chosen = S.select_labels(scores, TH, 6)
    assert chosen[:6] == ["Architecture", "Street", "Night", "Neon", "Food", "Market"]
    assert "Signage" not in chosen
    assert len(chosen) == 6

def test_implications_motorbike():
    scores = {n: 0.2 for n in S.VOCAB}
    scores["Motorbike"] = 0.96
    scores["Vehicle"] = 0.40  # below th, above IMPLIES_MIN_SCORE
    chosen = S.select_labels(scores, TH, 6)
    assert "Motorbike" in chosen
    assert "Vehicle" in chosen

def test_exclusive_concert_vs_motorsport():
    scores = {n: 0.2 for n in S.VOCAB}
    scores["Concert"] = 0.95
    scores["Motorsport"] = 0.90
    chosen = S.select_labels(scores, {**TH, "Motorsport": 0.80, "Concert": 0.82}, 6)
    # both high: higher source (Concert) wins and suppresses the other
    assert "Concert" in chosen and "Motorsport" not in chosen
    scores["Concert"] = 0.60
    scores["Motorsport"] = 0.95
    chosen = S.select_labels(scores, {**TH, "Motorsport": 0.80, "Concert": 0.82}, 6)
    assert "Motorsport" in chosen and "Concert" not in chosen

def test_portrait_implies_people_excludes_crowd():
    scores = {n: 0.2 for n in S.VOCAB}
    scores["Portrait"] = 0.97
    scores["Crowd"] = 0.90
    scores["People"] = 0.80  # below threshold, implied
    chosen = S.select_labels(scores, {**TH, "Portrait": 0.92}, 6)
    assert "Portrait" in chosen and "People" in chosen and "Crowd" not in chosen

def test_motorsport_suppresses_nature_and_portrait():
    scores = {n: 0.2 for n in S.VOCAB}
    scores["Motorsport"] = 0.85
    scores["Nature"] = 0.99
    scores["Portrait"] = 0.99
    scores["People"] = 0.95
    scores["Vehicle"] = 0.60
    chosen = S.select_labels(scores, {**TH, "Motorsport": 0.80, "Portrait": 0.92, "Vehicle": 0.85}, 6)
    assert "Motorsport" in chosen
    assert "Vehicle" in chosen  # implied
    assert "Nature" not in chosen
    assert "Portrait" not in chosen
    assert "People" not in chosen

def test_alley_does_not_imply_zero_street():
    scores = {n: 0.2 for n in S.VOCAB}
    scores["Alley"] = 0.96
    scores["Street"] = 0.01
    chosen = S.select_labels(scores, {**TH, "Alley": 0.92}, 6)
    assert "Alley" in chosen
    assert "Street" not in chosen

def test_stage_implies_concert():
    scores = {n: 0.2 for n in S.VOCAB}
    scores["Stage"] = 0.95
    scores["Concert"] = 0.50
    chosen = S.select_labels(scores, {**TH, "Stage": 0.90, "Concert": 0.82}, 6)
    assert "Stage" in chosen and "Concert" in chosen

def test_temple_suppresses_water():
    scores = {n: 0.2 for n in S.VOCAB}
    scores["Temple"] = 0.95
    scores["Water"] = 0.93
    chosen = S.select_labels(scores, {**TH, "Water": 0.94}, 6)
    assert "Temple" in chosen and "Water" not in chosen

def test_transport_requires_train():
    scores = {n: 0.2 for n in S.VOCAB}
    scores["Transport"] = 0.99
    chosen = S.select_labels(scores, {**TH, "Transport": 0.92}, 6)
    assert "Transport" not in chosen
    scores["Train"] = 0.95
    chosen = S.select_labels(scores, {**TH, "Transport": 0.92, "Train": 0.88}, 6)
    assert "Train" in chosen and "Transport" in chosen

def test_motorsport_suppresses_water():
    scores = {n: 0.2 for n in S.VOCAB}
    scores["Motorsport"] = 0.99
    scores["Water"] = 0.99
    scores["Vehicle"] = 0.90
    chosen = S.select_labels(scores, {**TH, "Motorsport": 0.80, "Vehicle": 0.85, "Water": 0.94}, 6)
    assert "Motorsport" in chosen and "Water" not in chosen

def test_garden_suppresses_people_keeps_water():
    scores = {n: 0.2 for n in S.VOCAB}
    scores["Garden"] = 0.99
    scores["Nature"] = 0.99
    scores["People"] = 0.99
    scores["Water"] = 0.96
    chosen = S.select_labels(scores, {**TH, "Garden": 0.88, "Nature": 0.94, "People": 0.92, "Water": 0.94}, 6)
    assert "Garden" in chosen and "Nature" in chosen
    assert "People" not in chosen
    assert "Water" in chosen

def test_binary_probs_shape():

    try:
        import numpy as np
    except ImportError:
        print("skip test_binary_probs_shape (no numpy)")
        return
    rng = np.random.default_rng(0)
    img = rng.normal(size=(2, 8)).astype(np.float32)
    img /= np.linalg.norm(img, axis=1, keepdims=True)
    stacked = np.zeros((2, 2, 8), dtype=np.float32)
    stacked[0, 0] = img[0]
    stacked[0, 1] = img[1]
    stacked[1, 0] = img[1]
    stacked[1, 1] = img[0]
    for i in range(2):
        for j in range(2):
            stacked[i, j] /= np.linalg.norm(stacked[i, j])
    probs = S.binary_probs(img, stacked, scale=10.0)
    assert probs.shape == (2, 2)
    assert probs[0, 0] > 0.8 and probs[1, 1] > 0.8
    assert probs[0, 1] < 0.2 and probs[1, 0] < 0.2

if __name__ == "__main__":
    for fn, f in list(globals().items()):
        if fn.startswith("test_") and callable(f):
            f()
            print("ok", fn)
    print("all passed")
