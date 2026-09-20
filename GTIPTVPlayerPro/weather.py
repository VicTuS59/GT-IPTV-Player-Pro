# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later

import datetime
import json
import os
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import PLUGIN_VERSION
from .diagnostics import log_event
from .i18n import N_, device_language, localized_short_weekday, _
from .settings import (
    plugin_config_section,
    save_plugin_config,
    set_config_value,
)


SETTINGS_PATH = "/etc/enigma2/gtiptvplayerpro-weather.json"
CACHE_PATH = "/etc/enigma2/gtiptvplayerpro-weather-cache.json"
CACHE_SECONDS = 30 * 60
DEFAULT_CITY = "Tekirdağ"
DEFAULT_UNIT = "C"
USER_AGENT = "GT-IPTV-Player-Pro/{}".format(PLUGIN_VERSION)
MAX_WEATHER_BYTES = 512 * 1024
WEATHER_READ_CHUNK_BYTES = 64 * 1024

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


class WeatherError(Exception):
    pass


def _optional_coordinate(value, minimum, maximum):
    if value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result < minimum or result > maximum:
        return None
    return result


class WeatherLocation(object):
    """One unambiguous Open-Meteo geocoding result."""

    def __init__(
        self,
        name,
        latitude,
        longitude,
        location_id="",
        country="",
        country_code="",
        admin1="",
        admin2="",
        admin3="",
        admin4="",
        timezone="",
    ):
        self.name = str(name or "").strip()
        self.latitude = _optional_coordinate(latitude, -90.0, 90.0)
        self.longitude = _optional_coordinate(longitude, -180.0, 180.0)
        self.location_id = str(location_id or "").strip()
        self.country = str(country or "").strip()
        self.country_code = str(country_code or "").strip().upper()
        self.admin1 = str(admin1 or "").strip()
        self.admin2 = str(admin2 or "").strip()
        self.admin3 = str(admin3 or "").strip()
        self.admin4 = str(admin4 or "").strip()
        self.timezone = str(timezone or "").strip()
        if not self.name or self.latitude is None or self.longitude is None:
            raise ValueError("invalid weather location")

    @classmethod
    def from_dict(cls, value):
        value = dict(value or {})
        return cls(
            value.get("name") or value.get("city"),
            value.get("latitude"),
            value.get("longitude"),
            location_id=value.get("location_id", value.get("id", "")),
            country=value.get("country", ""),
            country_code=value.get("country_code", ""),
            admin1=value.get("admin1", ""),
            admin2=value.get("admin2", ""),
            admin3=value.get("admin3", ""),
            admin4=value.get("admin4", ""),
            timezone=value.get("timezone", ""),
        )

    @property
    def display_name(self):
        parts = []
        seen = set()
        for value in (
            self.name,
            self.admin2,
            self.admin1,
            self.country,
        ):
            value = str(value or "").strip()
            key = value.casefold()
            if value and key not in seen:
                parts.append(value)
                seen.add(key)
        return " • ".join(parts)

    @property
    def identity(self):
        if self.location_id:
            return "id:{}".format(self.location_id)
        return "geo:{:.5f},{:.5f}".format(self.latitude, self.longitude)

    def as_dict(self):
        return {
            "location_id": self.location_id,
            "name": self.name,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "country": self.country,
            "country_code": self.country_code,
            "admin1": self.admin1,
            "admin2": self.admin2,
            "admin3": self.admin3,
            "admin4": self.admin4,
            "timezone": self.timezone,
        }


