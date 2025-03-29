import subprocess, sys, logging, os, threading, time, json, platform, asyncio
from functools import wraps
from urllib.parse import urlparse

logging.getLogger().setLevel(logging.ERROR)
log = logging.getLogger("werkzeug").setLevel(logging.ERROR)
sys.stdout = sys.stderr = type('NullWriter', (), {'write': lambda s,x: None, 'flush': lambda s: None})() if "--debug" not in sys.argv else sys.stdout

def ensure_module_installed(module_name, package_name=None):
    try: return __import__(module_name)
    except ImportError:
        subprocess.call([sys.executable, "-m", "pip", "install", package_name or module_name])
        return __import__(module_name)

CORS = ensure_module_installed("flask_cors", "flask-cors").CORS
psutil = ensure_module_installed("psutil")
requests = ensure_module_installed("requests")
flask = ensure_module_installed("flask")
websockets = ensure_module_installed("websockets")
bluetooth = ensure_module_installed("bleak")
volume = ensure_module_installed("pyvolume", "volume-control")
from flask import Flask, request, jsonify, Response

app = Flask(__name__)
sys.modules["flask.cli"].show_server_banner = lambda *x: None

CORS(app, resources={r"/*": {"origins": "*"}})

METRICS_BROADCAST_INTERVAL = 1.0
BLUETOOTH_SCAN_INTERVAL = 10.0
BLUETOOTH_BROADCAST_INTERVAL = 5.0
ORIGINS_REFRESH_INTERVAL = 300.0
HEARTBEAT_INTERVAL = 10
ORIGINS_URL = "https://link.rotur.dev/allowed.json"

system_metrics_cache = {"cpu_percent": 0, "cpu_percent_per_core": [], "last_update": 0, "memory": {}, 
                       "disk": {}, "network": {}, "battery": {}, "bluetooth": []}
connected_clients = set()
ALLOWED_ORIGINS = ["https://turbowarp.org", "https://origin.mistium.com", "http://localhost:5001", "http://localhost:5002"]
ORIGINS_LAST_UPDATE = 0
BLUETOOTH_DEVICES = {}
BLUETOOTH_DEVICE_TTL = 300

def fetch_allowed_origins():
    global ALLOWED_ORIGINS, ORIGINS_LAST_UPDATE
    try:
        response = requests.get(ORIGINS_URL, timeout=5)
        origins_data = response.json()
        if isinstance(origins_data, dict) and "origins" in origins_data:
            new_origins = origins_data["origins"]
            for local in ["http://localhost:5001", "http://localhost:5002"]:
                if local not in new_origins: new_origins.append(local)
            ALLOWED_ORIGINS = new_origins
            ORIGINS_LAST_UPDATE = time.time()
            if "--debug" in sys.argv: print(f"[roturLink] Updated allowed origins: {ALLOWED_ORIGINS}")
        return ALLOWED_ORIGINS
    except Exception as e:
        if "--debug" in sys.argv: print(f"[roturLink] Error fetching origins: {str(e)}")
        return ALLOWED_ORIGINS

def is_origin_allowed(origin):
    return origin.startswith("http://localhost:") or origin in ALLOWED_ORIGINS or True

def isAllowed(request):
    origin = request.headers.get("Origin", "")
    return request.remote_addr in ("127.0.0.1", "::1") or is_origin_allowed(origin)

def get_system_metrics():
    return {
        "cpu": {"percent": system_metrics_cache["cpu_percent"], "per_core": system_metrics_cache["cpu_percent_per_core"]},
        "memory": system_metrics_cache["memory"],
        "disk": system_metrics_cache["disk"],
        "network": system_metrics_cache["network"],
        "battery": system_metrics_cache.get("battery", {}),
        "timestamp": time.time(),
    }

