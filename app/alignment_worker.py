"""Isolated local inference worker, launched with alignment/.venv/bin/python."""
import json
import os
from pathlib import Path
import sys
import time


def main():
    request = json.loads(Path(sys.argv[1]).read_text())
    progress_path = Path(request['progress_path'])
    last_progress = 0

    def progress(phase, percent=None):
        nonlocal last_progress
        if percent is not None and time.monotonic()-last_progress < 1:
            return
        last_progress = time.monotonic()
        temp = progress_path.with_suffix('.tmp')
        temp.write_text(json.dumps(dict(phase=phase, percent=percent)))
        temp.replace(progress_path)

    progress('加载本地模型（首次需要下载约 461 MB）')
    import torch
    import stable_whisper
    torch.set_num_threads(max(1, min(4, (os.cpu_count() or 4)//2)))
    model = stable_whisper.load_model('small', device='cpu', download_root=request['model_dir'])
    progress('正在听这场演唱，定位歌词')
    result = model.align(request['audio_path'], request['text'], language=request['language'],
                         original_split=True, regroup=False, verbose=None, token_step=200,
                         suppress_silence=True,
                         progress_callback=lambda current, total: progress('正在听这场演唱，定位歌词', round(100*current/max(total,1))))
    if result is None:
        raise ValueError('没有得到对齐结果，请确认文字与演唱内容对应。')
    segments = []
    for segment in result.to_dict()['segments']:
        words = segment.get('words') or []
        confidence = sum(w.get('probability', 0) for w in words)/max(len(words),1)
        segments.append(dict(text=segment['text'].strip(), start=segment['start'], end=segment['end'],
                             confidence=round(confidence,3)))
    Path(request['output_path']).write_text(json.dumps(segments, ensure_ascii=False))
    progress('对齐完成，等待预览确认', 100)


if __name__ == '__main__':
    main()
