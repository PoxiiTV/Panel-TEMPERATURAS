#!/usr/bin/env python3
# Mini PC — RECOPILADOR de sensores (host Proxmox, obligatorio: los contenedores
# no ven el hardware). Mantiene el pico maximo de temperatura de forma persistente
# registrándolo EN SEGUNDO PLANO (aunque nadie mire la web) y expone una API.
# El panel web vive en el LXC de webs (IP configurable) y consume esta API.
import json, os, re, subprocess, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8686
BIND = '0.0.0.0'
HW = '/sys/class/hwmon'
DISKS = ['/dev/sda', '/dev/sdb']
DISK_TTL = 30
SAMPLE_S = 5            # sampler: cada 5s (flojito)
PEAKS_FILE = '/opt/monitor/peaks.json'
DIR = '/opt/monitor'
HIST_MAX = 120          # historial de temperaturas (para la grafica de linea)

_lock = threading.Lock()
_disk_cache = {}
_disk_time = 0.0
_history = []           # lista de {t, cpu, gpu, sda, sdb}
HIST_START = time.time()

# ---------- Configuracion persistente (config.json) ----------
CONFIG_FILE = '/opt/monitor/config.json'
DEFAULT_CONFIG = {
    'alarm_threshold': 95.0,      # °C que dispara la alarma Telegram
    'alarm_follow': 20,           # segundos de seguimiento a 1Hz
    'alarm_hysteresis': 5.0,      # °C por debajo del umbral para re-armar
    'color_green': 75.0,          # temp maxima para verde (<= green = verde)
    'color_red': 80.0,            # temp maxima para amarillo; > red = rojo
    'temp_max': 95.0,             # referencia maxima para los gauges
    'update_ms': 1000,            # intervalo de actualizacion del panel (ms)
    'bot_token': '',              # token del bot de Telegram
    'chat_id': '',                # chat al que envia las alarmas
    'update_mode': 'auto',        # 'auto' | 'manual'
}
def _load_config():
    try:
        with open(CONFIG_FILE) as f: return json.load(f)
    except Exception: return dict(DEFAULT_CONFIG)
def save_config(cfg):
    tmp = CONFIG_FILE + '.tmp'
    with open(tmp,'w') as f: json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_FILE)
def merge_config(cfg):
    d = _load_config()
    for k,v in cfg.items():
        if k in DEFAULT_CONFIG: d[k] = v
    return d

def _load_peaks():
    try:
        with open(PEAKS_FILE) as f: return json.load(f)
    except Exception: return {}

def _save_peaks(p):
    try:
        tmp = PEAKS_FILE + '.tmp'
        with open(tmp,'w') as f: json.dump(p, f)
        os.replace(tmp, PEAKS_FILE)
    except Exception: pass

def _hwmon(name):
    try:
        for h in os.listdir(HW):
            if open(f'{HW}/{h}/name').read().strip() == name:
                return f'{HW}/{h}'
    except Exception: pass
    return None

def _read_temp(hw):
    d = _hwmon(hw)
    if not d: return None
    best = None
    try:
        for f in sorted(os.listdir(d)):
            if re.fullmatch(r'temp\d+_input', f):
                try: t = int(open(f'{d}/{f}').read().strip())/1000.0
                except Exception: continue
                best = t if best is None else max(best, t)
    except Exception: pass
    return best

def cpu_temp(): return _read_temp('k10temp')
def gpu_temp(): return _read_temp('amdgpu')

def disk_temps():
    global _disk_cache, _disk_time
    now = time.time()
    if now - _disk_time > DISK_TTL:
        out = {}
        for disk in DISKS:
            if not os.path.exists(disk): continue
            try:
                r = subprocess.run(['smartctl','-a',disk], capture_output=True, text=True, timeout=12)
                for line in r.stdout.splitlines():
                    if 'Temperature_Celsius' in line:
                        partes = line.split()
                        try:
                            dash = partes.index('-')
                            out[disk] = int(partes[dash+1].split('(')[0]); break
                        except (ValueError, IndexError):
                            nums = re.findall(r'-?\d+', line)
                            if len(nums) >= 8: out[disk] = int(nums[7]); break
                    elif 'Current Drive Temperature' in line:
                        g = re.findall(r'Current Drive Temperature:\s+(\d+)', line)
                        if g: out[disk] = int(g[0]); break
            except Exception: pass
        _disk_cache = out; _disk_time = now
    return _disk_cache

def cpu_usage():
    def read():
        with open('/proc/stat') as f:
            p = f.readline().split()
        v=[int(x) for x in p[1:]]; return v[3]+v[4], sum(v)
    try:
        i1,t1=read(); time.sleep(0.12); i2,t2=read()
        dt=t2-t1
        return round(100*(1-(i2-i1)/dt),1) if dt>0 else 0.0
    except Exception: return None

def loadavg():
    with open('/proc/loadavg') as f: return [float(x) for x in f.read().split()[:3]]

def mem():
    d={}
    with open('/proc/meminfo') as f:
        for line in f:
            k,v=line.split(':')[0],int(line.split(':')[1].split()[0]); d[k]=v
    total=d['MemTotal']/1024/1024; avail=d['MemAvailable']/1024/1024
    return {'total_gb':round(total,1),'avail_gb':round(avail,1),'used_gb':round(total-avail,1)}

