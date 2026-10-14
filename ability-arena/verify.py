"""Headless browser verification with the standard-library Chrome DevTools client."""
import base64
import json
import os
import socket
import struct
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent

class DevTools:
    def __init__(self, url):
        from urllib.parse import urlparse
        u = urlparse(url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=15)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(f'GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n'.encode())
        response = b''
        while b'\r\n\r\n' not in response:
            response += self.sock.recv(1)
        assert response.startswith(b'HTTP/1.1 101 ')
        self.counter = 0

    def read(self, count):
        data = b''
        while len(data) < count:
            part = self.sock.recv(count-len(data))
            if not part:
                raise RuntimeError('Browser disconnected')
            data += part
        return data

    def call(self, method, params=None):
        self.counter += 1
        payload = json.dumps(dict(id=self.counter, method=method, params=params or {})).encode()
        mask = os.urandom(4)
        size = len(payload)
        header = bytes([0x81, 0x80 | (size if size < 126 else 126 if size < 65536 else 127)])
        if size >= 126:
            header += struct.pack('!H' if size < 65536 else '!Q', size)
        self.sock.sendall(header + mask + bytes(v ^ mask[i % 4] for i,v in enumerate(payload)))
        while True:
            h = self.read(2)
            n = h[1] & 127
            if n == 126: n = struct.unpack('!H',self.read(2))[0]
            elif n == 127: n = struct.unpack('!Q',self.read(8))[0]
            r = json.loads(self.read(n))
            if r.get('id') == self.counter:
                if 'error' in r: raise RuntimeError(r['error'])
                return r.get('result', {})

    def evaluate(self, expression):
        r = self.call('Runtime.evaluate',dict(expression=expression, returnByValue=True, awaitPromise=True))
        if 'exceptionDetails' in r: raise RuntimeError(r['exceptionDetails'])
        return r.get('result',{}).get('value')

process = subprocess.Popen([r'C:\Program Files\Google\Chrome\Application\chrome.exe','--headless','--disable-gpu','--no-first-run','--no-default-browser-check','--remote-debugging-port=9341','--user-data-dir='+str(ROOT / '.browser-verification'),'about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
client = None
try:
    for _ in range(100):
        try:
            with urllib.request.urlopen('http://127.0.0.1:9341/json',timeout=1) as r: pages = json.load(r)
            page = next(p for p in pages if p['type']=='page')
            break
        except Exception: time.sleep(.1)
    else: raise RuntimeError('Chrome did not start')
    client = DevTools(page['webSocketDebuggerUrl'])
    client.call('Page.enable')
    client.call('Page.navigate',dict(url=(ROOT/'dist/index.html').as_uri()))
    for _ in range(100):
        if client.evaluate("document.readyState === 'complete' && document.querySelectorAll('.metric').length===4"): break
        time.sleep(.1)
    def check(label, expression):
        assert client.evaluate(expression), label
        print('PASS:',label,flush=True)
    check('Dashboard renders',"document.querySelectorAll('.metric').length===4 && $('course-rows').children.length===4")
    check('Search filters records',"$('search').value='security'; $('search').oninput(); $('course-rows').children.length===1 && $('course-rows').textContent.includes('Information security')")
    check('Draft filter',"$('search').value=''; $('status-filter').value='Draft'; $('status-filter').onchange(); $('course-rows').textContent.includes('Advanced project management') && $('course-rows').children.length===1")
    client.evaluate("$('status-filter').value='all'; $('status-filter').onchange(); $('next').click()")
    check('Pagination',"$('course-rows').children.length===2 && $('page-number').textContent==='2'")
    client.evaluate("$('create').click(); $('training-name').value='Browser test training'; $('create-form').requestSubmit()")
    check('Training creation',"courses.length===7 && !$('dialog').open && $('course-rows').textContent.includes('Browser test training')")
    client.evaluate("courseDetail(courses[0].id); $('toggle-course').click()")
    check('Training publishing',"courses[0].status==='Published'")
    client.evaluate('location.reload()')
    time.sleep(.5)
    check('Local persistence',"courses[0].name==='Browser test training' && courses[0].status==='Published'")
    check('Reporting period',"$('period').value='7'; $('period').onchange(); $('chart-total').textContent==='479' && $('donut-value').textContent==='81%'")
    check('Chart toggle',"document.querySelector('[data-chart=completion]').click(); chartMode==='completion' && $('chart-legend').textContent==='Completions'")
    check('CSV export',"(() => {let b; const old=URL.createObjectURL; URL.createObjectURL=x=>{b=x; return old(x)}; exportCSV(); URL.createObjectURL=old; return b.text().then(t=>t.includes('Browser test training') && t.includes('Progress (%)'));})()")
    check('Management navigation',"navigate('Training management'); $('overview-content').style.display==='none' && $('page-title').textContent.includes('Training management')")
    client.evaluate("navigate('Overview'); openModule('User management')")
    check('Module details',"$('dialog').open && $('dialog-content').textContent.includes('Priya Sharma')")
    client.evaluate("$('dialog').close(); localStorage.removeItem('aa-courses'); localStorage.removeItem('aa-period'); location.reload()")
    time.sleep(.5)
    for width,height,name in [(1440,1080,'desktop-preview.png'),(390,900,'mobile-preview.png')]:
        client.call('Emulation.setDeviceMetricsOverride',dict(width=width,height=height,deviceScaleFactor=1,mobile=width<500))
        time.sleep(.4)
        check(f'No overflow at {width}px','document.documentElement.scrollWidth <= window.innerWidth')
        screenshot=client.call('Page.captureScreenshot',dict(format='png',captureBeyondViewport=False))
        (ROOT/name).write_bytes(base64.b64decode(screenshot['data']))
    print('All browser checks passed.',flush=True)
finally:
    if client:
        try: client.call('Browser.close')
        except Exception: pass
    try: process.wait(timeout=5)
    except subprocess.TimeoutExpired: process.terminate()
