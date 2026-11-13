"""
Ears — Unified Network NVR/Camera Discovery (CCTVSee + CCTVPeek Broadcasts & Fast Sweep)
and Multi-Brand Channel Auto-Discovery (Dahua CGI, Uniview LAPI, Hikvision ISAPI, Reolink API, ONVIF).
"""

import os
import sys
import re
import ssl
import json
import uuid
import time
import struct
import socket
import asyncio
import logging
import ipaddress
import subprocess
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from typing import Dict, Any, List, Optional, Set

logger = logging.getLogger("ears.discovery")

TARGET_PORTS = [80, 443, 554, 8000, 37777, 34567, 9000, 5060, 35000, 6000]

ECOSYSTEM_APPS = {
    "Dahua": "DMSS",
    "Hikvision": "Hik-Connect",
    "Reolink": "Reolink App",
    "Xiongmai": "XMEye/ICSee",
    "Uniview": "EZView",
    "Axis": "ACS Edge",
    "Vivotek": "VIVOCloud",
    "Speco": "Speco Blue",
    "Luma": "Luma View",
    "Lorex": "Lorex",
    "Amcrest": "Amcrest View Pro",
    "Anpviz": "Hik-Connect",
    "Hanwha": "Wisenet WAVE",
    "UniFi": "UniFi Protect",
    "Night Owl": "Night Owl Protect",
}

OUI_VENDORS = {
    "00:1A:07": "Dahua", "00:40:8C": "Dahua", "14:A7:8B": "Dahua", "3C:EF:8C": "Dahua",
    "4C:11:BF": "Dahua", "6C:F3:70": "Dahua", "90:02:A9": "Dahua", "BC:32:AC": "Dahua",
    "B4:4C:3B": "Dahua", "E4:15:F6": "Dahua", "F8:F6:4D": "Dahua", "10:07:23": "Lorex", "80:ED:2C": "Lorex",
    "08:ED:ED": "Dahua", "C0:39:5A": "Dahua", "E4:24:6C": "Dahua", "3C:E8:C6": "Dahua",
    "38:0B:3C": "Amcrest", "BC:67:1C": "Amcrest", "E0:B9:4D": "Amcrest", "F4:5E:AB": "Amcrest",
    "00:0F:7C": "Hikvision", "10:C6:1F": "Hikvision", "08:54:11": "Hikvision", "2C:26:C6": "Hikvision",
    "C0:56:E3": "Hikvision", "14:2F:FD": "Hikvision",
    "48:EA:63": "Uniview", "6C:F1:7E": "Uniview", "88:26:3F": "Uniview",
    "E4:F1:4C": "Uniview",
    "EC:71:DB": "Reolink", "1C:1E:16": "Reolink",
    "AC:CC:8E": "Axis", "B8:A4:4F": "Axis",
    "00:02:D1": "Vivotek", "00:50:C2": "Vivotek",
    "5C:F2:07": "Speco", "00:02:16": "Speco",
    "F0:00:06": "Xiongmai", "F4:00:00": "Xiongmai",
    "84:94:59": "Anpviz", "5C:A9:33": "Luma", "00:04:7D": "Pelco",
    "00:1F:54": "Lorex", "54:2B:57": "Night Owl",
    "00:00:F0": "Hanwha", "00:07:D8": "Hanwha", "00:16:6C": "Hanwha", "E4:30:22": "Hanwha",
    "00:09:18": "Hanwha", "00:15:99": "Hanwha",
    "00:07:5F": "Bosch", "00:1C:44": "Bosch", "18:03:73": "Bosch",
}

CCTV_SEED_VENDORS = set(OUI_VENDORS.values()) | {"Reolink", "UniFi"}

# Broadcast Payloads (from CCTVSee modules)
HIKVISION_SADP_PAYLOAD = (
    b'<?xml version="1.0" encoding="utf-8"?>'
    b'<Probe><Uuid>2E2D070C-ADDB-4261-BD33-02422DBE0386</Uuid><Types>inquiry</Types></Probe>'
)

_DAHUA_JSON = b'{ "method" : "DHDiscover.search", "params" : { "mac" : "", "uni" : 1 } }\n'
_DAHUA_LEN = struct.pack("<I", len(_DAHUA_JSON))
DAHUA_37810_PAYLOAD = (
    b"\x20\x00\x00\x00DHIP\x00\x00\x00\x00\x00\x00\x00\x00"
    + _DAHUA_LEN + b"\x00\x00\x00\x00"
    + _DAHUA_LEN + b"\x00\x00\x00\x00"
    + _DAHUA_JSON
)
DAHUA_37777_PAYLOAD = b"\x00" * 16

