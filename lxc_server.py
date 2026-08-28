#!/usr/bin/env python3
# PANEL web. Sirve index.html y hace de puente
# al recopilador de sensores del host (IP configurable).
import json, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8787
BIND = '0.0.0.0'
# IP del recopilador (host). Ajusta a tu red.
HOST = 'http://HOST_IP_RECOPILADOR:8686'
HTML = '/opt/minipc-panel/index.html'

def proxy(api_path, method='GET', body=None, content_type='application/json'):
    url = HOST + api_path
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method,
        headers={'Content-Type': content_type}) if data else urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return 502, json.dumps({'error': str(e)}).encode()

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _read_body(self):
        ln = int(self.headers.get('Content-Length', 0))
        return self.rfile.read(ln) if ln else None
    def _send(self, code, body, ctype):
        self.send_response(code); self.send_header('Content-Type', ctype)
        self.send_header('Cache-Control', 'no-store'); self.send_header('Content-Length', str(len(body))); self.end_headers()
        self.wfile.write(body)
    def _handle(self, path, method):
        if path.startswith('/api/'):
            body = self._read_body()
            code, rbody = proxy(path, method, body)
            self._send(code, rbody, 'application/json')
        else:
            try:
                with open(HTML, 'rb') as f: body = f.read()
                self._send(200, body, 'text/html; charset=utf-8')
            except Exception:
                self._send(500, b'index.html no encontrado', 'text/plain')
    def do_GET(self):
        self._handle(self.path.replace('?', ' ').split()[0], 'GET')
    def do_POST(self):
        self._handle(self.path.replace('?', ' ').split()[0], 'POST')

if __name__ == '__main__':
    print(f'Panel en {BIND}:{PORT} -> host {HOST}')
    ThreadingHTTPServer((BIND, PORT), H).serve_forever()