"""Provision one Telegraph account on the Debian server; never print its token."""
import grp
import json
import os
from pathlib import Path
import sys
import urllib.parse
import urllib.request


def main():
    if os.geteuid() != 0:
        raise ValueError("Run this one-time setup as root on the Debian server.")
    target = Path('/etc/telegram-quiz-bot/telegraph-token.txt')
    if target.exists():
        if target.stat().st_size:
            print('Telegraph account is already configured; existing token preserved.')
            return
        raise ValueError('An unfinished setup exists. Inspect it before creating another account.')
    group_id = grp.getgrnam('quizbot').gr_gid

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    # Reserve the destination before the remote mutation; no blind retry on an
    # ambiguous response, and no accidental overwriting of an existing secret.
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
        data = urllib.parse.urlencode({'short_name': 'Quiz Maker',
                                       'author_name': 'Quiz Maker'}).encode('ascii')
        request = urllib.request.Request('https://api.telegra.ph/createAccount', data=data,
                                         headers={'Content-Type': 'application/x-www-form-urlencoded'},
                                         method='POST')
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=45) as response:
            result = json.loads(response.read(65536))
        token = result.get('result', {}).get('access_token') if result.get('ok') else None
        if not isinstance(token, str) or not token or len(token) > 1024 or not token.isalnum():
            raise ValueError('Telegraph did not return an account token. Inspect setup before retrying.')
        handle.write(token + '\n')
        handle.flush()
        os.fsync(handle.fileno())
        os.fchown(handle.fileno(), 0, group_id)
        os.fchmod(handle.fileno(), 0o640)
    print('Telegraph account configured; token stored only on this server.')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Raw exception text can include response data. Inspect the file and
        # connectivity on the server without exposing credentials in logs.
        print('Telegraph setup did not complete. Inspect server configuration before retrying.', file=sys.stderr)
        sys.exit(1)
