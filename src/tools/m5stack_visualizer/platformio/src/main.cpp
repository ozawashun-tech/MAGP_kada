#include <M5Stack.h>
#include <ArduinoJson.h>

// 表示モード
enum DisplayMode {
    MODE_MUX_ONLY = 0,      // デフォルト: /mux_input/status のみ大きく表示
    MODE_BAG_AND_MUX = 1,   // Bag Recorder上部 + mux下部
    MODE_BAG_ONLY = 2       // Bag Recorder状態のみ大きく表示
};

// Bag Recorder状態
struct BagRecorderStatus {
    String state = "IDLE";
    bool system_unlocked = false;
    String current_bag = "";
    int bag_counter = 0;
    String recording_start_time = "";
};

struct BagRecorderEvent {
    String event_type = "";
    String message = "";
    unsigned long timestamp = 0;
};

// Mux Input状態
struct MuxInputStatus {
    String mode = "Manual";  // Auto, Manual, Error
    unsigned long last_update = 0;
};

// グローバル変数
DisplayMode current_mode = MODE_MUX_ONLY;
BagRecorderStatus bag_status;
BagRecorderStatus prev_bag_status;
BagRecorderEvent last_event;
MuxInputStatus mux_status;
MuxInputStatus prev_mux_status;
unsigned long last_receive_time = 0;
unsigned long bag_last_update = 0;
unsigned long mux_last_update = 0;
const unsigned long TIMEOUT_MS = 5000;
bool blink_state = false;
unsigned long last_blink = 0;
bool data_received = false;
bool bag_data_received = false;
bool mux_data_received = false;
DisplayMode prev_mode = MODE_MUX_ONLY;

// 色定義
#define COLOR_RECORDING TFT_RED
#define COLOR_IDLE TFT_GREEN
#define COLOR_AUTO TFT_RED
#define COLOR_MANUAL TFT_BLUE
#define COLOR_ERROR TFT_YELLOW
#define COLOR_BG TFT_BLACK
#define COLOR_TEXT TFT_WHITE

// 関数宣言
void parseStatusMessage(String json_str);
void parseEventMessage(String json_str);
void parseMuxMessage(String json_str);
void updateDisplay();
void drawModeIndicator();
void drawBagRecorderLarge();
void drawBagAndMux();
void drawMuxOnlyLarge();
void drawInitialScreen();
void drawStandbyArea(int x, int y, int w, int h, String title);
uint16_t getStateColor(String state);
uint16_t getMuxColor(String mode);
void handleButtonPress();
bool hasDataChanged();
bool isBagTimeout();
bool isMuxTimeout();

void setup() {
    M5.begin();
    M5.Power.begin();
    Serial.begin(115200);
    
    drawInitialScreen();
}

void loop() {
    M5.update();
    
    // シリアルデータの受信
    if (Serial.available()) {
        String line = Serial.readStringUntil('\n');
        line.trim();
        
        if (line.startsWith("{")) {
            last_receive_time = millis();
            
            prev_bag_status = bag_status;
            prev_mux_status = mux_status;
            
            if (line.indexOf("\"state\"") != -1) {
                parseStatusMessage(line);
                bag_last_update = millis();
                bag_data_received = true;
            } else if (line.indexOf("\"event_type\"") != -1) {
                parseEventMessage(line);
            } else if (line.indexOf("\"mode\"") != -1) {
                parseMuxMessage(line);
                mux_last_update = millis();
                mux_data_received = true;
            }
            
            if (!data_received) {
                data_received = true;
                updateDisplay();
            } else if (hasDataChanged()) {
                updateDisplay();
            }
        }
    }
    
    handleButtonPress();
    
    // 点滅処理（RECORDING時のみ）
    if (bag_status.state == "RECORDING" && current_mode == MODE_BAG_ONLY && 
        data_received && !isBagTimeout()) {
        if (millis() - last_blink > 500) {
            blink_state = !blink_state;
            last_blink = millis();
            updateDisplay();
        }
    }
    
    // 接続タイムアウト表示
    if (millis() - last_receive_time > TIMEOUT_MS && last_receive_time > 0) {
        M5.Lcd.fillRect(0, 215, 320, 25, TFT_DARKGREY);
        M5.Lcd.setTextColor(TFT_WHITE);
        M5.Lcd.setTextSize(1);
        M5.Lcd.setCursor(80, 222);
        M5.Lcd.println("Connection Lost");
    }
    
    delay(50);
}

bool isBagTimeout() {
    return !bag_data_received;
}

