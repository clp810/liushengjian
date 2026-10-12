"""Local launcher. A second launch reuses only a recognized 留声间 server."""
import argparse
import json
import os
import threading
import urllib.error
import urllib.request
import webbrowser
import uvicorn


def main():
    parser = argparse.ArgumentParser(description='留声间本地音乐空间')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    url = f'http://127.0.0.1:{args.port}'
    try:
        with urllib.request.urlopen(url + '/api/status', timeout=1) as response:
            status = json.load(response)
        if status.get('name') == '留声间':
            print(f'留声间已在运行：{url}', flush=True)
            if not args.no_browser:
                webbrowser.open(url)
            return
    except (OSError, ValueError, urllib.error.URLError):
        pass
    print(f'留声间将在这里打开：{url}\n收藏会自动保存，关闭此终端即可停止服务。', flush=True)
    if not args.no_browser:
        def open_when_ready():
            import time
            for _ in range(50):
                try:
                    with urllib.request.urlopen(url + '/api/status', timeout=.3) as response:
                        if json.load(response).get('name') == '留声间':
                            webbrowser.open(url)
                            return
                except (OSError, ValueError):
                    time.sleep(.2)
        threading.Thread(target=open_when_ready, daemon=True).start()
    uvicorn.run('app.main:app', host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
