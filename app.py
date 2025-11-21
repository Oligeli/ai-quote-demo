import os
from flask import Flask, jsonify, render_template_string
from dotenv import load_dotenv
from openai import OpenAI

# Načíta .env pri lokálnom testovaní (na Renderi použiješ environment variables)
load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

app = Flask(__name__)

# Jednoduchý cenník – kód produktu -> cena za kus bez DPH (iba príklad)
PRICE_LIST = {
    "VIZITKY_4_4_500": 0.08,   # 0,08 €/ks, 500 ks vizitiek
    "LETÁKY_A5_1000": 0.05,    # 0,05 €/ks, 1000 ks letákov
}


def calculate_items():
    """
    Tu normálne v budúcnosti bude parsovanie mailu + extrakcia položiek.
    Na demo to nasimulujeme pevnými položkami.
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
            # Pre demo predpokladáme, že všetko v cenníku existuje
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


def generate_quote_email_text(items_with_prices, total_with_vat):
    """
    Vygeneruje text cenovej ponuky pomocou GPT.
    Ak nie je API key, vráti fallback text.
    """
    # keď nemáme API KEY, nech aspoň niečo vráti – aby demo nespadlo
    if not client:
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

    # Popis položiek pre GPT
    items_text_lines = []
    for item in items_with_prices:
        line = (
            f"- {item['code']} | množstvo: {item['qty']} ks | "
            f"cena za ks: {item['unit_price']:.2f} € | spolu: {item['line_total']:.2f} €"
        )
        items_text_lines.append(line)
    items_text = "\n".join(items_text_lines)

    prompt = f"""
Si obchodný asistent firmy, ktorá robí cenové ponuky.

Na základe nasledujúcich položiek vytvor e-mail s cenovou ponukou v slovenčine.
Buď profesionálny, ale ľudský, vykaj. V úvode poďakuj za dopyt, potom prehľadne zhrň položky a ceny
a na konci jasne uveď CELKOVÚ cenu s DPH a informáciu o termíne dodania a platnosti ponuky.

Položky:
{items_text}

Celková cena s DPH: {total_with_vat:.2f} €
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


@app.route("/")
def index():
    return """
    <h1>AI cenová ponuka – demo (Render)</h1>
    <p>Pre simuláciu príchodu cenovej požiadavky choď na <a href="/trigger_example">/trigger_example</a>.</p>
    <p>Rozšírené verzie: napojenie na Gmail, skutočné parsovanie mailov, atď.</p>
    """


@app.route("/trigger_example", methods=["GET"])
def trigger_example():
    """
    DEMO: Nasimuluje príchod e-mailu s cenovou požiadavkou,
    spočíta ceny a vygeneruje návrh ponuky bez akéhokoľvek overovania.
    """
    items_with_prices, total_without_vat, vat, total_with_vat = calculate_items()
    email_text = generate_quote_email_text(items_with_prices, total_with_vat)

    # HTML šablóna na pekné zobrazenie
    html_template = """
    <h1>Návrh cenovej ponuky (demo)</h1>
    <h2>Text ponuky:</h2>
    <pre style="white-space: pre-wrap; border:1px solid #ddd; padding:1rem; border-radius:8px;">
{{ email_text }}
    </pre>

    <h2>Prehľad výpočtu (JSON):</h2>
    <pre style="white-space: pre-wrap; border:1px solid #eee; padding:1rem; border-radius:8px; font-size:0.9rem;">
{{ json_data }}
    </pre>
    """

    data = {
        "items": items_with_prices,
        "total_without_vat": total_without_vat,
        "vat": vat,
        "total_with_vat": total_with_vat,
    }

    return render_template_string(
        html_template,
        email_text=email_text,
        json_data=data
    )


@app.route("/api/generate_quote", methods=["GET"])
def api_generate_quote():
    """
    API varianta – to isté čo trigger_example, ale v čisto JSON forme.
    Môžeš ukázať, že z toho vieš spraviť aj normálne REST API.
    """
    items_with_prices, total_without_vat, vat, total_with_vat = calculate_items()
    email_text = generate_quote_email_text(items_with_prices, total_with_vat)

    return jsonify({
        "email_text": email_text,
        "items": items_with_prices,
        "total_without_vat": total_without_vat,
        "vat": vat,
        "total_with_vat": total_with_vat,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