bool isMuxTimeout() {
    return !mux_data_received;
}

void drawStandbyArea(int x, int y, int w, int h, String title) {
    M5.Lcd.drawRect(x, y, w, h, TFT_DARKGREY);
    M5.Lcd.setTextColor(TFT_DARKGREY);
    M5.Lcd.setTextSize(2);
    
    int title_x = x + (w - title.length() * 12) / 2;
    M5.Lcd.setCursor(title_x, y + 5);
    M5.Lcd.println(title);
    
    M5.Lcd.setTextSize(1);
    M5.Lcd.setCursor(x + w/2 - 10, y + h/2 - 5);
    M5.Lcd.println("---");
}

void drawInitialScreen() {
    M5.Lcd.fillScreen(COLOR_BG);
    
    switch (current_mode) {
        case MODE_MUX_ONLY:
            drawStandbyArea(10, 10, 300, 195, "DRIVE");
            break;
            
        case MODE_BAG_AND_MUX:
            drawStandbyArea(10, 10, 300, 95, "RECORDER");
            drawStandbyArea(10, 110, 300, 95, "DRIVE");
            break;
            
        case MODE_BAG_ONLY:
            drawStandbyArea(10, 10, 300, 195, "RECORDER");
            break;
    }
    
    M5.Lcd.fillRect(0, 215, 320, 25, TFT_DARKGREY);
    M5.Lcd.setTextSize(1);
    M5.Lcd.setTextColor(TFT_WHITE);
    M5.Lcd.setCursor(10, 222);
    M5.Lcd.print("Next");
    M5.Lcd.setCursor(125, 222);
    M5.Lcd.print("Standby");
    M5.Lcd.setCursor(260, 222);
    M5.Lcd.print("Prev");
}

bool hasDataChanged() {
    if (bag_status.state != prev_bag_status.state ||
        bag_status.system_unlocked != prev_bag_status.system_unlocked ||
        bag_status.bag_counter != prev_bag_status.bag_counter) {
        return true;
    }
    
    if (mux_status.mode != prev_mux_status.mode) {
        return true;
    }
    
    if (current_mode != prev_mode) {
        prev_mode = current_mode;
        return true;
    }
    
    return false;
}

void parseStatusMessage(String json_str) {
    StaticJsonDocument<512> doc;
    DeserializationError error = deserializeJson(doc, json_str);
    
    if (!error) {
        bag_status.state = doc["state"].as<String>();
        bag_status.system_unlocked = doc["system_unlocked"];
        bag_status.current_bag = doc["current_bag"] | "";
        bag_status.bag_counter = doc["bag_counter"];
        bag_status.recording_start_time = doc["recording_start_time"] | "";
    }
}

void parseEventMessage(String json_str) {
    StaticJsonDocument<512> doc;
    DeserializationError error = deserializeJson(doc, json_str);
    
    if (!error) {
        last_event.event_type = doc["event_type"].as<String>();
        last_event.message = doc["message"].as<String>();
        last_event.timestamp = millis();
    }
}

void parseMuxMessage(String json_str) {
    StaticJsonDocument<256> doc;
    DeserializationError error = deserializeJson(doc, json_str);
    
    if (!error) {
        mux_status.mode = doc["mode"].as<String>();
        mux_status.last_update = millis();
    }
}

void handleButtonPress() {
    if (M5.BtnA.wasPressed()) {
        current_mode = (DisplayMode)((current_mode + 1) % 3);
        if (!data_received) {
            drawInitialScreen();
        } else {
            updateDisplay();
        }
    }
    
    if (M5.BtnB.wasPressed()) {
        data_received = false;
        bag_data_received = false;
        mux_data_received = false;
        drawInitialScreen();
    }
    
    if (M5.BtnC.wasPressed()) {
        current_mode = (DisplayMode)((current_mode - 1 + 3) % 3);
        if (!data_received) {
            drawInitialScreen();
        } else {
            updateDisplay();
        }
    }
}

void updateDisplay() {
    M5.Lcd.fillScreen(COLOR_BG);
    
    switch (current_mode) {
        case MODE_MUX_ONLY:
            drawMuxOnlyLarge();
            break;
        case MODE_BAG_AND_MUX:
            drawBagAndMux();
            break;
        case MODE_BAG_ONLY:
            drawBagRecorderLarge();
            break;
    }
    
    drawModeIndicator();
}

