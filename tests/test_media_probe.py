"""Short FFV1 files may report only r_frame_rate in newer FFmpeg versions."""
import json

import pytest

from h3workbench import media


def metadata(average, stream_rate):
    return json.dumps({'streams': [{'codec_type': 'video', 'width': 64,
        'height': 64, 'avg_frame_rate': average, 'r_frame_rate': stream_rate,
        'nb_read_frames': '3'}], 'format': {'duration': '0.125'}})


@pytest.mark.parametrize('average', ['0/0', 'N/A', None])
def test_short_video_uses_reported_stream_rate(monkeypatch, average):
    monkeypatch.setattr(media, 'run', lambda args: metadata(average, '24/1'))
    result = media.probe('short-source.mkv')
    assert result['fps'] == 24
    assert result['frames'] == 3


def test_valid_average_keeps_fractional_frame_rate(monkeypatch):
    monkeypatch.setattr(media, 'run', lambda args: metadata('24000/1001', '24/1'))
    assert media.probe('source.mp4')['fps'] == pytest.approx(24000 / 1001)


def test_missing_rates_are_reported_without_guessing(monkeypatch):
    monkeypatch.setattr(media, 'run', lambda args: metadata('0/0', '0/0'))
    with pytest.raises(ValueError, match='no valid reported frame rate'):
        media.probe('invalid.mkv')
