import os
import json
import imaplib
import smtplib
import email
from email.header import decode_header
from email.mime.text import MIMEText

from flask import Flask, jsonify, render_template_string
from dotenv import load_dotenv
from openai import OpenAI

# Na lokále si vieš pomôcť .env, na Renderi použiješ Environment variables
load_dotenv()

# --- ENV PREMENNÉ ---

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

IMAP_HOST = os.getenv("IMAP_HOST")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
IMAP_USER = os.getenv("IMAP_USER")
IMAP_PASSWORD = os.getenv("IMAP_PASSWORD")

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
EMAIL_FROM = os.getenv("EMAIL_FROM")  # adresa, z ktorej sa bude odosielať ponuka

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

app = Flask(__name__)

# --------------------------------------------------------------------
# KATALÓG FÓLIÍ – TU SI BUDEŠ DOPĹŇAŤ SVOJE TYPY + CENY
# --------------------------------------------------------------------

FOIL_PRODUCTS = [
    {
        "code": "XPEL_ULTIMATE_PLUS",
        "brand": "XPEL",
        "name": "XPEL Ultimate Plus",
        "finish": "lesk",
        "thickness_microns": 200,
        "price_per_m2": 65.0,
        "recommended_for": "maximálna ochrana laku, autá vyššej triedy, dlhodobé používanie",
    },
    {
        "code": "XPEL_STEALTH",
        "brand": "XPEL",
        "name": "XPEL Stealth",
        "finish": "mat",
        "thickness_microns": 200,
        "price_per_m2": 70.0,
        "recommended_for": "matný vzhľad, zmena dizajnu, ochrana aj estetický efekt",
    },
    {
        "code": "XPEL_ECONOMY",
        "brand": "XPEL",
        "name": "XPEL Economy",
        "finish": "lesk",
        "thickness_microns": 150,
        "price_per_m2": 45.0,
        "recommended_for": "cenovo citliví zákazníci, základná ochrana, firemné autá",
    },
]

# --------------------------------------------------------------------
# AI "MOZOG NA FÓLIE" + CENOTVORBA
# --------------------------------------------------------------------


