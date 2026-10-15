'use strict';
// One playback clock for a trimmed audio rendition and its full-length live video.
class CollectionPlayer extends EventTarget {
  constructor(audio, video) {
    super();
    this.audio = audio;
    this.video = video;
    this.mode = 'audio';
    this.track = null;
    this.epoch = 0;
    this.repeating = false;
    this.finished = false;
    for (const element of [audio, video]) {
      for (const name of ['play', 'pause', 'loadedmetadata', 'error', 'ended', 'timeupdate']) {
        element.addEventListener(name, () => {
          if (element !== this.active) return;
          if (element === video && (name === 'ended' || (name === 'timeupdate' && !video.paused)) &&
              video.currentTime >= this.start + this.duration - .04) {
            if (this.repeating) {
              video.currentTime = this.start;
              video.play().catch(() => this.dispatchEvent(new Event('error')));
            } else if (!this.finished) {
              this.finished = true;
              video.pause();
              this.dispatchEvent(new Event('ended'));
            }
            this.dispatchEvent(new Event('timeupdate'));
            return;
          }
          this.dispatchEvent(new Event(name));
        });
      }
    }
  }
  get active() { return this.mode === 'video' ? this.video : this.audio; }
  get start() { return this.track?.trim_start || 0; }
  get duration() { return this.track?.duration || this.audio.duration || 0; }
  get currentTime() { return Math.max(0, Math.min(this.duration, this.active.currentTime - (this.mode === 'video' ? this.start : 0))); }
  set currentTime(value) {
    this.finished = false;
    this.active.currentTime = Math.max(0, Math.min(value, this.duration)) + (this.mode === 'video' ? this.start : 0);
  }
  get paused() { return this.active.paused; }
  get volume() { return this.audio.volume; }
  set volume(value) { this.audio.volume = value; this.video.volume = value; }
  get loop() { return this.repeating; }
  set loop(value) { this.repeating = value; this.audio.loop = value; }
  setTrack(track) { this.track = track; }
  set src(value) {
    this.pause();
    this.epoch += 1;
    this.mode = 'audio';
    this.finished = false;
    this.audio.src = value;
    this.video.removeAttribute('src');
    this.video.load();
    this.dispatchEvent(new Event('modechange'));
  }
  pause() { this.audio.pause(); this.video.pause(); }
  async play() {
    (this.mode === 'video' ? this.audio : this.video).pause();
    if (this.currentTime >= this.duration - .04) this.currentTime = 0;
    this.finished = false;
    await this.active.play();
  }
  async switchMode(mode) {
    if (mode === this.mode) return;
    if (mode === 'video' && !this.track?.has_video) throw Error('这份收藏还没有本地视频。');
    const position = this.currentTime;
    const playing = !this.paused;
    const previousMode = this.mode;
    this.pause();
    const epoch = ++this.epoch;
    const target = mode === 'video' ? this.video : this.audio;
    try {
      if (mode === 'video' && !target.getAttribute('src')) target.src = `/api/tracks/${this.track.id}/video`;
      if (target.readyState < 1) {
        await new Promise((resolve, reject) => {
          const cleanup = () => { clearTimeout(timer); target.removeEventListener('loadedmetadata', ready); target.removeEventListener('error', fail); };
          const ready = () => { cleanup(); resolve(); };
          const fail = () => { cleanup(); reject(Error('视频暂时无法加载，请确认文件处理完成后重试。')); };
          const timer = setTimeout(fail, 20000);
          target.addEventListener('loadedmetadata', ready, {once: true});
          target.addEventListener('error', fail, {once: true});
        });
      }
      if (epoch !== this.epoch) return;
      this.pause();
      this.mode = mode;
      this.currentTime = position;
      this.dispatchEvent(new Event('modechange'));
      this.dispatchEvent(new Event('loadedmetadata'));
      this.dispatchEvent(new Event('timeupdate'));
      if (playing) await this.play();
    } catch (error) {
      if (epoch !== this.epoch) return;
      this.mode = previousMode;
      this.dispatchEvent(new Event('modechange'));
      throw error;
    }
  }
}
