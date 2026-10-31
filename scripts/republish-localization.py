#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Explicit one-time rebuild of the 1.8.0-r0 welcome heading layout.

The normal publisher's immutable-version policy remains unchanged. Only the
known superseded package may be replaced, after validating all new assets.
"""
import base64,hashlib,importlib.util,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('_localization_publisher',ROOT/'scripts/publish-update.py')
pub=importlib.util.module_from_spec(spec);spec.loader.exec_module(pub)
TARGET='1.8.0-r0'
PREVIOUS_SHA256='8a71c17d610257db000fd37cca4ef49ea6566d29ad040e63498f90270b5a3a92'
NOTES_SHA256='123caa61fd08a718de254eda1268ca80bf7a4dcc3c1170cacf5c6dcfd64ec617'
normal_advance=pub.advance_pointer

def advance_rebuilt_pointer(body,api=None):
    api=api or pub.gh_api;manifest=pub.verify_envelope(body)
    endpoint=f'repos/{pub.REPOSITORY}/contents/updates/stable.json'
    old=api(endpoint+'?ref=main',optional=True)
    if old:
        previous=pub.verify_envelope(base64.b64decode(old['content']))
        if (manifest['version']==previous['version']==TARGET
                and previous['sha256']==PREVIOUS_SHA256 and manifest['sha256']!=PREVIOUS_SHA256):
            notes=(ROOT/'RELEASE-NOTES.txt').read_bytes()
            if hashlib.sha256(notes).hexdigest()!=NOTES_SHA256 or manifest['notes'].strip()!=notes.decode('utf-8').strip():
                raise pub.PublishError('The approved release notes must remain unchanged')
            # publish() has verified these assets already; repeat validation at
            # the exceptional pointer boundary, never trusting a new digest alone.
            verified=pub.released_assets('v'+TARGET,api)
            if pub.json_document(verified)!=pub.json_document(body):raise pub.PublishError('Rebuilt release metadata mismatch')
            if not isinstance(old.get('sha'),str) or not old['sha']:raise pub.PublishError('Missing pointer revision')
            api(endpoint,method='PUT',data=dict(message='Republish 1.8.0-r0 welcome heading layout',branch='main',sha=old['sha'],content=base64.b64encode(body).decode()))
            return True
    return normal_advance(body,api)

def main():
    if pub.pkg.PLUGIN_VERSION!=TARGET:raise pub.PublishError('This localization rebuild is limited to 1.8.0-r0')
    if hashlib.sha256((ROOT/'RELEASE-NOTES.txt').read_bytes()).hexdigest()!=NOTES_SHA256:raise pub.PublishError('Release notes changed')
    pub.advance_pointer=advance_rebuilt_pointer
    pub.publish()

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Localization rebuild failed: '+(str(error) if isinstance(error,pub.PublishError) else type(error).__name__),file=sys.stderr);sys.exit(1)
