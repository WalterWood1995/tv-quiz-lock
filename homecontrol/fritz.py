"""FRITZ!Box access via TR-064: list home network devices, block/unblock internet per device."""
from fritzconnection import FritzConnection
from fritzconnection.core.exceptions import FritzLookUpError
from fritzconnection.lib.fritzhosts import FritzHosts

from .config import normalize_mac

HOST_FILTER = "X_AVM-DE_HostFilter1"


class Router:
    def __init__(self, address, user, password):
        # TLS port 49443, so the password never crosses the LAN in clear text
        self.fc = FritzConnection(address=address, user=user, password=password,
                                  use_tls=True, timeout=10)
        self.hosts = FritzHosts(fc=self.fc)

    @property
    def model(self):
        return self.fc.modelname

    def list_devices(self):
        """All known devices: dicts with name, ip, mac, online, interface."""
        devices = []
        for h in self.hosts.get_hosts_info():
            devices.append({
                "name": h["name"],
                "ip": h["ip"],
                "mac": normalize_mac(h["mac"] or ""),
                "online": bool(h["status"]),
                "interface": h["interface_type"],
            })
        return devices

    def find(self, mac):
        """Look up a device by MAC, preferring an online entry.

        The RE650 repeater replaces the first 3 bytes of a client's MAC with
        62:32:B1, so the same device can appear under two MACs. Match on the
        last 3 bytes."""
        matches = self.find_all(mac)
        return matches[0] if matches else None

    def find_all(self, mac):
        """Every router entry for this device (direct + via repeater), online ones first."""
        suffix = normalize_mac(mac)[-8:]
        matches = [d for d in self.list_devices() if d["mac"][-8:] == suffix]
        matches.sort(key=lambda d: (not d["online"], not d["ip"]))
        return matches

    def is_blocked_any(self, mac):
        """True if any IP the device is known under has WAN access disallowed."""
        return any(self.is_blocked(d["ip"]) for d in self.find_all(mac) if d["ip"])

    def set_blocked_all(self, mac, blocked):
        """Block/unblock every IP the device is known under.

        The block is stored per IP on the FRITZ!Box. The TV shows up both with its
        real MAC (direct) and with the RE650-rewritten MAC (via repeater), under two
        IPs; blocking one and later unblocking the other left the TV cut off."""
        ips = [d["ip"] for d in self.find_all(mac) if d["ip"]]
        for ip in ips:
            self.set_blocked(ip, blocked)
        return ips

    def is_blocked(self, ip):
        r = self.fc.call_action(HOST_FILTER, "GetWANAccessByIP", NewIPv4Address=ip)
        return r["NewDisallow"] in (True, 1, "1")

    def set_blocked(self, ip, blocked):
        try:
            self.fc.call_action(HOST_FILTER, "DisallowWANAccessByIP",
                                NewIPv4Address=ip, NewDisallow=1 if blocked else 0)
        except FritzLookUpError:
            # 714 NoSuchEntryInArray: unblocking a device that has no block entry is already done
            if blocked:
                raise
