"""Small shared web errors; importing remote control loads no web services."""

class WebServiceError(Exception):
    def __init__(self, code, message='', status=400):
        self.code = str(code or 'request_failed')
        self.message = str(message or self.code)[:512]
        self.status = int(status)
        Exception.__init__(self, self.message)


class SubtitleError(Exception):
    def __init__(self, code, status=400):
        self.code, self.status = code, status
        self.message = code.replace('_', ' ')
        Exception.__init__(self, self.message)


class AudioError(SubtitleError):
    pass