class WeatherSettings(object):
    def __init__(
        self,
        enabled=True,
        city=DEFAULT_CITY,
        unit=DEFAULT_UNIT,
        latitude=None,
        longitude=None,
        location_id="",
        country="",
        country_code="",
        admin1="",
        admin2="",
        admin3="",
        admin4="",
        timezone="",
    ):
        self.enabled = bool(enabled)
        self.city = str(city or DEFAULT_CITY).strip() or DEFAULT_CITY
        unit = str(unit or DEFAULT_UNIT).strip().upper()
        self.unit = unit if unit in ("C", "F") else DEFAULT_UNIT
        self.latitude = _optional_coordinate(latitude, -90.0, 90.0)
        self.longitude = _optional_coordinate(longitude, -180.0, 180.0)
        if self.latitude is None or self.longitude is None:
            self.latitude = None
            self.longitude = None
        self.location_id = str(location_id or "").strip()
        self.country = str(country or "").strip()
        self.country_code = str(country_code or "").strip().upper()
        self.admin1 = str(admin1 or "").strip()
        self.admin2 = str(admin2 or "").strip()
        self.admin3 = str(admin3 or "").strip()
        self.admin4 = str(admin4 or "").strip()
        self.timezone = str(timezone or "").strip()

    @property
    def location(self):
        if self.latitude is None or self.longitude is None:
            return None
        try:
            return WeatherLocation(
                self.city,
                self.latitude,
                self.longitude,
                location_id=self.location_id,
                country=self.country,
                country_code=self.country_code,
                admin1=self.admin1,
                admin2=self.admin2,
                admin3=self.admin3,
                admin4=self.admin4,
                timezone=self.timezone,
            )
        except ValueError:
            return None

    @property
    def location_display_name(self):
        location = self.location
        return location.display_name if location is not None else self.city

    def clear_location(self):
        self.latitude = None
        self.longitude = None
        self.location_id = ""
        self.country = ""
        self.country_code = ""
        self.admin1 = ""
        self.admin2 = ""
        self.admin3 = ""
        self.admin4 = ""
        self.timezone = ""

    def set_location(self, location):
        if not isinstance(location, WeatherLocation):
            location = WeatherLocation.from_dict(location)
        self.city = location.name
        self.latitude = location.latitude
        self.longitude = location.longitude
        self.location_id = location.location_id
        self.country = location.country
        self.country_code = location.country_code
        self.admin1 = location.admin1
        self.admin2 = location.admin2
        self.admin3 = location.admin3
        self.admin4 = location.admin4
        self.timezone = location.timezone
        return self

    def as_dict(self):
        return {
            "enabled": self.enabled,
            "city": self.city,
            "unit": self.unit,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "location_id": self.location_id,
            "country": self.country,
            "country_code": self.country_code,
            "admin1": self.admin1,
            "admin2": self.admin2,
            "admin3": self.admin3,
            "admin4": self.admin4,
            "timezone": self.timezone,
        }


class WeatherDay(object):
    def __init__(self, date, code, minimum, maximum, rain_probability=0):
        self.date = str(date or "")
        self.code = int(code or 0)
        self.minimum = float(minimum or 0)
        self.maximum = float(maximum or 0)
        self.rain_probability = int(rain_probability or 0)


