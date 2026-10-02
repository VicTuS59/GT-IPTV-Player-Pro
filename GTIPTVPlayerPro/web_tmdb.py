# -*- coding: utf-8 -*-
"""Web configuration of the existing receiver TMDb metadata client."""
from .metadata import TMDbMetadataClient
from .settings import load_player_settings, save_player_settings
from .web_api import WebServiceError
from .web_remote import WEB_REMOTE


class WebTMDb(object):
    @staticmethod
    def status():
        return {"configured": bool(load_player_settings().tmdb_api_key)}

    def save(self, payload):
        key = payload.get("key")
        if payload.get("remove") is not True and (not isinstance(key, str) or not key.strip() or len(key) > 512 or any(ch.isspace() for ch in key)):
            raise WebServiceError("invalid_key", "Enter a valid TMDb API key", 400)

        def update():
            settings = load_player_settings()
            settings.tmdb_api_key = "" if payload.get("remove") is True else key.strip()
            if not save_player_settings(settings):
                raise WebServiceError("settings_not_saved", "Settings could not be saved", 500)
            return self.status()
        return WEB_REMOTE._run_on_gui(update)

    @staticmethod
    def test():
        key = load_player_settings().tmdb_api_key
        if not key:
            raise WebServiceError("tmdb_key_missing", "Add a TMDb API key in Settings", 409)
        try:
            TMDbMetadataClient(api_key=key, timeout=8).test_connection()
            return {"connected": True}
        except Exception:
            raise WebServiceError("tmdb_test_failed", "TMDb key test failed", 502)


WEB_TMDB = WebTMDb()

