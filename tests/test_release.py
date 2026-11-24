import copy
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import project
import release


def upstream_release():
    return {
        'id': 42, 'tag_name': 'v0.9.0', 'name': 'FlClash 0.9.0', 'body': 'Notes',
        'prerelease': False, 'draft': False, 'published_at': '2026-10-14T00:00:00Z',
        'assets': [
            {'id': index, 'name': f'FlClash-0.9.0-android-{abi}.apk',
             'size': 123, 'digest': 'sha256:' + 'a' * 64, 'updated_at': '2026-10-14'}
            for index, abi in enumerate(('arm64-v8a', 'armeabi-v7a', 'x86_64'))
        ],
    }


def modern_upstream_release(dev_names=False):
    value = upstream_release()
    value.update(tag_name='v0.9.4', name='FlClash 0.9.4')
    prefix = 'FlClash-dev-0.9.4-' if dev_names else 'FlClash-0.9.4-'
    names = [f'FlClash-0.9.4-android-{abi}.apk'
             for abi in ('arm64-v8a', 'armeabi-v7a', 'x86_64')]
    names += [f'{prefix}android-{arch}.apk' for arch in ('arm64', 'armv7', 'amd64')]
    if dev_names:
        names.append('FlClash-0.9.4-windows-amd64-setup.exe')
    names += [f'{prefix}windows-amd64-setup.exe', f'{prefix}linux-arm64.deb',
              f'{prefix}SHA256SUMS', 'SHA256SUMS', 'manifest.json']
    value['assets'] = [{'name': name} for name in names]
    return value


