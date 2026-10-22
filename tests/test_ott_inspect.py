"""OTT Mac-side inspect: SPM pins + stack frame parsing."""

from __future__ import annotations

from viewlift_ai.services.ott_inspect import (
    enrich_frames,
    load_spm_packages,
    parse_stack_frame,
)


def test_load_spm_packages_from_appcms() -> None:
    result = load_spm_packages()
    assert result["ok"] is True
    assert result["count"] >= 50
    identities = {row["identity"] for row in result["packages"]}
    assert "apollo-ios" in identities
    assert "alamofire" in identities


def test_parse_stack_frame_with_file_line() -> None:
    line = (
        "2   AppCMS  0x0000000102abcdef "
        "VLPageViewController.viewDidLoad() + 48 (VLPageViewController.swift:210)"
    )
    frame = parse_stack_frame(line)
    assert frame["parsed"] is True
    assert frame["class"] == "VLPageViewController"
    assert frame["file"] == "VLPageViewController.swift"
    assert frame["line"] == 210
    assert "viewDidLoad" in frame["symbol"]


def test_enrich_frames_merges_device_payload() -> None:
    enriched = enrich_frames(
        [
            {
                "raw": (
                    "0   AppCMS  0x1 "
                    "ParentalControlHelper.getUserDetails() + 0 "
                    "(ParentalControlHelper.swift:51)"
                ),
                "index": 0,
            }
        ]
    )
    assert enriched[0]["file"] == "ParentalControlHelper.swift"
    assert enriched[0]["line"] == 51
    assert enriched[0]["class"] == "ParentalControlHelper"
