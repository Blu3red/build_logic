from datetime import datetime, timezone
import json
import os
import threading
import time
from flask import Flask, Response, request
import requests

app = Flask(__name__)

# ZABEZPIECZENIE 1: Maksymalny rozmiar zapytania HTTP to 1 KB (chroni RAM na Render.com)
app.config["MAX_CONTENT_LENGTH"] = 1024

# ==============================================================================
# KONFIGURACJA
# ==============================================================================
LSB_FIRST = True  # True = wagi 1, 2, 4, 8 od lewej | False = wagi 8, 4, 2, 1
SIGNAL_TIMEOUT = 30.0  # Po ilu sekundach bezczynności zresetować niedokończone wpisywanie daty
API_COOLDOWN = (
    1.5  # Minimalny odstęp (w sekundach) między zapytaniami do API Binance
)

# Blokada wątków (chroni przed jednoczesnym nadpisywaniem zmiennych)
state_lock = threading.Lock()

# Stan serwera
state = {
    "post_signals": {0: None, 1: None, 2: None},
    "last_post_time": 0.0,
    "last_api_call": 0.0,
    "price_bcd_packets": ["00000000"] * 8,
    "get_index": 0,
}

# Cache na pobrane ceny: {"YYYY-MM-DD": 64321.50} (max 1000 wpisów, żeby nie zapchać RAMu)
price_cache = {}


def from_bits(bit_str):
    if LSB_FIRST:
        return int(bit_str[::-1], 2)
    return int(bit_str, 2)


def to_bits(val, length):
    b = f"{val:0{length}b}"
    if LSB_FIRST:
        return b[::-1]
    return b


def format_response(bit_string):
    """Zawsze zwraca czysty JSON oczekiwany przez Build Logic."""
    json_str = json.dumps({"value": bit_string}, separators=(",", ":"))
    return Response(json_str, mimetype="application/json", status=200)


def validate_date(day, month, year):
    """Sprawdza czy data w ogóle istnieje w kalendarzu i nie jest z przyszłości."""
    try:
        dt = datetime(year, month, day, 0, 0, 0, tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)

        # Anomalia: Data z przyszłości
        if dt > now:
            print(f"[ANOMALIA] Data z przyszłości: {day}/{month}/{year}")
            return None

        # Anomalia: Data sprzed startu rynku BTC/USDT na Binance (sierpień 2017)
        if dt < datetime(2017, 8, 17, tzinfo=timezone.utc):
            print(
                f"[ANOMALIA] Data zbyt stara (przed 17.08.2017): {day}/{month}/{year}"
            )
            return None

        return dt
    except ValueError:
        # Np. 31 luty, miesiąc 15, dzień 0 itp.
        print(f"[ANOMALIA] Nieistniejąca data: {day}/{month}/{year}")
        return None


def get_btc_price_safe(day, month, year):
    """Bezpieczne pobieranie ceny z Cache oraz ochroną przed spamem (Rate Limit)."""
    dt = validate_date(day, month, year)
    if dt is None:
        return 0.0

    date_key = f"{year:04d}-{month:02d}-{day:02d}"
    today_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 1. Sprawdzenie pamięci Cache (jeśli data jest z przeszłości i już ją pobieraliśmy)
    if date_key in price_cache:
        print(
            f"[CACHE] Pobrano cenę z pamięci dla {date_key}: {price_cache[date_key]}"
        )
        return price_cache[date_key]

    # 2. Ochrona przed spamowaniem API (Cooldown)
    now_ts = time.time()
    if now_ts - state["last_api_call"] < API_COOLDOWN:
        print(
            "[ANTY-SPAM] Zbyt szybkie zapytania do API! Zwracam 0.0 dla ochrony IP."
        )
        return 0.0

    state["last_api_call"] = now_ts

    # 3. Zapytanie do API Binance
    try:
        timestamp_ms = int(dt.timestamp() * 1000)
        url = "https://data-api.binance.vision/api/v3/klines"
        params = {
            "symbol": "BTCUSDT",
            "interval": "1d",
            "startTime": timestamp_ms,
            "limit": 1,
        }
        res = requests.get(url, params=params, timeout=4)

        if res.status_code != 200:
            print(f"[API BŁĄD] Kod HTTP: {res.status_code}")
            return 0.0

        data = res.json()
        if isinstance(data, list) and len(data) > 0:
            close_price = float(data[0][4])

            # Zapisujemy do cache tylko zamknięte dni (nie dzisiejszy)
            if date_key != today_key:
                if len(price_cache) > 1000:
                    price_cache.clear()  # Czyszczenie w razie przepełnienia
                price_cache[date_key] = close_price

            return close_price
        return 0.0
    except Exception as e:
        print(f"[WYJĄTEK API] {e}")
        return 0.0