def uptime():
    with open('/proc/uptime') as f: s=int(float(f.read().split()[0]))
    return f'{s//86400}d {(s%86400)//3600}h {(s%3600)//60}m'

def current():
    return {'cpu_temp':cpu_temp(),'gpu_temp':gpu_temp(),'disk':disk_temps(),
            'cpu_usage':cpu_usage(),'load':loadavg(),'mem':mem(),'uptime':uptime(),
            'ts':time.time()}

def _tick(peaks):
    global _history
    c = current()
    now = time.time()
    # registra historico (siempre, tambien sin picos)
    _history.append({'t': now, 'cpu': c['cpu_temp'], 'gpu': c['gpu_temp'],
                     'sda': c['disk'].get('/dev/sda'), 'sdb': c['disk'].get('/dev/sdb')})
    if len(_history) > HIST_MAX: _history = _history[-HIST_MAX:]
    sensors = {'cpu_temp': c['cpu_temp']}
    if c['gpu_temp'] is not None: sensors['gpu_temp'] = c['gpu_temp']
    for k,v in c['disk'].items(): sensors[k] = v
    for name, val in sensors.items():
        if val is None: continue
        cur = peaks.get(name)
        # Estricto: solo se actualiza al superar el pico registrado. Asi un reset
        # (que deja el pico en el valor actual) no se "reescribe" con == mismo valor.
        if cur is None or val > cur.get('v', -1):
            peaks[name] = {'v': round(float(val),1), 't': now}
    return c

def sampler_loop():
    # Recarca los picos del archivo en cada ciclo: asi respeta un reset hecho
    # desde la web (el archivo es la fuente de verdad compartida).
    while True:
        try:
            with _lock:
                peaks = _load_peaks()
                _tick(peaks)
                _save_peaks(peaks)
        except Exception: pass
        time.sleep(SAMPLE_S)

def reset_peaks():
    global _lock
    peaks = _load_peaks()
    c = current()
    now = time.time()
    n = {'cpu_temp': {'v': c['cpu_temp'] or 0, 't': now}}
    if c['gpu_temp'] is not None: n['gpu_temp'] = {'v': c['gpu_temp'], 't': now}
    for k,v in c['disk'].items(): n[k] = {'v': v, 't': now}
    with _lock:
        _save_peaks(n)
    return n

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body, ctype):
        self.send_response(code); self.send_header('Content-Type',ctype)
        self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(body))); self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        path=self.path.replace('?',' ').split()[0]
        if path=='/api/stats':
            try:
                with _lock:
                    c = current(); peaks = _load_peaks()
                    hist = list(_history)
                body=json.dumps({'ts':time.time(),'current':c,'peaks':peaks,'history':hist}).encode()
                self._send(200, body, 'application/json')
            except Exception as e:
                self._send(500, json.dumps({'error':str(e)}).encode(),'application/json')
        elif path=='/api/peaks':
            with _lock:
                body=json.dumps(_load_peaks()).encode()
            self._send(200, body, 'application/json')
        elif path=='/api/config':
            with _lock:
                body=json.dumps(_load_config()).encode()
            self._send(200, body, 'application/json')
        else:
            self._send(200, b'Mini PC collector OK', 'text/plain')
    def do_POST(self):
        path=self.path.replace('?',' ').split()[0]
        if path=='/api/reset':
            try:
                n=reset_peaks()
                self._send(200, json.dumps({'ok':True,'peaks':n}).encode(),'application/json')
            except Exception as e:
                self._send(500, json.dumps({'error':str(e)}).encode(),'application/json')
        elif path=='/api/config':
            try:
                ln = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(ln)) if ln else {}
                cfg = merge_config(body)
                save_config(cfg)
                self._send(200, json.dumps({'ok':True,'config':cfg}).encode(),'application/json')
            except Exception as e:
                self._send(500, json.dumps({'error':str(e)}).encode(),'application/json')
        elif path=='/api/test_alarm':
            try:
                cfg = _load_config()
                import urllib.request, urllib.parse
                tok = cfg.get('bot_token','')
                chat = cfg.get('chat_id','')
                if not tok or not chat:
                    self._send(400, json.dumps({'ok':False,'error':'Configura bot_token y chat_id primero'}).encode(),'application/json'); return
                data = urllib.parse.urlencode({'chat_id':chat,'text':'🧪 Prueba de alarma — funciona correctamente','parse_mode':'HTML'}).encode()
                req = urllib.request.Request(f'https://api.telegram.org/bot{tok}/sendMessage', data=data, method='POST')
                with urllib.request.urlopen(req, timeout=6) as r:
                    ok = r.status == 200
                self._send(200, json.dumps({'ok':ok}).encode(),'application/json')
            except Exception as e:
                self._send(500, json.dumps({'ok':False,'error':str(e)}).encode(),'application/json')
        else:
            self._send(404, b'Not found','text/plain')

# sampler persistente (registra picos aunque no se mire la web)
threading.Thread(target=sampler_loop, daemon=True).start()

if __name__=='__main__':
    print(f'Mini PC collector en {BIND}:{PORT}')
    ThreadingHTTPServer((BIND, PORT), H).serve_forever()