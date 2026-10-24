"""FRITZ!Box web UI (data.lua) access, for what TR-064 does not expose:
the parental-control 'Online-Zeit heute' per device, which FRITZ!OS counts from real traffic."""
import hashlib
import re
import ssl
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

_CTX = ssl._create_unverified_context()  # the box uses a self-signed certificate


class FritzWeb:
    def __init__(self, address, user, password):
        self.base = f"https://{address}"
        self.user, self.password = user, password
        self.sid = None

    def _req(self, path, data=None):
        body = urllib.parse.urlencode(data).encode() if data else None
        with urllib.request.urlopen(urllib.request.Request(self.base + path, data=body),
                                    context=_CTX, timeout=30) as r:
            return r.read().decode("utf-8", "replace")

    def login(self):
        ch = ET.fromstring(self._req("/login_sid.lua?version=2")).find("Challenge").text
        _, it1, salt1, it2, salt2 = ch.split("$")
        h1 = hashlib.pbkdf2_hmac("sha256", self.password.encode(), bytes.fromhex(salt1), int(it1))
        h2 = hashlib.pbkdf2_hmac("sha256", h1, bytes.fromhex(salt2), int(it2))
        x = ET.fromstring(self._req("/login_sid.lua?version=2",
                                    {"username": self.user, "response": f"{salt2}${h2.hex()}"}))
        self.sid = x.find("SID").text
        if self.sid == "0" * 16:
            raise RuntimeError("FRITZ!Box 网页登录失败")

    def page(self, name):
        if not self.sid:
            self.login()
        text = self._req("/data.lua", {"sid": self.sid, "page": name, "xhrId": "all", "xhr": "1"})
        if "login_sid" in text[:500] or not text.strip():  # session expired
            self.login()
            text = self._req("/data.lua", {"sid": self.sid, "page": name, "xhrId": "all", "xhr": "1"})
        return text

    def online_minutes_today(self):
        """{device name: minutes used today}. Devices whose access profile has no
        time budget show 'Unbegrenzt' and are left out."""
        html = self.page("kidLis")
        result = {}
        for row in re.findall(r"<tr>(.*?)</tr>", html, re.S):
            name = re.search(r'<td class="name" title="([^"]+)"', row)
            cell = re.search(r'datalabel="Online-Zeit heute"[^>]*>(.*?)</td>', row, re.S)
            if not name or not cell:
                continue
            used = re.search(r'title="(\d+):(\d+) von', cell.group(1))  # "00:02 von 08:00 Stunden"
            if not used:
                continue
            minutes = int(used.group(1)) * 60 + int(used.group(2))
            result[name.group(1)] = max(minutes, result.get(name.group(1), 0))
        return result
