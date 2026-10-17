#!/usr/bin/env python3
"""Discover upstream releases and publish verified mirrors through draft releases."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from project import ROOT, config, patches_for, run, sha256, verify_apk, write_json


def api(path, method='GET', value=None):
    args = ['gh', 'api', path, '--method', method]
    payload = None
    if value is not None:
        args += ['--input', '-']
        payload = json.dumps(value)
    text = run(args, capture=True, input=payload)
    return json.loads(text) if text else None


def release_fingerprint(release):
    data = {key: release.get(key) for key in ('id', 'tag_name', 'name', 'body', 'prerelease')}
    data['assets'] = sorted([
        {key: asset.get(key) for key in ('id', 'name', 'size', 'digest', 'updated_at')}
        for asset in release['assets']
    ], key=lambda asset: asset['name'])
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def validate_release(release):
    if release['draft'] or not re.fullmatch(r'v\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', release['tag_name']):
        raise ValueError('Only published version-tagged releases are supported')
    names = [asset['name'] for asset in release['assets']]
    if len(names) != len(set(names)):
        raise ValueError('Duplicate upstream asset names')
    for name in names:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]*', name):
            raise ValueError(f'Unsafe asset filename: {name}')
    apks = [name for name in names if name.endswith('.apk')]
    version = release['tag_name'].removeprefix('v').split('-')[0]
    expected_apks = {f'FlClash-{version}-android-{abi}.apk'
                     for abi in ('arm64-v8a', 'armeabi-v7a', 'x86_64')}
    if set(apks) != expected_apks:
        raise ValueError('Expected the upstream Android APKs for all three supported ABIs')
    for name in names:
        if name.endswith(('.idsig', '.asc', '.sig')):
            raise ValueError(f'Upstream signature sidecar requires explicit handling: {name}')
        if re.search(r'(checksum|sha\d+|md5)', name, re.I) and name != 'SHA256SUMS':
            raise ValueError(f'Unknown checksum manifest requires explicit handling: {name}')


def list_releases(repository):
    releases = []
    page = 1
    while True:
        batch = api(f'repos/{repository}/releases?per_page=100&page={page}')
        releases.extend(batch)
        if len(batch) < 100:
            return releases
        page += 1


def release_body(release):
    cfg = config()
    body = release['body'] or ''
    for asset in release['assets']:
        if asset['name'].endswith('.apk'):
            path = f'/releases/download/{release["tag_name"]}/{asset["name"]}'
            body = body.replace(f'https://github.com/{cfg["upstream_repository"]}{path}',
                                f'https://github.com/{cfg["destination_repository"]}{path}')
    return body


def is_current(state, release, remote):
    cfg = config()
    build = state.get('build', {})
    patches = {path.name: sha256(path) for path in patches_for(release['tag_name'])}
    return (
        state.get('upstream_fingerprint') == release_fingerprint(release)
        and build.get('patches') == patches
        and build.get('application_id') == cfg['application_id']
        and bool(build.get('apks'))
        and all(apk['certificate_sha256'] == cfg['certificate_sha256'] for apk in build['apks'].values())
        and remote is not None and not remote['draft']
        and remote['name'] == (release['name'] or release['tag_name'])
        and (remote['body'] or '') == release_body(release)
        and remote['prerelease'] == release['prerelease']
        and {asset['name']: asset.get('digest') for asset in remote['assets']}
        == {name: f'sha256:{digest}' for name, digest in state.get('assets', {}).items()}
    )


def plan(tag, output):
    cfg = config()
    upstream = cfg['upstream_repository']
    if tag:
        if not re.fullmatch(r'v\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', tag):
            raise ValueError('Expected an upstream release tag such as v0.9.1')
        candidates = [api(f'repos/{upstream}/releases/tags/{tag}')]
    else:
        baseline = api(f'repos/{upstream}/releases/tags/{cfg["first_release"]}')
        candidates = [item for item in list_releases(upstream)
                      if not item['draft'] and item['published_at'] >= baseline['published_at']]
        candidates.sort(key=lambda release: release['published_at'])
    remote_releases = {item['tag_name']: item for item in list_releases(cfg['destination_repository'])}
    chosen = None
    for release in candidates:
        validate_release(release)
        state = ROOT / 'releases' / f'{release["tag_name"]}.json'
        fingerprint = release_fingerprint(release)
        if state.exists() and is_current(json.loads(state.read_text()), release,
                                         remote_releases.get(release['tag_name'])):
            continue
        chosen = {'upstream': release, 'upstream_fingerprint': fingerprint}
        break
    write_json(output, chosen)
    if chosen:
        print(chosen['upstream']['tag_name'])
    else:
        print('No upstream changes')


def download_assets(release, destination):
    destination.mkdir(parents=True, exist_ok=True)
    for asset in release['assets']:
        run(['gh', 'release', 'download', release['tag_name'], '--repo',
             config()['upstream_repository'], '--pattern', asset['name'], '--dir', destination])
        file = destination / asset['name']
        if file.stat().st_size != asset['size']:
            raise ValueError(f'Upstream asset size mismatch: {file.name}')
        digest = asset.get('digest')
        if not digest or not re.fullmatch(r'sha256:[a-f0-9]{64}', digest):
            raise ValueError(f'Upstream SHA-256 digest missing: {file.name}')
        if sha256(file) != digest.removeprefix('sha256:'):
            raise ValueError(f'Upstream asset digest mismatch: {file.name}')


def rewrite_checksums(path, assets):
    text = path.read_text()
    seen = set()
    rewritten = []
    for line in text.splitlines():
        match = re.fullmatch(r'([0-9a-fA-F]{64})([ \t]+\*?)(\./)?([A-Za-z0-9][A-Za-z0-9._+-]*)', line)
        if not match or match[4] not in assets or match[4] in seen:
            raise ValueError(f'Unsupported SHA256SUMS entry: {line}')
        seen.add(match[4])
        rewritten.append(f'{sha256(assets[match[4]])}{match[2]}{match[3] or ""}{match[4]}')
    if seen != set(assets):
        raise ValueError('SHA256SUMS must cover every release attachment except itself')
    path.write_text('\n'.join(rewritten) + '\n')


def assemble(plan_path, android, output):
    selected = json.loads(Path(plan_path).read_text())
    release = selected['upstream']
    validate_release(release)
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Use a fresh attachment output directory')
    output.mkdir(parents=True)
    build = json.loads((Path(android) / 'android-build.json').read_text())
    if build['upstream_tag'] != release['tag_name']:
        raise ValueError('Android build does not match the selected upstream release')
    report = {}
    with tempfile.TemporaryDirectory(prefix='flclashx-download-') as temporary:
        originals = Path(temporary)
        download_assets(release, originals)
        expected_apks = {item['name'] for item in release['assets'] if item['name'].endswith('.apk')}
        if expected_apks != set(build['apks']):
            raise ValueError('Android APK filenames differ from the upstream release')
        for asset in release['assets']:
            name = asset['name']
            source = Path(android) / name if name.endswith('.apk') else originals / name
            if name.endswith('.apk'):
                checked = verify_apk(source)
                if checked != build['apks'][name]:
                    raise ValueError(f'Android APK changed after validation: {name}')
            destination = output / name
            shutil.copyfile(source, destination)
            report[name] = sha256(destination)
        checksum = output / 'SHA256SUMS'
        if checksum.exists():
            rewrite_checksums(checksum, {file.name: file for file in output.iterdir() if file != checksum})
            report[checksum.name] = sha256(checksum)
    selected['build'] = build
    selected['assets'] = report
    write_json(output.parent / 'release-state.json', selected)


def publish(state_path, assets):
    cfg = config()
    state = json.loads(Path(state_path).read_text())
    release = state['upstream']
    validate_release(release)
    tag = release['tag_name']
    destination = cfg['destination_repository']
    body = release_body(release)
    if run(['git', 'status', '--porcelain'], capture=True):
        raise ValueError('Publish from a clean wrapper checkout')
    current = api(f'repos/{cfg["upstream_repository"]}/releases/tags/{tag}')
    if release_fingerprint(current) != state['upstream_fingerprint']:
        raise ValueError('Upstream changed during the build; rerun before publishing')
    for name, digest in state['assets'].items():
        if sha256(Path(assets) / name) != digest:
            raise ValueError(f'Attachment changed before upload: {name}')
    previous = next((item for item in list_releases(destination) if item['tag_name'] == tag), None)
    if previous and previous.get('immutable'):
        raise ValueError('The existing release is immutable; manual handling is required')
    if previous:
        draft = api(f'repos/{destination}/releases/{previous["id"]}', 'PATCH', {'draft': True})
        for asset in api(f'repos/{destination}/releases/{draft["id"]}/assets?per_page=100'):
            api(f'repos/{destination}/releases/assets/{asset["id"]}', 'DELETE')
    else:
        draft = api(f'repos/{destination}/releases', 'POST', {
            'tag_name': tag, 'target_commitish': 'main', 'draft': True,
            'name': release['name'] or tag, 'body': body,
            'prerelease': release['prerelease'],
        })
    run(['gh', 'release', 'upload', tag, '--repo', destination,
         *[Path(assets) / name for name in sorted(state['assets'])]])
    uploaded = api(f'repos/{destination}/releases/{draft["id"]}/assets?per_page=100')
    if {asset['name'] for asset in uploaded} != set(state['assets']):
        raise ValueError('Uploaded attachment set differs from the validated set')
    for asset in uploaded:
        if asset.get('digest') != f'sha256:{state["assets"][asset["name"]]}':
            raise ValueError(f'Uploaded attachment digest mismatch: {asset["name"]}')
    commit = state['build']['source_commit']
    run(['git', 'fetch', 'origin', f'refs/tags/{tag}'], cwd=ROOT / 'upstream')
    if run(['git', 'rev-parse', 'FETCH_HEAD^{commit}'], cwd=ROOT / 'upstream', capture=True) != commit:
        raise ValueError('Upstream source tag changed during the build')
    run(['git', 'checkout', '--detach', commit], cwd=ROOT / 'upstream')
    record = ROOT / 'releases' / f'{tag}.json'
    write_json(record, state)
    run(['git', 'add', 'upstream', record])
    changed = subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=ROOT)
    if changed.returncode not in (0, 1):
        changed.check_returncode()
    if changed.returncode:
        run(['git', '-c', 'user.name=github-actions[bot]', '-c',
             'user.email=41898282+github-actions[bot]@users.noreply.github.com',
             'commit', '-m', f'chore(release): mirror {tag}'])
    local_tags = run(['git', 'tag', '--list', tag], capture=True)
    if not local_tags:
        run(['git', 'tag', tag])
    run(['git', 'push', '--atomic', 'origin', 'HEAD:main', f'refs/tags/{tag}'])
    stable = api(f'repos/{cfg["upstream_repository"]}/releases/latest')['tag_name']
    published = api(f'repos/{destination}/releases/{draft["id"]}', 'PATCH', {
        'draft': False, 'name': release['name'] or tag, 'body': body,
        'prerelease': release['prerelease'],
        'make_latest': 'true' if tag == stable else 'false',
    })
    if not is_current(state, release, published):
        raise ValueError('Release did not reach the expected published state; rerun to recover')
    print(f'https://github.com/{destination}/releases/tag/{tag}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('plan')
    command.add_argument('--tag')
    command.add_argument('--output', type=Path, default=ROOT / '.build/plan.json')
    command = commands.add_parser('assemble')
    command.add_argument('--plan', type=Path, default=ROOT / '.build/plan.json')
    command.add_argument('--android', type=Path, default=ROOT / 'dist/android')
    command.add_argument('--output', type=Path, default=ROOT / 'dist/release')
    command = commands.add_parser('publish')
    command.add_argument('--state', type=Path, default=ROOT / 'dist/release-state.json')
    command.add_argument('--assets', type=Path, default=ROOT / 'dist/release')
    args = parser.parse_args()
    if args.command == 'plan':
        plan(args.tag, args.output)
    elif args.command == 'assemble':
        assemble(args.plan, args.android, args.output)
    else:
        publish(args.state, args.assets)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
