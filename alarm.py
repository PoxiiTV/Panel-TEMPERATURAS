#!/usr/bin/env python3
# Alarma de temperatura -> Telegram (este chat).
# Lee la configuracion de /opt/monitor/config.json (editada desde el panel).
# Si la CPU supera threshold, envia un aviso y luego la temp cada segundo
# durante follow segundos. Histeresis para no re-alarmar en bucle.
import time, json, urllib.request, urllib.parse, re, glob, os

CONFIG_FILE = '/opt/monitor/config.json'

def get_cfg():
    try:
        with open(CONFIG_FILE) as f: return json.load(f)
    except Exception: return {}

def threshold(): return float(get_cfg().get('alarm_threshold', 95.0))
def follow():    return int(get_cfg().get('alarm_follow', 20))
def hyster():    return float(get_cfg().get('alarm_hysteresis', 5.0))
def token():
    # 1ra: config.json del panel; 2da: archivo token directo (fallback)
    t = get_cfg().get('bot_token', '')
    if t: return t
    for p in ['/opt/monitor/tg_token.txt', '/opt/monitor/alarm_token.txt']:
        try:
            tt = open(p).read().strip()
            if tt: return tt
        except Exception: pass
    return None
def chat_id(): return str(get_cfg().get('chat_id', '') or '1100299662')

def cpu_temp():
    try:
        r = urllib.request.urlopen('http://127.0.0.1:8686/api/stats', timeout=3)
        return json.loads(r.read()).get('current', {}).get('cpu_temp')
    except Exception:
        pass
    try:
        for h in glob.glob('/sys/class/hwmon/hwmon*'):
            if open(h + '/name').read().strip() == 'k10temp':
                best = None
                for f in glob.glob(h + '/temp*_input'):
                    try: t = int(open(f).read().strip()) / 1000.0
                    except Exception: continue
                    best = t if best is None else max(best, t)
                return best
    except Exception:
        pass
    return None

def send_tg(text):
    tk = token()
    if not tk: return False
    try:
        data = urllib.parse.urlencode({'chat_id': chat_id(), 'text': text, 'parse_mode': 'HTML'}).encode()
        req = urllib.request.Request(f'https://api.telegram.org/bot{tk}/sendMessage', data=data, method='POST')
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status == 200
    except Exception:
        return False

def run():
    armed = False
    while True:
        thr = threshold()
        fol = follow()
        hys = hyster()
        temp = cpu_temp()
        if temp is None:
            time.sleep(1); continue
        if not armed and temp >= thr:
            armed = True
            send_tg(f'🚨 <b>ALARMA temperatura</b>\nCPU a <b>{temp:.1f}°C</b> (umbral {thr:.0f}°)\nSeguimiento {fol} s…')
            for _ in range(fol):
                t = cpu_temp()
                if t is not None:
                    color = '🔴' if t >= thr else '🟠'
                    send_tg(f'{color} CPU: <b>{t:.1f}°C</b>')
                time.sleep(1)
            send_tg('✅ Fin del seguimiento.')
        elif armed and temp < thr - hys:
            armed = False
        time.sleep(0.5)

if __name__ == '__main__':
    print('Alarma termica v2 (configurable desde el panel):', threshold(), '°C')
    run()