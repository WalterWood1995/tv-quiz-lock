"""Samsung TV: power state, wake-on-LAN, and full-screen playback via DLNA (UPnP AVTransport)."""
import json
import re
import socket
import urllib.request
from xml.sax.saxutils import escape

AVT = "urn:schemas-upnp-org:service:AVTransport:1"


class SamsungTV:
    def __init__(self, ip, mac=None):
        self.ip = ip
        self.mac = mac
        self._control_url = None

    def power_state(self):
        """'on', 'standby', or None if the TV does not answer at all."""
        try:
            with urllib.request.urlopen(f"http://{self.ip}:8001/api/v2/", timeout=3) as r:
                info = json.load(r)
            return info.get("device", {}).get("PowerState", "on")
        except OSError:
            return None

    def app_visible(self, app_id):
        """True/False whether the app is in the foreground; None if the TV does not have it."""
        try:
            with urllib.request.urlopen(f"http://{self.ip}:8001/api/v2/applications/{app_id}", timeout=4) as r:
                return bool(json.load(r).get("visible"))
        except OSError:
            return None

    def launch_app(self, app_id):
        """Bring an installed app to the front (REST, no pairing needed). Unlike the websocket
        launch, this works for the built-in browser on the QN85B — but cannot pass it a URL,
        so the quiz page is set as the browser's home page instead."""
        req = urllib.request.Request(f"http://{self.ip}:8001/api/v2/applications/{app_id}", data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=6) as r:
            r.read()

    def wake(self):
        """Wake-on-LAN. Needs 'Power On with Mobile' enabled on the TV."""
        if not self.mac:
            return
        mac = bytes.fromhex(self.mac.replace(":", ""))
        packet = b"\xff" * 6 + mac * 16
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.sendto(packet, ("255.255.255.255", 9))

    def _avtransport_url(self):
        if self._control_url is None:
            with urllib.request.urlopen(f"http://{self.ip}:9197/dmr", timeout=5) as r:
                desc = r.read().decode("utf-8", "replace")
            m = re.search(r"<serviceType>" + re.escape(AVT) + r"</serviceType>.*?<controlURL>([^<]+)</controlURL>",
                          desc, re.S)
            if not m:
                raise RuntimeError("电视不支持 DLNA AVTransport")
            path = m.group(1)
            self._control_url = f"http://{self.ip}:9197{path if path.startswith('/') else '/' + path}"
        return self._control_url

    def _soap(self, action, args):
        body = "".join(f"<{k}>{v}</{k}>" for k, v in args.items())
        envelope = ('<?xml version="1.0" encoding="utf-8"?>'
                    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                    's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
                    f'<u:{action} xmlns:u="{AVT}">{body}</u:{action}></s:Body></s:Envelope>')
        req = urllib.request.Request(self._avtransport_url(), data=envelope.encode("utf-8"), headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPAction": f'"{AVT}#{action}"',
        })
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read()

    def stop(self):
        self._soap("Stop", {"InstanceID": 0})

    def play(self, url, title="家长的提醒", mime="video/mp4"):
        """Take over the screen with a video at `url`."""
        didl = ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
                f'<item id="0" parentID="-1" restricted="1"><dc:title>{escape(title)}</dc:title>'
                '<upnp:class>object.item.videoItem</upnp:class>'
                f'<res protocolInfo="http-get:*:{mime}:*">{escape(url)}</res></item></DIDL-Lite>')
        self._soap("SetAVTransportURI", {"InstanceID": 0, "CurrentURI": escape(url),
                                         "CurrentURIMetaData": escape(didl)})
        self._soap("Play", {"InstanceID": 0, "Speed": 1})