UNIVIEW_9949_PAYLOAD = (
    b'<?xml version="1.0" encoding="utf-8"?>'
    b'<Probe><Uuid>00000000-0000-0000-0000-000000000000</Uuid><Types>inquiry</Types></Probe>'
)

XIONGMAI_34567_PAYLOADS = [
    b'{"Name": "Ops.Search", "SessionID": "0x00000000", "Version": "1.0"}',
    b"\xff\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xfa\x05\x00\x00{\"Name\":\"DAS_SEARCH\"}\n",
]

REOLINK_PROBES = [
    (b"\x00\x00\x00\x00", 2000),
    (bytes.fromhex("aaaa00000000000000000000000000000000"), 2000),
    (bytes.fromhex("3acf872a000000000100000000000000"), 2018),
]

VIVOTEK_5678_PAYLOAD = b"\x01\x8e\x0e\x2e\x0f"
UNIFI_10001_PAYLOAD = b"\x01\x00\x00\x00"

SSDP_1900_PAYLOAD = (
    b"M-SEARCH * HTTP/1.1\r\n"
    b"HOST: 239.255.255.250:1900\r\n"
    b'MAN: "ssdp:discover"\r\n'
    b"MX: 2\r\n"
    b"ST: ssdp:all\r\n\r\n"
)


def build_onvif_probe_payload() -> bytes:
    msg_id = uuid.uuid4()
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" '
        f'xmlns:w="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
        f'xmlns:dn="http://www.onvif.org/ver10/network/wsdl" '
        f'xmlns:tds="http://www.onvif.org/ver10/device/wsdl">'
        f"<e:Header>"
        f"<w:MessageID>uuid:{msg_id}</w:MessageID>"
        f"<w:To>urn:schemas-xmlsoap-org:ws:2005/04/discovery</w:To>"
        f"<w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>"
        f"</e:Header>"
        f"<e:Body><w:Probe><w:Types>dn:NetworkVideoTransmitter tds:Device</w:Types></w:Probe></e:Body>"
        f"</e:Envelope>"
    ).encode("utf-8")


def get_vendor_from_mac(mac_address: str) -> str:
    if not mac_address or mac_address == "Unknown":
        return "Unknown"
    clean_mac = mac_address.upper().replace("-", ":").strip()
    parts = clean_mac.split(":")
    if len(parts) >= 3:
        oui = f"{parts[0]}:{parts[1]}:{parts[2]}"
        if oui in OUI_VENDORS:
            return OUI_VENDORS[oui]
    return "Unknown"


def map_vendor_to_ears_brand(vendor_str: str) -> str:
    """Map discovered CCTV vendor string to Ears NVR brand key."""
    v = (vendor_str or "").lower()
    if any(k in v for k in ("dahua", "lorex", "amcrest", "speco")):
        return "dahua"
    if any(k in v for k in ("hikvision", "anpviz", "luma", "night owl")):
        return "hikvision"
    if any(k in v for k in ("uniview", "unv")):
        return "uniview"
    if "reolink" in v:
        return "reolink"
    return "onvif"


