from flask import Flask, jsonify, request
import requests

app = Flask(__name__)

# Bufor na stan daty wysyłanej przez urządzenie (3 sygnały POST)
device_date_state = {"signals": {}, "day": 1, "month": 1, "year": 2024}


@app.route("/")
def home():
  return "Serwer Build Logic działa poprawnie pod jednym adresem /sync"


@app.route("/sync", methods=["GET", "POST"])
def sync_endpoint():
  # 1. OBSŁUGA WYSYŁANIA (POST) - kiedy twój układ przesyła datę
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

      # Zwracamy "1" jako potwierdzenie dla układu
      return "1", 200

    except Exception as e:
      print(f"Błąd POST: {e}")
      return "0", 400

  # 2. OBSŁUGA ODBIERANIA (GET) - kiedy blok pobiera kurs BTC
  elif request.method == "GET":
    try:
      res = requests.get(
          "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd",
          timeout=5,
      )
      btc_price = res.json()["bitcoin"]["usd"]
    except Exception:
      btc_price = 65432.1

    integer_part = int(btc_price)
    decimal_part = int((btc_price - integer_part) * 10) & 0x0F

    s0_data = (integer_part >> 12) & 0b111111
    s1_data = (integer_part >> 6) & 0b111111
    s2_data = integer_part & 0b111111
    s3_data = (decimal_part << 2) & 0b111111

    signal_0 = (0 << 6) | s0_data
    signal_1 = (1 << 6) | s1_data
    signal_2 = (2 << 6) | s2_data
    signal_3 = (3 << 6) | s3_data

    signals_str = f"{signal_0},{signal_1},{signal_2},{signal_3}"
    return jsonify({"value": signals_str})


if __name__ == "__main__":
  app.run(host="0.0.0.0", port=5000)
