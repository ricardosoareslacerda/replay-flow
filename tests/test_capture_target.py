from replayflow.services.capture_target import obs_escape, to_video


def test_click_on_second_monitor_maps_to_video() -> None:
    region = (1920, 0, 1920, 1080)
    assert to_video(2020, 50, region, 1920, 1080) == (100, 50)
    assert to_video(500, 50, region, 1920, 1080) is None  # clique no monitor 1, fora da gravação


def test_window_fitted_into_frame() -> None:
    region = (100, 200, 960, 540)  # janela pequena encaixada (escala 2x) no quadro 1920x1080
    assert to_video(100 + 480, 200 + 270, region, 1920, 1080) == (960, 540)


def test_without_region_keeps_screen_coordinates() -> None:
    assert to_video(10, 20, None, 1920, 1080) == (10, 20)


def test_obs_window_spec_escapes_colons() -> None:
    assert obs_escape("Boleto: 10/26 #1") == "Boleto#3A 10/26 #221"
