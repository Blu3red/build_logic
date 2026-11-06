from flask import Flask, jsonify, request
import requests

app = Flask(__name__)

# Bufor na stan daty wysyłanej przez urządzenie (3 sygnały POST)
device_date_state = {"signals": {}, "day": 1, "month": 1, "year": 2024}

# Licznik sekwencji dla żądań GET (wydaje kolejno sygnały 0, 1, 2, 3)
get_counter = 0


@app.route("/")
def home():
  return "Serwer Build Logic działa poprawnie!"


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

      # 2 bity nagłówka (identyfikator sygnału 0, 1 lub 2)
      header = (byte_val >> 6) & 0b11
      # 6 bitów danych
      payload = byte_val & 0x3F

      device_date_state["signals"][header] = payload

      # Gdy otrzymamy komplet 3 sygnałów (0, 1, 2)
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

      # Potwierdzenie przyjęcia sygnału dla Twojego układu
      return "1", 200

    except Exception as e:
      print(f"Błąd POST: {e}")
      return "0", 400

  # ==========================================
  # 2. OBSŁUGA ODBIERANIA (GET) - Kurs BTC
  # ==========================================
elif request.method == "GET":
    try:
      res = requests.get(
          "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT",
          timeout=5,
      )
      btc_price = float(res.json()["price"])
    except Exception as e:
      print(f"Błąd pobierania Binance: {e}")
      btc_price = 65432.1

    integer_part = int(btc_price)
    decimal_part = int((btc_price - integer_part) * 10) & 0x0F

    s0_data = (integer_part >> 12) & 0b111111
    s1_data = (integer_part >> 6) & 0b111111
    s2_data = integer_part & 0b111111
    s3_data = (decimal_part << 2) & 0b111111

    signals_list = [
        (0 << 6) | s0_data,
        (1 << 6) | s1_data,
        (2 << 6) | s2_data,
        (3 << 6) | s3_data,
    ]

    current_signal = signals_list[get_counter]
    
    # KLUCZOWA ZMIANA: Tworzymy czysty 8-bitowy ciąg znaków (np. "10011000")
    bin_str = format(current_signal, '08b')
    
    print(f"GET - Wydaję sygnał nr {get_counter}: {bin_str} (liczba: {current_signal})")

    get_counter = (get_counter + 1) % 4

    return jsonify({"value": bin_str})


if __name__ == "__main__":
  app.run(host="0.0.0.0", port=5000)
