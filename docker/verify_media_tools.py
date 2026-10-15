"""Exercise the release codecs, text overlay, frame clock and audio without a GPU."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prefix', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    ffmpeg = args.prefix / 'bin/ffmpeg'
    ffprobe = args.prefix / 'bin/ffprobe'

    def run(arguments):
        return subprocess.check_output([str(value) for value in arguments], stderr=subprocess.PIPE, text=True)

    video = args.output / 'clock-audio-text.mp4'
    run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
         'testsrc2=size=320x240:rate=24', '-f', 'lavfi', '-i',
         'sine=frequency=220:sample_rate=32000', '-t', '1', '-vf',
         'drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:text=H3:fontsize=24:fontcolor=white:x=8:y=8',
         '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', video])
    rgb = args.output / 'lossless.mkv'
    run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-i', video,
         '-an', '-c:v', 'ffv1', '-pix_fmt', 'gbrp', rgb])
    results = []
    for path in [video, rgb]:
        run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-i', path, '-f', 'null', '-'])
        data = json.loads(run([ffprobe, '-v', 'error', '-count_frames',
                               '-show_streams', '-show_format', '-of', 'json', path]))
        stream = next(item for item in data['streams'] if item['codec_type'] == 'video')
        if (stream['width'], stream['height'], int(stream['nb_read_frames']), stream['avg_frame_rate']) != (320, 240, 24, '24/1'):
            raise ValueError('Release media tools changed dimensions or the frame clock')
        audio = any(item['codec_type'] == 'audio' for item in data['streams'])
        if audio != (path == video):
            raise ValueError('Release media tools lost or unexpectedly added audio')
        results.append({'name': path.name, 'width': 320, 'height': 240, 'frames': 24,
                        'fps': 24, 'audio': audio, 'bytes': path.stat().st_size,
                        'sha256': hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()})
    report = {'schemaVersion': 1, 'status': 'passed', 'checks': [
        'h264-aac-encode', 'drawtext', 'ffv1-rgb-encode', 'full-decode', '24fps-frame-clock'], 'outputs': results}
    (args.output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