def get_local_interfaces_and_subnets() -> List[Dict[str, str]]:
    """
    Enumerate all active local IPv4 addresses and subnets without psutil.
    Uses exact cctvpeek.py logic across Windows (ipconfig), Linux (ip -4 addr), and macOS (ifconfig).
    Returns list of {"ip": "10.0.0.4", "subnet": "10.0.0.0/23"}.
    """
    results = []
    seen_subnets = set()
    try:
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            output = subprocess.check_output(
                ["ipconfig"], stderr=subprocess.STDOUT, timeout=2.5, creationflags=flags
            ).decode("ascii", errors="ignore")
            for block in output.split("\r\n\r\n"):
                ip_mh = re.search(r"IPv4 Address[ .]*: (\d{1,3}(?:\.\d{1,3}){3})", block)
                mask_mh = re.search(r"Subnet Mask[ .]*: (\d{1,3}(?:\.\d{1,3}){3})", block)
                if ip_mh and mask_mh:
                    ip_str = ip_mh.group(1)
                    mask_str = mask_mh.group(1)
                    if ip_str.startswith("169.254.") or ip_str.startswith("127."):
                        continue
                    try:
                        net = ipaddress.IPv4Network(f"{ip_str}/{mask_str}", strict=False)
                        if net.prefixlen < 22:
                            net = ipaddress.IPv4Network(f"{ip_str}/24", strict=False)
                        if str(net) not in seen_subnets:
                            seen_subnets.add(str(net))
                            results.append({"ip": ip_str, "subnet": str(net)})
                    except ValueError:
                        pass
        elif sys.platform == "darwin":
            output = subprocess.check_output(
                ["ifconfig"], stderr=subprocess.STDOUT, timeout=2.5
            ).decode("ascii", errors="ignore")
            for m in re.finditer(r"inet\s+(\d{1,3}(?:\.\d{1,3}){3})\s+netmask\s+0x([0-9a-fA-F]{8})", output):
                ip_str = m.group(1)
                mask_hex = m.group(2)
                if ip_str.startswith("127.") or ip_str.startswith("169.254."):
                    continue
                mask_int = int(mask_hex, 16)
                mask_str = socket.inet_ntoa(struct.pack("!I", mask_int))
                net = ipaddress.IPv4Network(f"{ip_str}/{mask_str}", strict=False)
                if net.prefixlen < 22:
                    net = ipaddress.IPv4Network(f"{ip_str}/24", strict=False)
                if str(net) not in seen_subnets:
                    seen_subnets.add(str(net))
                    results.append({"ip": ip_str, "subnet": str(net)})
        else:
            ip_bin = next((p for p in ("/sbin/ip", "/usr/sbin/ip", "/bin/ip", "ip") if p == "ip" or os.path.exists(p)), "ip")
            output = subprocess.check_output(
                [ip_bin, "-4", "addr"], stderr=subprocess.STDOUT, timeout=2.5
            ).decode("ascii", errors="ignore")
            for m in re.findall(r"inet\s+(\d{1,3}(?:\.\d{1,3}){3}/\d{1,2})", output):
                if m.startswith("127.") or m.startswith("169.254.") or m.startswith("172.17."):
                    continue
                try:
                    ip_str = m.split("/")[0]
                    net = ipaddress.IPv4Network(m, strict=False)
                    if net.prefixlen < 22:
                        net = ipaddress.IPv4Network(f"{ip_str}/24", strict=False)
                    if str(net) not in seen_subnets:
                        seen_subnets.add(str(net))
                        results.append({"ip": ip_str, "subnet": str(net)})
                except ValueError:
                    pass
    except Exception as e:
        logger.warning(f"Interface enumeration fallback: {e}")

    if not results:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                ip_str = s.getsockname()[0]
                net = ipaddress.IPv4Network(f"{ip_str}/24", strict=False)
                results.append({"ip": ip_str, "subnet": str(net)})
        except Exception:
            pass
    return results


def get_system_arp_table() -> Dict[str, str]:
    """Read OS ARP cache using 'arp -a' on Windows and 'ip neigh' on Linux (exact cctvpeek.py method)."""
    arp_map: Dict[str, str] = {}
    try:
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            output = subprocess.check_output(["arp", "-a"], text=True, timeout=2.5, creationflags=flags)
            for line in output.splitlines():
                parts = line.split()
                if len(parts) >= 3 and parts[0].count(".") == 3:
                    ip = parts[0]
                    mac = parts[1].replace("-", ":").upper()
                    if len(mac) == 17 and mac != "FF:FF:FF:FF:FF:FF":
                        arp_map[ip] = mac
        elif sys.platform == "darwin":
            output = subprocess.check_output(["arp", "-an"], text=True, timeout=2.5)
            for line in output.splitlines():
                ip_m = re.search(r"\((\d{1,3}(?:\.\d{1,3}){3})\)", line)
                mac_m = re.search(r"([0-9a-fA-F]{1,2}(?::[0-9a-fA-F]{1,2}){5})", line)
                if ip_m and mac_m:
                    mac = ":".join(p.zfill(2).upper() for p in mac_m.group(1).split(":"))
                    if mac != "FF:FF:FF:FF:FF:FF":
                        arp_map[ip_m.group(1)] = mac
        else:
            ip_bin = next((p for p in ("/sbin/ip", "/usr/sbin/ip", "/bin/ip", "ip") if p == "ip" or os.path.exists(p)), "ip")
            output = subprocess.check_output([ip_bin, "neigh"], text=True, timeout=2.5)
            for line in output.splitlines():
                parts = line.split()
                if len(parts) >= 5 and "lladdr" in parts:
                    mac_idx = parts.index("lladdr") + 1
                    if mac_idx < len(parts):
                        arp_map[parts[0]] = parts[mac_idx].upper()
    except Exception:
        pass
    return arp_map


