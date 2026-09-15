"""Offline runner must never write into or copy credentials out of UserData."""

import importlib
from pathlib import Path

import pytest


def runner():
    assert importlib.util.find_spec("scripts.offline_frontier_review") is not None
    return importlib.import_module("scripts.offline_frontier_review")


def test_output_under_formal_data_is_refused(tmp_path):
    module = runner()
    root = tmp_path / "UserData"
    for output in [root, root / "reports", tmp_path]:
        with pytest.raises(ValueError):
            module.validate_output(root, output)


def test_output_sibling_is_allowed(tmp_path):
    runner().validate_output(tmp_path / "UserData", tmp_path / "reports")


def test_sample_has_forty_unique_predeclared_references():
    samples = runner().SAMPLES
    assert len(samples) == 40
    assert len({row[0] for row in samples}) == 40
    assert {row[1] for row in samples} == {"positive", "negative", "boundary"}


def test_report_never_calls_related_papers_false_negatives_for_unknown_quality():
    module = runner()
    metrics = module.review_metrics([
        {"expected_group": "positive", "new": {"content_decision": "accept", "route": "pending_quality"}},
        {"expected_group": "negative", "new": {"content_decision": "pending", "route": "pending_content"}},
    ])
    assert metrics["reference_positive_accepted"] == 1
    assert metrics["reference_negative_rejected"] == 0
    assert metrics["reference_negative_pending"] == 1
