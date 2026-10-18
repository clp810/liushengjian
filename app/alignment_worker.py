"""Isolated local inference worker, launched with alignment/.venv/bin/python."""
import json
import os
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


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
    language=request['language']
    if language is None:
        import whisper
        audio=whisper.load_audio(request['audio_path'])
        mel=whisper.log_mel_spectrogram(whisper.pad_or_trim(audio)).to(model.device)
        _,probabilities=model.detect_language(mel)
        language=max(probabilities,key=probabilities.get)
    result = model.align(request['audio_path'], request['text'], language=language,
                         original_split=True, regroup=False, verbose=None, token_step=200,
                         suppress_silence=True,
                         progress_callback=lambda current, total: progress('正在听这场演唱，定位歌词', round(100*current/max(total,1))))
    if result is None and not request.get('automatic'):
        raise ValueError('没有得到对齐结果，请确认文字与演唱内容对应。')
    segments = []
    for segment in (result.to_dict()['segments'] if result is not None else []):
        words = segment.get('words') or []
        confidence = sum(w.get('probability', 0) for w in words)/max(len(words),1)
        segments.append(dict(text=segment['text'].strip(), start=segment['start'], end=segment['end'],
                             confidence=round(confidence,3)))
    output=segments
    if request.get('automatic'):
        from app.auto_quality import acceptable,match_transcription
        original=request['text'].splitlines()
        strategy='alignment';coverage=1
        if not acceptable(segments,len(original)):
            progress('正在重新核对演唱内容')
            heard=model.transcribe(request['audio_path'],language=language,verbose=None,regroup=False,
                                   condition_on_previous_text=False,fp16=False,temperature=0,beam_size=3,
                                   progress_callback=lambda current,total:progress('正在重新核对演唱内容',round(100*current/max(total,1))))
            heard_segments=heard.to_dict()['segments']
            Path(request['output_path']).with_name('heard.json').write_text(json.dumps(heard_segments,ensure_ascii=False))
            retry,coverage=match_transcription(heard_segments,original)
            if acceptable(retry,len(original),coverage):segments=retry;strategy='transcription'
            else:strategy='text_only'
        output=dict(segments=segments,strategy=strategy,coverage=coverage,language=language)
    Path(request['output_path']).write_text(json.dumps(output, ensure_ascii=False))
    progress('对齐完成，等待预览确认', 100)


if __name__ == '__main__':
    main()