def get_system_info(detailed=False):
    bluetooth_available, bluetooth_version, bluetooth_adapters = False, "Unknown", []
    try:
        if platform.system() == "Linux":
            try:
                result = subprocess.run(["hcitool", "dev"], capture_output=True, text=True)
                if "hci" in result.stdout:
                    bluetooth_available = True
                    bluetooth_adapters = [line.strip() for line in result.stdout.split('\n') if "hci" in line]
                    bluetooth_version = "Classic + BLE"
            except: pass
        elif platform.system() == "Darwin":
            try:
                result = subprocess.run(["system_profiler", "SPBluetoothDataType"], capture_output=True, text=True)
                if "Bluetooth" in result.stdout:
                    bluetooth_available = True
                    bluetooth_adapters = ["CoreBluetooth Adapter"]
                    bluetooth_version = "BLE (CoreBluetooth)"
            except: pass
        elif platform.system() == "Windows":
            try:
                result = subprocess.run(["powershell", "-Command", "(Get-PnpDevice -Class Bluetooth).Count"], 
                                       capture_output=True, text=True)
                if result.stdout.strip() and int(result.stdout.strip()) > 0:
                    bluetooth_available = True
                    bluetooth_adapters = ["Windows Bluetooth Adapter"]
                    bluetooth_version = "BLE (Windows)"
            except: pass
    except Exception as e:
        if "--debug" in sys.argv: print(f"[roturLink] Bluetooth detection error: {str(e)}")
    
    sys_info = {
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "compiler": platform.python_compiler(),
        },
        "platform": {
            "system": platform.system(),
            "node": platform.node(),
            "release": platform.release(),
            "version": platform.version(),
            "architecture": platform.machine(),
        },
        "cpu": {
            "processor": platform.processor(),
            "cpu_cores": psutil.cpu_count(logical=False),
            "cpu_threads": psutil.cpu_count(logical=True),
        },
        "bluetooth": {
            "available": bluetooth_available,
            "version": bluetooth_version,
            "adapters": bluetooth_adapters,
            "adapter_count": len(bluetooth_adapters),
            "backend": "bleak" if hasattr(bluetooth, "__version__") else "bleak",
        },
        "memory": {
            "total_gb": round(psutil.virtual_memory().total / (1024**3), 2),
            "total": psutil.virtual_memory().total,
        },
        "hostname": platform.node(),
        "ip_addresses": [addr.address for iface in psutil.net_if_addrs().values() 
                        for addr in iface if addr.family == 2][:2],
    }
    
    if detailed:
        sys_info["environment"] = {
            "user": os.getenv("USER") or os.getenv("USERNAME"),
            "home": os.path.expanduser("~"),
            "path_separator": os.path.sep,
            "line_separator": os.linesep,
        }
    return sys_info