class WeatherForecast(object):
    def __init__(
        self,
        city,
        unit,
        current_temperature,
        current_code,
        days,
        updated_at,
        stale=False,
        current_humidity=0,
        current_wind_speed=0,
        location=None,
    ):
        self.city = str(city or DEFAULT_CITY)
        self.unit = unit if unit in ("C", "F") else DEFAULT_UNIT
        self.current_temperature = float(current_temperature or 0)
        self.current_code = int(current_code or 0)
        self.days = list(days or [])[:5]
        self.updated_at = str(updated_at or "")
        self.stale = bool(stale)
        self.current_humidity = int(current_humidity or 0)
        self.current_wind_speed = float(current_wind_speed or 0)
        if location is not None and not isinstance(location, WeatherLocation):
            try:
                location = WeatherLocation.from_dict(location)
            except (TypeError, ValueError):
                location = None
        self.location = location

    @property
    def degree(self):
        return "°{}".format(self.unit)

    def as_dict(self):
        result = {
            "city": self.city,
            "unit": self.unit,
            "current_temperature": self.current_temperature,
            "current_code": self.current_code,
            "current_humidity": self.current_humidity,
            "current_wind_speed": self.current_wind_speed,
            "updated_at": self.updated_at,
            "days": [
                {
                    "date": day.date,
                    "code": day.code,
                    "minimum": day.minimum,
                    "maximum": day.maximum,
                    "rain_probability": day.rain_probability,
                }
                for day in self.days
            ],
        }
        if self.location is not None:
            result.update(self.location.as_dict())
        return result


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (IOError, OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _write_json(path, value, durable=True):
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    temporary = "{}.tmp".format(path)
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            if durable:
                os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        replace = getattr(os, "replace", os.rename)
        replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _weather_settings_from_dict(data):
    data = data if isinstance(data, dict) else {}
    return WeatherSettings(
        data.get("enabled", True),
        data.get("city", DEFAULT_CITY),
        data.get("unit", DEFAULT_UNIT),
        latitude=data.get("latitude"),
        longitude=data.get("longitude"),
        location_id=data.get("location_id", ""),
        country=data.get("country", ""),
        country_code=data.get("country_code", ""),
        admin1=data.get("admin1", ""),
        admin2=data.get("admin2", ""),
        admin3=data.get("admin3", ""),
        admin4=data.get("admin4", ""),
        timezone=data.get("timezone", ""),
    )


def _weather_settings_from_config(section):
    return WeatherSettings(
        section.weather_enabled.value,
        section.weather_city.value,
        section.weather_unit.value,
        latitude=section.weather_latitude.value,
        longitude=section.weather_longitude.value,
        location_id=section.weather_location_id.value,
        country=section.weather_country.value,
        country_code=section.weather_country_code.value,
        admin1=section.weather_admin1.value,
        admin2=section.weather_admin2.value,
        admin3=section.weather_admin3.value,
        admin4=section.weather_admin4.value,
        timezone=section.weather_timezone.value,
    )


def _coordinate_text(value):
    return "" if value is None else "{:.8f}".format(float(value))


def _store_weather_config(section, settings, migrated=True):
    values = {
        "weather_enabled": bool(settings.enabled),
        "weather_city": settings.city,
        "weather_unit": settings.unit,
        "weather_latitude": _coordinate_text(settings.latitude),
        "weather_longitude": _coordinate_text(settings.longitude),
        "weather_location_id": settings.location_id,
        "weather_country": settings.country,
        "weather_country_code": settings.country_code,
        "weather_admin1": settings.admin1,
        "weather_admin2": settings.admin2,
        "weather_admin3": settings.admin3,
        "weather_admin4": settings.admin4,
        "weather_timezone": settings.timezone,
    }
    try:
        for name, value in values.items():
            set_config_value(getattr(section, name), value)
        if migrated:
            set_config_value(section.weather_legacy_json_migrated, True)
    except (AttributeError, TypeError, ValueError):
        return False
    return save_plugin_config(section)


def _migrate_weather_settings(section):
    if bool(section.weather_legacy_json_migrated.value):
        return True
    settings = _weather_settings_from_dict(_read_json(SETTINGS_PATH) or {})
    return _store_weather_config(section, settings, migrated=True)


def load_weather_settings(path=None):
    section = plugin_config_section() if path is None else None
    if section is not None:
        if _migrate_weather_settings(section):
            return _weather_settings_from_config(section)
        # Keep the legacy file usable if an unusual image cannot save config.
    return _weather_settings_from_dict(_read_json(path or SETTINGS_PATH) or {})


def save_weather_settings(settings, path=None):
    if not isinstance(settings, WeatherSettings):
        settings = WeatherSettings(**dict(settings or {}))
    section = plugin_config_section() if path is None else None
    if section is not None:
        if not _store_weather_config(section, settings, migrated=True):
            raise OSError(
                N_("Could not save weather settings through Enigma2 config")
            )
    else:
        _write_json(path or SETTINGS_PATH, settings.as_dict())
    return settings


def weather_description(code):
    code = int(code or 0)
    if code == 0:
        return _("Clear skies")
    if code in (1, 2):
        return _("Partly cloudy")
    if code == 3:
        return _("Cloudy")
    if code in (45, 48):
        return _("Foggy")
    if code in (51, 53, 55, 56, 57):
        return _("Drizzle")
    if code in (61, 63, 65, 66, 67, 80, 81, 82):
        return _("Rainy")
    if code in (71, 73, 75, 77, 85, 86):
        return _("Snowy")
    if code in (95, 96, 99):
        return _("Thunderstorms")
    return _("Variable")


def weather_icon_name(code):
    """Return the bundled icon name for an Open-Meteo WMO weather code."""
    code = int(code or 0)
    if code == 0:
        return "clear"
    if code in (1, 2):
        return "partly-cloudy"
    if code == 3:
        return "cloudy"
    if code in (45, 48):
        return "fog"
    if code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82):
        return "rain"
    if code in (71, 73, 75, 77, 85, 86):
        return "snow"
    if code in (95, 96, 99):
        return "storm"
    return "partly-cloudy"


