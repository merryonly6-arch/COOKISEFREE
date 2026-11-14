import os
import time
import threading
import requests
import re
from flask import Flask, render_template, request
from flask_socketio import SocketIO

app = Flask(__name__)
app.config['SECRET_KEY'] = 'vip_loader_secret_key'
socketio = SocketIO(app, cors_allowed_origins="*")

# Global variables to manage threads and running status
active_threads = {}
thread_status = {}

def get_tokens(session, convo_id):
    url = f"https://mbasic.facebook.com/messages/read/?tid=cid.g.{convo_id}"
    res = session.get(url)
    if "fb_dtsg" not in res.text:
        url = f"https://mbasic.facebook.com/messages/read/?tid=cid.c.{convo_id}"
        res = session.get(url)
        
    fb_dtsg = re.search(r'name="fb_dtsg" value="(.*?)"', res.text)
    jazoest = re.search(r'name="jazoest" value="(.*?)"', res.text)
    send_url = re.search(r'action="(/messages/send/.*?)"', res.text)
    
    if fb_dtsg and jazoest and send_url:
        return fb_dtsg.group(1), jazoest.group(1), "https://mbasic.facebook.com" + send_url.group(1).replace("&", "&")
    return None, None, None

def worker_task(task_id, cookies, convo_ids, prefix, messages, delay):
    thread_status[task_id] = {"status": "Running", "start_time": time.time()}
    session_list = []
    
    # Initialize sessions for all cookies
    for cookie in cookies:
        cookie = cookie.strip()
        if not cookie:
            continue
        s = requests.Session()
        s.headers.update({
            'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36',
            'Cookie': cookie
        })
        session_list.append(s)
        
    if not session_list:
        socketio.emit('log_message', {'task_id': task_id, 'data': '<span class="text-danger">[!] No valid cookies provided!</span>'})
        thread_status[task_id]["status"] = "Stopped"
        return

    socketio.emit('log_message', {'task_id': task_id, 'data': f'<span class="text-info">[*] Loader started with {len(session_list)} cookies & {len(convo_ids)} convos.</span>'})

    cookie_index = 0
    while task_id in active_threads and active_threads[task_id]:
        for convo_id in convo_ids:
            if task_id not in active_threads or not active_threads[task_id]:
                break
            
            current_session = session_list[cookie_index % len(session_list)]
            cookie_index += 1
            
            # Fetch tokens fresh or reuse lightly
            fb_dtsg, jazoest, post_url = get_tokens(current_session, convo_id)
            if not fb_dtsg:
                socketio.emit('log_message', {'task_id': task_id, 'data': f'<span class="text-warning">[!] Token Failed for Convo {convo_id} (Cookie might be invalid/checkpoint)</span>'})
                continue
                
            for msg in messages:
                if task_id not in active_threads or not active_threads[task_id]:
                    break
                msg = msg.strip()
                if not msg:
                    continue
                
                full_msg = f"{prefix} {msg}" if prefix else msg
                payload = {
                    'fb_dtsg': fb_dtsg,
                    'jazoest': jazoest,
                    'body': full_msg,
                    'send': 'Send',
                    'tids': f'cid.g.{convo_id}' if len(convo_id) > 12 else f'cid.c.{convo_id}',
                    'wwwupp': 'C3',
                    'platform_xmd': '',
                    'referrer': '',
                    'ctype': '',
                    'cver': 'legacy'
                }
                
                try:
                    resp = current_session.post(post_url, data=payload, timeout=10)
                    if resp.status_code == 200:
                        socketio.emit('log_message', {'task_id': task_id, 'data': f'<span class="text-success">[SENT] Convo: {convo_id} | Msg: {full_msg}</span>'})
                    else:
                        socketio.emit('log_message', {'task_id': task_id, 'data': f'<span class="text-danger">[FAIL] Convo: {convo_id} | Status: {resp.status_code}</span>'})
                except Exception as e:
                    socketio.emit('log_message', {'task_id': task_id, 'data': f'<span class="text-danger">[ERROR] Convo: {convo_id} | {str(e)}</span>'})
                
                time.sleep(delay)
        
        # Short pause between rounds
        time.sleep(2)

    thread_status[task_id]["status"] = "Stopped"
    socketio.emit('log_message', {'task_id': task_id, 'data': '<span class="text-danger">[*] Loader Stopped Successfully.</span>'})

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('start_loader')
def handle_start(data):
    task_id = request.sid
    cookies = data.get('cookies', '').split('\n')
    convo_ids = [c.strip() for c in data.get('convos', '').split(',') if c.strip()]
    prefix = data.get('prefix', '')
    messages = data.get('messages', '').split('\n')
    try:
        delay = float(data.get('delay', 5))
    except:
        delay = 5.0

    if task_id in active_threads and active_threads[task_id]:
        return

    active_threads[task_id] = True
    t = threading.Thread(target=worker_task, args=(task_id, cookies, convo_ids, prefix, messages, delay))
    t.start()

@socketio.on('stop_loader')
def handle_stop():
    task_id = request.sid
    if task_id in active_threads:
        active_threads[task_id] = False

@socketio.on('get_status')
def handle_status():
    task_id = request.sid
    if task_id in thread_status and thread_status[task_id]["status"] == "Running":
        uptime = int(time.time() - thread_status[task_id]["start_time"])
        mins, secs = divmod(uptime, 60)
        hours, mins = divmod(mins, 60)
        time_str = f"{hours:02d}:{mins:02d}:{secs:02d}"
        socketio.emit('status_response', {'status': 'Running', 'uptime': time_str})
    else:
        socketio.emit('status_response', {'status': 'Stopped', 'uptime': '00:00:00'})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port)
