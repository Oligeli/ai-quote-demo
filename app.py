
import os
from flask import Flask, request, jsonify
from twilio.rest import Client as TwilioClient
from dotenv import load_dotenv
from openai import OpenAI

# Načíta .env ak existuje (lokálne), na Renderi použijeme env vars
load_dotenv()

# --- Konfigurácia z env premenných ---

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM")  # napr. "whatsapp:+14155238886"
MANAGER_WHATSAPP_TO = os.getenv("MANAGER_WHATSAPP_TO")    # napr. "whatsapp:+4219XXXXXXXX"

# Inicializácia klientov (na Renderi už budú env premenné nastavené)
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN) if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN else None

app = Flask(__name__)

# --- Jednoduchý 'cenník' ako dict (produkt_kód -> cena za kus bez DPH) ---

price_list = {
    "VIZITKY_4_4_500": 0.08,
}

pending_quote = {
    "items": [],
    "missing_codes": []
}


def send_whatsapp_message(text: str):
    """Pošle správu na WhatsApp manažérovi."""
    if not twilio_client:
        print("⚠️ Twilio klient nie je inicializovaný. Správa by mala byť:")
        print(text)
        return

    message = twilio_client.messages.create(
        body=text,
        from_=TWILIO_WHATSAPP_FROM,
        to=MANAGER_WHATSAPP_TO
    )
    print(f"📨 WhatsApp message SID: {message.sid}")


def generate_quote_email_text(items_with_prices, total_with_vat):
    """Použije OpenAI na vygenerovanie textu ponuky (e-mail)."""
    if not client:
        # fallback – keby nebol nastavený OPENAI_API_KEY
        lines = []
        for item in items_with_prices:
            lines.append(f"{item['code']} x {item['qty']} ks = {item['line_total']:.2f} €")
        items_text = "\n".join(lines)
        return f"(DEMO BEZ OPENAI)\nPoložky:\n{items_text}\n\nCelkom s DPH: {total_with_vat:.2f} €"

    items_text_lines = []
    for item in items_with_prices:
        line = f"- {item['code']} | množstvo: {item['qty']} ks | cena za ks: {item['unit_price']:.2f} € | spolu: {item['line_total']:.2f} €"
        items_text_lines.append(line)
    items_text = "\n".join(items_text_lines)

    prompt = f"""
Si obchodný asistent firmy, ktorá robí cenové ponuky.

Na základe nasledujúcich položiek vytvor e-mail s cenovou ponukou v slovenčine.
Buď profesionálny, ale ľudský, vykaj, zhrň položky a na konci uveď CELKOVÚ cenu s DPH.

Položky:
{items_text}

Celková cena s DPH: {total_with_vat:.2f} €
"""

    response = client.responses.create(
        model="gpt-4o-mini",
        input=[
            {"role": "system", "content": "Si slušný obchodník, píšeš stručné a jasné cenové ponuky v slovenčine."},
            {"role": "user", "content": prompt}
        ]
    )

    email_text = response.output_text
    return email_text


def calculate_quote(items):
    known_items = []
    missing_codes = []

    for item in items:
        code = item["code"]
        qty = item["qty"]
        if code in price_list:
            unit_price = price_list[code]
            line_total = unit_price * qty
            known_items.append({
                "code": code,
                "qty": qty,
                "unit_price": unit_price,
                "line_total": line_total
            })
        else:
            missing_codes.append({"code": code, "qty": qty})

    return known_items, missing_codes


@app.route("/trigger_example", methods=["GET"])
def trigger_example():
    """
    DEMO: Nasimuluje príchod e-mailu s cenovou požiadavkou.
    """
    global pending_quote

    requested_items = [
        {"code": "VIZITKY_4_4_500", "qty": 500},
        {"code": "NEZNAMY_PRODUKT", "qty": 100}
    ]

    known_items, missing_codes = calculate_quote(requested_items)

    pending_quote["items"] = requested_items
    pending_quote["missing_codes"] = missing_codes

    if missing_codes:
        lines = []
        for m in missing_codes:
            lines.append(f"{m['code']} (množstvo {m['qty']} ks)")
        missing_text = "\n".join(lines)

        msg = (
            "🔍 Pri cenovej ponuke chýbajú ceny pre tieto produkty:\n"
            f"{missing_text}\n\n"
            "Odpíš na WhatsApp vo formáte:\n"
            "KOD_PRODUKTU=cena_za_ks\n"
            "Príklad: NEZNAMY_PRODUKT=12.50"
        )
        send_whatsapp_message(msg)

        return jsonify({
            "status": "waiting_for_prices",
            "missing_codes": missing_codes
        })

    else:
        total_without_vat = sum(i["line_total"] for i in known_items)
        vat = total_without_vat * 0.20
        total_with_vat = total_without_vat + vat

        email_text = generate_quote_email_text(known_items, total_with_vat)

        send_whatsapp_message("✅ Hotový návrh cenovej ponuky:\n\n" + email_text)

        return jsonify({
            "status": "quote_ready",
            "email_text": email_text
        })


@app.route("/whatsapp", methods=["POST"])
def whatsapp_webhook():
    global price_list, pending_quote

    from_number = request.form.get("From", "")
    body = request.form.get("Body", "").strip()

    print(f"📥 WhatsApp od {from_number}: {body}")

    if "=" not in body:
        send_whatsapp_message("❌ Nerozumiem formátu. Použi prosím KOD=12.50")
        return "OK", 200

    code, price_str = body.split("=", 1)
    code = code.strip()
    price_str = price_str.strip().replace(",", ".")

    try:
        unit_price = float(price_str)
    except ValueError:
        send_whatsapp_message("❌ Cena musí byť číslo, napr. 12.50")
        return "OK", 200

    price_list[code] = unit_price
    send_whatsapp_message(f"✅ Nastavil som cenu produktu {code} na {unit_price:.2f} € / ks.")

    if pending_quote["items"]:
        all_known_items, still_missing = calculate_quote(pending_quote["items"])

        if still_missing:
            lines = []
            for m in still_missing:
                lines.append(f"{m['code']} (množstvo {m['qty']} ks)")
            missing_text = "\n".join(lines)

            msg = (
                "Stále chýbajú ceny pre:\n"
                f"{missing_text}\n\n"
                "Odpíš vo formáte KOD=10.50"
            )
            send_whatsapp_message(msg)
        else:
            total_without_vat = sum(i["line_total"] for i in all_known_items)
            vat = total_without_vat * 0.20
            total_with_vat = total_without_vat + vat

            email_text = generate_quote_email_text(all_known_items, total_with_vat)

            send_whatsapp_message("📄 Hotový návrh cenovej ponuky:\n\n" + email_text)

            pending_quote["items"] = []
            pending_quote["missing_codes"] = []

    return "OK", 200


@app.route("/")
def index():
    return """
    <h1>AI cenová ponuka – demo (Render)</h1>
    <p>Pre simuláciu príchodu cenovej požiadavky choď na <a href="/trigger_example">/trigger_example</a></p>
    """


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