def day_name(value):
    return localized_short_weekday(value)


class OpenMeteoClient(object):
    def __init__(self, opener=None, timeout=8):
        self.opener = opener or urlopen
        self.timeout = int(timeout)

    @staticmethod
    def _check_cancel(cancel_event=None, deadline=None):
        if cancel_event is not None and cancel_event.is_set():
            raise WeatherError(N_("Weather request cancelled"))
        if deadline is not None and time.monotonic() >= float(deadline):
            raise WeatherError(N_("Weather request timed out"))

    @staticmethod
    def _set_response_timeout(response, timeout):
        """Best-effort deadline update for urllib's underlying socket."""
        fp = getattr(response, "fp", None)
        raw = getattr(fp, "raw", None)
        candidates = (
            getattr(raw, "_sock", None),
            getattr(raw, "sock", None),
            getattr(fp, "_sock", None),
            getattr(fp, "sock", None),
        )
        seen = set()
        for candidate in candidates:
            if candidate is None or id(candidate) in seen:
                continue
            seen.add(id(candidate))
            setter = getattr(candidate, "settimeout", None)
            if not callable(setter):
                continue
            try:
                setter(float(timeout))
            except (OSError, TypeError, ValueError):
                continue
            return

    def _read_response_body(
        self,
        response,
        cancel_event=None,
        deadline=None,
    ):
        """Read a bounded body while enforcing cancellation and total time."""
        read1 = getattr(response, "read1", None)
        chunks = []
        total = 0
        limit = MAX_WEATHER_BYTES + 1
        while total < limit:
            self._check_cancel(cancel_event, deadline)
            remaining = float(deadline) - time.monotonic()
            if remaining <= 0:
                raise WeatherError(N_("Weather request timed out"))
            self._set_response_timeout(
                response,
                min(float(self.timeout), remaining),
            )
            size = min(WEATHER_READ_CHUNK_BYTES, limit - total)
            if callable(read1):
                chunk = read1(size)
            else:
                chunk = response.read(size)
            self._check_cancel(cancel_event, deadline)
            if not chunk:
                break
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8")
            elif not isinstance(chunk, bytes):
                chunk = bytes(chunk)
            chunks.append(chunk)
            total += len(chunk)
        return b"".join(chunks)

    def _json(self, url, parameters, cancel_event=None, deadline=None):
        self._check_cancel(cancel_event, deadline)
        request_deadline = time.monotonic() + float(self.timeout)
        if deadline is not None:
            request_deadline = min(request_deadline, float(deadline))
        self._check_cancel(cancel_event, request_deadline)
        request = Request(
            "{}?{}".format(url, urlencode(parameters)),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        try:
            remaining = request_deadline - time.monotonic()
            if remaining <= 0:
                raise WeatherError(N_("Weather request timed out"))
            timeout = min(float(self.timeout), remaining)
            response = self.opener(request, timeout=timeout)
            try:
                payload = self._read_response_body(
                    response,
                    cancel_event=cancel_event,
                    deadline=request_deadline,
                )
            finally:
                close = getattr(response, "close", None)
                if close is not None:
                    close()
            self._check_cancel(cancel_event, request_deadline)
            if len(payload) > MAX_WEATHER_BYTES:
                raise WeatherError(N_("Weather service response is too large"))
            if not isinstance(payload, str):
                payload = payload.decode("utf-8", "replace")
            result = json.loads(payload)
        except WeatherError:
            raise
        except HTTPError as error:
            try:
                error.close()
            except Exception:
                pass
            log_event("weather", N_("Open-Meteo request failed"), error)
            raise WeatherError(N_("Could not reach the weather service"))
        except Exception as error:
            log_event("weather", N_("Open-Meteo request failed"), error)
            raise WeatherError(N_("Could not reach the weather service"))
        if not isinstance(result, dict):
            raise WeatherError(N_("Weather service returned an invalid response"))
        return result

    def search_locations(
        self,
        query,
        count=10,
        cancel_event=None,
        deadline=None,
    ):
        query = str(query or "").strip()
        if not query:
            raise WeatherError(N_("City name cannot be empty"))
        try:
            count = int(count)
        except (TypeError, ValueError):
            count = 10
        count = max(1, min(10, count))
        parameters = {
            "name": query,
            "count": count,
            "language": device_language().split("_", 1)[0],
            "format": "json",
        }
        if cancel_event is None and deadline is None:
            result = self._json(GEOCODING_URL, parameters)
        else:
            result = self._json(
                GEOCODING_URL,
                parameters,
                cancel_event=cancel_event,
                deadline=deadline,
            )
        matches = result.get("results") or []
        locations = []
        seen = set()
        for match in matches[:count]:
            try:
                location = WeatherLocation.from_dict(match)
            except (KeyError, TypeError, ValueError):
                continue
            if location.identity in seen:
                continue
            seen.add(location.identity)
            locations.append(location)
        return locations

    def geocode(self, city, cancel_event=None, deadline=None):
        city = str(city or "").strip()
        locations = self.search_locations(
            city,
            count=1,
            cancel_event=cancel_event,
            deadline=deadline,
        )
        if not locations:
            raise WeatherError(_(N_("Could not find {}")).format(city))
        return locations[0].as_dict()

    def forecast(
        self,
        city,
        unit=DEFAULT_UNIT,
        cancel_event=None,
        deadline=None,
    ):
        location = WeatherLocation.from_dict(
            self.geocode(
                city,
                cancel_event=cancel_event,
                deadline=deadline,
            )
        )
        return self.forecast_location(
            location,
            unit,
            cancel_event=cancel_event,
            deadline=deadline,
        )

    def forecast_location(
        self,
        location,
        unit=DEFAULT_UNIT,
        cancel_event=None,
        deadline=None,
    ):
        if not isinstance(location, WeatherLocation):
            try:
                location = WeatherLocation.from_dict(location)
            except (TypeError, ValueError):
                raise WeatherError(N_("Weather service returned an invalid response"))
        fahrenheit = str(unit).upper() == "F"
        parameters = {
            "latitude": location.latitude,
            "longitude": location.longitude,
            "current": (
                "temperature_2m,weather_code,relative_humidity_2m,"
                "wind_speed_10m"
            ),
            "daily": (
                "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max"
            ),
            "timezone": location.timezone or "auto",
            "forecast_days": 5,
        }
        if fahrenheit:
            parameters["temperature_unit"] = "fahrenheit"
        if cancel_event is None and deadline is None:
            result = self._json(FORECAST_URL, parameters)
        else:
            result = self._json(
                FORECAST_URL,
                parameters,
                cancel_event=cancel_event,
                deadline=deadline,
            )
        current = result.get("current") or {}
        daily = result.get("daily") or {}
        dates = daily.get("time") or []
        codes = daily.get("weather_code") or []
        maximums = daily.get("temperature_2m_max") or []
        minimums = daily.get("temperature_2m_min") or []
        rains = daily.get("precipitation_probability_max") or []
        days = []
        for index, date in enumerate(dates[:5]):
            try:
                days.append(
                    WeatherDay(
                        date,
                        codes[index],
                        minimums[index],
                        maximums[index],
                        rains[index] if index < len(rains) else 0,
                    )
                )
            except (IndexError, TypeError, ValueError):
                continue
        if not days:
            raise WeatherError(N_("Could not load the five-day forecast"))
        updated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        return WeatherForecast(
            location.name,
            "F" if fahrenheit else "C",
            current.get("temperature_2m", days[0].maximum),
            current.get("weather_code", days[0].code),
            days,
            updated_at,
            current_humidity=current.get("relative_humidity_2m", 0),
            current_wind_speed=current.get("wind_speed_10m", 0),
            location=location,
        )


class WeatherService(object):
    def __init__(self, client=None, cache_path=None, now=None):
        self.client = client or OpenMeteoClient()
        self.cache_path = cache_path or CACHE_PATH
        self.now = now or time.time

    @staticmethod
    def _from_dict(data, stale=False):
        try:
            days = [
                WeatherDay(
                    entry.get("date"),
                    entry.get("code"),
                    entry.get("minimum"),
                    entry.get("maximum"),
                    entry.get("rain_probability"),
                )
                for entry in data.get("days", [])
            ]
            if not days:
                return None
            location = None
            if data.get("latitude") is not None and data.get("longitude") is not None:
                location = WeatherLocation.from_dict(data)
            return WeatherForecast(
                data.get("city"),
                data.get("unit"),
                data.get("current_temperature"),
                data.get("current_code"),
                days,
                data.get("updated_at"),
                stale=stale,
                current_humidity=data.get("current_humidity", 0),
                current_wind_speed=data.get("current_wind_speed", 0),
                location=location,
            )
        except (AttributeError, TypeError, ValueError):
            return None

    def cached(self, settings, allow_stale=True):
        data = _read_json(self.cache_path)
        if not data:
            return None
        location = getattr(settings, "location", None)
        if location is not None:
            cached_latitude = _optional_coordinate(
                data.get("latitude"), -90.0, 90.0
            )
            cached_longitude = _optional_coordinate(
                data.get("longitude"), -180.0, 180.0
            )
            if cached_latitude is None or cached_longitude is None:
                return None
            if (
                abs(cached_latitude - location.latitude) > 0.00001
                or abs(cached_longitude - location.longitude) > 0.00001
            ):
                return None
        elif str(data.get("city", "")).casefold() != settings.city.casefold():
            return None
        if data.get("unit") != settings.unit:
            return None
        try:
            age = max(0, float(self.now()) - float(data.get("cached_at", 0)))
        except (TypeError, ValueError, OverflowError):
            return None
        if age > CACHE_SECONDS and not allow_stale:
            return None
        return self._from_dict(data, stale=age > CACHE_SECONDS)

    @staticmethod
    def _cancelled(cancel_event=None, deadline=None):
        return bool(
            (cancel_event is not None and cancel_event.is_set())
            or (
                deadline is not None
                and time.monotonic() >= float(deadline)
            )
        )

    def get(self, settings, force=False, cancel_event=None, deadline=None):
        if not settings.enabled:
            return None
        if self._cancelled(cancel_event, deadline):
            raise WeatherError(N_("Weather request cancelled"))
        if not force:
            cached = self.cached(settings, allow_stale=False)
            if cached is not None:
                return cached
        try:
            location = getattr(settings, "location", None)
            location_forecast = getattr(self.client, "forecast_location", None)
            if location is not None and callable(location_forecast):
                forecast = location_forecast(
                    location,
                    settings.unit,
                    cancel_event=cancel_event,
                    deadline=deadline,
                )
            else:
                forecast = self.client.forecast(
                    settings.city,
                    settings.unit,
                    cancel_event=cancel_event,
                    deadline=deadline,
                )
            if self._cancelled(cancel_event, deadline):
                raise WeatherError(N_("Weather request cancelled"))
            data = forecast.as_dict()
            data["cached_at"] = float(self.now())
            # Forecasts are disposable cache data. Avoid fsync contention with
            # video startup while preserving durable writes for user settings.
            _write_json(self.cache_path, data, durable=False)
            return forecast
        except WeatherError:
            if self._cancelled(cancel_event, deadline):
                raise
            cached = self.cached(settings, allow_stale=True)
            if cached is not None:
                cached.stale = True
                return cached
            raise


def last_weather_update(cache_path=None):
    data = _read_json(cache_path or CACHE_PATH) or {}
    return str(data.get("updated_at") or N_("Not fetched yet"))
