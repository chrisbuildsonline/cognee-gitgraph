"""The checks behind `bun run demo:video`: pure logic only, no ffmpeg, browser or network."""

import copy
import importlib.util
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parent.parent
_spec = importlib.util.spec_from_file_location("demo_video", ROOT / "scripts" / "demo_video.py")
assert _spec and _spec.loader
demo_video = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(demo_video)

_quick_start = (ROOT / "README.md").read_text().split("## Quick start", 1)[1]
_block = re.search(r"```text\n(.*?)\n```", _quick_start, re.DOTALL)
assert _block
DEMO_STDOUT = _block.group(1)

GOOD_PROBE: dict[str, Any] = {
    "streams": [
        {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
        {"codec_type": "audio", "codec_name": "aac"},
    ],
    "format": {"duration": "100.4"},
}


def _probe(**changes: Any) -> dict[str, Any]:
    probe = copy.deepcopy(GOOD_PROBE)
    probe["streams"][0].update(changes.pop("video", {}))
    if changes.pop("no_audio", False):
        probe["streams"] = probe["streams"][:1]
    probe["format"].update(changes)
    return probe


def test_a_good_probe_has_no_problems() -> None:
    """Given a 1920x1080 h264 + aac 100 s probe, then nothing is reported."""
    assert demo_video.video_problems(GOOD_PROBE) == []


def test_wrong_size_codec_audio_and_length_are_reported() -> None:
    """Given a bad probe, then each wrong property is named."""
    assert any("width" in p for p in demo_video.video_problems(_probe(video={"width": 1280})))
    assert any("height" in p for p in demo_video.video_problems(_probe(video={"height": 720})))
    assert any("codec" in p for p in demo_video.video_problems(_probe(video={"codec_name": "vp9"})))
    assert any("audio" in p for p in demo_video.video_problems(_probe(no_audio=True)))
    assert any("short" in p for p in demo_video.video_problems(_probe(duration="59.9")))
    assert any("long" in p for p in demo_video.video_problems(_probe(duration="180.5")))


def test_a_probe_with_nothing_in_it_does_not_crash() -> None:
    """Given an empty probe, then it reports the missing streams instead of raising."""
    problems = demo_video.video_problems({})
    assert any("video" in p for p in problems)
    assert any("audio" in p for p in problems)


def test_pytest_count_is_read_from_the_summary() -> None:
    """Given pytest -q output, then the passed count is the number in the last summary line."""
    out = "....\n\n171 passed, 60 warnings in 400.12s (0:06:40)\n"
    assert demo_video.parse_pytest_passed(out) == 171
    assert demo_video.parse_pytest_passed("1 passed in 0.01s") == 1


@pytest.mark.parametrize(
    "out",
    ["1 failed, 170 passed in 5s", "2 errors in 1s", "no tests ran in 0.1s", ""],
)
def test_a_failing_or_missing_summary_raises(out: str) -> None:
    """Given a red or empty run, then the parser refuses to produce a count."""
    with pytest.raises(ValueError):
        demo_video.parse_pytest_passed(out)


def test_the_readme_demo_output_has_the_narrated_facts() -> None:
    """Given the README's Quick start block, then every narrated fact is found."""
    assert demo_video.missing_facts(DEMO_STDOUT) == []


def test_a_tampered_demo_output_fails_the_fact_check() -> None:
    """Given altered hops, a missing service or a search service at risk, then it is reported."""
    wrong_hops = DEMO_STDOUT.replace("notifications  4 hops", "notifications  3 hops")
    assert demo_video.missing_facts(wrong_hops)
    no_billing = "\n".join(
        line for line in DEMO_STDOUT.splitlines() if not line.startswith("  billing  ")
    )
    assert demo_video.missing_facts(no_billing)
    extra = "  search  1 hop  a -> b\n  orders  2 hops"
    with_search = DEMO_STDOUT.replace("  orders  2 hops", extra)
    assert demo_video.missing_facts(with_search)
    with pytest.raises(ValueError):
        demo_video.assert_facts(wrong_hops)


def test_the_ffmpeg_audio_filter_delays_each_clip_to_its_start() -> None:
    """Given clip offsets, then input N+1 is delayed by its offset on both channels and mixed."""
    graph = demo_video.audio_filter([0, 12345, 99000])
    assert "[1:a]adelay=0|0[a0]" in graph
    assert "[2:a]adelay=12345|12345[a1]" in graph
    assert "[3:a]adelay=99000|99000[a2]" in graph
    assert "[a0][a1][a2]amix=inputs=3:normalize=0[aud]" in graph


def test_the_ffmpeg_command_maps_video_and_audio_with_the_wanted_encoders() -> None:
    """Given a recording and clips, then the command scales, encodes and places every input."""
    clips = [(Path("a.mp3"), 0), (Path("b.mp3"), 500)]
    cmd = demo_video.ffmpeg_command(Path("r.webm"), clips, Path("o.mp4"))
    assert cmd[0] == "ffmpeg"
    assert cmd.count("-i") == 3
    joined = " ".join(cmd)
    assert "scale=1920:1080:flags=lanczos,fps=30" in joined
    for token in ("libx264", "yuv420p", "aac", "192k", "-crf", "17"):
        assert token in cmd
    assert cmd[-1] == "o.mp4"


def test_the_picture_is_stretched_in_time_to_the_wall_clock() -> None:
    """Given a slow recording (wall clock / video length < 1), then setpts scales the picture."""
    cmd = demo_video.ffmpeg_command(Path("r.webm"), [(Path("a.mp3"), 0)], Path("o.mp4"), 0.9)
    assert "[0:v]setpts=0.900000*PTS,scale=1920:1080" in " ".join(cmd)
