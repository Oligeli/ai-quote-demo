import os
import imaplib
import smtplib
import email
from email.header import decode_header
from email.mime.text import MIMEText

from flask import Flask, jsonify, render_template_string
from dotenv import load_dotenv
from openai import OpenAI

# Lokálne si vieš pomôcť .env, na Renderi sa použijú env variables
load_dotenv()

# ----- ENV PREMENNÉ -----

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

IMAP_HOST = os.getenv("IMAP_HOST")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
IMAP_USER = os.getenv("IMAP_USER")
IMAP_PASSWORD = os.getenv("IMAP_PASSWORD")

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
EMAIL_FROM = os.getenv("EMAIL_FROM")  # adresa, z ktorej ide ponuka

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

app = Flask(__name__)

# Jednoduchý cenník – demo
PRICE_LIST = {
    "VIZITKY_4_4_500": 0.08,   # 0,08 €/ks, 500 ks
    "LETÁKY_A5_1000": 0.05,    # 0,05 €/ks, 1000 ks
}


def calculate_items():
    """
    DEMO: Nasimulované položky, ktoré by sme normálne vyťahovali z e-mailu.
    """
    requested_items = [
        {"code": "VIZITKY_4_4_500", "qty": 500},
        {"code": "LETÁKY_A5_1000", "qty": 1000},
    ]

    items_with_prices = []
    total_without_vat = 0.0

    for item in requested_items:
        code = item["code"]
        qty = item["qty"]
        unit_price = PRICE_LIST.get(code)

        if unit_price is None:
            continue

        line_total = unit_price * qty
        total_without_vat += line_total

        items_with_prices.append({
            "code": code,
            "qty": qty,
            "unit_price": unit_price,
            "line_total": line_total
        })

    vat = total_without_vat * 0.20
    total_with_vat = total_without_vat + vat

    return items_with_prices, total_without_vat, vat, total_with_vat


def generate_quote_email_text(items_with_prices, total_with_vat, original_subject=None, original_body=None):
    """
    Vygeneruje text cenovej ponuky pomocou GPT.
    """
    if not client:
        # fallback keď nie je API key – aby demo nespadlo
        lines = []
        for item in items_with_prices:
            lines.append(
                f"{item['code']} – {item['qty']} ks × {item['unit_price']:.2f} € = {item['line_total']:.2f} €"
            )
        items_text = "\n".join(lines)
        return (
            "DEMO BEZ OPENAI API\n\n"
            "Položky:\n"
            f"{items_text}\n\n"
            f"Celková cena s DPH: {total_with_vat:.2f} €"
        )

    # Popis položiek
    items_text_lines = []
    for item in items_with_prices:
        line = (
            f"- {item['code']} | množstvo: {item['qty']} ks | "
            f"cena za ks: {item['unit_price']:.2f} € | spolu: {item['line_total']:.2f} €"
        )
        items_text_lines.append(line)
    items_text = "\n".join(items_text_lines)

    extra_context = ""
    if original_subject:
        extra_context += f"Predmet pôvodného dopytu: {original_subject}\n"
    if original_body:
        extra_context += f"Text pôvodného e-mailu:\n{original_body[:1000]}\n"  # skrátime pre istotu

    prompt = f"""
Si obchodný asistent firmy, ktorá robí cenové ponuky.

Na základe nasledujúcich položiek vytvor e-mail s cenovou ponukou v slovenčine.
Buď profesionálny, ale ľudský, vykaj. V úvode poďakuj za dopyt,
potom prehľadne zhrň položky a ceny a na konci jasne uveď CELKOVÚ cenu s DPH
a informáciu o termíne dodania a platnosti ponuky.

Položky:
{items_text}

Celková cena s DPH: {total_with_vat:.2f} €

Doplňujúci kontext:
{extra_context}
"""

    response = client.responses.create(
        model="gpt-4o-mini",
        input=[
            {
                "role": "system",
                "content": "Si slušný obchodník, píšeš stručné a jasné cenové ponuky v slovenčine."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
    )

    email_text = response.output_text
    return email_text


def send_email(to_addr, subject, body):
    """
    Pošle e-mail cez SMTP.
    """
    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM or SMTP_USER
    msg["To"] = to_addr

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)

    print(f"📨 Sent quote e-mail to {to_addr}")


def fetch_latest_unseen_email():
    """
    Stiahne najnovší neprečítaný e-mail z IMAP schránky.
    Vráti (from_addr, subject, body_text) alebo None, ak žiadny nie je.
    """
    if not all([IMAP_HOST, IMAP_USER, IMAP_PASSWORD]):
        print("❗ Chýbajú IMAP nastavenia.")
        return None

    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(IMAP_USER, IMAP_PASSWORD)
    mail.select("INBOX")

    status, messages = mail.search(None, "(UNSEEN)")
    if status != "OK":
        mail.logout()
        return None

    msg_ids = messages[0].split()
    if not msg_ids:
        mail.logout()
        return None

    latest_id = msg_ids[-1]
    status, msg_data = mail.fetch(latest_id, "(RFC822)")
    if status != "OK":
        mail.logout()
        return None

    raw_email = msg_data[0][1]
    msg = email.message_from_bytes(raw_email)

    # From
    from_addr = email.utils.parseaddr(msg.get("From"))[1]

    # Subject
    raw_subject = msg.get("Subject", "")
    dh = decode_header(raw_subject)[0]
    if isinstance(dh[0], bytes):
        subject = dh[0].decode(dh[1] or "utf-8", errors="ignore")
    else:
        subject = dh[0]

    # Body – prvá textová časť
    body_text = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))
            if content_type == "text/plain" and "attachment" not in content_disposition:
                charset = part.get_content_charset() or "utf-8"
                body_text = part.get_payload(decode=True).decode(charset, errors="ignore")
                break
    else:
        charset = msg.get_content_charset() or "utf-8"
        body_text = msg.get_payload(decode=True).decode(charset, errors="ignore")

    # Označíme ako prečítané
    mail.store(latest_id, "+FLAGS", "\\Seen")
    mail.logout()

    return from_addr, subject, body_text


