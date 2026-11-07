from datetime import datetime, timezone
import json
import os
from flask import Flask, Response, request
import requests

app = Flask(__name__)

# ==============================================================================
# KONFIGURACJA BITÓW
# True  -> kolejność wag od lewej do prawej to: 1, 2, 4, 8, 16... (np. liczba 1 na 4 bitach to "1000")
# False -> kolejność wag od lewej do prawej to: 8, 4, 2, 1 (np. liczba 1 na 4 bitach to "0001")
# ==============================================================================
LSB_FIRST = True

# Stan serwera
state = {
    "post_signals": {0: None, 1: None, 2: None},
    "price_bcd_packets": ["00000000"] * 8,
    "get_index": 0,
}


def from_bits(bit_str):
    """Zamienia string z bitami (0 i 1) na liczbę całkowitą z uwzględnieniem kolejności wag."""
    if LSB_FIRST:
        return int(bit_str[::-1], 2)
    return int(bit_str, 2)


def to_bits(val, length):
    """Zamienia liczbę całkowitą na string bitów o podanej długości z uwzględnieniem kolejności wag."""
    b = f"{val:0{length}b}"
    if LSB_FIRST:
        return b[::-1]
    return b


def format_response(bit_string):
    """Zwraca ściśle określony JSON {"value":"00000000"} bez zbędnych spacji."""
    json_str = json.dumps({"value": bit_string}, separators=(",", ":"))
    return Response(json_str, mimetype="application/json")


def get_btc_price(day, month, year):
    """Pobiera cenę zamknięcia BTC/USDT z danego dnia (używa endpointu odpornego na blokady USA w Render.com)."""
    try:
        dt = datetime(year, month, day, 0, 0, 0, tzinfo=timezone.utc)
        timestamp_ms = int(dt.timestamp() * 1000)

        # Endpoint data-api.binance.vision działa na serwerach w USA (Render.com)
        url = "https://data-api.binance.vision/api/v3/klines"
        params = {
            "symbol": "BTCUSDT",
            "interval": "1d",
            "startTime": timestamp_ms,
            "limit": 1,
        }
        res = requests.get(url, params=params, timeout=5)
        data = res.json()

        if isinstance(data, list) and len(data) > 0:
            close_price = float(data[0][4])  # Cena zamknięcia z danego dnia
            return close_price
        else:
            print(f"[API] Brak danych dla daty {day:02d}/{month:02d}/{year}.")
            return 0.0
    except Exception as e:
        print(f"[BŁĄD] Nieprawidłowa data lub problem z API ({day}/{month}/{year}): {e}")
        return 0.0


def prepare_price_packets(price):
    """Zamienia cenę (np. 64321.50) na 8 sygnałów (xxx xxx,xx):

    Pierwsze 4 bity: cyfra (0-9)
    Ostatnie 4 bity: numer sygnału (0-7)
    """
    price_int = int(round(price * 100))
    # Dopełnienie zerami z lewej strony do 8 cyfr (np. 64321.50 -> "06432150")
    price_str = f"{price_int:08d}"[-8:]

    packets = []
    for sig_num, digit_char in enumerate(price_str):
        digit_val = int(digit_char)
        digit_bits = to_bits(digit_val, 4)  # 4 bity dla cyfry
        sig_bits = to_bits(sig_num, 4)  # 4 bity dla numeru sygnału (0-7)
        packets.append(digit_bits + sig_bits)

    print(f"[CENA] {price:.2f} USD -> Pakiety do GET: {packets}")
    return packets


def extract_value_from_request(req):
    """Wyciąga 8-bitowy string spod klucza 'value' niezależnie od tego jak Roblox go zakodował."""
    if "value" in req.form:
        return req.form["value"]
    if "value" in req.args:
        return req.args["value"]

    raw_data = req.get_data(as_text=True)
    try:
        parsed = json.loads(raw_data)
        if "value" in parsed:
            return parsed["value"]
    except:
        pass

    # Gdy Roblox wysyła JSON jako klucz w ApplicationUrlEncoded
    for key in req.form.keys():
        try:
            parsed = json.loads(key)
            if "value" in parsed:
                return parsed["value"]
        except:
            pass

    return "00000000"


@app.route("/", methods=["POST", "GET"])
def handle_transmitter():
    # ==========================================
    # ODBIERANIE DATY (POST)
    # ==========================================
    if request.method == "POST":
        val = extract_value_from_request(request).strip()
        print(f"[POST] Odebrano bity: {val}")

        if len(val) != 8 or not all(c in "01" for c in val):
            print("[BŁĄD] Odebrana wartość nie ma 8 bitów!")
            return format_response("11111111")

        # Ostatnie 2 bity (indeksy 6 i 7) to numer sygnału (0, 1 lub 2)
        sig_num = from_bits(val[6:8])

        if sig_num in [0, 1, 2]:
            state["post_signals"][sig_num] = val
            print(f" -> Zapisano jako Sygnał #{sig_num}")
        else:
            print(f" -> [UWAGA] Numer sygnału {sig_num} spoza zakresu 0-2!")

        # Sprawdzamy czy mamy już komplet 3 sygnałów (0, 1 oraz 2)
        if all(state["post_signals"][i] is not None for i in [0, 1, 2]):
            s0 = state["post_signals"][0]
            s1 = state["post_signals"][1]
            s2 = state["post_signals"][2]

            # Składanie bitów wg schematu: 5(0:0-4) / 4(0:5, 1:0-2) / 7(1:3-5, 2:0-3)
            day_bits = s0[0:5]  # 5 bitów dnia
            month_bits = s0[5] + s1[0:3]  # 4 bity miesiąca
            year_bits = s1[3:6] + s2[0:4]  # 7 bitów roku

            day = from_bits(day_bits)
            month = from_bits(month_bits)
            year = 2000 + from_bits(year_bits)

            print(f"=== ZDEKODOWANO DATĘ: {day:02d}/{month:02d}/{year} ===")

            # Pobieramy cenę BTC i generujemy 8 pakietów odpowiedzi
            price = get_btc_price(day, month, year)
            state["price_bcd_packets"] = prepare_price_packets(price)
            state["get_index"] = 0  # Resetujemy indeks dla nadchodzących zapytań GET

            # Czyścimy odebrane sygnały pod kolejne zapytanie
            state["post_signals"] = {0: None, 1: None, 2: None}

        # Zwracamy numer odebranego sygnału na dwóch ostatnich bitach dla weryfikacji
        verification_response = "000000" + to_bits(sig_num, 2)
        print(f"[POST] Zwracam weryfikację: {verification_response}")
        return format_response(verification_response)

    # ==========================================
    # WYSYŁANIE CENY DO GRY (GET)
    # ==========================================
    elif request.method == "GET":
        idx = state["get_index"]
        packet_to_send = state["price_bcd_packets"][idx]

        print(f"[GET] Wysyłam pakiet #{idx}/7: {packet_to_send}")

        # Przechodzimy do kolejnego pakietu (0 -> 1 -> ... -> 7 -> 0)
        state["get_index"] = (idx + 1) % 8

        return format_response(packet_to_send)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