void drawModeIndicator() {
    M5.Lcd.fillRect(0, 215, 320, 25, TFT_DARKGREY);
    M5.Lcd.setTextSize(1);
    M5.Lcd.setTextColor(TFT_WHITE);
    
    M5.Lcd.setCursor(10, 222);
    M5.Lcd.print("Next");
    M5.Lcd.setCursor(125, 222);
    M5.Lcd.print("Standby");
    M5.Lcd.setCursor(260, 222);
    M5.Lcd.print("Prev");
}

void drawBagRecorderLarge() {
    if (isBagTimeout()) {
        drawStandbyArea(10, 10, 300, 195, "RECORDER");
        return;
    }
    
    if (bag_status.state == "RECORDING") {
        M5.Lcd.fillRect(0, 0, 320, 215, COLOR_RECORDING);
        
        if (blink_state) {
            M5.Lcd.fillCircle(40, 60, 20, TFT_WHITE);
        }
        
        M5.Lcd.setTextSize(6);
        M5.Lcd.setTextColor(TFT_WHITE);
        M5.Lcd.setCursor(90, 40);
        M5.Lcd.println("REC");
        
        M5.Lcd.setTextSize(4);
        M5.Lcd.setCursor(80, 120);
        M5.Lcd.print("Bag #");
        M5.Lcd.println(bag_status.bag_counter);
        
    } else {
        uint16_t status_color = getStateColor(bag_status.state);
        M5.Lcd.fillRect(0, 0, 320, 60, status_color);
        
        M5.Lcd.setTextSize(3);
        M5.Lcd.setTextColor(TFT_WHITE);
        int x = (320 - bag_status.state.length() * 18) / 2;
        M5.Lcd.setCursor(x, 20);
        M5.Lcd.println(bag_status.state);
        M5.Lcd.setCursor(x+1, 20);
        M5.Lcd.println(bag_status.state);
        
        M5.Lcd.setTextColor(COLOR_TEXT);
        M5.Lcd.setCursor(40, 75);
        M5.Lcd.setTextSize(2);
        if (!bag_status.system_unlocked) {
            M5.Lcd.setTextColor(TFT_YELLOW);
            M5.Lcd.println("SYSTEM LOCKED");
        } else {
            M5.Lcd.setTextColor(TFT_GREEN);
            M5.Lcd.println("UNLOCKED");
        }
        
        if (bag_status.bag_counter > 0) {
            M5.Lcd.setTextColor(COLOR_TEXT);
            M5.Lcd.setTextSize(2);
            M5.Lcd.setCursor(80, 110);
            M5.Lcd.print("Bag #: ");
            M5.Lcd.println(bag_status.bag_counter);
        }
        
        if (last_event.event_type != "" && millis() - last_event.timestamp < 10000) {
            M5.Lcd.setTextSize(1);
            M5.Lcd.setTextColor(TFT_CYAN);
            M5.Lcd.setCursor(10, 155);
            M5.Lcd.println(last_event.event_type);
            M5.Lcd.setCursor(10, 170);
            M5.Lcd.println(last_event.message.substring(0, 35));
        }
    }
}