def prepare_price_packets(price):
    """Konwertuje cenę na 8 pakietów, zabezpieczając przed przekroczeniem zakresu 999 999,99."""
    # Zabezpieczenie przed ujemną ceną lub wyższą niż 999 999.99 (8 cyfr)
    price = max(0.0, min(price, 999999.99))
    price_int = int(round(price * 100))
    price_str = f"{price_int:08d}"[-8:]

    packets = []
    for sig_num, digit_char in enumerate(price_str):
        digit_bits = to_bits(int(digit_char), 4)
        sig_bits = to_bits(sig_num, 4)
        packets.append(digit_bits + sig_bits)

    print(f"[GOTOWE] Cena: {price:.2f} USD -> Pakiety: {packets}")
    return packets


def extract_value_from_request(req):
    """Bezpieczne wyciąganie 8 bitów z zapytania."""
    val = None
    if "value" in req.form:
        val = req.form["value"]
    elif "value" in req.args:
        val = req.args["value"]
    else:
        raw_data = req.get_data(as_text=True)
        try:
            parsed = json.loads(raw_data)
            if isinstance(parsed, dict) and "value" in parsed:
                val = parsed["value"]
        except:
            pass

        if val is None:
            for key in req.form.keys():
                try:
                    parsed = json.loads(key)
                    if isinstance(parsed, dict) and "value" in parsed:
                        val = parsed["value"]
                        break
                except:
                    pass

    if not isinstance(val, str):
        return None

    val = val.strip()
    # Walidacja: dokładnie 8 znaków, tylko '0' i '1'
    if len(val) == 8 and all(c in "01" for c in val):
        return val
    return None


@app.route("/", methods=["POST", "GET"])
def handle_transmitter():
    with state_lock:
        # ==========================================
        # ODBIERANIE DATY (POST)
        # ==========================================
        if request.method == "POST":
            val = extract_value_from_request(request)

            # Anomalia: Uszkodzony pakiet (zła długość lub inne znaki niż 0/1)
            if val is None:
                print(
                    "[ANOMALIA] Odebrano nieprawidłowe dane w POST! Zwracam kod błędu (Sygnał 3)."
                )
                return format_response("000000" + to_bits(3, 2))

            now = time.time()
            # Jeśli minęło więcej niż 30 sekund od ostatniego POSTa, czyścimy stare niedokończone sygnały
            if now - state["last_post_time"] > SIGNAL_TIMEOUT:
                state["post_signals"] = {0: None, 1: None, 2: None}
            state["last_post_time"] = now

            sig_num = from_bits(val[6:8])

            # Sygnał nr 3 (bity '11' na końcu) działa jako RĘCZNY RESET układu
            if sig_num == 3:
                print(
                    "[RESET] Odebrano sygnał #3 -> Czyszczenie bufora i reset indeksów!"
                )
                state["post_signals"] = {0: None, 1: None, 2: None}
                state["get_index"] = 0
                return format_response("000000" + to_bits(3, 2))

            # Jeśli przychodzi Sygnał 0, a w buforze są jakieś śmieci z poprzedniej próby, zaczynamy od nowa
            if sig_num == 0 and any(
                state["post_signals"][i] is not None for i in [1, 2]
            ):
                print("[NOWA SEKWENCJA] Nadpisuję niedokończoną datę od sygnału #0.")
                state["post_signals"] = {0: None, 1: None, 2: None}

            state["post_signals"][sig_num] = val
            print(f"[POST] Odebrano poprawny Sygnał #{sig_num}: {val}")

            # Sprawdzamy czy mamy komplet 3 sygnałów (0, 1, 2)
            if all(state["post_signals"][i] is not None for i in [0, 1, 2]):
                s0 = state["post_signals"][0]
                s1 = state["post_signals"][1]
                s2 = state["post_signals"][2]

                day = from_bits(s0[0:5])
                month = from_bits(s0[5] + s1[0:3])
                year = 2000 + from_bits(s1[3:6] + s2[0:4])

                print(f"=== ZDEKODOWANO DATĘ: {day:02d}/{month:02d}/{year} ===")

                price = get_btc_price_safe(day, month, year)
                state["price_bcd_packets"] = prepare_price_packets(price)
                state["get_index"] = 0
                state["post_signals"] = {0: None, 1: None, 2: None}

            # Zwracamy numer sygnału dla weryfikacji
            return format_response("000000" + to_bits(sig_num, 2))

        # ==========================================
        # WYSYŁANIE CENY DO GRY (GET)
        # ==========================================
        elif request.method == "GET":
            idx = state["get_index"]
            packet_to_send = state["price_bcd_packets"][idx]
            print(f"[GET] Wysyłam pakiet #{idx}/7: {packet_to_send}")
            state["get_index"] = (idx + 1) % 8
            return format_response(packet_to_send)


# ZABEZPIECZENIE GLOBALNE: Nawet jak wydarzy się coś nieprzewidzianego, nie wywalaj serwera
@app.errorhandler(Exception)
def handle_exception(e):
    print(f"[KRYTYCZNY WYJĄTEK] {e}")
    return format_response("00000011")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