@app.route("/")
def index():
    return """
    <h1>AI cenová ponuka – demo (Render + IMAP/SMTP)</h1>
    <p>1. Pošli testovací e-mail na schránku, ktorú tento skript číta (IMAP_USER).</p>
    <p>2. Potom choď na <a href="/check_email">/check_email</a> – spracuje najnovší neprečítaný e-mail a odošle ponuku späť.</p>
    """


@app.route("/check_email", methods=["GET"])
def check_email():
    """
    Skontroluje IMAP schránku, spracuje najnovší neprečítaný e-mail
    a odošle automatickú ponuku späť odosielateľovi.
    """
    result = fetch_latest_unseen_email()
    if not result:
        return jsonify({"status": "no_unseen_email"}), 200

    from_addr, subject, body_text = result

    # Demo: spočítame fixné položky z cenníka
    items_with_prices, total_without_vat, vat, total_with_vat = calculate_items()

    # Vygenerujeme text ponuky s kontextom pôvodného mailu
    email_text = generate_quote_email_text(
        items_with_prices,
        total_with_vat,
        original_subject=subject,
        original_body=body_text,
    )

    # Tepelne jednoducho: odošleme naspäť odosielateľovi
    reply_subject = f"Re: {subject}" if subject else "Vaša cenová ponuka"
    send_email(from_addr, reply_subject, email_text)
 
    return jsonify({
        "status": "quote_sent",
        "to": from_addr,
        "subject": reply_subject,
        "preview": email_text[:300] + "..."
    }), 200


@app.route("/trigger_example", methods=["GET"])
def trigger_example():
    """
    Čisté demo bez IMAPu – len vygeneruje ponuku a zobrazí ju v prehliadači.
    """
    items_with_prices, total_without_vat, vat, total_with_vat = calculate_items()
    email_text = generate_quote_email_text(items_with_prices, total_with_vat)

    html_template = """
    <h1>Návrh cenovej ponuky (demo)</h1>
    <h2>Text ponuky:</h2>
    <pre style="white-space: pre-wrap; border:1px solid #ddd; padding:1rem; border-radius:8px;">
{{ email_text }}
    </pre>
    """

    return render_template_string(html_template, email_text=email_text)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