void drawBagAndMux() {
    bool bag_timeout = isBagTimeout();
    bool mux_timeout = isMuxTimeout();
    
    if (bag_timeout && mux_timeout) {
        drawStandbyArea(10, 10, 300, 95, "RECORDER");
        drawStandbyArea(10, 110, 300, 95, "DRIVE");
        return;
    }
    
    if (bag_timeout && !mux_timeout) {
        drawStandbyArea(10, 10, 300, 95, "RECORDER");
        
        uint16_t mux_color = getMuxColor(mux_status.mode);
        M5.Lcd.fillRect(0, 108, 320, 107, mux_color);
        M5.Lcd.setTextSize(2);
        M5.Lcd.setTextColor(TFT_WHITE);
        M5.Lcd.setCursor(10, 120);
        M5.Lcd.println("DRIVE");
        M5.Lcd.fillTriangle(250, 120, 280, 135, 250, 150, TFT_WHITE);
        M5.Lcd.fillTriangle(260, 125, 260, 145, 240, 135, TFT_WHITE);
        M5.Lcd.setTextSize(5);
        int mux_x = (320 - mux_status.mode.length() * 30) / 2;
        M5.Lcd.setCursor(mux_x, 155);
        M5.Lcd.println(mux_status.mode);
        M5.Lcd.setCursor(mux_x+1, 155);
        M5.Lcd.println(mux_status.mode);
        return;
    }
    
    if (!bag_timeout && mux_timeout) {
        uint16_t bag_color = getStateColor(bag_status.state);
        M5.Lcd.fillRect(0, 0, 320, 107, bag_color);
        M5.Lcd.setTextSize(2);
        M5.Lcd.setTextColor(TFT_WHITE);
        M5.Lcd.setCursor(10, 10);
        M5.Lcd.println("RECORDER");
        M5.Lcd.drawRect(250, 10, 30, 30, TFT_WHITE);
        M5.Lcd.drawRect(251, 11, 28, 28, TFT_WHITE);
        M5.Lcd.setTextSize(3);
        int bag_x = (320 - bag_status.state.length() * 18) / 2;
        M5.Lcd.setCursor(bag_x, 50);
        M5.Lcd.println(bag_status.state);
        M5.Lcd.setCursor(bag_x+1, 50);
        M5.Lcd.println(bag_status.state);
        if (bag_status.bag_counter > 0) {
            M5.Lcd.setTextSize(2);
            M5.Lcd.setCursor(120, 85);
            M5.Lcd.print("#");
            M5.Lcd.print(bag_status.bag_counter);
        }
        
        drawStandbyArea(10, 110, 300, 95, "DRIVE");
        return;
    }
    
    uint16_t bag_color = getStateColor(bag_status.state);
    M5.Lcd.fillRect(0, 0, 320, 107, bag_color);
    M5.Lcd.setTextSize(2);
    M5.Lcd.setTextColor(TFT_WHITE);
    M5.Lcd.setCursor(10, 10);
    M5.Lcd.println("RECORDER");
    M5.Lcd.drawRect(250, 10, 30, 30, TFT_WHITE);
    M5.Lcd.drawRect(251, 11, 28, 28, TFT_WHITE);
    M5.Lcd.setTextSize(3);
    int bag_x = (320 - bag_status.state.length() * 18) / 2;
    M5.Lcd.setCursor(bag_x, 50);
    M5.Lcd.println(bag_status.state);
    M5.Lcd.setCursor(bag_x+1, 50);
    M5.Lcd.println(bag_status.state);
    if (bag_status.bag_counter > 0) {
        M5.Lcd.setTextSize(2);
        M5.Lcd.setCursor(120, 85);
        M5.Lcd.print("#");
        M5.Lcd.print(bag_status.bag_counter);
    }
    
    uint16_t mux_color = getMuxColor(mux_status.mode);
    M5.Lcd.fillRect(0, 108, 320, 107, mux_color);
    M5.Lcd.setTextSize(2);
    M5.Lcd.setTextColor(TFT_WHITE);
    M5.Lcd.setCursor(10, 120);
    M5.Lcd.println("DRIVE");
    M5.Lcd.fillTriangle(250, 120, 280, 135, 250, 150, TFT_WHITE);
    M5.Lcd.fillTriangle(260, 125, 260, 145, 240, 135, TFT_WHITE);
    M5.Lcd.setTextSize(5);
    int mux_x = (320 - mux_status.mode.length() * 30) / 2;
    M5.Lcd.setCursor(mux_x, 155);
    M5.Lcd.println(mux_status.mode);
    M5.Lcd.setCursor(mux_x+1, 155);
    M5.Lcd.println(mux_status.mode);
}

void drawMuxOnlyLarge() {
    if (isMuxTimeout()) {
        drawStandbyArea(10, 10, 300, 195, "DRIVE");
        return;
    }
    
    uint16_t color = getMuxColor(mux_status.mode);
    M5.Lcd.fillRect(0, 0, 320, 215, color);
    
    M5.Lcd.setTextSize(3);
    M5.Lcd.setTextColor(TFT_WHITE);
    M5.Lcd.setCursor(70, 30);
    M5.Lcd.println("Mux Input");
    
    M5.Lcd.setTextSize(6);
    M5.Lcd.setTextColor(TFT_WHITE);
    int x = (320 - mux_status.mode.length() * 36) / 2;
    M5.Lcd.setCursor(x, 100);
    M5.Lcd.println(mux_status.mode);
    M5.Lcd.setCursor(x+1, 100);
    M5.Lcd.println(mux_status.mode);
}

uint16_t getStateColor(String state) {
    if (state == "RECORDING") {
        return COLOR_RECORDING;
    } else if (state == "IDLE") {
        return COLOR_IDLE;
    }
    return COLOR_TEXT;
}

uint16_t getMuxColor(String mode) {
    if (mode == "Auto") {
        return COLOR_AUTO;
    } else if (mode == "Manual") {
        return COLOR_MANUAL;
    } else if (mode == "Error") {
        return COLOR_ERROR;
    }
    return COLOR_TEXT;
}