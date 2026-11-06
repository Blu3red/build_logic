from datetime import datetime, timezone
from flask import Flask, jsonify, request
import requests

app = Flask(__name__)

# Bufor na datę z 3 sygnałów POST (domyślnie 1 listopada 2024)
device_date_state = {"signals": {}, "day": 1, "month": 11, "year": 2024}

# Zapasowy licznik dla żądań GET (cykl 8 sygnałów: 0 do 7)
get_counter = 0


@app.route("/")
def home():
  return "Serwer Build Logic działa poprawnie (tryb historyczny)"


@app.route("/sync", methods=["GET", "POST"])
def sync_endpoint():
  global get_counter

  # ==========================================
  # 1. OBSŁUGA WYSYŁANIA (POST) - Zapis daty
  # ==========================================
  if request.method == "POST":
    try:
      val_str = (
          request.form.get("value")
          or request.args.get("value")
          or (request.json.get("value") if request.is_json else None)
      )

      if val_str is None and request.form:
        val_str = list(request.form.keys())[0]

      if val_str is None:
        val_str = "0"

      val_str = str(val_str).strip()
      if all(c in "01" for c in val_str) and len(val_str) > 1:
        byte_val = int(val_str, 2)
      else:
        byte_val = int(float(val_str))

      header = (byte_val >> 6) & 0b11
      payload = byte_val & 0x3F

      device_date_state["signals"][header] = payload

      if all(k in device_date_state["signals"] for k in [0, 1, 2]):
        s0 = device_date_state["signals"][0]
        s1 = device_date_state["signals"][1]
        s2 = device_date_state["signals"][2]

        day = s0 & 0x1F
        bit5_s0 = (s0 >> 5) & 0x01
        bits_0_2_s1 = s1 & 0x07
        month = (bit5_s0 << 3) | bits_0_2_s1

        bits_3_5_s1 = (s1 >> 3) & 0x07
        bits_0_3_s2 = s2 & 0x0F
        year_offset = (bits_3_5_s1 << 4) | bits_0_3_s2
        year = 2000 + year_offset

        device_date_state["day"] = day
        device_date_state["month"] = month
        device_date_state["year"] = year
        device_date_state["signals"] = {}
        print(f"Zapisano nową datę historyczną: {day}/{month}/{year}")

      return "1", 200

    except Exception as e:
      print(f"Błąd POST: {e}")
      return "0", 400

  # ==========================================
  # 2. OBSŁUGA ODBIERANIA (GET) - Historyczna cena BTC
  # ==========================================
  elif request.method == "GET":
    user_agent = request.headers.get("User-Agent", "")

    y = device_date_state["year"]
    m = device_date_state["month"]
    d = device_date_state["day"]

    btc_price = 70000.0  # Fallback w razie błędu API
    try:
      # Precyzyjne obliczenie timestampu w UTC dla konkretnego dnia
      dt = datetime(y, m, d, tzinfo=timezone.utc)
      start_ts = int(dt.timestamp() * 1000)
      end_ts = start_ts + 86400000  # +24h w milisekundach

      url = (
          f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d"
          f"&startTime={start_ts}&endTime={end_ts}&limit=1"
      )
      res = requests.get(url, timeout=5)
      data = res.json()
      if data and len(data) > 0:
        btc_price = float(data[0][4])  # Cena zamknięcia (Close)
    except Exception as e:
      print(f"Błąd pobierania historycznego z Binance dla {d}/{m}/{y}: {e}")

    # Konwersja ceny na centy (2 miejsca po przecinku)
    total_cents = int(round(btc_price * 100))

    d_100k = (total_cents // 10000000) % 10
    d_10k = (total_cents // 1000000) % 10
    d_1k = (total_cents // 100000) % 10
    d_100 = (total_cents // 10000) % 10
    d_10 = (total_cents // 1000) % 10
    d_1 = (total_cents // 100) % 10
    d_dec1 = (total_cents // 10) % 10
    d_dec2 = total_cents % 10

    # 8 sygnałów (nagłówek 4 bity + cyfra BCD 4 bity)
    signals_list = [
        (0b0000 << 4) | (d_100k & 0x0F),
        (0b0001 << 4) | (d_10k & 0x0F),
        (0b0010 << 4) | (d_1k & 0x0F),
        (0b0011 << 4) | (d_100 & 0x0F),
        (0b0100 << 4) | (d_10 & 0x0F),
        (0b0101 << 4) | (d_1 & 0x0F),
        (0b0110 << 4) | (d_dec1 & 0x0F),
        (0b0111 << 4) | (d_dec2 & 0x0F),
    ]

    # Obsługa jawnego kroku z URL (np. ?step=0 do 7) lub automatyczny licznik
    step_param = request.args.get("step") or request.args.get("index")
    if step_param is not None and step_param.isdigit():
      current_step = int(step_param) % 8
    else:
      current_step = get_counter
      get_counter = (get_counter + 1) % 8

    current_signal = signals_list[current_step]
    bin_str = format(current_signal, "08b")

    print(
        f"GET (Data {d}/{m}/{y}) - Sygnał nr {current_step}: {bin_str} (Cyfra:"
        f" {current_signal & 0x0F}, Cena historyczna: {btc_price})"
        f" [Agent: {user_agent}]"
    )

    return jsonify({"value": bin_str})


if __name__ == "__main__":
  app.run(host="0.0.0.0", port=5000)
