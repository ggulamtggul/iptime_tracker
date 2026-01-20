"""API for ipTIME Tracker."""
from __future__ import annotations

import logging
import re
from datetime import timedelta

import requests
from bs4 import BeautifulSoup
from homeassistant.core import HomeAssistant

from .const import (
    HOSTINFO_URN, LOGIN_URN, LOGOUT_URN, WLAN_2G_URN, WLAN_5G_URN, MESH_URN,
    M_LOGIN_URN, M_LOGOUT_URN, M_WLAN_2G_URN, M_WLAN_5G_URN, M_MESH_URN,
    MESH_STATION_URN, BETA_UI_URN, BETA_SERVICE_URN, RSS_LIMIT, TIME_OUT
)

_LOGGER = logging.getLogger(__name__)

class IPTimeAPI:
    """ipTIME API Class."""

    def __init__(self, hass: HomeAssistant, url: str, user_id: str, user_pw: str):
        self._hass = hass
        self._user_id = user_id
        self._user_pw = user_pw
        self._ismobile = False
        self._ismesh = False
        self._beta_ui = False
        
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
        """Sync request to be run in executor."""
        response = requests.request(method, url, timeout=TIME_OUT, **kwargs)
        _ = response.text  # Pre-load text in executor
        return response

    async def _request(self, method, url, **kwargs):
        """Run request in executor."""
        return await self._hass.async_add_executor_job(
            lambda: self._request_sync(method, url, **kwargs)
        )

    async def async_update(self):
        """Fetch data from API."""
        try:
            if self.efm_session_id:
                if self._beta_ui:
                    result = await self.beta_ui_wlan_check()
                    await self.session_update_beta_ui()
                elif self._ismobile:
                    result = await self.m_wlan_check()
                else:
                    result = await self.wlan_check()
            else:
                # Login logic
                if await self.verify_beta_ui():
                    self._beta_ui = True
                    if await self.login_beta_ui():
                        await self.beta_ui_check_mesh()
                        result = await self.beta_ui_wlan_check()
                    else:
                         return None
                elif await self.verify_mobile():
                    if self._ismobile:
                        if await self.m_login():
                            await self.m_check_mesh()
                            result = await self.m_wlan_check()
                        else:
                            return None
                    else:
                        if await self.login():
                            await self.check_mesh()
                            result = await self.wlan_check()
                        else:
                            return None
                else:
                     return None

            return result

        except Exception as err:
            _LOGGER.error(f"Error communicating with ipTIME: {err}")
            raise Exception(f"Error communicating with ipTIME: {err}") from err

    # --- Login & Verify Methods ---
    async def verify_beta_ui(self):
        try:
            url = self._url + BETA_UI_URN
            response = await self._request("GET", url, headers=self.headers)
            return "/cgi/service.cgi" in response.text
        except Exception:
            return False

    async def login_beta_ui(self):
        url = self._url + BETA_SERVICE_URN
        data = {"method": "session/login", "params": {"id": self._user_id, "pw": self._user_pw}}
        try:
            response = await self._request("POST", url, headers=self.json_headers, json=data)
            if response.json().get("result"):
                self.efm_session_id = response.cookies.get("efm_session_id")
                return True
            return False
        except Exception:
            return False

    async def session_update_beta_ui(self):
        url = self._url + BETA_SERVICE_URN
        try:
            await self._request("POST", url, headers=self.json_headers, json={"method": "session/update"}, cookies={"efm_session_id": self.efm_session_id})
        except Exception:
            pass

    async def verify_mobile(self):
        url = self._url + HOSTINFO_URN
        try:
            response = await self._request("GET", url, headers=self.headers)
            if "iux_package_installed" in response.text:
                self._ismobile = True
            elif "iux" not in response.text:
                 self._ismobile = False
            else:
                 self._ismobile = False
            return True
        except Exception:
            return False

    async def login(self):
        url = self._url + LOGIN_URN
        data = {"username": self._user_id, "passwd": self._user_pw}
        try:
            response = await self._request("POST", url, headers=self.headers, data=data)
            session_match = re.search(r"\w{16}", response.text)
            if session_match:
                self.efm_session_id = session_match.group()
                return True
        except Exception:
            pass
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
                return True
        except Exception:
            pass
        return False

    async def logout(self):
        self.efm_session_id = None
        try:
            await self._request("GET", self._url + LOGOUT_URN, headers=self.headers)
        except Exception:
            pass
    
    async def m_logout(self):
        self.efm_session_id = None
        try:
            await self._request("GET", self._url + M_LOGOUT_URN, headers=self.headers)
        except Exception:
            pass

    # --- Mesh & WLAN Checks ---
    async def check_mesh(self):
        try:
            response = await self._request("GET", self._url + MESH_URN, headers=self.headers, cookies={"efm_session_id": self.efm_session_id})
            soup = BeautifulSoup(response.text, "html.parser")
            mesh_mode = soup.find("input", attrs={"id": "mode_none"})
            self._ismesh = not (not mesh_mode or "checked" in mesh_mode.attrs)
            return self._ismesh
        except Exception:
            return False

    async def m_check_mesh(self):
        try:
            response = await self._request("GET", self._url + M_MESH_URN, headers=self.headers, cookies={"efm_session_id": self.efm_session_id})
            self._ismesh = "easymesh" in response.text
            return self._ismesh
        except Exception:
            return False

    async def beta_ui_check_mesh(self):
        try:
            response = await self._request("POST", self._url + BETA_SERVICE_URN, headers=self.json_headers, cookies={"efm_session_id": self.efm_session_id}, json={"method": "easymesh/info"})
            self._ismesh = response.json().get("result", {}).get("active", False)
        except Exception:
            self._ismesh = False

    async def wlan_check(self):
        result_dict = {}
        cookies = {"efm_session_id": self.efm_session_id}
        for url, band in [(self._url + WLAN_2G_URN, "2.4GHz"), (self._url + WLAN_5G_URN, "5GHz")]:
            try:
                response = await self._request("GET", url, headers=self.headers, cookies=cookies)
                soup = BeautifulSoup(response.text, "html.parser")
                result_dict.update(self._parse_table(soup.find_all("tr"), band))
            except Exception:
                await self.logout()
                return {"session": False}
        if self._ismesh:
            result_dict.update(await self.get_mesh_station())
        return result_dict

    async def m_wlan_check(self):
        result_dict = {}
        cookies = {"efm_session_id": self.efm_session_id}
        for url, band in [(self._url + M_WLAN_2G_URN, "2.4GHz"), (self._url + M_WLAN_5G_URN, "5GHz")]:
            try:
                response = await self._request("GET", url, headers=self.headers, cookies=cookies)
                try: data = response.json()
                except ValueError: data = None
                if data: result_dict.update(self._parse_json(data, band))
            except Exception:
                await self.m_logout()
                return {"session": False}
        if self._ismesh:
            result_dict.update(await self.get_mesh_station())
        return result_dict

    async def beta_ui_wlan_check(self):
        try:
            response = await self._request("POST", self._url + BETA_SERVICE_URN, headers=self.json_headers, json={"method": "network/interface/lan/stations"}, cookies={"efm_session_id": self.efm_session_id})
            res_json = response.json()
        except Exception:
            return {"session": False}

        if not res_json.get("result"):
            self.efm_session_id = None
            return {"session": False}

        result_dict = self._parse_beta(res_json["result"])
        if self._ismesh:
            result_dict.update(await self.get_mesh_station())
        result_dict["session"] = True
        return result_dict

    async def get_mesh_station(self):
        result = {}
        try:
            response = await self._request("GET", self._url + MESH_STATION_URN, headers=self.headers, cookies={"efm_session_id": self.efm_session_id})
            for device in response.json().get("station", []):
                if device.get("connection") in ["WIRED", "Unknown"] or "mac" not in device:
                    continue
                rss = device.get("rssi", 0)
                band = "5GHz" if device["mode"] == "5G" else ("2.4GHz" if device["mode"] == "2.4G" else device["mode"])
                result[device["mac"].replace(":", "-")] = {
                    "ip": device.get("ip", "N/A"),
                    "band": band,
                    "rssi": rss,
                    "state": "not_home" if rss and rss < RSS_LIMIT else "home",
                }
        except Exception:
            pass
        return result

    def _parse_table(self, rows, band):
        res = {}
        for row in rows:
            tds = row.find_all("td")
            if len(tds) == 4:
                ip_match = re.search(r"\d{1,3}.\d{1,3}.\d{1,3}.\d{1,3}", tds[3].text)
                res[tds[0].text] = {
                    "ip": ip_match.group() if ip_match else "N/A",
                    "band": band,
                    "state": "home",
                }
        return res

    def _parse_json(self, data, band):
        res = {}
        if "stalist" in data:
            for d in data["stalist"]:
                if "mac" in d:
                    res[d["mac"]] = {
                        "ip": d.get("ipaddr", "N/A"),
                        "band": band,
                        "state": "home",
                    }
        return res

    def _parse_beta(self, devices):
        res = {}
        for d in devices:
            if d["connection"]["type"] != "wireless": continue
            bss = d["connection"]["wireless"]["bss"]
            band = "5GHz" if bss == "5g.1" else ("2.4GHz" if bss == "2g.1" else bss)
            rss = d["connection"]["wireless"]["rssi"]
            res[d["mac"].replace(":", "-")] = {
                "ip": d["info"]["ip"],
                "band": band,
                "rssi": rss,
                "state": "not_home" if rss < RSS_LIMIT else "home",
            }
        return res
