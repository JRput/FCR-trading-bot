#!/usr/bin/env python3
"""Monitor the TSLA trading bot progress"""
import requests
import time
import json
from datetime import datetime

def get_bot_status():
    try:
        r = requests.get('http://localhost:5001/api/bot/status', timeout=2)
        if r.status_code == 200:
            return r.json()
    except:
        pass
    return None

def restart_bot():
    print("=" * 60)
    print("RESTARTING BOT...")
    print("=" * 60)
    
    # Stop
    try:
        r = requests.post('http://localhost:5001/api/bot/stop', timeout=2)
        print(f"Stop: {r.json().get('message', 'OK')}")
    except Exception as e:
        print(f"Stop error: {e}")
    
    time.sleep(2)
    
    # Start
    try:
        r = requests.post('http://localhost:5001/api/bot/start', timeout=2)
        print(f"Start: {r.json().get('message', 'OK')}")
    except Exception as e:
        print(f"Start error: {e}")
    
    print()

def monitor_bot(duration=60):
    print("=" * 60)
    print("MONITORING BOT PROGRESS")
    print("=" * 60)
    print()
    
    start_time = time.time()
    last_ws_state = {}
    
    while time.time() - start_time < duration:
        status = get_bot_status()
        if not status:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Cannot connect to bot API")
            time.sleep(5)
            continue
        
        ws = status.get('websocket', {})
        current_ws_state = {
            'enabled': ws.get('enabled', False),
            'connected': ws.get('connected', False),
            'authenticated': ws.get('authenticated', False),
            'bars': ws.get('bars_in_buffer', 0)
        }
        
        # Only print if state changed
        if current_ws_state != last_ws_state:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Bot Status:")
            print(f"  Running: {status.get('running', False)}")
            print(f"  State: {status.get('state', 'unknown')}")
            print(f"  WebSocket:")
            print(f"    Enabled: {current_ws_state['enabled']}")
            print(f"    Connected: {current_ws_state['connected']}")
            print(f"    Authenticated: {current_ws_state['authenticated']}")
            print(f"    Bars in buffer: {current_ws_state['bars']}")
            
            if status.get('first_candle'):
                fc = status['first_candle']
                print(f"  First Candle: High=${fc.get('high', 0):.2f}, Low=${fc.get('low', 0):.2f}")
            
            print()
            last_ws_state = current_ws_state
        
        time.sleep(3)

if __name__ == '__main__':
    restart_bot()
    monitor_bot(60)

