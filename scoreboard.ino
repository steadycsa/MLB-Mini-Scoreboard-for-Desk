/*
  MLB Live Scoreboard
  Arduino UNO + 16x2 HD44780 LCD (4-bit parallel, no I2C backpack)

  Wiring:
    LCD Pin 1  (VSS)  -> GND
    LCD Pin 2  (VDD)  -> 5V
    LCD Pin 3  (V0)   -> Potentiometer wiper (contrast)
    LCD Pin 4  (RS)   -> Arduino pin 12
    LCD Pin 5  (RW)   -> GND
    LCD Pin 6  (EN)   -> Arduino pin 11
    LCD Pin 11 (D4)   -> Arduino pin 5
    LCD Pin 12 (D5)   -> Arduino pin 4
    LCD Pin 13 (D6)   -> Arduino pin 3
    LCD Pin 14 (D7)   -> Arduino pin 2
    LCD Pin 15 (A)    -> 220 ohm resistor -> 5V  (backlight +)
    LCD Pin 16 (K)    -> GND                     (backlight -)
    Potentiometer:
      Left leg  -> GND
      Right leg -> 5V
      Wiper     -> LCD Pin 3

  Cycle button:
    One leg -> Arduino pin 7
    Other leg -> GND
    (uses internal pull-up, no resistor needed)

  Serial packet format from Python script:
    NYM,4,LAD,2,B3,S2,O1,T7,I2\n
    Fields: away_team, away_score, home_team, home_score,
            balls (B#), strikes (S#), outs (O#),
            top_bot (T=top / B=bot), inning (I#)

  Display layout:
    Row 1: NYM  4    LAD  2
    Row 2: T7  B:3 S:2 O:1
*/

#include <LiquidCrystal.h>

// --- Pin definitions ---
LiquidCrystal lcd(12, 11, 5, 4, 3, 2);  // RS, EN, D4, D5, D6, D7
const int BTN_PIN = 7;

// --- Serial packet buffer ---
const int PACKET_LEN = 64;
char packetBuf[PACKET_LEN];
int  bufIdx = 0;

// --- Game state ---
struct GameState {
  char awayTeam[5];
  int  awayScore;
  char homeTeam[5];
  int  homeScore;
  int  balls;
  int  strikes;
  int  outs;
  bool isTop;       // true = top of inning, false = bottom
  int  inning;
  bool valid;       // has a valid packet been received
};

GameState game;

// --- Button state ---
bool     btnPrev     = HIGH;
bool     cycleGame   = false;

// --- Splash / no-data display ---
unsigned long lastPacketMs = 0;
const unsigned long TIMEOUT_MS = 90000; // 90 sec no data -> show waiting screen

void setup() {
  Serial.begin(9600);

  pinMode(BTN_PIN, INPUT_PULLUP);

  lcd.begin(16, 2);
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("MLB Scoreboard");
  lcd.setCursor(0, 1);
  lcd.print("Waiting...");

  memset(&game, 0, sizeof(game));
  game.valid = false;
}

void loop() {
  readSerial();
  handleButton();
  updateDisplay();
}

// -------------------------------------------------------
//  Read incoming serial bytes, build packet on newline
// -------------------------------------------------------
void readSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      packetBuf[bufIdx] = '\0';
      parsePacket(packetBuf);
      bufIdx = 0;
    } else if (bufIdx < PACKET_LEN - 1) {
      packetBuf[bufIdx++] = c;
    }
  }
}

// -------------------------------------------------------
//  Parse:  NYM,4,LAD,2,B3,S2,O1,T7,I2
// -------------------------------------------------------
void parsePacket(char* pkt) {
  // Tokenize by comma
  char tmp[PACKET_LEN];
  strncpy(tmp, pkt, PACKET_LEN);

  char* tok = strtok(tmp, ",");
  if (!tok) return;
  strncpy(game.awayTeam, tok, 4); game.awayTeam[4] = '\0';

  tok = strtok(NULL, ","); if (!tok) return;
  game.awayScore = atoi(tok);

  tok = strtok(NULL, ","); if (!tok) return;
  strncpy(game.homeTeam, tok, 4); game.homeTeam[4] = '\0';

  tok = strtok(NULL, ","); if (!tok) return;
  game.homeScore = atoi(tok);

  // B#  S#  O#  T# or B#  I#
  tok = strtok(NULL, ","); if (!tok) return;
  if (tok[0] == 'B') game.balls = atoi(tok + 1);

  tok = strtok(NULL, ","); if (!tok) return;
  if (tok[0] == 'S') game.strikes = atoi(tok + 1);

  tok = strtok(NULL, ","); if (!tok) return;
  if (tok[0] == 'O') game.outs = atoi(tok + 1);

  // T# = top of inning #, B# = bottom of inning #
  tok = strtok(NULL, ","); if (!tok) return;
  if (tok[0] == 'T') {
    game.isTop  = true;
    game.inning = atoi(tok + 1);
  } else if (tok[0] == 'B') {
    game.isTop  = false;
    game.inning = atoi(tok + 1);
  }

  // Legacy format: separate inning field
  tok = strtok(NULL, ",");
  if (tok && tok[0] == 'I') {
    game.inning = atoi(tok + 1);
  }

  game.valid    = true;
  lastPacketMs  = millis();

  // Acknowledge to Python (optional)
  Serial.println("ACK");
}

// -------------------------------------------------------
//  Cycle button: tell Python to send the next game
// -------------------------------------------------------
void handleButton() {
  bool btnNow = digitalRead(BTN_PIN);
  if (btnPrev == HIGH && btnNow == LOW) {
    // Falling edge = press
    Serial.println("NEXT");
    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.print("Switching...");
    delay(300); // debounce
  }
  btnPrev = btnNow;
}

// -------------------------------------------------------
//  Render to LCD
// -------------------------------------------------------
void updateDisplay() {
  // Timeout: no data received recently
  if (millis() - lastPacketMs > TIMEOUT_MS && lastPacketMs != 0) {
    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.print("No live games");
    lcd.setCursor(0, 1);
    lcd.print("Check back later");
    delay(5000);
    return;
  }

  if (!game.valid) return;

  // --- Row 1: away team / score / home team / score ---
  // Format: "NYM  4    LAD  2  " (16 chars)
  lcd.setCursor(0, 0);

  char row1[17];
  // Left side: team (3 chars) + space + score (up to 2 digits)
  // Right side: team (3 chars) + space + score (up to 2 digits)
  // Pad to fill 16 chars
  snprintf(row1, sizeof(row1), "%-3s %2d  %-3s %2d  ",
           game.awayTeam, game.awayScore,
           game.homeTeam, game.homeScore);
  row1[16] = '\0';
  lcd.print(row1);

  // --- Row 2: inning / balls / strikes / outs ---
  // Format: "T7  B:3 S:2 O:1 " (16 chars)
  lcd.setCursor(0, 1);

  char row2[17];
  char innStr[4];
  snprintf(innStr, sizeof(innStr), "%c%d", game.isTop ? 'T' : 'B', game.inning);
  snprintf(row2, sizeof(row2), "%-3s B:%d S:%d O:%d  ",
           innStr, game.balls, game.strikes, game.outs);
  row2[16] = '\0';
  lcd.print(row2);

  delay(500); // only refresh twice per second, avoids flicker
}
