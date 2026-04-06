"""
MLB Live Scoreboard - Python Middleware
Polls statsapi.mlb.com every 30 seconds, formats a serial packet,
and sends it to the Arduino over USB serial.

Install dependencies:
    pip install pyserial requests

Usage:
    python mlb_serial.py

Optional: hardcode your favorite team below so the scoreboard
defaults to that game on startup without needing the button.
"""

import serial
import serial.tools.list_ports
import requests
import time
import sys
from datetime import datetime

# -------------------------------------------------------
#  Config
# -------------------------------------------------------
BAUD_RATE       = 9600
POLL_INTERVAL   = 30        # seconds between API calls
FAVORITE_TEAM   = ""        # e.g. "Cubs", "Yankees" — leave blank to show all live games
SERIAL_PORT     = ""        # e.g. "COM3" on Windows, "/dev/ttyUSB0" on Linux/Mac
                            # leave blank to auto-detect

# -------------------------------------------------------
#  Auto-detect serial port (looks for Arduino)
# -------------------------------------------------------
def find_arduino_port():
    ports = serial.tools.list_ports.comports()
    for port in ports:
        desc = (port.description or "").lower()
        mfr  = (port.manufacturer or "").lower()
        if "arduino" in desc or "arduino" in mfr or "ch340" in desc or "ftdi" in desc:
            return port.device
    # Fallback: return first available port
    if ports:
        return ports[0].device
    return None


# -------------------------------------------------------
#  Fetch today's live games from MLB Stats API
# -------------------------------------------------------
def get_live_games():
    today = datetime.now().strftime("%Y-%m-%d")
    url   = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={today}&hydrate=linescore"

    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"[ERROR] API request failed: {e}")
        return []

    games = []
    for date_entry in data.get("dates", []):
        for g in date_entry.get("games", []):
            status      = g.get("status", {})
            abstract    = status.get("abstractGameState", "")
            detailed    = status.get("detailedState", "")

            # Only include live or recently completed games
            if abstract not in ("Live", "Final"):
                continue

            linescore   = g.get("linescore", {})
            teams       = g.get("teams", {})
            away        = teams.get("away", {})
            home        = teams.get("home", {})

            away_abr    = away.get("team", {}).get("abbreviation", "???")
            home_abr    = home.get("team", {}).get("abbreviation", "???")
            away_score  = away.get("score", 0)
            home_score  = home.get("score", 0)

            inning      = linescore.get("currentInning", 1)
            inning_half = linescore.get("inningHalf", "Top")
            is_top      = "top" in inning_half.lower()

            offense     = linescore.get("offense", {})
            defense     = linescore.get("defense", {})
            balls       = linescore.get("balls", 0)
            strikes     = linescore.get("strikes", 0)
            outs        = linescore.get("outs", 0)

            games.append({
                "away_team"  : away_abr[:3].upper(),
                "away_score" : away_score,
                "home_team"  : home_abr[:3].upper(),
                "home_score" : home_score,
                "balls"      : balls,
                "strikes"    : strikes,
                "outs"       : outs,
                "is_top"     : is_top,
                "inning"     : inning,
                "status"     : abstract,
                "detail"     : detailed,
            })

    return games


# -------------------------------------------------------
#  Format serial packet
#  Example: NYM,4,LAD,2,B3,S2,O1,T7
# -------------------------------------------------------
def format_packet(game):
    half = "T" if game["is_top"] else "B"
    return (
        f"{game['away_team']},"
        f"{game['away_score']},"
        f"{game['home_team']},"
        f"{game['home_score']},"
        f"B{game['balls']},"
        f"S{game['strikes']},"
        f"O{game['outs']},"
        f"{half}{game['inning']}"
    )


# -------------------------------------------------------
#  Find favorite team game, fallback to first live game
# -------------------------------------------------------
def select_game(games, favorite, current_idx):
    if not games:
        return None, 0

    # If a favorite team is set, try to find their game first
    if favorite:
        for i, g in enumerate(games):
            if favorite.upper() in g["away_team"].upper() or \
               favorite.upper() in g["home_team"].upper():
                return g, i

    # Otherwise return game at current index (wraps around)
    idx = current_idx % len(games)
    return games[idx], idx


# -------------------------------------------------------
#  Main loop
# -------------------------------------------------------
def main():
    # Resolve serial port
    port = SERIAL_PORT or find_arduino_port()
    if not port:
        print("[ERROR] No serial port found. Plug in your Arduino or set SERIAL_PORT manually.")
        sys.exit(1)

    print(f"[INFO] Connecting to Arduino on {port} at {BAUD_RATE} baud...")
    try:
        ser = serial.Serial(port, BAUD_RATE, timeout=1)
    except serial.SerialException as e:
        print(f"[ERROR] Could not open serial port: {e}")
        sys.exit(1)

    # Give Arduino time to reset after serial connection
    time.sleep(2)
    print("[INFO] Connected. Starting poll loop...")

    current_game_idx = 0
    last_poll        = 0
    games            = []

    while True:
        now = time.time()

        # Check for incoming serial from Arduino (button press)
        if ser.in_waiting:
            line = ser.readline().decode("utf-8", errors="ignore").strip()
            if line == "NEXT":
                current_game_idx += 1
                print(f"[INFO] Button pressed — switching to game index {current_game_idx}")
                # Send updated game immediately without waiting for next poll
                if games:
                    game, current_game_idx = select_game(games, FAVORITE_TEAM, current_game_idx)
                    if game:
                        packet = format_packet(game)
                        ser.write((packet + "\n").encode("utf-8"))
                        print(f"[SEND] {packet}")
            elif line == "ACK":
                pass  # Arduino acknowledged last packet, nothing to do

        # Poll API on interval
        if now - last_poll >= POLL_INTERVAL:
            last_poll = now
            print(f"[INFO] Polling MLB API at {datetime.now().strftime('%H:%M:%S')}...")
            games = get_live_games()

            if not games:
                print("[INFO] No live or recent games found.")
                ser.write(b"NOGAMES\n")
            else:
                print(f"[INFO] {len(games)} game(s) found.")
                game, current_game_idx = select_game(games, FAVORITE_TEAM, current_game_idx)
                if game:
                    packet = format_packet(game)
                    ser.write((packet + "\n").encode("utf-8"))
                    print(f"[SEND] {packet}  ({game['detail']})")

        time.sleep(0.1)  # tight loop with small sleep to stay responsive to button


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[INFO] Stopped.")