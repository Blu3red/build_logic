from flask import Flask, jsonify, request
import requests

app = Flask(__name__)

# Bufor na datę z 3 sygnałów POST (zostaje po staremu)
device_date_state = {"signals": {}, "day": 1, "month": 1, "year": 2024}

# Licznik dla żądań GET (cykl 8 sygnałów: 0 do 7)
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

      # Dla POST daty zostawiamy 2 bity nagłówka i 6 bitów danych
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
        print(f"Zapisano datę: {day}/{month}/{year}")

      return "1", 200

    except Exception as e:
      print(f"Błąd POST: {e}")
      return "0", 400

  # ==========================================
  # 2. OBSŁUGA ODBIERANIA (GET) - Cyfry BCD BTC (2 miejsca po przecinku)
  # ==========================================
  elif request.method == "GET":
    user_agent = request.headers.get("User-Agent", "")

    try:
      res = requests.get(
          "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT",
          timeout=5,
      )
      btc_price = float(res.json()["price"])
    except Exception as e:
      print(f"Błąd pobierania Binance: {e}")
      btc_price = 95432.18

    # Mnożymy cenę przez 100, aby zamienić ją na całkowitą liczbę centów
    total_cents = int(round(btc_price * 100))

    # Wyciągamy poszczególne cyfry dziesiętne z centów
    d_100k = (total_cents // 10000000) % 10  # 100 tysięcy
    d_10k = (total_cents // 1000000) % 10  # 10 tysięcy
    d_1k = (total_cents // 100000) % 10  # Tysiące
    d_100 = (total_cents // 10000) % 10  # Setki
    d_10 = (total_cents // 1000) % 10  # Dziesiątki
    d_1 = (total_cents // 100) % 10  # Jedności
    d_dec1 = (total_cents // 10) % 10  # 1. cyfra po przecinku (dziesiąte)
    d_dec2 = total_cents % 10  # 2. cyfra po przecinku (setne)

    # 4 bity nagłówka (bity 7-4) + 4 bity cyfry BCD (bity 3-0)
    signals_list = [
        (0b0000 << 4) | (d_100k & 0x0F),  # Sygnał 0: 100k
        (0b0001 << 4) | (d_10k & 0x0F),  # Sygnał 1: 10k
        (0b0010 << 4) | (d_1k & 0x0F),  # Sygnał 2: 1k
        (0b0011 << 4) | (d_100 & 0x0F),  # Sygnał 3: Setki
        (0b0100 << 4) | (d_10 & 0x0F),  # Sygnał 4: Dziesiątki
        (0b0101 << 4) | (d_1 & 0x0F),  # Sygnał 5: Jedności
        (0b0110 << 4) | (d_dec1 & 0x0F),  # Sygnał 6: .X
        (0b0111 << 4) | (d_dec2 & 0x0F),  # Sygnał 7: .XX
    ]

    current_signal = signals_list[get_counter]
    bin_str = format(current_signal, "08b")

    print(
        f"GET - Sygnał nr {get_counter}: {bin_str} (Cyfra:"
        f" {current_signal & 0x0F}, Cena: {btc_price}) [Agent: {user_agent}]"
    )

    # Zwiększamy licznik w cyklu 8-elementowym (0 do 7)
    get_counter = (get_counter + 1) % 8

    return jsonify({"value": bin_str})


if __name__ == "__main__":
  app.run(host="0.0.0.0", port=5000)
