from datetime import datetime, timezone
import fcntl
import json
import os
import time
from flask import Flask, Response, request
import requests

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1024

# ==============================================================================
# KONFIGURACJA
# ==============================================================================
LSB_FIRST = True  # True = wagi 1, 2, 4, 8 od lewej | False = wagi 8, 4, 2, 1
SIGNAL_TIMEOUT = 600.0  # 10 minut na spokojne wysłanie 3 sygnałów POST
API_COOLDOWN = 1.0  # Ochrona przed spamem API (1 sekunda)
STATE_FILE = "/tmp/btc_state.json"


def from_bits(bit_str):
    if LSB_FIRST:
        return int(bit_str[::-1], 2)
    return int(bit_str, 2)


def to_bits(val, length):
    b = f"{val:0{length}b}"
    if LSB_FIRST:
        return b[::-1]
    return b


def prepare_price_packets(price):
    """Konwertuje cenę na 8 pakietów: [4 bity cyfry] + [4 bity numeru sygnału 0-7]."""
    price = max(0.0, min(price, 999999.99))
    price_int = int(round(price * 100))
    price_str = f"{price_int:08d}"[-8:]

    packets = []
    for sig_num, digit_char in enumerate(price_str):
        digit_bits = to_bits(int(digit_char), 4)
        sig_bits = to_bits(sig_num, 4)
        packets.append(digit_bits + sig_bits)
    return packets


def get_default_state():
    return {
        "post_signals": {"0": None, "1": None, "2": None},
        "last_post_time": 0.0,
        "last_api_call": 0.0,
        "price_bcd_packets": prepare_price_packets(0.0),
        "get_index": 0,
        "cache": {},
    }


def load_and_lock_state():
    if not os.path.exists(STATE_FILE):
        with open(STATE_FILE, "w") as f:
            json.dump(get_default_state(), f)

    f = open(STATE_FILE, "r+")
    fcntl.flock(f, fcntl.LOCK_EX)
    try:
        data = json.load(f)
    except Exception:
        data = get_default_state()
    return f, data


def save_and_unlock_state(f, data):
    f.seek(0)
    f.truncate()
    json.dump(data, f)
    f.flush()
    fcntl.flock(f, fcntl.LOCK_UN)
    f.close()


def format_response(bit_string):
    json_str = json.dumps({"value": bit_string}, separators=(",", ":"))
    return Response(json_str, mimetype="application/json", status=200)


