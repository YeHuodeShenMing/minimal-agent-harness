"""Portable local launcher; run from the repository root."""
import argparse
import os
import secrets
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    os.chdir(Path(__file__).resolve().parents[1])
    data = Path('data')
    data.mkdir(exist_ok=True)
    if not os.environ.get('WEB_SECRET'):
        path = data / 'web-secret'
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, 'w') as file:
                file.write(secrets.token_hex(32))
        os.environ['WEB_SECRET'] = path.read_text().strip()
    os.environ.setdefault('COOKIE_SECURE', '0')  # localhost HTTP; production must use 1
    os.environ.setdefault('WEB_ALLOWED_HOSTS', 'localhost,127.0.0.1')
    os.environ.setdefault('AGENT_DB', 'data/web.db')
    import uvicorn
    uvicorn.run('agent_harness.web:create_app', factory=True, host=args.host, port=args.port, workers=1)


if __name__ == '__main__':
    main()
