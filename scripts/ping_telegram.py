#!/usr/bin/env python3
import os
import requests
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / '.env'
ENV_SH_PATH = PROJECT_ROOT / '.env.sh'


def load_env_file(path: Path) -> dict:
    env = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        if not line or line.strip().startswith('#'):
            continue
        if '=' in line:
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def save_env_var(key: str, value: str):
    # update .env
    lines = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text().splitlines()
    updated = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            updated = True
            break
    if not updated:
        lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n")
    # update .env.sh
    sh_lines = []
    if ENV_SH_PATH.exists():
        sh_lines = ENV_SH_PATH.read_text().splitlines()
    updated = False
    for i, line in enumerate(sh_lines):
        if line.startswith(f"export {key}="):
            sh_lines[i] = f"export {key}='{value}'"
            updated = True
            break
    if not updated:
        sh_lines.append(f"export {key}='{value}'")
    ENV_SH_PATH.write_text("\n".join(sh_lines) + "\n")


def main():
    env = load_env_file(ENV_PATH)
    # allow actual environment to override .env
    token = os.getenv('TELEGRAM_BOT_TOKEN') or env.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID') or env.get('TELEGRAM_CHAT_ID')

    if not token:
        print('TELEGRAM_BOT_TOKEN not found in environment or .env. Please run onboarding or set it.')
        return

    base = f'https://api.telegram.org/bot{token}'

    def send_test_message(cid: str) -> bool:
        url = f"{base}/sendMessage"
        payload = {"chat_id": cid, "text": "Aether gateway ping test ✅", "parse_mode": "Markdown"}
        try:
            r = requests.post(url, json=payload, timeout=10)
            if r.status_code == 200:
                j = r.json()
                if j.get('ok'):
                    print('Message sent successfully to', cid)
                    return True
                else:
                    print('Telegram API error:', j)
            else:
                print('HTTP', r.status_code, r.text)
        except Exception as e:
            print('Exception while sending message:', e)
        return False

    if chat_id:
        print('Found TELEGRAM_CHAT_ID:', chat_id)
        ok = send_test_message(chat_id)
        if not ok:
            print('Failed to send message. If the bot uses webhook mode, try sending a message to the bot from your account first so getUpdates contains the chat id, or set TELEGRAM_CHAT_ID manually.')
        return

    # No chat_id set. Try to fetch recent updates to discover a chat id.
    print('No TELEGRAM_CHAT_ID found. Attempting to fetch updates to discover chat id...')
    try:
        r = requests.get(f"{base}/getUpdates", timeout=10)
        if r.status_code != 200:
            print('getUpdates HTTP', r.status_code, r.text)
            return
        j = r.json()
        if not j.get('ok'):
            print('getUpdates returned error:', j)
            return
        results = j.get('result', [])
        # search for the most recent message/chat id
        found = None
        for u in reversed(results):
            # message or edited_message or callback_query
            msg = u.get('message') or u.get('edited_message') or (u.get('callback_query') and u['callback_query'].get('message'))
            if not msg:
                continue
            chat = msg.get('chat') or msg.get('from')
            if chat and chat.get('id'):
                found = str(chat.get('id'))
                break
        if found:
            print('Discovered chat id:', found)
            resp = input('Save this chat id to .env and .env.sh as TELEGRAM_CHAT_ID? [y/N]: ').strip().lower()
            if resp == 'y':
                save_env_var('TELEGRAM_CHAT_ID', found)
                print('Saved TELEGRAM_CHAT_ID to .env and .env.sh')
                send_test_message(found)
            else:
                print('Not saving. To save manually, run:\n  save TELEGRAM_CHAT_ID=<id> to .env or use the onboarding flow')
        else:
            print('No chat id found in getUpdates. Make sure someone has messaged the bot or the bot is not configured exclusively for webhooks.')
    except Exception as e:
        print('Error while calling getUpdates:', e)


if __name__ == '__main__':
    main()
