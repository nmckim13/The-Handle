#!/usr/bin/env python3
"""Build an isolated static staging copy. No network, deployment or DB access."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_REF = 'lnsvacnbgmklpkgzbodb'
PRODUCTION_ROOM = '512060739'
PAGES = {'index.html': ('SURL', 'SKEY'), 'big-screen.html': ('SURL', 'SKEY'),
         'profile-setup.html': ('SURL', 'SKEY'), 'admin/index.html': ('SUPABASE_URL', 'SUPABASE_KEY')}
ASSETS = ['admin-auth.js', 'race-night-client.js', 'driver-graphics.js',
          'live-board.js', 'live-board.css', 'vercel.json', '_redirects']


def build(output, supabase_url, public_key, room):
    url = urlsplit(supabase_url)
    host = re.fullmatch(r'([a-z0-9]{20})\.supabase\.co', url.netloc)
    if url.scheme != 'https' or not host or url.path not in ('', '/') or url.query or url.fragment:
        raise ValueError('Use a staging project HTTPS URL: https://<20-character-project-ref>.supabase.co')
    project = host[1]
    if project == PRODUCTION_REF:
        raise ValueError('Refusing the production Supabase project')
    supabase_url = 'https://' + url.netloc
    public_key = public_key.strip()
    # The plain REST client uses the public key as a Bearer JWT. Modern
    # publishable keys are not JWTs and cannot be substituted without client changes.
    try:
        if not re.fullmatch(r'[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', public_key):
            raise ValueError()
        parts = public_key.split('.')
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + '=' * (-len(parts[1]) % 4)))
        if payload.get('role') != 'anon' or payload.get('ref') != project:
            raise ValueError()
    except (ValueError, UnicodeError, AttributeError):
        raise ValueError('Use only a matching-project legacy anon JWT; service, secret and publishable keys are not accepted') from None
    if not re.fullmatch(r'[0-9]{1,20}', room) or int(room) == int(PRODUCTION_ROOM):
        raise ValueError('Use a separate numeric staging LapMonitor room, not the production room')
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Output must not exist; refusing to overwrite any files')

    # Validate every source before creating the output. Keep production-facing
    # files unchanged; the public key is embedded ONLY in the generated copy.
    generated = {}
    sources = {}
    for path, (url_name, key_name) in PAGES.items():
        source = (ROOT / path).read_text()
        sources[path] = hashlib.sha256(source.encode()).hexdigest()
        for name, value in [(url_name, supabase_url), (key_name, public_key)]:
            pattern = rf"(const {name}\s*=\s*)'[^']*';"
            source, count = re.subn(pattern, lambda m: m[1] + json.dumps(value) + ';', source)
            if count != 1:
                raise ValueError(f'Expected one {name} constant in {path}; source layout changed')
        source = re.sub(r'https://lapmonitor\.live/[0-9]+', 'https://lapmonitor.live/' + room, source)
        if PRODUCTION_REF in source or 'https://lapmonitor.live/' + PRODUCTION_ROOM in source:
            raise ValueError(f'Production project/room reference remains in {path}')
        source = source.replace('<head>', '<head>\n<meta name="robots" content="noindex,nofollow">', 1)
        generated[path] = source.encode()
    files = ASSETS + sorted(p.name for p in ROOT.iterdir() if p.is_file() and p.suffix.lower() in {'.png', '.jpg', '.jpeg', '.svg', '.webp', '.ico'})
    for path in files:
        generated[path] = (ROOT / path).read_bytes()
        sources[path] = hashlib.sha256(generated[path]).hexdigest()
    for path, data in generated.items():
        if Path(path).suffix in {'.html', '.js', '.css', '.json'} or path == '_redirects':
            if PRODUCTION_REF.encode() in data or PRODUCTION_ROOM.encode() in data:
                raise ValueError(f'Production project/room reference remains in {path}')
    manifest = {
        'purpose': 'STAGING ONLY — not a production deployment artifact',
        'supabase_url': supabase_url, 'lapmonitor_room': room,
        'public_key_format': 'anon JWT (claims checked locally; signature not verified)',
        'source_sha256': sources,
        'bundle_sha256': {path: hashlib.sha256(data).hexdigest() for path, data in generated.items()}
    }
    output.mkdir(parents=True)
    try:
        for path, data in generated.items():
            destination = output / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        (output / 'staging-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    except BaseException:
        shutil.rmtree(output)
        raise
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New staging-only static output directory')
    parser.add_argument('--supabase-url', required=True, help='Separate staging Supabase project URL')
    parser.add_argument('--public-key-file', type=Path, required=True, help='Private local file containing ONLY the staging public key')
    parser.add_argument('--lapmonitor-room', required=True, help='Separate staging timing room')
    args = parser.parse_args()
    try:
        build(args.output, args.supabase_url, args.public_key_file.read_text(), args.lapmonitor_room)
    except (ValueError, OSError) as error:
        parser.exit(1, f'Staging preparation failed: {error}\n')
    print('Staging-only bundle prepared. No network access, database changes or deployment performed.')


if __name__ == '__main__':
    main()
