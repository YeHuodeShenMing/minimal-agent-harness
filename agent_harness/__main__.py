import argparse
import json
import sys
from .config import load_env
from .model import ChatModel, ModelError
from .runtime import Runtime
from .store import BusyError, Store


def main():
    parser = argparse.ArgumentParser(description='Framework-free persistent agent')
    parser.add_argument('--user', default='local')
    parser.add_argument('--session', default='default')
    parser.add_argument('--db', default='data/agent.db')
    parser.add_argument('--message', '-m')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--trace', action='store_true')
    args = parser.parse_args()
    load_env()
    store = Store(args.db)
    if args.trace:
        print(json.dumps(store.events(args.user, args.session), ensure_ascii=False, indent=2))
        return 0
    try:
        runtime = Runtime(ChatModel(), store)
        if args.message is not None or args.resume:
            answer = runtime.chat(args.user, args.session, None if args.resume else args.message)
            print(answer)
            return 1 if store.events(args.user, args.session, 1)[0]['kind'] in ('error', 'limit') else 0
        print('Commands: /session NAME, /trace, /resume, /quit. Ctrl-C interrupts; /resume recovers.')
        while True:
            try:
                text = input(f'[{args.user}/{args.session}] > ').strip()
                if not text:
                    continue
                if text == '/quit':
                    return 0
                if text.startswith('/session '):
                    args.session = text.split(maxsplit=1)[1]
                    continue
                if text == '/trace':
                    print(json.dumps(store.events(args.user, args.session), ensure_ascii=False, indent=2))
                    continue
                print(runtime.chat(args.user, args.session, None if text == '/resume' else text))
            except (ValueError, BusyError) as error:
                print(str(error), file=sys.stderr)
            except EOFError:
                return 0
    except (ModelError, ValueError, BusyError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\nInterrupted. Use --resume to continue the saved turn.', file=sys.stderr)
        return 130


if __name__ == '__main__':
    sys.exit(main())
