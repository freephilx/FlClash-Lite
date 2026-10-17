#!/usr/bin/env python3
"""Prepare and build the pinned FlClash Android source without editing the submodule."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'config.json'
FORBIDDEN_DEX = (
    b'Lcom/google/firebase/analytics/',
    b'Lcom/google/android/gms/measurement/',
    b'Lcom/google/firebase/crashlytics/',
    b'Lcom/google/firebase/installations/',
    b'Lcom/google/firebase/provider/FirebaseInitProvider;',
)


def run(args, cwd=ROOT, capture=False, env=None, input=None):
    environment = dict(os.environ if env is None else env)
    environment['PUB_HOSTED_URL'] = 'https://pub.dev'
    result = subprocess.run([str(arg) for arg in args], cwd=cwd, check=True,
                            text=True, capture_output=capture, env=environment, input=input)
    return result.stdout.strip() if capture else ''


def config():
    value = json.loads(CONFIG.read_text())
    if not re.fullmatch(r'[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){2,}', value['application_id']):
        raise ValueError('Invalid application_id in config.json')
    if value['application_id'] == 'com.follow.clash' or value['application_id'].endswith('.dev'):
        raise ValueError('Choose a distinct, fixed release application_id')
    return value


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def dependencies(source):
    flutter = os.environ.get('FLUTTER', 'flutter')
    dart = os.environ.get('DART', str(Path(shutil.which(flutter) or flutter).parent / 'dart'))
    run([flutter, 'pub', 'get', '--enforce-lockfile'], cwd=source,
        env={**os.environ, 'PUB_HOSTED_URL': 'https://pub.dev'})
    run([dart, 'run', 'intl_utils:generate'], cwd=source)
    return flutter


def check(source):
    source = Path(source).resolve()
    flutter = dependencies(source)
    run([flutter, 'analyze', '--no-fatal-infos'], cwd=source)
    pubspec = source / 'pubspec.yaml'
    original = pubspec.read_text()
    try:
        marker = '    setup:\n      build_assets: true'
        if original.count(marker) != 1:
            raise ValueError('Upstream setup build-hook configuration changed')
        pubspec.write_text(original.replace(marker, '    setup:\n      build_assets: false'))
        run([flutter, 'test', 'test/common/boot_guard_test.dart', 'test/common/request_test.dart',
             'test/providers/backup_action_test.dart', 'test/common/dialog_test.dart',
             'test/widgets', 'test/l10n', 'test/lint/dynamic_message_key_test.dart'], cwd=source)
    finally:
        pubspec.write_text(original)


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def replace_once(path, before, after):
    text = path.read_text()
    if text.count(before) != 1:
        raise ValueError(f'{path}: expected exactly one configuration marker')
    path.write_text(text.replace(before, after))


def patches_for(tag):
    match = re.fullmatch(r'v(\d+)\.(\d+)\.(\d+)(?:-([A-Za-z0-9.-]+))?', tag)
    if not match:
        raise ValueError('Expected an upstream release tag such as v0.9.1')
    version = (*map(int, match.group(1, 2, 3)), match[4] is None)
    directory = ROOT / 'patches'
    if version < (0, 9, 1, True):
        directory = directory / 'legacy/v0.9.0'
    patches = sorted(directory.glob('*.patch'))
    if not patches:
        raise ValueError(f'No source patches configured for {tag}')
    return patches


def prepare(ref=None, output=None):
    cfg = config()
    upstream = ROOT / 'upstream'
    output = Path(output or ROOT / '.build/source').resolve()
    if output.exists():
        raise ValueError(f'Build directory already exists: {output}; choose a new --output')
    if not (ROOT / '.build').resolve() in output.parents:
        raise ValueError('The prepared source must be inside .build/')
    pinned = run(['git', 'rev-parse', 'HEAD'], cwd=upstream, capture=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    run(['git', 'clone', '--no-hardlinks', upstream, output])
    run(['git', 'remote', 'set-url', 'origin',
         f'https://github.com/{cfg["upstream_repository"]}.git'], cwd=output)
    target = pinned
    if ref:
        if not re.fullmatch(r'v\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', ref):
            raise ValueError('Expected an upstream release tag such as v0.9.1')
        run(['git', 'fetch', 'origin', f'refs/tags/{ref}'], cwd=output)
        target = run(['git', 'rev-parse', 'FETCH_HEAD^{commit}'], cwd=output, capture=True)
    run(['git', 'checkout', '--detach', target], cwd=output)
    tag = ref or run(['git', 'describe', '--tags', '--exact-match', target], cwd=output, capture=True)
    patches = patches_for(tag)
    for patch in patches:
        run(['git', 'apply', '--check', '--whitespace=error', patch], cwd=output)
        run(['git', 'apply', '--whitespace=error', patch], cwd=output)
    replace_once(output / 'android/app/build.gradle.kts', '__FLCLASHX_APPLICATION_ID__', cfg['application_id'])
    hook = patches[0].parent / 'prepare.py'
    if hook.is_file():
        run([sys.executable, hook, output])
    run(['git', '-c', 'url.https://github.com/.insteadOf=git@github.com:',
         'submodule', 'update', '--init', '--recursive'], cwd=output)
    metadata = {
        'source_commit': target, 'upstream_tag': ref,
        'application_id': cfg['application_id'],
        'patches': {patch.name: sha256(patch) for patch in patches},
    }
    write_json(output / '.flclashx-source.json', metadata)
    print(f'Prepared {target} at {output}')
    return output


def sdk_tool(name):
    override = os.environ.get(name.upper())
    if override:
        return override
    found = shutil.which(name)
    if found:
        return found
    sdk = Path(os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT')
               or Path.home() / 'Library/Android/sdk')
    if name in ('apksigner', 'aapt2'):
        candidates = list((sdk / 'build-tools').glob(f'*/{name}'))
    else:
        candidates = list((sdk / 'cmdline-tools').glob(f'*/bin/{name}'))
        candidates += list((sdk / 'tools/bin').glob(name))
    if not candidates:
        raise ValueError(f'{name} not found; set {name.upper()} or ANDROID_HOME')
    return str(max(candidates, key=lambda path: tuple(int(n) for n in re.findall(r'\d+', str(path.parent)))))


def verify_apk(apk, fingerprint=None):
    cfg = config()
    expected = (fingerprint or cfg['certificate_sha256']).lower().replace(':', '')
    if not re.fullmatch('[0-9a-f]{64}', expected):
        raise ValueError('Set the release certificate_sha256 before verifying APKs')
    signature = run([sdk_tool('apksigner'), 'verify', '--verbose', '--print-certs', apk], capture=True)
    signers = re.findall(
        r'^(?:Signer #\d+|V\d+(?:\.\d+)? Signer(?: #\d+)?):? certificate SHA-256 digest: ([0-9a-fA-F]+)$',
        signature, re.M,
    )
    if not signers or {item.lower() for item in signers} != {expected} or not re.search(
        r'^Number of signers: 1$', signature, re.M,
    ):
        raise ValueError(f'Unexpected APK signer: {apk}')
    badging = run([sdk_tool('aapt2'), 'dump', 'badging', apk], capture=True)
    package = re.search(r"^package: name='([^']+)' versionCode='(\d+)' versionName='([^']+)'", badging)
    if not package or package[1] != cfg['application_id']:
        raise ValueError(f'Unexpected application ID: {apk}')
    manifest = run([sdk_tool('aapt2'), 'dump', 'xmltree', apk, '--file', 'AndroidManifest.xml'], capture=True)
    if re.search(r'com\.google\.firebase\.(?:analytics|crashlytics|installations|provider)|com\.google\.android\.gms\.permission\.AD_ID', manifest):
        raise ValueError(f'Telemetry component or advertising ID permission in {apk}')
    with zipfile.ZipFile(apk) as archive:
        for name in archive.namelist():
            if re.fullmatch(r'classes\d*\.dex', name):
                data = archive.read(name)
                if any(marker in data for marker in FORBIDDEN_DEX):
                    raise ValueError(f'Firebase telemetry classes remain in {name}: {apk}')
    return {'sha256': sha256(apk), 'application_id': package[1],
            'version_code': int(package[2]), 'version_name': package[3],
            'certificate_sha256': expected}


def signing(source):
    cfg = config()
    keystore = Path(os.environ.get('FLCLASHX_KEYSTORE', '')).expanduser()
    password = os.environ.get('FLCLASHX_STORE_PASSWORD', '')
    if not keystore.is_file() or len(password) < 12:
        raise ValueError('Set FLCLASHX_KEYSTORE and FLCLASHX_STORE_PASSWORD (at least 12 characters)')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', password):
        raise ValueError('Use an ASCII letter/digit/underscore/hyphen signing password')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', cfg['key_alias']):
        raise ValueError('Invalid key_alias')
    target = source / 'android/app/keystore.jks'
    shutil.copyfile(keystore, target)
    target.chmod(0o600)
    properties = source / 'android/local.properties'
    text = properties.read_text() if properties.exists() else ''
    text = '\n'.join(line for line in text.splitlines()
                     if not line.startswith(('keyAlias=', 'keyPassword=', 'storePassword=')))
    properties.write_text(f'{text}\nkeyAlias={cfg["key_alias"]}\nstorePassword={password}\nkeyPassword={password}\n')
    properties.chmod(0o600)


def build(source, output, prerelease=False):
    source, output = Path(source).resolve(), Path(output).resolve()
    metadata = json.loads((source / '.flclashx-source.json').read_text())
    if metadata['application_id'] != config()['application_id']:
        raise ValueError('Prepared source uses another application ID')
    output.mkdir(parents=True, exist_ok=True)
    if list(output.glob('*.apk')):
        raise ValueError('Output already contains APKs; choose an empty build output')
    flutter = dependencies(source)
    metadata['pubspec_lock_sha256'] = sha256(source / 'pubspec.lock')
    ca = source / 'core/Clash.Meta/component/ca/ca-certificates.crt'
    with urllib.request.urlopen('https://curl.se/ca/cacert.pem', timeout=60) as response:
        bundle = response.read()
    if bundle.count(b'BEGIN CERTIFICATE') < 100:
        raise ValueError('Invalid CA bundle')
    ca.write_bytes(bundle)
    metadata['ca_bundle_sha256'] = sha256(ca)
    write_json(source / 'env.json', {'APP_ENV': 'pre' if prerelease else 'stable'})
    try:
        signing(source)
        run([flutter, 'build', 'apk', '--release', '--split-per-abi',
             '--dart-define-from-file=env.json'], cwd=source)
    finally:
        (source / 'android/app/keystore.jks').unlink(missing_ok=True)
        properties = source / 'android/local.properties'
        text = '\n'.join(line for line in (properties.read_text() if properties.exists() else '').splitlines()
                         if not line.startswith(('keyAlias=', 'keyPassword=', 'storePassword=')))
        properties.write_text(text + '\n')
    report = {}
    for apk in sorted((source / 'build/app/outputs/flutter-apk').glob('app-*-release.apk')):
        abi = apk.name.removeprefix('app-').removesuffix('-release.apk')
        if abi not in ('arm64-v8a', 'armeabi-v7a', 'x86_64'):
            raise ValueError(f'Unexpected ABI: {abi}')
        checked = verify_apk(apk)
        destination = output / f'FlClash-{checked["version_name"]}-android-{abi}.apk'
        shutil.copyfile(apk, destination)
        report[destination.name] = checked
    if len(report) != 3:
        raise ValueError('Expected exactly three Android ABI APKs')
    write_json(output / 'android-build.json', {**metadata, 'apks': report})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('prepare')
    command.add_argument('--ref')
    command.add_argument('--output', type=Path)
    command = commands.add_parser('build')
    command.add_argument('--source', type=Path, default=ROOT / '.build/source')
    command.add_argument('--output', type=Path, default=ROOT / 'dist/android')
    command.add_argument('--prerelease', action='store_true')
    command = commands.add_parser('check')
    command.add_argument('--source', type=Path, default=ROOT / '.build/source')
    command = commands.add_parser('verify')
    command.add_argument('apk', type=Path)
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare(args.ref, args.output)
    elif args.command == 'build':
        build(args.source, args.output, args.prerelease)
    elif args.command == 'check':
        check(args.source)
    else:
        print(json.dumps(verify_apk(args.apk), indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