async def _single_scan_pass(listen_time: float = 2.5) -> Dict[str, Any]:
    """Single pass of multi-protocol UDP broadcast + fast TCP subnet sweep."""
    start_time = time.time()
    ifaces = get_local_interfaces_and_subnets()
    local_ips = {i["ip"] for i in ifaces}
    subnets = [i["subnet"] for i in ifaces]

    found_devices: Dict[str, Dict[str, Any]] = {}
    discovered_ips: Set[str] = set()

    def ensure_dev(ip: str) -> Dict[str, Any]:
        if ip not in found_devices:
            found_devices[ip] = {
                "ip": ip,
                "mac": "Unknown",
                "vendor": "Unknown",
                "brand": "dahua",
                "model": "",
                "ports": set(),
                "server_header": "Unknown",
                "discovery_sources": set(),
                "unactivated": False,
            }
        return found_devices[ip]

    # 0. Pre-warm from ARP Cache
    arp_cache = get_system_arp_table()
    for arp_ip, arp_mac in arp_cache.items():
        if arp_ip in local_ips:
            continue
        vendor = get_vendor_from_mac(arp_mac)
        if vendor in CCTV_SEED_VENDORS:
            discovered_ips.add(arp_ip)
            dev = ensure_dev(arp_ip)
            dev["mac"] = arp_mac
            dev["vendor"] = vendor
            dev["discovery_sources"].add("ARP-OUI")

    # 1. Multi-Protocol UDP Broadcast & Multicast Listener
    def _parse_udp_reply(ip: str, port: int, data: bytes) -> None:
        if ip in local_ips or ip.endswith(".255") or ip.endswith(".0"):
            return
        discovered_ips.add(ip)
        dev = ensure_dev(ip)
        text = data.decode("utf-8", errors="ignore")

        # Dahua ConfigTool (37810) / 37777
        if b"DHIP" in data or port in (37810, 37777):
            dev["discovery_sources"].add("Dahua-UDP")
            if dev["vendor"] == "Unknown":
                if "Amcrest" in text:
                    dev["vendor"] = "Amcrest"
                elif "Lorex" in text or ".LR" in text:
                    dev["vendor"] = "Lorex"
                else:
                    dev["vendor"] = "Dahua"
            mac_m = re.search(r'["\']?(?:mac|macAddr)["\']?\s*[:=]\s*["\']?([0-9a-fA-F:-]{12,17})["\']?', text, re.I)
            if mac_m and dev["mac"] == "Unknown":
                dev["mac"] = mac_m.group(1).upper().replace("-", ":")
            mod_m = re.search(r'["\']?(?:DeviceType|Model)["\']?\s*[:=]\s*["\']?([a-zA-Z0-9_-]{3,32})["\']?', text, re.I)
            if mod_m and not dev["model"]:
                dev["model"] = mod_m.group(1)

        # Hikvision SADP (37020)
        elif b"Hikvision" in data or b"<ProbeMatch" in data or port == 37020:
            if "ws-discovery" not in text.lower():
                dev["discovery_sources"].add("Hikvision-SADP")
                if dev["vendor"] == "Unknown":
                    dev["vendor"] = "Hikvision"
                mac_m = re.search(r"<MAC>(.*?)</MAC>", text, re.I)
                if mac_m and dev["mac"] == "Unknown":
                    dev["mac"] = mac_m.group(1).upper().replace("-", ":")
                if "Activated>false" in text or "Activated>0" in text:
                    dev["unactivated"] = True

        # ONVIF WS-Discovery (3702)
        if b"ProbeMatches" in data or "onvif.org" in text:
            dev["discovery_sources"].add("ONVIF-WSD")
            if dev["vendor"] == "Unknown":
                for vname in ("Hikvision", "Dahua", "Uniview", "Amcrest", "Reolink", "Vivotek", "Hanwha", "Axis"):
                    if vname.lower() in text.lower():
                        dev["vendor"] = vname
                        break
                else:
                    dev["vendor"] = "ONVIF"
            hw_m = re.search(r"onvif://www\.onvif\.org/hardware/([^\s<\"']+)", text, re.I)
            if hw_m and not dev["model"]:
                dev["model"] = hw_m.group(1)

        # Uniview (9949)
        if port == 9949:
            dev["discovery_sources"].add("Uniview-UDP")
            if dev["vendor"] == "Unknown":
                dev["vendor"] = "Uniview"

        # Xiongmai (34567)
        if b"Ops.Search" in data or b"System.Search" in data or port == 34567:
            dev["discovery_sources"].add("Xiongmai-UDP")
            if dev["vendor"] == "Unknown":
                dev["vendor"] = "Xiongmai"

        # Reolink (2000 / 2018 / 3000)
        if port in (2000, 2018, 3000):
            dev["discovery_sources"].add("Reolink-UDP")
            if dev["vendor"] == "Unknown":
                dev["vendor"] = "Reolink"

        # Vivotek (5678)
        if port in (5678, 10000, 10119):
            dev["discovery_sources"].add("Vivotek-VDP")
            if dev["vendor"] == "Unknown":
                dev["vendor"] = "Vivotek"

    async def _udp_blast_worker(bind_ip: str) -> None:
        loop = asyncio.get_running_loop()

        class MultiProbeProtocol(asyncio.DatagramProtocol):
            def __init__(self):
                self.transport = None

            def connection_made(self, transport):
                self.transport = transport
                sock = transport.get_extra_info("socket")
                if sock:
                    try:
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 3)
                        if bind_ip != "0.0.0.0":
                            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(bind_ip))
                    except Exception:
                        pass

                # Fire all CCTVSee ecosystem broadcasts in parallel
                blasts = [
                    # Dahua ConfigTool 37810 (Multicast + Broadcast) & 37777
                    (DAHUA_37810_PAYLOAD, ("239.255.255.251", 37810)),
                    (DAHUA_37810_PAYLOAD, ("255.255.255.255", 37810)),
                    (DAHUA_37777_PAYLOAD, ("255.255.255.255", 37777)),
                    # Hikvision SADP 37020 (Multicast + Broadcast)
                    (HIKVISION_SADP_PAYLOAD, ("239.255.255.250", 37020)),
                    (HIKVISION_SADP_PAYLOAD, ("255.255.255.255", 37020)),
                    # ONVIF WS-Discovery 3702
                    (build_onvif_probe_payload(), ("239.255.255.250", 3702)),
                    (build_onvif_probe_payload(), ("255.255.255.255", 3702)),
                    # Uniview 9949
                    (UNIVIEW_9949_PAYLOAD, ("255.255.255.255", 9949)),
                    # Vivotek 5678
                    (VIVOTEK_5678_PAYLOAD, ("255.255.255.255", 5678)),
                    # UniFi 10001
                    (UNIFI_10001_PAYLOAD, ("255.255.255.255", 10001)),
                    # SSDP 1900
                    (SSDP_1900_PAYLOAD, ("239.255.255.250", 1900)),
                ]
                for xm_p in XIONGMAI_34567_PAYLOADS:
                    blasts.append((xm_p, ("255.255.255.255", 34567)))
                for rl_p, rl_port in REOLINK_PROBES:
                    blasts.append((rl_p, ("255.255.255.255", rl_port)))

                for payload, addr in blasts:
                    try:
                        transport.sendto(payload, addr)
                    except Exception:
                        pass

            def datagram_received(self, data, addr):
                _parse_udp_reply(addr[0], addr[1], data)

        try:
            transport, _ = await loop.create_datagram_endpoint(
                MultiProbeProtocol,
                local_addr=(bind_ip, 0),
                allow_broadcast=True,
            )
            await asyncio.sleep(listen_time)
            transport.close()
        except Exception as e:
            logger.debug(f"UDP broadcast error on {bind_ip}: {e}")

    # 2. Fast TCP Subnet Sweep (Ports 80, 554, 37777, 8000)
    sem = asyncio.Semaphore(384)

    async def _tcp_probe(ip: str, port: int, timeout: float = 0.22) -> bool:
        if ip in local_ips:
            return False
        async with sem:
            try:
                fut = asyncio.open_connection(ip, port)
                _, writer = await asyncio.wait_for(fut, timeout=timeout)
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                discovered_ips.add(ip)
                ensure_dev(ip)["ports"].add(port)
                return True
            except Exception:
                return False

    async def _grab_http_header(ip: str, timeout: float = 0.35) -> None:
        async with sem:
            try:
                fut = asyncio.open_connection(ip, 80)
                reader, writer = await asyncio.wait_for(fut, timeout=timeout)
                req = f"GET / HTTP/1.0\r\nHost: {ip}\r\nUser-Agent: EarsScanner/1.0\r\n\r\n".encode("ascii")
                writer.write(req)
                await writer.drain()
                resp = await asyncio.wait_for(reader.read(2048), timeout=timeout)
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                text = resp.decode("utf-8", errors="ignore")
                for line in text.split("\n"):
                    if line.lower().startswith("server:"):
                        ensure_dev(ip)["server_header"] = line.split(":", 1)[1].strip()
                        break
            except Exception:
                pass

    # Launch UDP broadcast listeners across all local interfaces
    bind_ips = [i["ip"] for i in ifaces] or ["0.0.0.0"]
    udp_tasks = [asyncio.create_task(_udp_blast_worker(bip)) for bip in bind_ips]

    # Launch concurrent TCP sweep across subnets
    sweep_tasks = []
    for subnet_str in subnets:
        try:
            net = ipaddress.ip_network(subnet_str, strict=False)
            for host_ip in net.hosts():
                ip_s = str(host_ip)
                if ip_s in local_ips:
                    continue
                for p in (80, 554, 37777, 8000):
                    sweep_tasks.append(_tcp_probe(ip_s, p, timeout=0.22))
        except Exception:
            pass

    # Path-warm ARP-seeded CCTV IPs with slightly longer timeout
    for seeded_ip in list(discovered_ips):
        for p in (80, 554, 37777, 8000):
            sweep_tasks.append(_tcp_probe(seeded_ip, p, timeout=1.0))

    if sweep_tasks:
        await asyncio.gather(*sweep_tasks)
    await asyncio.gather(*udp_tasks)

    # 3. Full CCTV Port Scan on Responsive IPs
    detail_tasks = []
    for ip in list(discovered_ips):
        for p in TARGET_PORTS:
            if p not in ensure_dev(ip)["ports"]:
                detail_tasks.append(_tcp_probe(ip, p, timeout=0.25))
    if detail_tasks:
        await asyncio.gather(*detail_tasks)

    # 3.5 Grab HTTP Server headers for noise filtering
    http_tasks = [
        _grab_http_header(ip)
        for ip in list(discovered_ips)
        if 80 in ensure_dev(ip)["ports"]
    ]
    if http_tasks:
        await asyncio.gather(*http_tasks)

    # 4. Refresh ARP cache & classify devices
    arp_cache.update(get_system_arp_table())

    cctv_server_headers = ["App-webs", "uc-httpd", "Cross Web Server", "Dahua", "KEDACOM", "GoAhead", "Webs", "DNVRS-Webs", "Hikvision"]
    noise_server_headers = ["Synology", "nginx", "Apache", "lighttpd", "Microsoft-IIS", "MiniServ", "Caddy"]

    final_devices = []
    screened_out = 0

    for ip in sorted(discovered_ips, key=lambda x: tuple(int(p) for p in x.split("."))):
        dev = found_devices[ip]
        if ip in arp_cache and dev["mac"] == "Unknown":
            dev["mac"] = arp_cache[ip]
        if dev["vendor"] == "Unknown" and dev["mac"] != "Unknown":
            dev["vendor"] = get_vendor_from_mac(dev["mac"])

        is_cctv = False
        if dev["vendor"] != "Unknown" or dev["discovery_sources"]:
            is_cctv = True

        ports = dev["ports"]
        if 37777 in ports:
            is_cctv = True
            if dev["vendor"] == "Unknown":
                dev["vendor"] = "Dahua"
        elif 34567 in ports:
            is_cctv = True
            if dev["vendor"] == "Unknown":
                dev["vendor"] = "Xiongmai"
        elif 8000 in ports:
            is_cctv = True
            if dev["vendor"] == "Unknown":
                dev["vendor"] = "Hikvision"
        elif 9000 in ports:
            is_cctv = True
            if dev["vendor"] == "Unknown":
                dev["vendor"] = "Reolink"
        elif ports.intersection({554, 35000, 6000}):
            is_cctv = True

        header = dev.get("server_header", "Unknown")
        if any(sig.lower() in header.lower() for sig in cctv_server_headers):
            is_cctv = True
            if dev["vendor"] == "Unknown":
                if "dnvrs" in header.lower() or "app-webs" in header.lower():
                    dev["vendor"] = "Hikvision"
                elif "dahua" in header.lower():
                    dev["vendor"] = "Dahua"
                else:
                    dev["vendor"] = f"CCTV ({header[:18]})"

        # Filter out NAS/web servers unless they have explicit CCTV RTSP/SDK ports
        if any(sig.lower() in header.lower() for sig in noise_server_headers):
            if dev["vendor"] == "Unknown" and not {554, 37777, 34567}.intersection(ports):
                is_cctv = False

        if not is_cctv:
            screened_out += 1
            continue

        brand_key = map_vendor_to_ears_brand(dev["vendor"])
        ecosystem_app = ECOSYSTEM_APPS.get(dev["vendor"], "")
        display_vendor = f"{dev['vendor']} ({ecosystem_app})" if ecosystem_app else dev["vendor"]

        final_devices.append({
            "ip": ip,
            "vendor": dev["vendor"],
            "display_vendor": display_vendor,
            "brand": brand_key,
            "mac": dev["mac"],
            "model": dev["model"],
            "ports": sorted(list(ports)),
            "discovery_sources": sorted(list(dev["discovery_sources"])) or ["TCP-Sweep"],
            "unactivated": dev["unactivated"],
        })

    elapsed = round(time.time() - start_time, 2)
    return {
        "status": "success",
        "elapsed_seconds": elapsed,
        "subnets_scanned": subnets,
        "devices": final_devices,
        "screened_out": screened_out,
    }


