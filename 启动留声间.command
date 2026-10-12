#!/bin/zsh
cd -- "${0:A:h}"
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null; then
  print '没有找到 uv。请先按 README 安装运行环境。'
  read '?按回车关闭窗口'; exit 1
fi
if ! command -v ffmpeg >/dev/null || ! command -v ffprobe >/dev/null; then
  print '需要安装 FFmpeg。若已安装 Homebrew，可运行：brew install ffmpeg'
  read '?按回车关闭窗口'; exit 1
fi
uv run --frozen --no-dev python run.py
if [[ $? -ne 0 ]]; then read '?启动未完成，按回车关闭窗口'; fi