class ReleaseTests(unittest.TestCase):
    def test_versioned_patches_preserve_existing_release_and_prerelease(self):
        old = project.patches_for('v0.9.0')
        self.assertEqual([path.name for path in old], [
            '001-android-package.patch', '002-remove-firebase.patch',
            '003-remove-startup-disclaimer.patch', '004-update-repository.patch',
        ])
        legacy = project.ROOT / 'patches/legacy/v0.9.0'
        self.assertTrue(all(path.parent == legacy for path in old))
        self.assertTrue((legacy / 'prepare.py').is_file())
        self.assertEqual(project.patches_for('v0.9.1-pre.1'), old)
        for tag in ('v0.9.1', 'v0.10.0', 'v1.0.0'):
            with self.subTest(tag=tag):
                patches = project.patches_for(tag)
                self.assertEqual([path.name for path in patches], [
                    '001-android-package.patch', '003-remove-startup-disclaimer.patch',
                    '004-update-repository.patch',
                ])
                self.assertTrue(all(path.parent == project.ROOT / 'patches' for path in patches))
                self.assertFalse((patches[0].parent / 'prepare.py').exists())
        for tag in ('v0.9.2-pre.1', 'v0.9.2'):
            with self.subTest(tag=tag):
                patches = project.patches_for(tag)
                self.assertEqual([path.name for path in patches], [
                    '001-android-package.patch', '003-remove-startup-disclaimer.patch',
                    '004-update-repository.patch',
                ])
                self.assertEqual(patches[0].parent, project.ROOT / 'patches')
                self.assertEqual(patches[1].parent, project.ROOT / 'patches/v0.9.2')
                self.assertEqual(patches[2].parent, project.ROOT / 'patches/v0.9.2')
        for tag in ('v0.9.3-pre.1', 'v0.9.3'):
            with self.subTest(tag=tag):
                patches = project.patches_for(tag)
                self.assertEqual([path.name for path in patches], [
                    '001-android-package.patch', '003-remove-startup-disclaimer.patch',
                    '004-update-repository.patch',
                ])
                self.assertEqual(patches[0].parent, project.ROOT / 'patches')
                self.assertEqual(patches[1].parent, project.ROOT / 'patches/v0.9.3')
                self.assertEqual(patches[2].parent, project.ROOT / 'patches/v0.9.3')
        for tag in ('v0.9.4-pre.1', 'v0.9.4'):
            with self.subTest(tag=tag):
                patches = project.patches_for(tag)
                self.assertEqual([path.name for path in patches], [
                    '001-android-package.patch', '003-remove-startup-disclaimer.patch',
                    '004-update-repository.patch',
                ])
                self.assertEqual(patches[0].parent, project.ROOT / 'patches')
                self.assertEqual(patches[1].parent, project.ROOT / 'patches/v0.9.4')
                self.assertEqual(patches[2].parent, project.ROOT / 'patches/v0.9.4')
        with self.assertRaises(ValueError):
            project.patches_for('../main')

    def test_original_archived_patches_match_published_release_record(self):
        tag = 'v0.9.0'
        state = json.loads((project.ROOT / 'releases' / f'{tag}.json').read_text())
        for name in ('001-android-package.patch', '002-remove-firebase.patch',
                     '003-remove-startup-disclaimer.patch'):
            with self.subTest(patch=name):
                self.assertEqual(state['build']['patches'][name], project.sha256(
                    project.ROOT / 'patches/legacy/v0.9.0' / name,
                ))

    def test_android_download_links_use_mirror_without_changing_other_content(self):
        for tag in ('v0.9.0', 'v0.9.0-beta.1'):
            with self.subTest(tag=tag):
                value = upstream_release()
                value['tag_name'] = tag
                source = f'https://github.com/{project.config()["upstream_repository"]}'
                destination = f'https://github.com/{project.config()["destination_repository"]}'
                buttons = [f'<a href="{source}/releases/download/{tag}/{asset["name"]}">'
                           '<img src="https://img.shields.io/badge/APK-Android-green"></a>'
                           for asset in value['assets']]
                desktop = f'[Windows]({source}/releases/download/{tag}/desktop.zip)'
                changelog = f'[ChangeLog]({source}/blob/main/CHANGELOG.md)'
                value['body'] = '\n'.join(['Release notes', *buttons, desktop, changelog])
                original = copy.deepcopy(value)
                mirrored = release.release_body(value)
                for asset in value['assets']:
                    self.assertIn(f'{destination}/releases/download/{tag}/{asset["name"]}', mirrored)
                    self.assertNotIn(f'{source}/releases/download/{tag}/{asset["name"]}', mirrored)
                self.assertIn(desktop, mirrored)
                self.assertIn(changelog, mirrored)
                self.assertIn('https://img.shields.io/badge/APK-Android-green', mirrored)
                self.assertIn('Release notes', mirrored)
                self.assertEqual(value, original)
        for body in ('', None):
            value['body'] = body
            self.assertEqual(release.release_body(value), '')

    def test_release_fingerprint_ignores_download_counts_and_order(self):
        original = upstream_release()
        edited = copy.deepcopy(original)
        edited['assets'].reverse()
        edited['assets'][0]['download_count'] = 500
        self.assertEqual(release.release_fingerprint(original), release.release_fingerprint(edited))
        edited['body'] = 'Corrected notes'
        self.assertNotEqual(release.release_fingerprint(original), release.release_fingerprint(edited))

    def test_rejects_unsafe_or_unhandled_upstream_attachments(self):
        for name in ('../malicious.apk', 'SHA512SUMS', 'application.apk.asc'):
            with self.subTest(name=name):
                value = upstream_release()
                value['assets'].append({'name': name})
                with self.assertRaises(ValueError):
                    release.validate_release(value)

    def test_accepts_modern_apk_aliases_but_rejects_unknown_or_incomplete_sets(self):
        for dev_names in (False, True):
            value = modern_upstream_release(dev_names)
            prefix = 'FlClash-dev-0.9.4-' if dev_names else 'FlClash-0.9.4-'
            with self.subTest(dev_names=dev_names):
                release.validate_release(value)
            for name in ('other.apk', 'FlClash-other-0.9.4-SHA256SUMS', 'SHA512SUMS'):
                with self.subTest(dev_names=dev_names, name=name):
                    changed = copy.deepcopy(value)
                    changed['assets'].append({'name': name})
                    with self.assertRaises(ValueError):
                        release.validate_release(changed)
            for name in (f'{prefix}android-arm64.apk', 'FlClash-0.9.4-android-arm64-v8a.apk'):
                with self.subTest(dev_names=dev_names, missing=name):
                    changed = copy.deepcopy(value)
                    changed['assets'] = [asset for asset in changed['assets'] if asset['name'] != name]
                    with self.assertRaises(ValueError):
                        release.validate_release(changed)

    def test_assemble_replaces_both_apk_names_and_rewrites_all_release_metadata(self):
        self.assert_assembled_release_metadata(dev_names=False)

    def test_assemble_preserves_previous_dev_release_filenames(self):
        self.assert_assembled_release_metadata(dev_names=True)

    def assert_assembled_release_metadata(self, dev_names):
        value = modern_upstream_release(dev_names)
        prefix = 'FlClash-dev-0.9.4-' if dev_names else 'FlClash-0.9.4-'
        checksum_name = f'{prefix}SHA256SUMS'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            android = root / 'android'
            android.mkdir()
            originals = {asset['name']: asset['name'].encode() for asset in value['assets']}
            payloads = {name: data for name, data in originals.items()
                        if name not in ('manifest.json', 'SHA256SUMS', checksum_name)}
            legacy_names = {f'FlClash-0.9.4-android-{abi}.apk'
                            for abi in ('arm64-v8a', 'armeabi-v7a', 'x86_64')}
            named = {name: data for name, data in payloads.items()
                     if name.startswith(prefix) and name not in legacy_names}
            legacy_names.add('FlClash-0.9.4-windows-amd64-setup.exe')
            manifest = {
                'checksums': checksum_name,
                'artifacts': [{'name': name, 'sha256': 'a' * 64, 'size': len(data),
                               'line': 'dev', 'channel': 'stable', 'commit': 'upstream-commit'}
                              for name, data in named.items()],
            }
            originals['manifest.json'] = json.dumps(manifest).encode()
            for checksum, names in (
                (checksum_name, named),
                ('SHA256SUMS', legacy_names),
            ):
                originals[checksum] = ''.join('a' * 64 + f'  {name}\n' for name in sorted(names)).encode()
            build = {'upstream_tag': 'v0.9.4', 'apks': {}}
            for abi in ('arm64-v8a', 'armeabi-v7a', 'x86_64'):
                apk = android / f'FlClash-0.9.4-android-{abi}.apk'
                apk.write_bytes(f'rebuilt-{abi}'.encode())
                build['apks'][apk.name] = {
                    'sha256': project.sha256(apk),
                    'certificate_sha256': project.config()['certificate_sha256'],
                }
            project.write_json(android / 'android-build.json', build)
            project.write_json(root / 'plan.json', {'upstream': value})

            def download(_, destination):
                destination.mkdir(parents=True, exist_ok=True)
                for name, data in originals.items():
                    (destination / name).write_bytes(data)

            output = root / 'release'
            with patch.object(release, 'download_assets', side_effect=download), \
                    patch.object(release, 'verify_apk', side_effect=lambda file: build['apks'][file.name]):
                complete_checksums = originals['SHA256SUMS']
                missing = 'FlClash-0.9.4-android-arm64-v8a.apk'
                originals['SHA256SUMS'] = b''.join(
                    line for line in complete_checksums.splitlines(keepends=True)
                    if missing.encode() not in line
                )
                with self.assertRaisesRegex(ValueError, 'cover every release attachment'):
                    release.assemble(root / 'plan.json', android, root / 'incomplete')
                originals['SHA256SUMS'] = complete_checksums
                release.assemble(root / 'plan.json', android, output)
            self.assertEqual({file.name for file in output.iterdir()}, set(originals))
            for arch, abi in (('arm64', 'arm64-v8a'), ('armv7', 'armeabi-v7a'), ('amd64', 'x86_64')):
                expected = f'rebuilt-{abi}'.encode()
                self.assertEqual((output / f'FlClash-0.9.4-android-{abi}.apk').read_bytes(), expected)
                self.assertEqual((output / f'{prefix}android-{arch}.apk').read_bytes(), expected)
            for name in payloads:
                if not name.endswith('.apk'):
                    self.assertEqual((output / name).read_bytes(), originals[name])
            rewritten = json.loads((output / 'manifest.json').read_text())
            self.assertEqual(rewritten['checksums'], manifest['checksums'])
            for artifact in rewritten['artifacts']:
                file = output / artifact['name']
                self.assertEqual(artifact['sha256'], project.sha256(file))
                self.assertEqual(artifact['size'], file.stat().st_size)
                self.assertEqual(artifact['commit'], 'upstream-commit')
                self.assertEqual(artifact['channel'], 'stable')
                self.assertEqual(artifact['line'], 'dev')
            for checksum in ('SHA256SUMS', checksum_name):
                for line in (output / checksum).read_text().splitlines():
                    digest, name = line.split()
                    self.assertEqual(digest, project.sha256(output / name))
            state = json.loads((root / 'release-state.json').read_text())
            self.assertEqual(state['assets'], {name: project.sha256(output / name) for name in originals})

    def test_manifest_rejects_missing_duplicate_or_unknown_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            apk = root / 'android.apk'
            apk.write_bytes(b'rebuilt')
            path = root / 'manifest.json'
            for names, checksum in (([], 'SUMS'), (['android.apk'] * 2, 'SUMS'),
                                    (['../android.apk'], 'SUMS'), (['android.apk'], '../SUMS')):
                with self.subTest(names=names, checksum=checksum):
                    project.write_json(path, {'checksums': checksum,
                                              'artifacts': [{'name': name} for name in names]})
                    with self.assertRaises(ValueError):
                        release.rewrite_manifest(path, {'android.apk': apk}, 'SUMS')

    def test_checksum_rewrite_covers_desktop_and_rebuilt_android(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assets = {'desktop.zip': root / 'desktop.zip', 'android.apk': root / 'android.apk'}
            for name, file in assets.items():
                file.write_bytes(name.encode())
            sums = root / 'SHA256SUMS'
            sums.write_text('a' * 64 + '  ./desktop.zip\n' + 'b' * 64 + ' *android.apk\n')
            release.rewrite_checksums(sums, assets)
            self.assertEqual(sums.read_text(),
                             f'{project.sha256(assets["desktop.zip"])}  ./desktop.zip\n'
                             f'{project.sha256(assets["android.apk"])} *android.apk\n')
            for contents in ('a' * 64 + '  desktop.zip\n',
                             ('a' * 64 + '  desktop.zip\n') * 2,
                             'a' * 64 + '  ../desktop.zip\n'):
                sums.write_text(contents)
                with self.assertRaises(ValueError):
                    release.rewrite_checksums(sums, assets)

    def test_plan_recovers_missing_draft_or_damaged_release(self):
        value = upstream_release()
        cfg = project.config()
        value['body'] = (f'<a href="https://github.com/{cfg["upstream_repository"]}'
                         f'/releases/download/{value["tag_name"]}/{value["assets"][0]["name"]}">APK</a>')
        patches = {path.name: project.sha256(path) for path in project.patches_for(value['tag_name'])}
        state = {
            'upstream_fingerprint': release.release_fingerprint(value),
            'assets': {'android.apk': 'b' * 64},
            'build': {'application_id': cfg['application_id'], 'patches': patches,
                      'apks': {'android.apk': {'certificate_sha256': cfg['certificate_sha256']}}},
        }
        remote = {**value, 'body': release.release_body(value),
                  'assets': [{'name': 'android.apk', 'digest': 'sha256:' + 'b' * 64}]}
        self.assertTrue(release.is_current(state, value, remote))
        for mutation in (None, {**remote, 'draft': True}, {**remote, 'assets': []},
                         {**remote, 'body': 'Altered'}, {**remote, 'body': value['body']},
                         {**remote, 'prerelease': True}):
            self.assertFalse(release.is_current(state, value, mutation))
        changed = copy.deepcopy(state)
        changed['build']['patches'] = {}
        self.assertFalse(release.is_current(changed, value, remote))

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'releases').mkdir()
            (root / 'releases/v0.9.0.json').write_text(json.dumps(state))
            with patch.object(release, 'ROOT', root), patch.object(release, 'api', return_value=value), \
                    patch.object(release, 'list_releases', return_value=[{**remote, 'draft': True}]):
                release.plan('v0.9.0', root / 'plan.json')
                self.assertEqual(json.loads((root / 'plan.json').read_text())['upstream']['id'], 42)

    def test_current_release_uses_its_versioned_patch_fingerprints(self):
        cfg = project.config()
        for tag, other in (('v0.9.0', 'v0.9.1'), ('v0.9.1', 'v0.9.0')):
            with self.subTest(tag=tag):
                value = upstream_release()
                value['tag_name'] = tag
                state = {
                    'upstream_fingerprint': release.release_fingerprint(value),
                    'assets': {'android.apk': 'b' * 64},
                    'build': {
                        'application_id': cfg['application_id'],
                        'patches': {path.name: project.sha256(path)
                                    for path in project.patches_for(tag)},
                        'apks': {'android.apk': {'certificate_sha256': cfg['certificate_sha256']}},
                    },
                }
                remote = {**value, 'assets': [{'name': 'android.apk', 'digest': 'sha256:' + 'b' * 64}]}
                self.assertTrue(release.is_current(state, value, remote))
                outdated = copy.deepcopy(state)
                del outdated['build']['patches']['004-update-repository.patch']
                self.assertFalse(release.is_current(outdated, value, remote))
                state['build']['patches'] = {path.name: project.sha256(path)
                                           for path in project.patches_for(other)}
                self.assertFalse(release.is_current(state, value, remote))

    def test_plan_skips_prereleases_and_finds_stable_changes(self):
        current = upstream_release()
        upcoming = copy.deepcopy(current)
        upcoming.update(tag_name='v0.9.5', published_at='2026-11-24T00:00:00Z')
        for asset in upcoming['assets']:
            asset['name'] = asset['name'].replace('0.9.0', '0.9.5')
        excluded = [
            {**current, 'tag_name': tag, 'prerelease': prerelease, 'draft': draft,
             'published_at': '2026-11-22T00:00:00Z', 'assets': []}
            for tag, prerelease, draft in (
                ('v0.9.5-pre.1', True, False), ('v0.9.5-rs.1', True, False),
                ('v0.9.5-beta.1', False, False), ('v0.9.6', True, False),
                ('v0.9.7', False, True),
            )
        ]
        for pending in (None, upcoming):
            with self.subTest(pending=pending is not None), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                project.write_json(root / 'releases/v0.9.0.json', {})
                candidates = [*excluded, current]
                if pending is not None:
                    candidates.insert(0, pending)
                output = root / 'plan.json'
                with patch.object(release, 'ROOT', root), \
                        patch.object(release, 'api', return_value=current), \
                        patch.object(release, 'list_releases', side_effect=[candidates, []]), \
                        patch.object(release, 'is_current', return_value=True):
                    release.plan(None, output)
                selected = json.loads(output.read_text())
                if pending is None:
                    self.assertIsNone(selected)
                else:
                    self.assertEqual(selected['upstream'], upcoming)

    def test_rejects_non_stable_releases_before_attachment_validation(self):
        for changes in ({'prerelease': True}, {'draft': True},
                        {'tag_name': 'v0.9.5-pre.1'}, {'tag_name': 'v0.9.5-rs.1'}):
            with self.subTest(changes=changes):
                value = {**upstream_release(), **changes, 'assets': []}
                with self.assertRaisesRegex(ValueError, 'Only published stable releases'):
                    release.validate_release(value)

    def test_invalid_tag_never_reaches_github(self):
        for tag in ('../main', 'v0.9.5-pre.1', 'v0.9.5-rs.1', 'v0.9.5-beta.1'):
            with self.subTest(tag=tag), patch.object(release, 'api') as api:
                with self.assertRaises(ValueError):
                    release.plan(tag, Path('unused'))
                api.assert_not_called()


