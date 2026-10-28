#!/usr/bin/env python3
"""Offline staging-bundle tests using nonfunctional public JWT fixtures."""
import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import prepare_staging as staging

PROJECT = 'a' * 20
URL = f'https://{PROJECT}.supabase.co'
ROOM = '123456789'


def public_key(role='anon', project=PROJECT):
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
    return encode({'alg': 'HS256', 'typ': 'JWT'}) + '.' + encode({'role': role, 'ref': project}) + '.nonfunctional'


class PrepareStaging(unittest.TestCase):
    def test_bundle_uses_only_staging_targets_and_preserves_original_sources(self):
        before = {path: (staging.ROOT / path).read_bytes() for path in staging.PAGES}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'site'
            manifest = staging.build(output, URL + '/', public_key(), ROOM)
            self.assertEqual(json.loads((output / 'staging-manifest.json').read_text()), manifest)
            for path in staging.PAGES:
                text = (output / path).read_text()
                self.assertIn(URL, text)
                self.assertIn(public_key(), text)
                self.assertIn('noindex,nofollow', text)
                self.assertNotIn(staging.PRODUCTION_REF, text)
                self.assertNotIn('https://lapmonitor.live/' + staging.PRODUCTION_ROOM, text)
                self.assertEqual((staging.ROOT / path).read_bytes(), before[path])
            self.assertIn('https://lapmonitor.live/' + ROOM, (output / 'index.html').read_text())
            for path, digest in manifest['bundle_sha256'].items():
                self.assertEqual(hashlib.sha256((output / path).read_bytes()).hexdigest(), digest)
            self.assertEqual((output / 'logo.png').read_bytes(), (staging.ROOT / 'logo.png').read_bytes())
            for path in output.rglob('*'):
                if path.is_file():
                    self.assertNotIn(path.suffix, {'.zip', '.sql', '.py', '.env'})
                    self.assertNotIn('.test.', path.name)
            self.assertFalse((output / 'lapmonitor-bridge').exists())

    def test_unsafe_targets_and_keys_fail_before_creating_any_output(self):
        cases = [
            (f'https://{staging.PRODUCTION_REF}.supabase.co', public_key(), ROOM),
            (URL, public_key(), staging.PRODUCTION_ROOM),
            (URL, public_key(), '0' + staging.PRODUCTION_ROOM),
            (URL, public_key('service_role'), ROOM),
            (URL, public_key(project=staging.PRODUCTION_REF), ROOM),
            (URL, 'sb_secret_private-sentinel', ROOM),
            (URL, 'sb_publishable_not-a-bearer-jwt', ROOM),
            (URL, 'broken-key', ROOM),
            (URL.replace('https:', 'http:'), public_key(), ROOM),
            (URL + '/rest/v1', public_key(), ROOM),
            (URL + '?project=another', public_key(), ROOM),
        ]
        with tempfile.TemporaryDirectory() as directory:
            for url, key, room in cases:
                with self.subTest(url=url, room=room):
                    output = Path(directory) / 'site'
                    with self.assertRaises(ValueError):
                        staging.build(output, url, key, room)
                    self.assertFalse(output.exists())

    def test_production_references_in_copied_assets_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'source'
            root.mkdir()
            for path in list(staging.PAGES) + staging.ASSETS:
                destination = root / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(staging.ROOT / path, destination)
            for target in (staging.PRODUCTION_REF, staging.PRODUCTION_ROOM):
                with self.subTest(target=target), patch.object(staging, 'ROOT', root):
                    (root / 'race-night-client.js').write_text('// ' + target)
                    output = Path(directory) / 'site'
                    with self.assertRaisesRegex(ValueError, 'Production project/room reference remains'):
                        staging.build(output, URL, public_key(), ROOM)
                    self.assertFalse(output.exists())

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            sentinel = Path(directory) / 'keep.txt'
            sentinel.write_text('keep existing work')
            with self.assertRaisesRegex(ValueError, 'refusing to overwrite'):
                staging.build(directory, URL, public_key(), ROOM)
            self.assertEqual(sentinel.read_text(), 'keep existing work')
            self.assertEqual(list(Path(directory).iterdir()), [sentinel])

    def test_cli_errors_never_echo_private_key_input(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / 'key.txt'
            key.write_text('sb_secret_private-sentinel')
            result = subprocess.run([sys.executable, str(staging.ROOT / 'scripts/prepare_staging.py'),
                                     '--output', str(Path(directory) / 'site'), '--supabase-url', URL,
                                     '--public-key-file', str(key), '--lapmonitor-room', ROOM],
                                    text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('sb_secret_private-sentinel', result.stdout + result.stderr)
            self.assertFalse((Path(directory) / 'site').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
