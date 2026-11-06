from flask import Flask, jsonify, request
import requests

app = Flask(__name__)

# Tymczasowy bufor na stan daty wysyłanej przez urządzenie (3 sygnały POST)
device_date_state = {"signals": {}, "day": 1, "month": 1, "year": 2024}


@app.route("/device", methods=["POST"])
def receive_device_data():
  try:
    content = request.json
    byte_val = content.get("byte", 0)

    # 2 starsze bity to nagłówek (identyfikator sygnału: 0, 1 lub 2)
    header = (byte_val >> 6) & 0b11
    # 6 młodszych bitów to dane (poprawiono z 0b3F na 0x3F)
    payload = byte_val & 0x3F

    device_date_state["signals"][header] = payload

    # Gdy otrzymamy komplet 3 sygnałów (0, 1, 2)
    if all(k in device_date_state["signals"] for k in [0, 1, 2]):
      s0 = device_date_state["signals"][0]
      s1 = device_date_state["signals"][1]
      s2 = device_date_state["signals"][2]

      # --- PARSANIE DATY ZGODNIE Z TWOIM OPISEM ---
      # Dzień: 5 bitów z sygnału 0 (0:0-4)
      day = s0 & 0x1F

      # Miesiąc: 4 bity -> 1 bit z S0 (bit 5) + 3 bity z S1 (0-2)
      bit5_s0 = (s0 >> 5) & 0x01
      bits_0_2_s1 = s1 & 0x07
      month = (bit5_s0 << 3) | bits_0_2_s1

      # Rok: 7 bitów -> 3 bity z S1 (3-5) + 4 bity z S2 (0-3) + dodajemy 2000
      bits_3_5_s1 = (s1 >> 3) & 0x07
      bits_0_3_s2 = s2 & 0x0F
      year_offset = (bits_3_5_s1 << 4) | bits_0_3_s2
      year = 2000 + year_offset

      device_date_state["day"] = day
      device_date_state["month"] = month
      device_date_state["year"] = year

      # Reset bufora dla kolejnej paczki
      device_date_state["signals"] = {}

    # Wymagane potwierdzenie jako "1" dla układu
    return "1", 200

  except Exception as e:
    # W razie anomalii / błędu nie zwracamy "1", co wymusi powtórzenie w układzie
    return "0", 400


@app.route("/roblox", methods=["GET"])
def roblox_get():
  # Pobieramy aktualny kurs BTC z darmowego API (np. CoinGecko)
  try:
    res = requests.get(
        "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd",
        timeout=5,
    )
    btc_price = res.json()["bitcoin"]["usd"]
  except Exception:
    btc_price = 65432.1  # Fallback w razie awarii zewnętrznego API

  # Rozbijamy cenę na część całkowitą i pierwszą cyfrę po przecinku
  integer_part = int(btc_price)
  decimal_part = int((btc_price - integer_part) * 10) & 0x0F

  # --- PAKOWANIE DO 4 SYGNALÓW GET (po 8 bitów: 2 bity nagłówka + 6 bitów danych) ---
  s0_data = (integer_part >> 12) & 0b111111
  s1_data = (integer_part >> 6) & 0b111111
  s2_data = integer_part & 0b111111
  s3_data = (decimal_part << 2) & 0b111111

  # Doklejamy 2 bity nagłówka (0, 1, 2, 3 przesunięte o 6 bitów)
  signal_0 = (0 << 6) | s0_data
  signal_1 = (1 << 6) | s1_data
  signal_2 = (2 << 6) | s2_data
  signal_3 = (3 << 6) | s3_data

  return jsonify({
      "signals": [signal_0, signal_1, signal_2, signal_3],
      "saved_date": {
          "day": device_date_state["day"],
          "month": device_date_state["month"],
          "year": device_date_state["year"],
      },
      "raw_btc": btc_price,
  })


if __name__ == "__main__":
  app.run(host="0.0.0.0", port=5000)
