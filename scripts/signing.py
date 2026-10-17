#!/usr/bin/env python3
"""Create a persistent release key outside Git and optionally configure repository secrets."""

import argparse
import base64
import json
import os
import re
import secrets
import sys
from pathlib import Path

from project import CONFIG, ROOT, config, run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path.home() / '.config/flclashx')
    parser.add_argument('--upload', action='store_true', help='Upload the existing key to GitHub Actions Secrets')
    args = parser.parse_args()
    directory = args.directory.expanduser().resolve()
    if directory == ROOT or ROOT in directory.parents:
        raise ValueError('Store the private key outside the repository')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    key = directory / 'release.p12'
    password_file = directory / 'release.password'
    cfg = config()
    if key.exists() != password_file.exists():
        raise ValueError('Incomplete signing identity; restore both key and password instead of replacing them')
    if not key.exists():
        if cfg['certificate_sha256']:
            raise ValueError('A certificate is already pinned; restore its private key instead of generating a new one')
        password = secrets.token_urlsafe(48)
        descriptor = os.open(password_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(password + '\n')
        env = {**os.environ, 'FLCLASHX_KEY_PASSWORD': password}
        previous = os.umask(0o077)
        try:
            run(['keytool', '-genkeypair', '-keystore', key, '-storetype', 'PKCS12',
                 '-alias', cfg['key_alias'], '-storepass:env', 'FLCLASHX_KEY_PASSWORD',
                 '-keypass:env', 'FLCLASHX_KEY_PASSWORD', '-keyalg', 'RSA', '-keysize', '3072',
                 '-validity', '10950', '-dname', 'CN=FlClashX'], env=env)
        finally:
            os.umask(previous)
    password = password_file.read_text().strip()
    env = {**os.environ, 'FLCLASHX_KEY_PASSWORD': password}
    certificate = run(['keytool', '-exportcert', '-rfc', '-keystore', key, '-alias', cfg['key_alias'],
                       '-storepass:env', 'FLCLASHX_KEY_PASSWORD'], capture=True, env=env)
    der = base64.b64decode(''.join(line for line in certificate.splitlines() if not line.startswith('-----')))
    import hashlib
    fingerprint = hashlib.sha256(der).hexdigest()
    if cfg['certificate_sha256'] and cfg['certificate_sha256'] != fingerprint:
        raise ValueError('The key does not match the certificate pinned in config.json')
    cfg['certificate_sha256'] = fingerprint
    CONFIG.write_text(json.dumps(cfg, indent=2) + '\n')
    (ROOT / 'signing/certificate.pem').write_text(certificate + '\n')
    if args.upload:
        for name, value in {
            'FLCLASHX_KEYSTORE_BASE64': base64.b64encode(key.read_bytes()).decode(),
            'FLCLASHX_STORE_PASSWORD': password,
        }.items():
            run(['gh', 'secret', 'set', name, '--repo', cfg['destination_repository']], input=value)
    print(f'Private signing identity: {directory}')
    print(f'Certificate SHA-256: {fingerprint}')


if __name__ == '__main__':
    try:
        main()
    except ValueError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
