#!/usr/bin/env python3
"""Reproduce the privacy wording shipped with the archived v0.9.0 patches."""

import json
import sys
from pathlib import Path

PRIVACY = {
    'en': 'This version does not include Firebase Analytics or Firebase Crashlytics.',
    'zh_CN': '本版本未集成 Firebase Analytics 使用统计或 Firebase Crashlytics 崩溃上报服务。',
    'ja': 'このバージョンには Firebase Analytics および Firebase Crashlytics は組み込まれていません。',
    'ru': 'Эта версия не включает Firebase Analytics и Firebase Crashlytics.',
    'fr': 'Cette version n’intègre ni Firebase Analytics ni Firebase Crashlytics.',
    'es': 'Esta versión no incluye Firebase Analytics ni Firebase Crashlytics.',
    'ko': '이 버전에는 Firebase Analytics 및 Firebase Crashlytics가 포함되어 있지 않습니다.',
}


def main(source):
    for arb in sorted((source / 'arb').glob('intl_*.arb')):
        locale = arb.stem.removeprefix('intl_')
        if locale not in PRIVACY:
            raise ValueError(f'Add the no-Firebase privacy translation for {locale}')
        document = json.loads(arb.read_text())
        old = document['disclaimerPrivacyContent']
        if 'Firebase' not in old or len(old.split('\n\n')) != 3:
            raise ValueError(f'Upstream privacy statement changed: {arb.name}')
        new = '\n\n'.join(old.split('\n\n')[:2] + [PRIVACY[locale]])
        before = json.dumps(old, ensure_ascii=False)
        text = arb.read_text()
        if text.count(before) != 1:
            raise ValueError(f'{arb}: expected exactly one configuration marker')
        arb.write_text(text.replace(before, json.dumps(new, ensure_ascii=False)))


if __name__ == '__main__':
    main(Path(sys.argv[1]))
