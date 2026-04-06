"""
MLB Live Scoreboard - Python Middleware
Polls statsapi.mlb.com every 30 seconds, formats a serial packet,
and sends it to the Arduino over USB serial.

Install dependencies:
    pip3 install pyserial requests

Usage:
    python3 mlb_serial.py

- Shows all games (live and final)
- Button on Arduino cycles through every game
- Favorite team loads first on startup if set
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
BAUD_RATE     = 9600
POLL_INTERVAL = 30      # seconds between API calls
FAVORITE_TEAM = ""      # e.g. "Cubs" or "NYY" — loads first on startup, leave blank to start at game 1
SERIAL_PORT   = ""      # e.g. "/dev/cu.usbmodem14101" on Mac, "COM3" on Windows
                        # leave blank to auto-detect

# -------------------------------------------------------
#  Auto-detect serial port
# -------------------------------------------------------
def find_arduino_port():
    ports = serial.tools.list_ports.comports()
    for port in ports:
        desc = (port.description or "").lower()
        mfr  = (port.manufacturer or "").lower()
        if "arduino" in desc or "arduino" in mfr or "ch340" in desc or "ftdi" in desc:
            return port.device
    if ports:
        return ports[0].device
    return None


# -------------------------------------------------------
#  Get team abbreviation safely
#  Falls back to first 3 letters of team name if abbr missing
# -------------------------------------------------------
def get_abbr(team_data):
    team = team_data.get("team", {})
    abbr = team.get("abbreviation")
    if abbr:
        return abbr[:3].upper()
    name = team.get("name", "???")
    return name[:3].upper()


# -------------------------------------------------------
#  Fetch today's games (live + final)
# -------------------------------------------------------
def get_games():
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
            status   = g.get("status", {})
            abstract = status.get("abstractGameState", "")
            detailed = status.get("detailedState", "")

            # Include live, final, and scheduled/preview games
            if abstract not in ("Live", "Final", "Preview"):
                continue

            linescore   = g.get("linescore", {})
            teams       = g.get("teams", {})
            away        = teams.get("away", {})
            home        = teams.get("home", {})

            away_abr   = get_abbr(away)
            home_abr   = get_abbr(home)
            away_score = away.get("score", 0)
            home_score = home.get("score", 0)

            inning      = linescore.get("currentInning", 1)
            inning_half = linescore.get("inningHalf", "Top")
            is_top      = "top" in inning_half.lower()

            balls   = linescore.get("balls", 0)
            strikes = linescore.get("strikes", 0)
            outs    = linescore.get("outs", 0)

            games.append({
                "away_team"  : away_abr,
                "away_score" : away_score,
                "home_team"  : home_abr,
                "home_score" : home_score,
                "balls"      : balls,
                "strikes"    : strikes,
                "outs"       : outs,
                "is_top"     : is_top,
                "inning"     : inning,
                "status"     : abstract,
                "detail"     : detailed,
            })

    # Sort: live games first, then final, then upcoming
    order = {"Live": 0, "Final": 1, "Preview": 2}
    games.sort(key=lambda g: order.get(g["status"], 3))

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
#  Find starting index — favorite team or 0
# -------------------------------------------------------
def find_start_index(games, favorite):
    if not favorite:
        return 0
    for i, g in enumerate(games):
        if favorite.upper() in g["away_team"].upper() or \
           favorite.upper() in g["home_team"].upper():
            return i
    return 0


# -------------------------------------------------------
#  Main loop
# -------------------------------------------------------
def main():
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

    time.sleep(2)
    print("[INFO] Connected. Starting poll loop...")

    current_idx = 0
    last_poll   = 0
    games       = []

    while True:
        now = time.time()

        # Check for button press from Arduino
        if ser.in_waiting:
            line = ser.readline().decode("utf-8", errors="ignore").strip()
            if line == "NEXT":
                if games:
                    current_idx = (current_idx + 1) % len(games)
                    game = games[current_idx]
                    packet = format_packet(game)
                    ser.write((packet + "\n").encode("utf-8"))
                    print(f"[NEXT] Game {current_idx + 1}/{len(games)}: {packet}  ({game['detail']})")
                else:
                    print("[INFO] Button pressed but no games loaded yet.")
            elif line == "ACK":
                pass

        # Poll API on interval
        if now - last_poll >= POLL_INTERVAL:
            last_poll = now
            print(f"[INFO] Polling MLB API at {datetime.now().strftime('%H:%M:%S')}...")
            fresh_games = get_games()

            if not fresh_games:
                print("[INFO] No games found today.")
                ser.write(b"NOGAMES\n")
            else:
                # On first load set starting index, after that keep current position
                if not games:
                    current_idx = find_start_index(fresh_games, FAVORITE_TEAM)

                games = fresh_games
                live_count  = sum(1 for g in games if g["status"] == "Live")
                final_count = sum(1 for g in games if g["status"] == "Final")
                print(f"[INFO] {len(games)} game(s): {live_count} live, {final_count} final.")

                # Clamp index in case game count changed
                current_idx = current_idx % len(games)

                game = games[current_idx]
                packet = format_packet(game)
                ser.write((packet + "\n").encode("utf-8"))
                print(f"[SEND] Game {current_idx + 1}/{len(games)}: {packet}  ({game['detail']})")

        time.sleep(0.1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[INFO] Stopped.")