async def scan_network_for_nvrs(
    listen_time: float = 2.6,
    two_pass: bool = True,
) -> Dict[str, Any]:
    """
    Execute a two-pass network scan:
    Pass 1 (Warm-up pass, ignored): Wakes up sleepy Wi-Fi/PoE cameras, populates OS ARP tables,
    and primes switch/router L2 paths.
    Pass 2 ("For Reals"): Performs the authoritative broadcast + TCP sweep and returns the result.
    """
    total_start = time.time()
    if two_pass:
        logger.info("Running Pass 1 (network & ARP warm-up)...")
        await _single_scan_pass(listen_time=1.8)
        await asyncio.sleep(0.15)

    logger.info("Running Pass 2 (authoritative NVR/Camera discovery)...")
    result = await _single_scan_pass(listen_time=listen_time)
    result["elapsed_seconds"] = round(time.time() - total_start, 2)
    logger.info(
        f"Two-pass LAN Scan finished in {result['elapsed_seconds']}s: "
        f"{len(result['devices'])} CCTV devices found ({result['screened_out']} filtered)."
    )
    return result


def discover_nvr_channels(
    ip: str,
    username: str,
    password: str,
    brand: str = "dahua",
    timeout: float = 5.0,
) -> Dict[str, Any]:
    """
    Interrogate an NVR or IP Camera to auto-discover its channel names and IDs.
    Supports:
    - Dahua / Amcrest / Lorex CGI (/cgi-bin/configManager.cgi?action=getConfig&name=ChannelTitle)
    - Uniview / UNV LAPI (/LAPI/V1.0/Channels/System/ChannelDetailInfos, etc.)
    - Hikvision ISAPI (/ISAPI/ContentMgmt/InputProxy/channels & /ISAPI/System/Video/inputs/channels)
    - Reolink HTTP API (/cgi-bin/api.cgi?cmd=GetChannelstatus)
    - Generic ONVIF fallback (GetVideoSources / GetProfiles)
    """
    brand_clean = (brand or "dahua").lower().strip()
    import requests
    from requests.auth import HTTPDigestAuth, HTTPBasicAuth
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    auth_methods = [HTTPDigestAuth(username, password), HTTPBasicAuth(username, password)]

    # 1. Dahua / Amcrest / Lorex CGI
    if brand_clean in ("dahua", "amcrest", "lorex"):
        for proto in ("http", "https"):
            url = f"{proto}://{ip}/cgi-bin/configManager.cgi?action=getConfig&name=ChannelTitle"
            for auth in auth_methods:
                try:
                    res = requests.get(url, auth=auth, timeout=timeout, verify=False)
                    if res.status_code == 200 and ".Name=" in res.text:
                        channels = []
                        for line in res.text.strip().splitlines():
                            if ".Name=" in line:
                                try:
                                    name = line.split("=", 1)[1].strip()
                                    idx = int(line.split("[")[1].split("]")[0]) + 1
                                    channels.append({"id": idx, "name": name or f"Channel {idx}", "enabled": False})
                                except Exception:
                                    pass
                        if channels:
                            return {"status": "success", "brand_detected": "dahua", "channels": channels}
                except Exception:
                    pass

    # 2. Uniview / UNV LAPI
    if brand_clean in ("unv", "uniview"):
        endpoints = [
            f"http://{ip}/LAPI/V1.0/Channels/System/ChannelDetailInfos",
            f"http://{ip}/LAPI/V1.0/System/Video/Inputs/Channels",
            f"http://{ip}/LAPI/V1.0/Channel/VideoIn",
        ]
        for url in endpoints:
            for auth in auth_methods:
                try:
                    res = requests.get(url, auth=auth, timeout=timeout, verify=False)
                    if res.status_code == 200:
                        data = res.json()
                        resp_obj = data.get("Response", data)
                        data_obj = resp_obj.get("Data", resp_obj)
                        channel_list = None
                        for key in ("ChannelDetailInfos", "ChannelInfo", "ChannelList", "Channels"):
                            if isinstance(data_obj, dict) and key in data_obj:
                                channel_list = data_obj[key]
                                break
                        if channel_list is None and isinstance(data_obj, list):
                            channel_list = data_obj
                        channels = []
                        if isinstance(channel_list, list):
                            for ch in channel_list:
                                ch_id = ch.get("ID", ch.get("ChannelID"))
                                ch_name = ch.get("Name", ch.get("ChannelName", f"Channel {ch_id}"))
                                if ch_id is not None:
                                    channels.append({"id": int(ch_id), "name": ch_name, "enabled": False})
                        if channels:
                            return {"status": "success", "brand_detected": "uniview", "channels": channels}
                except Exception:
                    pass

    # 3. Hikvision ISAPI
    if brand_clean in ("hikvision", "anpviz", "luma", "nightowl"):
        hik_endpoints = [
            f"http://{ip}/ISAPI/ContentMgmt/InputProxy/channels",
            f"http://{ip}/ISAPI/System/Video/inputs/channels",
        ]
        for url in hik_endpoints:
            for auth in auth_methods:
                try:
                    res = requests.get(url, auth=auth, timeout=timeout, verify=False)
                    if res.status_code == 200 and "<" in res.text:
                        root = ET.fromstring(res.text)
                        channels = []
                        for elem in root.iter():
                            tag = elem.tag.split("}")[-1]
                            if tag in ("InputProxyChannel", "VideoInputChannel"):
                                cid = None
                                cname = None
                                for child in elem:
                                    ctag = child.tag.split("}")[-1]
                                    if ctag == "id" and child.text:
                                        cid = int(child.text.strip())
                                    elif ctag == "name" and child.text:
                                        cname = child.text.strip()
                                if cid is not None:
                                    channels.append({"id": cid, "name": cname or f"Channel {cid}", "enabled": False})
                        if channels:
                            return {"status": "success", "brand_detected": "hikvision", "channels": channels}
                except Exception:
                    pass

    # 4. Reolink HTTP CGI API
    if brand_clean == "reolink":
        for proto in ("http", "https"):
            url = f"{proto}://{ip}/cgi-bin/api.cgi?cmd=GetChannelstatus&user={username}&password={password}"
            try:
                res = requests.get(url, timeout=timeout, verify=False)
                if res.status_code == 200:
                    arr = res.json()
                    if isinstance(arr, list) and arr:
                        val = arr[0].get("value", {})
                        status_list = val.get("status", [])
                        channels = []
                        for item in status_list:
                            cid = int(item.get("channel", 0)) + 1
                            cname = item.get("name") or f"Camera {cid}"
                            if item.get("online", 1) == 1 or len(status_list) <= 8:
                                channels.append({"id": cid, "name": cname, "enabled": False})
                        if channels:
                            return {"status": "success", "brand_detected": "reolink", "channels": channels}
            except Exception:
                pass

    # 5. Generic ONVIF WS-SOAP GetVideoSources / GetProfiles (Zero extra dependency required, or onvif_zeep if present)
    try:
        from onvif import ONVIFCamera
        mycam = ONVIFCamera(ip, 80, username, password, adjust_time=True)
        media = mycam.create_media_service()
        try:
            sources = media.GetVideoSources()
            channels = [
                {"id": i + 1, "name": f"Camera {i + 1}", "enabled": False}
                for i, _ in enumerate(sources)
            ]
            if channels:
                return {"status": "success", "brand_detected": "onvif", "channels": channels}
        except Exception:
            pass
        profiles = media.GetProfiles()
        channels = [
            {"id": i + 1, "name": getattr(p, "Name", f"Channel {i + 1}"), "enabled": False}
            for i, p in enumerate(profiles)
        ]
        if channels:
            return {"status": "success", "brand_detected": "onvif", "channels": channels}
    except Exception as e:
        fallback = [{"id": i + 1, "name": f"Channel {i + 1}", "enabled": False} for i in range(8)]
        return {
            "status": "error",
            "message": f"Auto-discovery could not query channel list ({e}). Populated default channels 1–8.",
            "fallback": fallback,
        }

    fallback = [{"id": i + 1, "name": f"Channel {i + 1}", "enabled": False} for i in range(8)]
    return {
        "status": "error",
        "message": "No channels returned by device; populated default channels 1–8.",
        "fallback": fallback,
    }