async def execute_command(command):
    try:
        process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = process.communicate()
        return {
            "status": "success",
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
            "exit_code": process.returncode,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

async def scan_bluetooth_devices():
    try:
        from bleak import BleakScanner
        devices = []
        discovered_devices = await BleakScanner.discover(timeout=3.0)
        
        current_time = int(time.time())
        active_addresses = set()
        
        for device in discovered_devices:
            device_info = {"name": device.name or "Unknown", "address": device.address, "rssi": None}
            if hasattr(device, 'advertisement_data'):
                device_info["rssi"] = getattr(device.advertisement_data, 'rssi', None)
            if device_info["rssi"] is None and hasattr(device, '_rssi'):
                device_info["rssi"] = getattr(device, '_rssi', None)

            BLUETOOTH_DEVICES[device.address] = {
                "name": device_info["name"],
                "address": device.address,
                "rssi": device_info["rssi"],
                "last_seen": current_time
            }
            active_addresses.add(device.address)
        
        for address, device_data in BLUETOOTH_DEVICES.items():
            if address not in active_addresses:
                device_data["rssi"] = -90
            
            devices.append({
                "name": device_data["name"],
                "address": device_data["address"],
                "rssi": device_data["rssi"],
                "last_seen": device_data["last_seen"]
            })
        
        addresses_to_remove = [addr for addr, data in BLUETOOTH_DEVICES.items() 
                              if int(current_time) - data["last_seen"] > BLUETOOTH_DEVICE_TTL]
        for addr in addresses_to_remove:
            if "--debug" in sys.argv:
                print(f"[roturLink] Removing stale Bluetooth device: {BLUETOOTH_DEVICES[addr]['name']} ({addr})")
            del BLUETOOTH_DEVICES[addr]
            
        return devices
    except Exception as e:
        if "--debug" in sys.argv: print(f"[roturLink] Bluetooth scan error: {str(e)}")
        return [{"name": data["name"], "address": data["address"], "rssi": -90} 
                for data in BLUETOOTH_DEVICES.values()]

async def send_to_client(ws, message):
    try:
        await ws.send(json.dumps(message))
        return True
    except Exception as e:
        if "--debug" in sys.argv and isinstance(e, websockets.exceptions.ConnectionClosed):
            print("[roturLink] Connection closed when sending message")
        elif "--debug" in sys.argv:
            print(f"[roturLink] Error sending message: {str(e)}")
        return False

async def broadcast_to_all_clients(message):
    disconnected = []
    for client in connected_clients:
        if not await send_to_client(client, message): disconnected.append(client)
    for client in disconnected:
        if client in connected_clients: connected_clients.remove(client)

async def update_metrics_cache():
    while True:
        try:
            system_metrics_cache["cpu_percent"] = psutil.cpu_percent(interval=0.5)
            system_metrics_cache["cpu_percent_per_core"] = psutil.cpu_percent(percpu=True, interval=0.1)
            memory = psutil.virtual_memory()
            system_metrics_cache["memory"] = {
                "total": memory.total, "available": memory.available,
                "percent": memory.percent, "used": memory.used, "free": memory.free
            }
            disk = psutil.disk_usage("/")
            system_metrics_cache["disk"] = {
                "total": disk.total, "used": disk.used,
                "free": disk.free, "percent": disk.percent
            }
            network = psutil.net_io_counters()
            system_metrics_cache["network"] = {
                "sent": network.bytes_sent, "received": network.bytes_recv,
                "packets_sent": network.packets_sent, "packets_received": network.packets_recv
            }
            if hasattr(psutil, "sensors_battery"):
                battery = psutil.sensors_battery()
                if battery:
                    system_metrics_cache["battery"] = {
                        "percent": battery.percent if hasattr(battery, "percent") else 0,
                        "power_plugged": battery.power_plugged if hasattr(battery, "power_plugged") else False
                    }
            await asyncio.sleep(1)
        except Exception as e:
            if "--debug" in sys.argv: print(f"[roturLink] Metrics cache error: {str(e)}")
            await asyncio.sleep(2)

async def broadcast_metrics_task():
    while True:
        if connected_clients:
            await broadcast_to_all_clients({"cmd": "metrics_update", "val": get_system_metrics()})
        await asyncio.sleep(METRICS_BROADCAST_INTERVAL)

async def broadcast_bluetooth_task():
    while True:
        if connected_clients:
            try:
                devices = await scan_bluetooth_devices()
                system_metrics_cache["bluetooth"] = devices
                system_metrics_cache["last_bluetooth_scan"] = time.time()
                
                await broadcast_to_all_clients({
                    "cmd": "bluetooth_update",
                    "val": {
                        "bluetooth": {
                            "devices": devices, 
                            "count": len(devices), 
                            "active_count": sum(1 for device in devices if device.get("rssi", -90) > -90),
                            "timestamp": time.time(),
                        }
                    }
                })
                if "--debug" in sys.argv and len(devices) > 0:
                    active_count = sum(1 for device in devices if device.get("rssi", -90) > -90)
                    print(f"[roturLink] Broadcasting {len(devices)} BT devices ({active_count} active, to {len(connected_clients)} clients")
            except Exception as e:
                if "--debug" in sys.argv: print(f"[roturLink] Bluetooth broadcast error: {str(e)}")
        await asyncio.sleep(BLUETOOTH_BROADCAST_INTERVAL)

async def refresh_origins_task():
    while True:
        try: fetch_allowed_origins()
        except Exception as e:
            if "--debug" in sys.argv: print(f"[roturLink] Origins refresh error: {str(e)}")
        await asyncio.sleep(ORIGINS_REFRESH_INTERVAL)

async def handle_command(websocket, message):
    try:
        if not isinstance(message, dict):
            if isinstance(message, str):
                try: message = json.loads(message)
                except json.JSONDecodeError:
                    return await send_to_client(websocket, {
                        "cmd": "error", "val": {"message": "Invalid JSON format"}
                    })
            else:
                return await send_to_client(websocket, {
                    "cmd": "error", "val": {"message": f"Invalid type: {type(message).__name__}"}
                })
        
        cmd, val = message.get("cmd"), message.get("val", {})
        if not cmd:
            return await send_to_client(websocket, {
                "cmd": "error", "val": {"message": "Missing 'cmd' field"}
            })
            
        if cmd == "ping":
            await send_to_client(websocket, {"cmd": "pong", "val": {"timestamp": time.time()}})
        else:
            await send_to_client(websocket, {
                "cmd": "error", "val": {"message": f"Unknown command: {cmd}"}
            })
    except Exception as e:
        if "--debug" in sys.argv: print(f"[roturLink] Command handler error: {str(e)}")
        await send_to_client(websocket, {
            "cmd": "error", "val": {"message": f"Command error: {str(e)}"}
        })

async def handler(websocket):
    request_headers = websocket.request.headers

    origin = request_headers.get("origin", "")
    client_ip = websocket.remote_address[0] if hasattr(websocket, 'remote_address') else "unknown"
    
    is_local = client_ip in ("127.0.0.1", "::1")
    if not (is_origin_allowed(origin)):
        if "--debug" in sys.argv: print(f"[roturLink] Rejected: {client_ip}, origin: {origin}")
        return
    
    if "--debug" in sys.argv: print(f"[roturLink] New connection: {client_ip}, origin: {origin}")
    connected_clients.add(websocket)
    
    try:
        await send_to_client(websocket, {
            "cmd": "handshake", "val": {"server": "rotur-websocket", "version": "1.0.0"}
        })
        await send_to_client(websocket, {"cmd": "metrics", "val": get_system_metrics()})
        
        async for message in websocket:
            try:
                data = json.loads(message) if isinstance(message, str) else message
                await handle_command(websocket, data)
            except json.JSONDecodeError:
                if "--debug" in sys.argv: print(f"[roturLink] Invalid JSON: {message}")
                await send_to_client(websocket, {
                    "cmd": "error", "val": {"message": "Invalid JSON format"}
                })
            except Exception as e:
                if "--debug" in sys.argv: print(f"[roturLink] Message error: {str(e)}")
                await send_to_client(websocket, {
                    "cmd": "error", "val": {"message": f"Error: {str(e)}"}
                })
    except websockets.exceptions.ConnectionClosed:
        if "--debug" in sys.argv: print(f"[roturLink] Connection closed: {client_ip}")
    except Exception as e:
        if "--debug" in sys.argv: print(f"[roturLink] Connection error: {str(e)}")
    finally:
        if websocket in connected_clients:
            connected_clients.remove(websocket)
            if "--debug" in sys.argv:
                print(f"[roturLink] Client {client_ip} removed. {len(connected_clients)} clients left")

async def start_websocket_server():
    try:
        fetch_allowed_origins()
        asyncio.create_task(refresh_origins_task())
        asyncio.create_task(broadcast_metrics_task())
        asyncio.create_task(broadcast_bluetooth_task())
        
        async with websockets.serve(
            handler, 
            "127.0.0.1", 
            5002, 
            ping_interval=None,
            origins=None
        ):
            if "--debug" in sys.argv: print(f"[roturLink] WebSocket server at ws://127.0.0.1:5002")
            await asyncio.Future()
    except Exception as e:
        if "--debug" in sys.argv: print(f"[roturLink] WebSocket server error: {str(e)}")

def run_websocket_server():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(start_websocket_server())

def cache_result(timeout=5):
    def decorator(func):
        cache = {}
        @wraps(func)
        def wrapper(*args, **kwargs):
            now, key = time.time(), json.dumps((args, sorted(kwargs.items())))
            if key in cache and now - cache[key]["timestamp"] < timeout:
                return cache[key]["result"]
            result = func(*args, **kwargs)
            cache[key] = {"result": result, "timestamp": now}
            return result
        return wrapper
    return decorator

@app.route("/rotur", methods=["GET"])
def ping():
    return "true", 200, {'Access-Control-Allow-Origin': '*'}

@app.route("/run", methods=["GET", "OPTIONS"])
def run():
    if request.method == "OPTIONS":
        return handle_preflight()
        
    if not isAllowed(request): return {"status": "error", "message": "Unauthorized"}, 403
    command = request.args.get("command")
    if not command: return {"status": "error", "message": "No command provided"}, 400

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(execute_command(command))
        loop.close()
        
        if result["status"] == "success":
            return {
                "status": "success",
                "data": {
                    "stdout": result["stdout"],
                    "stderr": result["stderr"],
                    "exit_code": result["exit_code"],
                },
            }, 200
        else:
            return {"status": "error", "message": result["message"]}, 500
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500

@app.route("/sysobj", methods=["GET", "OPTIONS"])
@cache_result(timeout=1)
def sysObj():
    if request.method == "OPTIONS":
        return handle_preflight()
        
    if not isAllowed(request): return {"status": "error", "message": "Unauthorized"}, 403
    try:
        return {"status": "success", "data": get_system_metrics()}, 200
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500

@app.route("/eval", methods=["GET", "OPTIONS"])
def eval_code():
    if request.method == "OPTIONS":
        return handle_preflight()
        
    if not isAllowed(request): return {"status": "error", "message": "Unauthorized"}, 403
    code = request.args.get("code")
    if not code: return {"status": "error", "message": "No code provided"}, 400
    try:
        return {"status": "success", "return": eval(code)}, 200
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500

@app.route("/proxy", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
def cors():
    if request.method == "OPTIONS":
        return handle_preflight()
        
    url = request.args.get('url')
    if not url: return jsonify({"error": "URL parameter is missing"}), 400
    
    try:
        method = request.method
        headers = {key: value for key, value in request.headers if key.lower() not in 
                 ['host', 'content-length', 'connection', 'origin', 'referer']}
        
        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            params=request.args,
            data=request.get_data(),
            timeout=10,
            allow_redirects=True
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        return jsonify({"error": str(e)}), 500

    proxy_response = Response(response.content)
    
    if 'Content-Type' in response.headers:
        proxy_response.headers['Content-Type'] = response.headers['Content-Type']
    
    proxy_response.headers['Access-Control-Allow-Origin'] = '*'
    proxy_response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, PATCH, OPTIONS'
    proxy_response.headers['Access-Control-Allow-Headers'] = '*'
    
    return proxy_response, response.status_code

@app.route("/sysinfo", methods=["GET", "OPTIONS"])
@cache_result(timeout=30)
def system_info():
    if request.method == "OPTIONS":
        return handle_preflight()
        
    if not isAllowed(request): return {"status": "error", "message": "Unauthorized"}, 403
    try:
        return {"status": "success", "data": get_system_info(detailed=True)}, 200
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500

def handle_preflight():
    response = app.response_class(
        response="",
        status=200
    )
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, PATCH, OPTIONS'
    response.headers['Access-Control-Max-Age'] = '86400'
    return response

if __name__ == "__main__":
    fetch_allowed_origins()
    app.config['CORS_ORIGINS'] = ALLOWED_ORIGINS
    
    threading.Thread(target=run_websocket_server, daemon=True).start()
    
    threading.Thread(target=lambda: asyncio.run(update_metrics_cache()), daemon=True).start()
    
    app.run(host="127.0.0.1", port=5001, debug=False)