def validate_date(day, month, year):
    try:
        dt = datetime(year, month, day, 0, 0, 0, tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if dt > now:
            print(
                f"[ANOMALIA] Data z przyszłości: {day:02d}/{month:02d}/{year}",
                flush=True,
            )
            return None
        if dt < datetime(2010, 7, 17, tzinfo=timezone.utc):
            print(
                f"[ANOMALIA] Data sprzed powstania giełd BTC (przed 17.07.2010): {day:02d}/{month:02d}/{year}",
                flush=True,
            )
            return None
        return dt
    except ValueError:
        print(
            f"[ANOMALIA] Nieistniejąca data w kalendarzu: {day}/{month}/{year}",
            flush=True,
        )
        return None


def get_btc_price_safe(day, month, year, state):
    dt = validate_date(day, month, year)
    if dt is None:
        return 0.0

    date_key = f"{year:04d}-{month:02d}-{day:02d}"
    today_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if date_key in state["cache"]:
        price = state["cache"][date_key]
        print(f"[CACHE] Cena dla {date_key} z pamięci: {price} USD", flush=True)
        return price

    now_ts = time.time()
    if now_ts - state["last_api_call"] < API_COOLDOWN:
        print("[ANTY-SPAM] Zbyt szybkie zapytania do API!", flush=True)
        return 0.0
    state["last_api_call"] = now_ts

    # 1. Główne API: CryptoCompare (historia od 2010 roku)
    try:
        ts_sec = int(dt.timestamp())
        url = "https://min-api.cryptocompare.com/data/v2/histoday"
        params = {"fsym": "BTC", "tsym": "USD", "limit": 1, "toTs": ts_sec}
        res = requests.get(url, params=params, timeout=5)
        data = res.json()

        if data.get("Response") == "Success":
            candles = data.get("Data", {}).get("Data", [])
            if candles:
                close_price = float(candles[-1]["close"])
                if close_price > 0:
                    if date_key != today_key:
                        if len(state["cache"]) > 500:
                            state["cache"].clear()
                        state["cache"][date_key] = close_price
                    return close_price
    except Exception as e:
        print(f"[BŁĄD CryptoCompare] {e}", flush=True)

    # 2. Zapasowe API: Binance (dla dat >= 2017)
    try:
        url = "https://data-api.binance.vision/api/v3/klines"
        params = {
            "symbol": "BTCUSDT",
            "interval": "1d",
            "startTime": int(dt.timestamp() * 1000),
            "limit": 1,
        }
        res = requests.get(url, params=params, timeout=5)
        data = res.json()
        if isinstance(data, list) and len(data) > 0:
            close_price = float(data[0][4])
            if date_key != today_key:
                state["cache"][date_key] = close_price
            return close_price
    except Exception as e:
        print(f"[BŁĄD Binance] {e}", flush=True)

    return 0.0


def extract_value_from_request(req):
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
    if len(val) == 8 and all(c in "01" for c in val):
        return val
    return None


@app.route("/", methods=["POST", "GET", "HEAD"])
def handle_transmitter():
    user_agent = request.headers.get("User-Agent", "")

    # Ignorujemy zapytania HEAD oraz bota sprawdzającego z Render.com (żeby nie przesuwał licznika GET!)
    if request.method == "HEAD" or "Go-http-client" in user_agent:
        return format_response("00000000")

    f, state = load_and_lock_state()
    try:
        # ==========================================
        # ODBIERANIE DATY (POST)
        # ==========================================
        if request.method == "POST":
            val = extract_value_from_request(request)

            if val is None:
                print(
                    f"[ANOMALIA] Błędny pakiet POST: {request.get_data(as_text=True)}",
                    flush=True,
                )
                return format_response("000000" + to_bits(3, 2))

            now = time.time()
            if now - state["last_post_time"] > SIGNAL_TIMEOUT:
                state["post_signals"] = {"0": None, "1": None, "2": None}
            state["last_post_time"] = now

            sig_num = from_bits(val[6:8])

            # Sygnał #3 resetuje układ
            if sig_num == 3:
                print(
                    "[RESET] Odebrano sygnał #3 -> Resetuję stan!", flush=True
                )
                state["post_signals"] = {"0": None, "1": None, "2": None}
                state["get_index"] = 0
                return format_response("000000" + to_bits(3, 2))

            state["post_signals"][str(sig_num)] = val
            print(
                f"[POST] Odebrano Sygnał #{sig_num}: {val} | Stan bufora: {state['post_signals']}",
                flush=True,
            )

            # Jeśli zebrano wszystkie 3 sygnały (0, 1, 2) -> dekodujemy datę i pobieramy cenę!
            if all(
                state["post_signals"][k] is not None for k in ["0", "1", "2"]
            ):
                s0 = state["post_signals"]["0"]
                s1 = state["post_signals"]["1"]
                s2 = state["post_signals"]["2"]

                day = from_bits(s0[0:5])
                month = from_bits(s0[5] + s1[0:3])
                year = 2000 + from_bits(s1[3:6] + s2[0:4])

                print(
                    f"=== ZDEKODOWANO DATĘ: {day:02d}/{month:02d}/{year} ===",
                    flush=True,
                )

                price = get_btc_price_safe(day, month, year, state)
                state["price_bcd_packets"] = prepare_price_packets(price)
                state["get_index"] = 0
                state["post_signals"] = {"0": None, "1": None, "2": None}

                print(
                    f"=== CENA BTC: {price:.2f} USD | PAKIETY GET: {state['price_bcd_packets']} ===",
                    flush=True,
                )

            return format_response("000000" + to_bits(sig_num, 2))

        # ==========================================
        # WYSYŁANIE CENY DO GRY (GET)
        # ==========================================
        else:
            idx = state["get_index"]
            packet_to_send = state["price_bcd_packets"][idx]
            print(f"[GET] Wysyłam pakiet #{idx}/7: {packet_to_send}", flush=True)
            state["get_index"] = (idx + 1) % 8
            return format_response(packet_to_send)

    finally:
        save_and_unlock_state(f, state)


@app.errorhandler(Exception)
def handle_exception(e):
    print(f"[KRYTYCZNY WYJĄTEK] {e}", flush=True)
    return format_response("00000011")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
