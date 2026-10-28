from pathlib import Path

from replayflow.core import timeline_store
from replayflow.models import TimelineEvent


def test_write_source_replaces_only_that_source_and_sorts(tmp_path: Path) -> None:
    path = tmp_path / "timeline.jsonl"
    timeline_store.write_source(path, "frame", [TimelineEvent(t=5, source="frame", kind="captured")])
    timeline_store.write_source(path, "transcript", [TimelineEvent(t=1, source="transcript", kind="segment")])
    timeline_store.write_source(path, "frame", [TimelineEvent(t=3, source="frame", kind="captured")])

    events = timeline_store.read(path)

    assert [(e.t, e.source) for e in events] == [(1, "transcript"), (3, "frame")]
