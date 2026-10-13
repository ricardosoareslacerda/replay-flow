import sys

import pytest

from replayflow.services import audio_devices


@pytest.mark.skipif(sys.platform != "win32", reason="lista vem do registro do Windows")
def test_devices_use_obs_wasapi_ids() -> None:
    for d in audio_devices.microphones():
        assert d.id.startswith("{0.0.1.00000000}.{")
    for d in audio_devices.outputs():
        assert d.id.startswith("{0.0.0.00000000}.{")
