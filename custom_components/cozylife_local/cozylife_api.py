import asyncio
import json
import logging
import socket
import time
from typing import Any, Dict, List, Optional

from .discovery import get_model_info

_LOGGER = logging.getLogger(__name__)
_RAW_LOGGER = logging.getLogger(f"{__name__}.raw")
_RAW_LOGGER.setLevel(logging.INFO)

COZYLIFE_DISCOVERY_PORT = 6095
COZYLIFE_DISCOVERY_ADDRESS = "255.255.255.255"
COZYLIFE_DISCOVERY_RETRY_INTERVAL = 5.0
COZYLIFE_DISCOVERY_BROADCAST_ATTEMPTS = 16
COZYLIFE_PORT = 5555
CMD_INFO = 0
CMD_QUERY = 2
CMD_SET = 3
DPID_QUERY_ATTEMPTS = 3

# CozyLife's own Android client uses CMD_QUERY with attr [0] for device
# properties and attr [1] for the current device status. Keep the property
# selector as the low-level default for discovery, but make normal state reads
# explicitly request status below.
QUERY_PROPERTIES = 0
QUERY_STATUS = 1

def _get_sn() -> str:
    """Generates a unique sequence number based on current timestamp."""
    return str(int(time.time() * 1000))

class CozyLifeDevice:
    """Represents a CozyLife device and handles its TCP communication."""

    def __init__(self, ip_address: str, timeout: float = 3.0):
        self._ip_address = ip_address
        self._timeout = timeout
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._device_id: Optional[str] = None
        self._pid: Optional[str] = None
        self._device_model_name: Optional[str] = None
        self._dpid: Optional[List[str]] = None
        self._last_discovery_attempt = 0.0
        self._device_type_code: Optional[str] = None

    @property
    def ip_address(self) -> str:
        return self._ip_address

    @property
    def device_id(self) -> Optional[str]:
        return self._device_id
    
    @property
    def pid(self) -> Optional[str]:
        return self._pid

    @property
    def device_model_name(self) -> Optional[str]:
        return self._device_model_name

    @property
    def dpid(self) -> Optional[List[str]]:
        return self._dpid

    @property
    def device_type_code(self) -> Optional[str]:
        return self._device_type_code

    def restore_from_cache(self, device_id: str, pid: str, device_type_code: str, dpids: List[str]) -> None:
        """Restore device info from cached config entry data without contacting the device."""
        model_info = get_model_info(pid)
        self._device_id = device_id
        self._pid = pid
        self._device_type_code = device_type_code
        self._dpid = dpids
        self._device_model_name = (
            model_info.model_name if model_info else f"CozyLife Device ({pid})"
        )

    async def async_refresh_ip_from_discovery(self) -> bool:
        """Refresh this device LAN IP using CozyLife UDP discovery."""
        if not self._device_id:
            return False
        now = time.monotonic()
        if now - self._last_discovery_attempt < COZYLIFE_DISCOVERY_RETRY_INTERVAL:
            return False
        self._last_discovery_attempt = now

        loop = asyncio.get_running_loop()
        reply: asyncio.Future[dict[str, Any] | None] = loop.create_future()
        device_id = self._device_id

        class DiscoveryProtocol(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
                if reply.done():
                    return
                try:
                    payload = json.loads(data.decode("utf-8").strip())
                except (json.JSONDecodeError, UnicodeDecodeError):
                    return
                if payload.get("did") != device_id:
                    return
                payload.setdefault("ip", addr[0])
                reply.set_result(payload)

        transport = None
        try:
            transport, _protocol = await loop.create_datagram_endpoint(
                DiscoveryProtocol,
                family=socket.AF_INET,
                allow_broadcast=True,
            )
            message = (
                json.dumps(self._create_message(CMD_INFO, {}), separators=(",", ":"))
                + "\r\n"
            )
            for _ in range(COZYLIFE_DISCOVERY_BROADCAST_ATTEMPTS):
                transport.sendto(
                    message.encode("utf-8"),
                    (COZYLIFE_DISCOVERY_ADDRESS, COZYLIFE_DISCOVERY_PORT),
                )
            payload = await asyncio.wait_for(reply, timeout=self._timeout)
        except (asyncio.TimeoutError, OSError):
            return False
        finally:
            if transport is not None:
                transport.close()

        if not payload or not isinstance(payload.get("ip"), str):
            return False
        if payload["ip"] != self._ip_address:
            _LOGGER.info(
                "CozyLife UDP discovery updated %s from %s to %s",
                self._device_id,
                self._ip_address,
                payload["ip"],
            )
            self._ip_address = payload["ip"]
        return True

    async def _connect(self):
        """Establishes an asynchronous TCP connection to the device."""
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._ip_address, COZYLIFE_PORT),
                timeout=self._timeout,
            )
            _RAW_LOGGER.debug(f"Connected to CozyLife device at {self._ip_address}:{COZYLIFE_PORT}")
        except (asyncio.TimeoutError, ConnectionRefusedError, OSError) as e:
            self._disconnect()
            raise

    def _disconnect(self):
        """Closes the TCP connection."""
        if self._writer:
            try:
                self._writer.close()
            except Exception:
                pass
        self._reader = None
        self._writer = None

    async def _send_receive(self, cmd: int, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Sends a command and awaits a matching response.
        This method will loop and read messages from the socket until a response
        with a matching sequence number (sn) is found or a timeout occurs.
        """
        try:
            await self._connect()
            message = self._create_message(cmd, payload)
            sent_sn = message.get('sn')
            encoded_message = (json.dumps(message, separators=(',', ':')) + "\r\n").encode('utf8')
            
            _RAW_LOGGER.debug(f"Sending to {self._ip_address}: {encoded_message!r}")
            self._writer.write(encoded_message)
            await self._writer.drain()

            end_time = time.monotonic() + self._timeout
            while time.monotonic() < end_time:
                try:
                    remaining_time = end_time - time.monotonic()
                    if remaining_time <= 0:
                        raise asyncio.TimeoutError
                    
                    response_data = await asyncio.wait_for(self._reader.readline(), timeout=remaining_time)
                    
                    if not response_data:
                        _LOGGER.debug(f"Connection closed by {self._ip_address} while waiting for response.")
                        self._disconnect()
                        return None

                    _RAW_LOGGER.debug(f"Received from {self._ip_address}: {response_data!r}")
                    resp_json = json.loads(response_data.decode('utf8').strip())

                    if resp_json.get('sn') == sent_sn:
                        if resp_json.get('res') == 0:
                            return resp_json.get('msg')
                        else:
                            _LOGGER.warning(f"Received error response for sn {sent_sn}: {resp_json}")
                            return None
                    else:
                        _RAW_LOGGER.debug(f"Discarding message with mismatched sn: {resp_json}")

                except asyncio.TimeoutError:
                    _LOGGER.warning(f"Timeout waiting for response with sn {sent_sn} from {self._ip_address}")
                    return None
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    _LOGGER.warning(f"Error decoding response from {self._ip_address}: {e} - Data: {response_data!r}")

            _LOGGER.warning(f"Outer loop timeout for sn {sent_sn}, no matching response received.")
            return None

        except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
            # A sleeping device often drops or ignores a connection outside its
            # wake window. Treat connect timeouts like every other no-response
            # path so the coordinator can enter catch polling.
            self._disconnect()
            return None
        finally:
            self._disconnect()

    def _create_message(self, cmd: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Helper to create the JSON message structure."""
        sn = _get_sn()
        if cmd == CMD_SET:
            return {
                'pv': 0, 'cmd': cmd, 'sn': sn,
                'msg': {'attr': [int(k) for k in payload.keys()], 'data': payload}
            }
        elif cmd == CMD_QUERY:
            return {
                'pv': 0, 'cmd': cmd, 'sn': sn,
                'msg': {'attr': payload.get('attr', [QUERY_PROPERTIES])}
            }
        elif cmd == CMD_INFO:
            return {'pv': 0, 'cmd': cmd, 'sn': sn, 'msg': {}}
        else:
            raise ValueError(f"Unknown command: {cmd}")

    async def async_update_device_info(self) -> bool:
        """
        Fetches and updates device information locally.
        Gets DID, PID, and Device Type from CMD_INFO.
        Gets the full DPID list from CMD_QUERY.
        This method is now fully local and does not require a cloud connection.
        """
        # Step 1: Get basic device info (did, pid, dtp)
        info_msg = await self._send_receive(CMD_INFO, {})
        if not info_msg or not all(k in info_msg for k in ['did', 'pid', 'dtp']):
            _LOGGER.warning(f"Device info query failed or missing critical fields: {info_msg}")
            return False
        
        self._device_id = info_msg['did']
        self._pid = info_msg['pid']
        self._device_type_code = info_msg['dtp']
        model_info = get_model_info(self._pid)
        self._device_model_name = (
            model_info.model_name if model_info else f"CozyLife Device ({self._pid})"
        )

        # Step 2: Get the full list of supported DPIDs. Some metered plugs can
        # drop the first query during setup, so retry before falling back.
        query_msg = None
        for attempt in range(1, DPID_QUERY_ATTEMPTS + 1):
            query_msg = await self._send_receive(CMD_QUERY, {})
            if query_msg and ('attr' in query_msg or 'data' in query_msg):
                break
            _LOGGER.debug(
                "DPID query attempt %s/%s failed for %s: %s",
                attempt,
                DPID_QUERY_ATTEMPTS,
                self._ip_address,
                query_msg,
            )

        if not query_msg:
            _LOGGER.warning(f"Failed to query DPID list from device: {query_msg}")
            # We can still proceed with a minimal setup if this fails
            self._dpid = []
            return True # Return true as we have the basic info

        if 'attr' in query_msg:
            self._dpid = [str(dpid) for dpid in query_msg['attr']]
        elif 'data' in query_msg:
            self._dpid = [str(dpid) for dpid in query_msg['data']]
        else:
            self._dpid = []

        _LOGGER.info(
            f"Successfully discovered device {self._ip_address} locally: "
            f"DID={self.device_id}, PID={self.pid}, Type={self.device_type_code}, DPIDs={self.dpid}"
        )
        return True

    async def async_get_state(self, dpids: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """Query the current device status using CozyLife's status selector."""
        payload = {"attr": [QUERY_STATUS]}
        if dpids:
            payload["attr"] = [int(dpid) for dpid in dpids]

        msg = await self._send_receive(CMD_QUERY, payload)
        if msg and 'data' in msg:
            return {str(k): v for k, v in msg['data'].items()}
        return None

    async def async_set_state(self, attributes: Dict[str, Any]) -> bool:
        """Sets attributes on the device."""
        msg = await self._send_receive(CMD_SET, attributes)
        # For CMD_SET, the response msg contains the set attributes if successful.
        # We can compare it with the sent attributes for confirmation.
        if msg and 'data' in msg:
            # A more robust check would compare the sent attributes with the received
            # for exact match, but for now, just checking 'data' is present
            return True
        return False
