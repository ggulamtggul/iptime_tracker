"""Platform for sensor integration."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
import re
from typing import Any

import requests
import voluptuous as vol
from bs4 import BeautifulSoup

from homeassistant.components.device_tracker import PLATFORM_SCHEMA
from homeassistant.components.device_tracker.const import CONF_SCAN_INTERVAL
from homeassistant.const import (
    CONF_NAME,
    CONF_MAC,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .const import (
    CONF_URL,
    CONF_ID,
    CONF_PASSWORD,
    CONF_TARGET,
    DEFAULT_INTERVAL,
    HOSTINFO_URN,
    LOGIN_URN,
    LOGOUT_URN,
    WLAN_2G_URN,
    WLAN_5G_URN,
    MESH_URN,
    M_LOGIN_URN,
    M_LOGOUT_URN,
    M_WLAN_2G_URN,
    M_WLAN_5G_URN,
    M_MESH_URN,
    MESH_STATION_URN,
    TIME_OUT,
    BETA_UI_URN,
    BETA_SERVICE_URN,
    RSS_LIMIT,
)

_LOGGER = logging.getLogger(__name__)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_URL): cv.string,
        vol.Required(CONF_ID): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Required(CONF_TARGET): vol.All(
            cv.ensure_list,
            [
                {
                    vol.Required(CONF_NAME): cv.string,
                    vol.Required(CONF_MAC): cv.string,
                }
            ],
        ),
    }
)

async def async_setup_scanner(
    hass: HomeAssistant, config: dict, async_see, discovery_info=None
):
    """Set up the device tracker."""
    url = config.get(CONF_URL)
    user_id = config.get(CONF_ID)
    user_pw = config.get(CONF_PASSWORD)
    targets = config.get(CONF_TARGET)
    scan_interval = config.get(
        CONF_SCAN_INTERVAL, timedelta(seconds=DEFAULT_INTERVAL)
    )

    api = IPTimeAPI(hass, url, user_id, user_pw)
    
    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="iptime_tracker",
        update_method=api.async_update,
        update_interval=scan_interval,
    )

    await coordinator.async_refresh()

    sensors = [IPTimeSensor(target[CONF_NAME], target[CONF_MAC], api) for target in targets]

    async def async_update_devices():
        """코디네이터 업데이트 후 디바이스 상태를 HA에 알림"""
        for sensor in sensors:
            sensor.update_state_from_coordinator()
            await async_see(
                mac=f"{sensor.state_attributes.get('iptime_url', 'iptime')}_{sensor._target_mac}",
                host_name=sensor.name,
                location_name=sensor.state,
                attributes=sensor.state_attributes,
                source_type="ipTIME_Tracker",
            )

    @callback
    def _update_listener():
        hass.async_create_task(async_update_devices())

    coordinator.async_add_listener(_update_listener)
    await async_update_devices()

    return True


class IPTimeAPI:
    """ipTIME API Class."""

    def __init__(self, hass: HomeAssistant, url: str, user_id: str, user_pw: str):
        self._hass = hass
        self._user_id = user_id
        self._user_pw = user_pw
        self._ismobile = False
        self._ismesh = False
        self._beta_ui = False
        
        # 초기값을 None으로 설정하여 '데이터 없음'과 '빈 목록'을 구분
        self.result = None 

        if not url.startswith("http"):
            self._url = f"http://{url}"
        else:
            self._url = url

        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
            "Referer": self._url,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Upgrade-Insecure-Requests": "1",
            "Content-type": "text/plain; charset=utf-8",
        }
        self.json_headers = self.headers.copy()
        self.json_headers["Content-type"] = "application/json; charset=utf-8"

        self.efm_session_id = None

    def _request_sync(self, method, url, **kwargs):
        response = requests.request(method, url, timeout=TIME_OUT, **kwargs)
        _ = response.text
        return response

    async def _request(self, method, url, **kwargs):
        return await self._hass.async_add_executor_job(
            lambda: self._request_sync(method, url, **kwargs)
        )

    async def async_update(self):
        try:
            if self.efm_session_id:
                if self._beta_ui:
                    self.result = await self.beta_ui_wlan_check()
                    await self.session_update_beta_ui()
                elif self._ismobile:
                    self.result = await self.m_wlan_check()
                else:
                    self.result = await self.wlan_check()
            else:
                if await self.verify_beta_ui():
                    _LOGGER.info(f"[ipTIME-BetaUI] {self._url}")
                    self._beta_ui = True
                    if await self.login_beta_ui():
                        await self.beta_ui_check_mesh()
                        self.result = await self.beta_ui_wlan_check()
                
                elif await self.verify_mobile():
                    if self._ismobile:
                        _LOGGER.info(f"[ipTIME-Mobile] {self._url}")
                        if await self.m_login():
                            await self.m_check_mesh()
                            self.result = await self.m_wlan_check()
                    else:
                        _LOGGER.info(f"[ipTIME-PC] {self._url}")
                        if await self.login():
                            await self.check_mesh()
                            self.result = await self.wlan_check()
            return self.result

        except Exception as err:
            _LOGGER.error(f"Error communicating with ipTIME: {err}")
            raise UpdateFailed(f"Error communicating with ipTIME: {err}") from err

    async def verify_beta_ui(self):
        try:
            url = self._url + BETA_UI_URN + "flutter_bootstrap.js"
            response = await self._request("GET", url, headers=self.headers)
            if "/cgi/service.cgi" in response.text:
                return True
            
            url = self._url + BETA_UI_URN
            response = await self._request("GET", url, headers=self.headers)
            if "/cgi/service.cgi" in response.text:
                return True
            return False
        except Exception:
            return False

    async def login_beta_ui(self):
        url = self._url + BETA_SERVICE_URN
        data = {
            "method": "session/login",
            "params": {"id": self._user_id, "pw": self._user_pw},
        }
        try:
            response = await self._request("POST", url, headers=self.json_headers, json=data)
            response_json = response.json()
            if response_json.get("result"):
                self.efm_session_id = response.cookies.get("efm_session_id")
                _LOGGER.debug(f"{self._url}: (B)Login Success !! [{self.efm_session_id}]")
                return True
            else:
                _LOGGER.error(f"{self._url}: (B)Login Fail !! {response_json.get('error')}")
                return False
        except Exception:
            return False

    async def session_update_beta_ui(self):
        url = self._url + BETA_SERVICE_URN
        cookies = {"efm_session_id": self.efm_session_id}
        data = {"method": "session/update"}
        try:
            await self._request("POST", url, headers=self.json_headers, json=data, cookies=cookies)
        except Exception:
            pass

    async def verify_mobile(self):
        url = self._url + HOSTINFO_URN
        try:
            response = await self._request("GET", url, headers=self.headers)
            product_name_match = re.search(r"product_name=[ a-zA-Z0-9]+", response.text)
            product_name = product_name_match.group().split("=")[1] if product_name_match else "Unknown"

            if "iux" not in response.text:
                self._ismobile = False
                return True
            if "iux_package_installed" not in response.text:
                self._ismobile = True
                return True

            iux = re.search(r"iux=\d", response.text)
            iux_pkg = re.search(r"iux_package_installed=\d", response.text)
            
            if iux and int(iux.group().split("=")[1]):
                if iux_pkg and int(iux_pkg.group().split("=")[1]):
                    self._ismobile = True
                else:
                    self._ismobile = False
            else:
                self._ismobile = False
            return True

        except Exception as e:
            _LOGGER.error(f"{self._url}: Verify Mobile Error: {e}")
            self._ismobile = False
            return False

    async def check_mesh(self):
        url = self._url + MESH_URN
        cookies = {"efm_session_id": self.efm_session_id}
        try:
            response = await self._request("GET", url, headers=self.headers, cookies=cookies)
            soup = BeautifulSoup(response.text, "html.parser")
            mesh_mode = soup.find("input", attrs={"id": "mode_none"})
            if not mesh_mode or "checked" in mesh_mode.attrs:
                self._ismesh = False
            else:
                self._ismesh = True
            return self._ismesh
        except Exception:
            return False

    async def m_check_mesh(self):
        url = self._url + M_MESH_URN
        cookies = {"efm_session_id": self.efm_session_id}
        try:
            response = await self._request("GET", url, headers=self.headers, cookies=cookies)
            if "easymesh" in response.text:
                self._ismesh = True
            else:
                self._ismesh = False
            return self._ismesh
        except Exception:
            return False

    async def beta_ui_check_mesh(self):
        url = self._url + BETA_SERVICE_URN
        cookies = {"efm_session_id": self.efm_session_id}
        data = {"method": "easymesh/info"}
        try:
            response = await self._request("POST", url, headers=self.json_headers, cookies=cookies, json=data)
            response_json = response.json()
            if response_json.get("result", {}).get("active"):
                self._ismesh = True
            else:
                self._ismesh = False
        except Exception:
            self._ismesh = False

    async def login(self):
        url = self._url + LOGIN_URN
        data = {"username": self._user_id, "passwd": self._user_pw}
        try:
            response = await self._request("POST", url, headers=self.headers, data=data)
            session_match = re.search(r"\w{16}", response.text)
            if session_match:
                self.efm_session_id = session_match.group()
                _LOGGER.debug(f"{self._url}: Login Success !! [{self.efm_session_id}]")
                return True
        except Exception:
            pass
        _LOGGER.error(f"{self._url}: Login Fail !!")
        return False

    async def m_login(self):
        url = self._url + M_LOGIN_URN
        data = {"username": self._user_id, "passwd": self._user_pw}
        try:
            response = await self._request("POST", url, headers=self.headers, data=data)
            if 'location = "/";' in response.text:
                await self.verify_mobile()
                return False
                
            session_match = re.search(r"\w{16}", response.text)
            if session_match:
                self.efm_session_id = session_match.group()
                _LOGGER.debug(f"{self._url}: M_Login Success !! [{self.efm_session_id}]")
                return True
        except Exception:
            pass
        _LOGGER.error(f"{self._url}: M_Login Fail !!")
        return False

    async def logout(self):
        self.efm_session_id = None
        url = self._url + LOGOUT_URN
        try:
            await self._request("GET", url, headers=self.headers)
        except Exception:
            pass

    async def m_logout(self):
        self.efm_session_id = None
        url = self._url + M_LOGOUT_URN
        try:
            await self._request("GET", url, headers=self.headers)
        except Exception:
            pass

    async def wlan_check(self):
        result_dict = {}
        cookies = {"efm_session_id": self.efm_session_id}
        
        for url, band in [(self._url + WLAN_2G_URN, "2.4GHz"), (self._url + WLAN_5G_URN, "5GHz")]:
            try:
                response = await self._request("GET", url, headers=self.headers, cookies=cookies)
                soup = BeautifulSoup(response.text, "html.parser")
                result_dict.update(self.device_parsing(soup.find_all("tr"), band))
            except Exception:
                await self.logout()
                return {"session": False}

        if self._ismesh:
            try:
                result_dict.update(await self.get_mesh_station())
            except Exception:
                pass

        return result_dict

    async def beta_ui_wlan_check(self):
        url = self._url + BETA_SERVICE_URN
        cookies = {"efm_session_id": self.efm_session_id}
        data = {"method": "network/interface/lan/stations"}
        try:
            response = await self._request("POST", url, headers=self.json_headers, json=data, cookies=cookies)
            response_json = response.json()
        except Exception:
            return {"session": False}

        if not response_json.get("result"):
            self.efm_session_id = None
            return {"session": False}

        result_dict = self.beta_ui_device_parsing(response_json["result"])
        if self._ismesh:
            try:
                result_dict.update(await self.get_mesh_station())
            except Exception:
                pass
        
        result_dict["session"] = True
        return result_dict

    def beta_ui_device_parsing(self, device_list):
        result_dict = {}
        for device in device_list:
            if device["connection"]["type"] != "wireless":
                continue
            
            bss = device["connection"]["wireless"]["bss"]
            band = "5GHz" if bss == "5g.1" else ("2.4GHz" if bss == "2g.1" else bss)
            rss = device["connection"]["wireless"]["rssi"]
            
            connected_seconds = device["connection"]["wireless"]["duration"]
            days = timedelta(seconds=connected_seconds).days
            h, m, s = str(timedelta(seconds=connected_seconds)).split(":")
            
            result_dict[device["mac"].replace(":", "-")] = {
                "ip": device["info"]["ip"],
                "band": band,
                "stay_time": f"{days}일 {h}시간 {m}분 {s}초",
                "rssi": rss,
                "state": "not_home" if rss < RSS_LIMIT else "home",
            }
        return result_dict

    async def m_wlan_check(self):
        result_dict = {}
        cookies = {"efm_session_id": self.efm_session_id}

        for url, band in [(self._url + M_WLAN_2G_URN, "2.4GHz"), (self._url + M_WLAN_5G_URN, "5GHz")]:
            try:
                response = await self._request("GET", url, headers=self.headers, cookies=cookies)
                try:
                    data = response.json()
                except ValueError:
                    data = None
                
                if data:
                    result_dict.update(self.json_parsing(data, band))
            except Exception:
                await self.m_logout()
                return {"session": False}

        if self._ismesh:
            try:
                result_dict.update(await self.get_mesh_station())
            except Exception:
                pass

        return result_dict

    async def get_mesh_station(self):
        result_dict = {}
        url = self._url + MESH_STATION_URN
        cookies = {"efm_session_id": self.efm_session_id}
        
        response = await self._request("GET", url, headers=self.headers, cookies=cookies)
        device_list = response.json().get("station", [])

        for device in device_list:
            if device.get("connection") in ["WIRED", "Unknown"]:
                continue
            if "mac" not in device:
                continue

            connected_seconds = device["timestamp"] - device["connected_ts"]
            days = timedelta(seconds=connected_seconds).days
            h, m, s = str(timedelta(seconds=connected_seconds)).split(":")
            
            rss = device.get("rssi", 0)
            band = "5GHz" if device["mode"] == "5G" else ("2.4GHz" if device["mode"] == "2.4G" else device["mode"])

            result_dict[device["mac"].replace(":", "-")] = {
                "ip": device.get("ip", "N/A"),
                "band": band,
                "stay_time": f"{days}일 {h}시간 {m}분 {s}초",
                "rssi": rss,
                "state": "not_home" if rss and rss < RSS_LIMIT else "home",
            }
        return result_dict

    def device_parsing(self, response_list, band):
        result_dict = {}
        if not response_list:
            raise KeyError()

        for device in response_list:
            tds = device.find_all("td")
            if len(tds) == 4:
                ip_match = re.search(r"\d{1,3}.\d{1,3}.\d{1,3}.\d{1,3}", tds[3].text)
                ip = ip_match.group() if ip_match else "N/A"
                
                result_dict[tds[0].text] = {
                    "ip": ip,
                    "band": band,
                    "stay_time": tds[2].text,
                    "state": "home",
                }
        return result_dict

    def json_parsing(self, response_json, band):
        result_dict = {}
        if "stalist" not in response_json:
            raise KeyError()
            
        for device in response_json["stalist"]:
            if "mac" in device:
                connected_time = f"{device.get('day',0)}일 {device.get('hour',0)}시간 {device.get('min',0)}분 {device.get('sec',0)}초"
                result_dict[device["mac"]] = {
                    "ip": device.get("ipaddr", False),
                    "band": band,
                    "stay_time": connected_time,
                    "state": "home",
                }
        return result_dict


class IPTimeSensor:
    """Representation of a Sensor."""

    def __init__(self, name, mac, api) -> None:
        self._state = "N/A"
        self._entity_id = name
        self._target_mac = mac.replace(":", "-")
        self._api = api
        self.error_count = 0
        self.error_threshold = 3
        self.not_home_count = 0
        self.not_home_threshold = 5
        self._state_attributes = {}

    @property
    def name(self):
        if self._entity_id:
            return f"iptime_{self._entity_id}"
        return f"iptime_{self._api._user_id}"

    @property
    def state(self):
        return self._state

    @property
    def state_attributes(self):
        return self._state_attributes

    def update_state_from_coordinator(self):
        result_dict = self._api.result

        data = {
            "name": self._entity_id,
            "mac_address": self._target_mac,
            "iptime_url": self._api._url,
        }

        # 1. API가 한 번도 실행되지 않았거나 에러 상태(None)인 경우 -> N/A
        if result_dict is None:
            if self.error_count < self.error_threshold:
                self.error_count += 1
            else:
                self._state = "N/A"
            self._state_attributes = data
            return

        # 2. 세션 만료 등의 명시적 실패 -> 상태 유지 (return)
        if result_dict.get("session") is False:
             return

        # 3. 정상 응답 (빈 딕셔너리 {} 포함) -> 로직 수행
        self.error_count = 0
        
        if self._target_mac in result_dict:
            # 목록에 있음 -> Home (단, RSSI 기반 로직이 있다면 따름)
            device_info = result_dict[self._target_mac]
            self.not_home_count = 0
            self._state = device_info.get("state", "home")
            
            data.update({
                "stay_time": device_info.get("stay_time", "N/A"),
                "band": device_info.get("band", "N/A"),
                "ip": device_info.get("ip", "N/A"),
                "rssi": device_info.get("rssi", "N/A"),
            })
        else:
            # 목록에 없음 (빈 목록 포함) -> Not Home
            if self.not_home_count < self.not_home_threshold:
                self.not_home_count += 1
            else:
                self._state = "not_home"
            
            data.update({
                "stay_time": "N/A",
                "band": "N/A",
                "ip": "N/A",
                "rssi": "N/A",
            })

        self._state_attributes = data