class ApkVerificationTests(unittest.TestCase):
    def test_rejects_wrong_identity_manifest_or_telemetry_classes(self):
        cfg = project.config()
        good_cert = f'Number of signers: 1\nSigner #1 certificate SHA-256 digest: {cfg["certificate_sha256"]}'
        good_package = f"package: name='{cfg['application_id']}' versionCode='1' versionName='0.9.0'"
        with tempfile.TemporaryDirectory() as temporary:
            apk = Path(temporary) / 'test.apk'
            for cert, package, manifest, dex, accepted in (
                (good_cert, good_package, '', b'Lcom/google/firebase/encoders/Encoder;', True),
                (good_cert.replace('Signer #1 ', 'V2 Signer: '), good_package, '', b'', True),
                (good_cert.replace('signers: 1', 'signers: 2'), good_package, '', b'', False),
                (good_cert.replace(cfg['certificate_sha256'], '0' * 64), good_package, '', b'', False),
                (good_cert, good_package.replace(cfg['application_id'], 'com.follow.clash'), '', b'', False),
                (good_cert, good_package, 'com.google.android.gms.permission.AD_ID', b'', False),
                (good_cert, good_package, '', b'Lcom/google/firebase/crashlytics/FirebaseCrashlytics;', False),
            ):
                with self.subTest(cert=cert, package=package, manifest=manifest, dex=dex):
                    with zipfile.ZipFile(apk, 'w') as archive:
                        archive.writestr('classes.dex', dex)
                    with patch.object(project, 'sdk_tool', side_effect=lambda name: name), \
                            patch.object(project, 'run', side_effect=[cert, package, manifest]):
                        if accepted:
                            self.assertEqual(project.verify_apk(apk)['application_id'], cfg['application_id'])
                        else:
                            with self.assertRaises(ValueError):
                                project.verify_apk(apk)


if __name__ == '__main__':
    unittest.main()
