# -*- coding: utf-8 -*-
"""Persistent HDD/USB recording destination using the native folder picker."""
from Screens.MessageBox import MessageBox
from .downloads_ui import GTDownloadFolderScreen
from .i18n import N_, _
from . import live_recording as recorder

MESSAGES = {
    'folder': N_('Please set the recording folder in the plugin settings.'),
    'storage': N_('Recording failed. Check the storage device.'),
    'space': N_('Not enough free space.'),
    'unsupported': N_('Recording is not available for this stream.'),
    'reopen': N_('Open the channel again to enable recording.'),
    'busy': N_('Please wait'),
}

def recording_message(code):
    return _(MESSAGES.get(code, MESSAGES['storage']))

class GTLiveRecordingFolderScreen(GTDownloadFolderScreen):
    def __init__(self, session):
        super().__init__(session, recorder.recording_folder())
        self['title'].setText(_('Live TV recording'))
        self.setTitle(_('Live TV recording'))

    def save(self):
        try:
            path = recorder.save_recording_folder(self['filelist'].getCurrentDirectory())
        except Exception as error:
            self.session.open(MessageBox, recording_message(getattr(error, 'code', 'storage')), MessageBox.TYPE_INFO)
            return
        self.close(path)
