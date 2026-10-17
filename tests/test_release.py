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
        for tag in ('v0.9.1', 'v0.9.2-pre.1', 'v0.10.0', 'v1.0.0'):
            with self.subTest(tag=tag):
                patches = project.patches_for(tag)
                self.assertEqual([path.name for path in patches], [
                    '001-android-package.patch', '003-remove-startup-disclaimer.patch',
                    '004-update-repository.patch',
                ])
                self.assertTrue(all(path.parent == project.ROOT / 'patches' for path in patches))
                self.assertFalse((patches[0].parent / 'prepare.py').exists())
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

    def test_invalid_tag_never_reaches_github(self):
        with patch.object(release, 'api') as api:
            with self.assertRaises(ValueError):
                release.plan('../main', Path('unused'))
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
