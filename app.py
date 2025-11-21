import os
import json

from flask import Flask, request, render_template_string
from dotenv import load_dotenv
from openai import OpenAI

# Na lokál test načítame .env, na Renderi sa použijú environment variables
load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

app = Flask(__name__)

# --------------------------------------------------------------------
# KATALÓG FÓLIÍ – TU SI BUDEŠ NAPLŇAŤ SVOJE PRODUKTY / CENNÍK
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
# POMOCNÉ FUNKCIE – "MOZOG NA FÓLIE" + CENOTVORBA
# --------------------------------------------------------------------

def ai_select_foil(email_text: str) -> dict:
    """
    AI 'mozog na fólie':
    - prečíta text dopytu,
    - pozrie sa na FOIL_PRODUCTS,
    - vyberie najvhodnejší produkt,
    - odhadne plochu v m2,
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
        # Fallback, ak nemáš nastavený OPENAI_API_KEY – aby demo nespadlo
        return {
            "product_code": "XPEL_ULTIMATE_PLUS",
            "area_m2": 4.0,
            "reason": "DEMO režim bez OpenAI – vyberám XPEL Ultimate Plus.",
            "notes_for_pricing": "Predná časť auta – odhad."
        }

    response = client.responses.create(
        model="gpt-4o-mini",
        input=[
            {"role": "system", "content": "Si odborník na PPF fólie a pomáhaš vybrať správny typ fólie."},
            {"role": "user", "content": prompt}
        ],
    )

    raw = response.output_text.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # fallback keď sa náhodou netrafí úplne JSON
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
    Pre vybraný produkt a odhad plochy spočíta cenu.
    Zahŕňa: materiál + práca + DPH 20 %.
    """
    product = find_product_by_code(selection.get("product_code"))
    if not product:
        return None

    try:
        area = float(selection.get("area_m2", 0))
    except (TypeError, ValueError):
        area = 0

    price_per_m2 = product["price_per_m2"]

    material_price = area * price_per_m2
    # Jednoduchý príklad práce: 40 €/m²
    labour_price = area * 40.0
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


def generate_quote_email(email_text: str) -> dict:
    """
    Kompletný flow:
    - AI vyberie fóliu + odhadne plochu,
    - spočíta cenu,
    - AI vygeneruje text emailu s ponukou.
    Vráti dict s email_text + debug dátami.
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
"""

    if not client:
        # fallback text, keď nemáš API key
        email_text_out = (
            "DEMO bez OpenAI API – ukážka dát, ktoré by šli do ponuky:\n\n"
            + summary_for_ai
        )
    else:
        prompt = f"""
Na základe nasledujúcich informácií priprav profesionálnu cenovú ponuku v slovenčine.
Na začiatku poďakuj za dopyt, zhrň čo odporúčaš (typ fólie a prečo),
uved prehľadnú tabuľku / zoznam ceny (materiál, práca, celková cena s DPH)
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
# FLASK ROUTES – WEBOVÉ ROZHRANIE (FORMULÁR + VÝSLEDOK)
# --------------------------------------------------------------------

INDEX_TEMPLATE = """
<!doctype html>
<html lang="sk">
<head>
  <meta charset="utf-8">
  <title>AI cenová ponuka – fólie XPEL (demo)</title>
  <style>
    body { font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif; margin: 2rem auto; max-width: 900px; line-height: 1.5; }
    textarea { width: 100%; min-height: 180px; padding: 0.75rem; font-family: inherit; font-size: 1rem; }
    button { padding: 0.6rem 1.4rem; font-size: 1rem; cursor: pointer; }
    .card { border: 1px solid #ddd; border-radius: 8px; padding: 1rem 1.25rem; margin-top: 1rem; background: #fafafa; }
    pre { white-space: pre-wrap; font-size: 0.95rem; }
    .debug { font-size: 0.85rem; color: #555; }
    label { font-weight: 600; }
  </style>
</head>
<body>
  <h1>AI cenová ponuka – fólie XPEL (demo)</h1>
  <p>Napíš sem text dopytu, ako keby ti zákazník poslal e-mail (napr. „Zdravím, chcel by som XPEL fóliu na prednú časť auta…“).</p>

  <form method="post" action="/generate">
    <label for="email_text">Text dopytu:</label><br>
    <textarea id="email_text" name="email_text" required>{{ email_text or '' }}</textarea>
    <br><br>
    <button type="submit">Vygenerovať cenovú ponuku</button>
  </form>

  {% if result %}
    <div class="card">
      <h2>Vygenerovaný text cenovej ponuky:</h2>
      <pre>{{ result.email_text }}</pre>
    </div>

    <div class="card debug">
      <h3>Debug – AI výber produktu a výpočet ceny:</h3>
      <pre>{{ debug_json }}</pre>
    </div>
  {% endif %}
</body>
</html>
"""


@app.route("/", methods=["GET"])
def index():
    return render_template_string(INDEX_TEMPLATE, result=None, email_text="", debug_json="")

@app.route("/generate", methods=["POST"])
def generate():
    email_text = request.form.get("email_text", "")
    result = generate_quote_email(email_text)

    debug_data = {
        "selection": result["selection"],
        "pricing": result["pricing"],
    }

    return render_template_string(
        INDEX_TEMPLATE,
        result=result,
        email_text=email_text,
        debug_json=json.dumps(debug_data, ensure_ascii=False, indent=2)
    )


# health-check / info
@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok", "has_openai": bool(client)}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