def build_rtsp_url(
    ip: str,
    username: str,
    password: str,
    brand: str,
    channel: int,
    rtsp_url_override: Optional[str] = None,
) -> str:
    """Construct the low-bandwidth audio+substream RTSP URL for a given NVR brand & channel."""
    from urllib.parse import quote

    safe_user = quote(username or "", safe="")
    safe_pass = quote(password or "", safe="")

    if rtsp_url_override and rtsp_url_override.strip():
        url = rtsp_url_override.strip()
        # Only allow genuine rtsp:// or rtsps:// URLs without control characters
        if url.lower().startswith(("rtsp://", "rtsps://")) and not any(c in url for c in ("\r", "\n", "\x00")):
            scheme, rest = url.split("://", 1)
            if "@" not in rest and safe_user:
                url = f"{scheme}://{safe_user}:{safe_pass}@{rest}"
            return url

    b = (brand or "dahua").lower().strip()
    if b in ("hikvision", "anpviz", "luma", "nightowl"):
        return f"rtsp://{safe_user}:{safe_pass}@{ip}:554/Streaming/Channels/{channel}02"
    elif b in ("uniview", "unv"):
        return f"rtsp://{safe_user}:{safe_pass}@{ip}:554/unicast/c{channel}/s1/live"
    elif b == "reolink":
        ch_pad = f"{int(channel):02d}"
        return f"rtsp://{safe_user}:{safe_pass}@{ip}:554/h264Preview_{ch_pad}_sub"
    else:
        # Default Dahua / Amcrest / Lorex substream
        return f"rtsp://{safe_user}:{safe_pass}@{ip}:554/cam/realmonitor?channel={channel}&subtype=1"