def ai_select_foil(email_text: str) -> dict:
    """
    AI 'mozog na fólie':
    - prečíta text dopytu,
    - pozrie sa na FOIL_PRODUCTS,
    - vyberie najvhodnejší produkt,
    - odhadne plochu v m²,
    - vráti JSON.
    """
    catalog_str = json.dumps(FOIL_PRODUCTS, ensure_ascii=False)

    prompt = f"""
Si odborník na ochranné fólie na autá (PPF). Máš katalóg produktov (XPEL a pod.).
Na základe textu dopytu vyber najvhodnejší produkt z katalógu a odhadni plochu v m².

Výstup vráť STRICTNE ako platný JSON s týmito kľúčmi:
- "product_code": kód vybraného produktu z katalógu (pole 'code')
- "area_m2": číselný odhad plochy v m², ktorú chce zákazník polepiť
- "reason": krátke vysvetlenie (po slovensky), prečo si vybral práve tento produkt
- "notes_for_pricing": poznámky pre cenotvorbu (napr. či je to len nárazník, celá predná časť, celé auto...)

KATALÓG PRODUKTOV (JSON):
{catalog_str}

TEXT DOPYTU:
{email_text}
"""

    if not client:
        # Fallback, keby si nemal nastavený OPENAI_API_KEY
        return {
            "product_code": "XPEL_ULTIMATE_PLUS",
            "area_m2": 4.0,
            "reason": "DEMO režim bez OpenAI – vyberám XPEL Ultimate Plus.",
            "notes_for_pricing": "Predná časť auta – odhad."
        }

    response = client.responses.create(
        model="gpt-4o-mini",
        input=[
            {
                "role": "system",
                "content": "Si odborník na PPF fólie a pomáhaš vybrať správny typ fólie."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
    )

    raw = response.output_text.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {
            "product_code": None,
            "area_m2": 4.0,
            "reason": f"Nepodarilo sa načítať JSON. AI odpoveď bola: {raw}",
            "notes_for_pricing": ""
        }

    return data


def find_product_by_code(code: str):
    for p in FOIL_PRODUCTS:
        if p["code"] == code:
            return p
    return None


def calculate_price(selection: dict):
    """
    Pre vybraný produkt a odhad plochy spočíta cenu:
    materiál + práca + DPH 20 %.
    """
    product = find_product_by_code(selection.get("product_code"))
    if not product:
        return None

    try:
        area = float(selection.get("area_m2", 0))
    except (TypeError, ValueError):
        area = 0.0

    price_per_m2 = product["price_per_m2"]

    material_price = area * price_per_m2
    labour_price = area * 40.0  # príklad práce 40 €/m²
    total_without_vat = material_price + labour_price
    vat = total_without_vat * 0.20
    total_with_vat = total_without_vat + vat

    return {
        "product": product,
        "area_m2": area,
        "material_price": material_price,
        "labour_price": labour_price,
        "total_without_vat": total_without_vat,
        "vat": vat,
        "total_with_vat": total_with_vat,
    }


def generate_quote_email(email_text: str, original_subject: str | None = None) -> dict:
    """
    Kompletný flow:
    - AI vyberie fóliu + odhadne plochu,
    - spočíta cenu,
    - AI vygeneruje text e-mailu s ponukou.
    """
    selection = ai_select_foil(email_text)
    pricing = calculate_price(selection)

    if not pricing:
        return {
            "email_text": "Ospravedlňujeme sa, nepodarilo sa nájsť vhodný produkt v cenníku.",
            "selection": selection,
            "pricing": None
        }

    product = pricing["product"]

    summary_for_ai = f"""
Vybraná fólia: {product['name']} ({product['code']})
Značka: {product['brand']}
Povrch: {product['finish']}
Hrúbka: {product['thickness_microns']} mikrónov
Odporúčané použitie: {product['recommended_for']}

Odhadovaná plocha: {pricing['area_m2']:.2f} m²
Cena materiálu (fólia): {pricing['material_price']:.2f} €
Cena práce: {pricing['labour_price']:.2f} €
Medzisúčet bez DPH: {pricing['total_without_vat']:.2f} €
DPH 20 %: {pricing['vat']:.2f} €
Celková cena s DPH: {pricing['total_with_vat']:.2f} €

Dôvod výberu fólie (AI): {selection.get('reason', '')}
Poznámky k použitiu: {selection.get('notes_for_pricing', '')}
Pôvodný predmet dopytu: {original_subject or ''}
"""

    if not client:
        email_text_out = (
            "DEMO bez OpenAI API – ukážka dát, ktoré by šli do ponuky:\n\n"
            + summary_for_ai
        )
    else:
        prompt = f"""
Na základe nasledujúcich informácií priprav profesionálnu cenovú ponuku v slovenčine.
Na začiatku poďakuj za dopyt, zhrň čo odporúčaš (typ fólie a prečo),
uved prehľadnú cenu (materiál, práca, celková cena s DPH)
a na konci pridaj informáciu o termíne montáže a platnosti ponuky.
Píš vecne, ale ľudsky, vykaj.

Informácie:
{summary_for_ai}
"""

        response = client.responses.create(
            model="gpt-4o-mini",
            input=[
                {
                    "role": "system",
                    "content": "Si obchodník, ktorý pripravuje cenové ponuky na ochranné fólie XPEL."
                },
                {"role": "user", "content": prompt},
            ],
        )
        email_text_out = response.output_text

    return {
        "email_text": email_text_out,
        "selection": selection,
        "pricing": pricing
    }

# --------------------------------------------------------------------
# EMAIL UTILITKY – IMAP (čítanie) + SMTP (odoslanie)
# --------------------------------------------------------------------


def fetch_latest_unseen_email():
    """
    Stiahne najnovší neprečítaný e-mail z IMAP INBOX-u.
    Vráti (from_addr, subject, body_text) alebo None.
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

    from_addr = email.utils.parseaddr(msg.get("From"))[1]

    raw_subject = msg.get("Subject", "")
    dh = decode_header(raw_subject)[0]
    if isinstance(dh[0], bytes):
        subject = dh[0].decode(dh[1] or "utf-8", errors="ignore")
    else:
        subject = dh[0]

    body_text = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition") or "")
            if content_type == "text/plain" and "attachment" not in content_disposition:
                charset = part.get_content_charset() or "utf-8"
                body_text = part.get_payload(decode=True).decode(charset, errors="ignore")
                break
    else:
        charset = msg.get_content_charset() or "utf-8"
        payload = msg.get_payload(decode=True)
        if payload is not None:
            body_text = payload.decode(charset, errors="ignore")

    # Označíme ako prečítané
    mail.store(latest_id, "+FLAGS", "\\Seen")
    mail.logout()

    return from_addr, subject, body_text


def send_email(to_addr: str, subject: str, body: str):
    """
    Pošle e-mail cez SMTP.
    """
    if not all([SMTP_HOST, SMTP_USER, SMTP_PASSWORD]):
        print("❗ Chýbajú SMTP nastavenia. IBA PRINTUJEM:")
        print("To:", to_addr)
        print("Subject:", subject)
        print("Body:", body[:500])
        return

    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM or SMTP_USER
    msg["To"] = to_addr

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)

    print(f"📨 Odoslaný e-mail s ponukou na {to_addr}")


# --------------------------------------------------------------------
# FLASK ROUTES – WEBOVÉ ROZHRANIE NA RENDERI
# --------------------------------------------------------------------

INDEX_HTML = """
<!doctype html>
<html lang="sk">
<head>
  <meta charset="utf-8">
  <title>AI cenová ponuka – XPEL (email → AI → email)</title>
  <style>
    body { font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif; margin: 2rem auto; max-width: 900px; line-height: 1.5; }
    code { background:#f5f5f5; padding:0.15rem 0.35rem; border-radius:4px; }
    pre { white-space: pre-wrap; font-size:0.9rem; background:#fafafa; border-radius:8px; padding:1rem; border:1px solid #eee; }
    .card { border: 1px solid #ddd; border-radius: 8px; padding: 1rem 1.25rem; margin-top: 1rem; background: #fcfcfc; }
  </style>
</head>
<body>
  <h1>AI cenová ponuka – XPEL fólie (demo)</h1>
  <p>Flow:</p>
  <ol>
    <li>Pošli e-mail na schránku <code>{{ imap_user }}</code> s dopytom typu:<br>
      <em>"Zdravím, potreboval by som XPEL fóliu na prednú časť auta, auto je čierne v lesku…" </em>
    </li>
    <li>Potom otvor <a href="/check_email">/check_email</a> – appka zoberie najnovší neprečítaný e-mail,
        vyberie vhodnú fóliu z katalógu, spočíta cenu a pošle cenovú ponuku späť odosielateľovi.</li>
  </ol>

  <div class="card">
    <h2>Simulácia bez e-mailu</h2>
    <p>Na rýchle testovanie môžeš použiť <a href="/simulate">/simulate</a> – nasimuluje e-mail a ukáže ponuku v prehliadači.</p>
  </div>

  <div class="card">
    <h3>Health / konfigurácia</h3>
    <pre>{{ health_json }}</pre>
  </div>
</body>
</html>
"""


@app.route("/", methods=["GET"])
def index():
    health_info = {
        "has_openai": bool(client),
        "imap_configured": bool(IMAP_HOST and IMAP_USER),
        "smtp_configured": bool(SMTP_HOST and SMTP_USER),
        "imap_user": IMAP_USER,
    }
    return render_template_string(
        INDEX_HTML,
        imap_user=IMAP_USER or "IMAP_USER nie je nastavený",
        health_json=json.dumps(health_info, ensure_ascii=False, indent=2),
    )


@app.route("/check_email", methods=["GET"])
def check_email_route():
    """
    Trigger: skontroluje IMAP, zoberie najnovší UNSEEN e-mail,
    vygeneruje cenovú ponuku a odošle ju späť odosielateľovi.
    """
    result = fetch_latest_unseen_email()
    if not result:
        return jsonify({"status": "no_unseen_email"}), 200

    from_addr, subject, body_text = result

    quote = generate_quote_email(body_text, original_subject=subject)
    reply_subject = f"Re: {subject}" if subject else "Vaša cenová ponuka na fólie"

    send_email(from_addr, reply_subject, quote["email_text"])

    return jsonify({
        "status": "quote_sent",
        "to": from_addr,
        "subject": reply_subject,
        "selection": quote["selection"],
        "pricing": quote["pricing"],
    }), 200


@app.route("/simulate", methods=["GET"])
def simulate():
    """
    Simulácia bez IMAP – použije pevný text dopytu a ukáže ponuku v prehliadači.
    """
    sample_text = """
Zdravím,
mám nové BMW 3, chcel by som ochrániť prednú časť auta (nárazník, kapotu, zrkadlá).
Preferujem XPEL fólie, auto je čierne v lesku. Viete mi prosím pripraviť cenovú ponuku?
Ďakujem.
"""
    quote = generate_quote_email(sample_text, original_subject="Dopyt na XPEL fólie")

    html = f"""
    <h1>Simulácia cenovej ponuky – bez e-mailu</h1>
    <h2>Vstupný dopyt:</h2>
    <pre>{sample_text}</pre>
    <h2>Vygenerovaný e-mail s ponukou:</h2>
    <pre>{quote['email_text']}</pre>
    <h2>Debug – výber fólie a ceny:</h2>
    <pre>{json.dumps({"selection": quote["selection"], "pricing": quote["pricing"]}, ensure_ascii=False, indent=2)}</pre>
    <p><a href="/">Späť na úvod</a></p>
    """
    return html


@app.route("/health", methods=["GET"])
def health():
    return {
        "status": "ok",
        "has_openai": bool(client),
        "imap_configured": bool(IMAP_HOST and IMAP_USER),
        "smtp_configured": bool(SMTP_HOST and SMTP_USER),
    }


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
