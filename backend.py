"""
RomânăPro Backend — FastAPI
Запуск:
  pip install fastapi uvicorn groq serpapi playwright beautifulsoup4 requests
  playwright install chromium
  python backend.py
"""

import os, re, time, asyncio, json, random
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import requests
from bs4 import BeautifulSoup

# ─── BAZA DE DATE CU POEZII REALE ─────────────────────────────────────────────
def load_poezii():
    """Incarca poezii din poezii.json daca exista, altfel lista de fallback"""
    json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "poezii.json")
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                poems = json.load(f)
            with_text    = [p for p in poems if p.get("text","").strip()]
            without_text = [p for p in poems if not p.get("text","").strip()]
            print(f"[poezii] Incarcate: {len(with_text)} cu text, {len(without_text)} fara text")
            return with_text, without_text
        except Exception as e:
            print(f"[poezii] Eroare la citire: {e}")
    return [], []

POEMS_WITH_TEXT, POEMS_WITHOUT_TEXT = load_poezii()

POEMS_FALLBACK = [
    {"titlu":"Plumb",                          "autor":"George Bacovia",   "motiv":"solitudine / moarte / apasare",        "text":""},
    {"titlu":"Floare albastra",                "autor":"Mihai Eminescu",   "motiv":"iubire / natura / dor",                "text":""},
    {"titlu":"Eu nu strivesc corola de minuni","autor":"Lucian Blaga",     "motiv":"mister / cunoastere / lumina",         "text":""},
    {"titlu":"Testament",                      "autor":"Tudor Arghezi",    "motiv":"creatie / mostenire / cuvantul",       "text":""},
    {"titlu":"Leoaica tanara, iubirea",        "autor":"Nichita Stanescu", "motiv":"iubire / transfigurare / putere",      "text":""},
    {"titlu":"Limba noastra",                  "autor":"Alexei Mateevici", "motiv":"limba / identitate / patriotism",      "text":""},
    {"titlu":"Moartea caprioarei",             "autor":"Nicolae Labis",    "motiv":"moarte / natura / vinovatie",          "text":""},
]

def pick_poem(prefer_text=True):
    """Alege o poezie aleatorie - prefera cele cu text real"""
    if prefer_text and POEMS_WITH_TEXT:
        return random.choice(POEMS_WITH_TEXT)
    all_poems = POEMS_WITH_TEXT + POEMS_WITHOUT_TEXT
    if all_poems:
        return random.choice(all_poems)
    return random.choice(POEMS_FALLBACK)


# ─── CONFIG ───────────────────────────────────────────────────────────────────
GROQ_API_KEY   = os.getenv("GROQ_API_KEY", "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")
SERP_API_KEY   = os.getenv("SERP_API_KEY", "9ffcc5cb39a6d75aeb10d5d42ac4c5d23be8a09ffa8f9251be83238abe3aa063")
GROQ_MODEL     = "llama-3.3-70b-versatile"   # mai bun decât 8b pentru analiză literară
GROQ_URL       = "https://api.groq.com/openai/v1/chat/completions"

app = FastAPI(title="RomânăPro API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── GROQ CALL ────────────────────────────────────────────────────────────────
def groq(prompt: str, system: str = "", temperature: float = 0.3, max_tokens: int = 2000) -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    resp = requests.post(GROQ_URL, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


# ─── SERPAPI SEARCH ───────────────────────────────────────────────────────────
def serp_search(query: str, num: int = 5) -> list[dict]:
    """Returnează lista de rezultate [{title, link, snippet}]"""
    params = {
        "q": query,
        "location": "Romania",
        "hl": "ro",
        "gl": "ro",
        "google_domain": "google.com",
        "num": num,
        "api_key": SERP_API_KEY,
    }
    try:
        from serpapi import search as serp
        results = serp(params)
        organic = results.get("organic_results", [])
        return [{"title": r.get("title",""), "link": r.get("link",""), "snippet": r.get("snippet","")} for r in organic]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"SerpAPI error: {e}")


def find_scribd_url(query: str) -> Optional[str]:
    """Caută URL Scribd pentru query"""
    results = serp_search(f"site:scribd.com {query} rezumat", num=5)
    for r in results:
        if "scribd.com" in r["link"]:
            return r["link"]
    # fallback fără site: filter
    results2 = serp_search(f"{query} rezumat scribd", num=5)
    for r in results2:
        if "scribd.com" in r["link"]:
            return r["link"]
    return None


def scrape_scribd(url: str) -> str:
    """Extrage textul dintr-o pagină Scribd cu Playwright"""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
            )
            page = ctx.new_page()
            page.goto(url, timeout=20000)
            page.wait_for_timeout(4000)

            # scroll ca să se încarce mai mult conținut
            for _ in range(3):
                page.evaluate("window.scrollBy(0, window.innerHeight)")
                page.wait_for_timeout(1000)

            html = page.content()
            browser.close()

            soup = BeautifulSoup(html, "html.parser")
            text_parts = []

            # Încearcă mai multe selectoare posibile
            for selector in ["div.text_layer", "div[class*='text_layer']", "p[class*='reader']", "div[class*='page_text']"]:
                divs = soup.find_all("div" if "div" in selector else "p", class_=re.compile(selector.split(".")[-1].replace("[class*='","").replace("']","")))
                if divs:
                    for d in divs:
                        text_parts.append(d.get_text(separator=" ", strip=True))
                    break

            # Fallback: tot textul vizibil
            if not text_parts:
                for tag in soup.find_all(["p", "span", "div"]):
                    txt = tag.get_text(strip=True)
                    if len(txt) > 80:
                        text_parts.append(txt)

            return "\n".join(text_parts[:50])  # primele 50 blocuri

    except ImportError:
        raise HTTPException(status_code=500, detail="Playwright neinstalat. Rulează: playwright install chromium")
    except Exception as e:
        return ""  # returnăm string gol, fallback la cunoștințele Groq


def get_text_fallback_snippets(title: str, author: str) -> str:
    """Dacă Scribd nu merge, adună snippet-uri din Google"""
    results = serp_search(f'"{title}" {author} rezumat subiect personaje', num=8)
    parts = []
    for r in results:
        if r["snippet"]:
            parts.append(r["snippet"])
    return "\n".join(parts)


# ─── MODELS ───────────────────────────────────────────────────────────────────
class TextRequest(BaseModel):
    title: str
    author: str

class CheckRequest(BaseModel):
    question: str
    model_answer: str
    user_answer: str
    title: str
    author: str

class BACRequest(BaseModel):
    type: str  # figuri | motive | personaj

class BACEvalRequest(BaseModel):
    type: str
    content: dict
    user_answer: str
    barem: str

class GramRequest(BaseModel):
    task_type: str  # traducere | perifraza | cuvant | comentariu

class GramCheckRequest(BaseModel):
    task_type: str
    task: dict
    user_answer: str

class TranslateRequest(BaseModel):
    word: str
    target_lang: str  # ru | en


# ─── ENDPOINTS ────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "model": GROQ_MODEL}


@app.post("/texte/generate")
def texte_generate(req: TextRequest):
    """Generează rezumat + 10 întrebări BAC pentru o operă literară"""

    # Pasul 1: Caută text real din internet
    print(f"[texte] Caut text pentru: {req.title} de {req.author}")
    scribd_url = find_scribd_url(f"{req.title} {req.author}")
    source_text = ""

    if scribd_url:
        print(f"[texte] Scribd găsit: {scribd_url}")
        source_text = scrape_scribd(scribd_url)

    if not source_text:
        print("[texte] Scribd eșuat, folosesc snippet-uri din Google")
        source_text = get_text_fallback_snippets(req.title, req.author)

    # Pasul 2: Generează cu Groq
    context = f"Text/informații găsite online:\n{source_text[:3000]}\n\n" if source_text else ""

    system = "Ești profesor de română pentru bacalaureat. Răspunzi STRICT în JSON valid, fără text în afara JSON-ului, fără markdown."

    prompt = f"""{context}Opera: "{req.title}" de {req.author}.

Generează JSON cu structura exactă:
{{
  "rezumat": "rezumat complet 200-250 cuvinte în română, bazat pe opera reală",
  "scribd_url": "{scribd_url or ''}",
  "intrebari": [
    {{"id":1,"tip":"grila","intrebare":"întrebare reală despre subiect","variante":["A) răspuns1","B) răspuns2","C) răspuns3","D) răspuns4"],"corect":"A"}},
    {{"id":2,"tip":"grila","intrebare":"întrebare despre personaje","variante":["A) ","B) ","C) ","D) "],"corect":"B"}},
    {{"id":3,"tip":"grila","intrebare":"întrebare despre temă/mesaj","variante":["A) ","B) ","C) ","D) "],"corect":"C"}},
    {{"id":4,"tip":"grila","intrebare":"întrebare despre context/epocă","variante":["A) ","B) ","C) ","D) "],"corect":"D"}},
    {{"id":5,"tip":"grila","intrebare":"întrebare despre simboluri/motive","variante":["A) ","B) ","C) ","D) "],"corect":"A"}},
    {{"id":6,"tip":"grila","intrebare":"întrebare dificilă despre structura operei","variante":["A) ","B) ","C) ","D) "],"corect":"C"}},
    {{"id":7,"tip":"deschis","intrebare":"Analizați tipul uman al personajului principal din opera dată, raportându-vă la trăsături, motive și evoluție.","raspunsModel":"răspuns model complet nivel BAC 150 cuvinte"}},
    {{"id":8,"tip":"deschis","intrebare":"Identificați și comentați motivele principale ale personajului central, cu argumente din text.","raspunsModel":"răspuns model complet"}},
    {{"id":9,"tip":"deschis","intrebare":"Prezentați tema operei și ilustrați motivele literare principale cu exemple din text.","raspunsModel":"răspuns model complet"}},
    {{"id":10,"tip":"deschis","intrebare":"Analizați relația dintre personajele principale și semnificația ei în economia operei.","raspunsModel":"răspuns model complet"}}
  ]
}}
Completează cu conținut real și specific despre opera "{req.title}"."""

    raw = groq(prompt, system=system, max_tokens=2500)

    # Curăță și parsează JSON
    clean = re.sub(r"```json|```", "", raw).strip()
    # Găsește primul { ... }
    match = re.search(r'\{.*\}', clean, re.DOTALL)
    if not match:
        raise HTTPException(status_code=500, detail="Model nu a returnat JSON valid")

    import json
    try:
        data = json.loads(match.group())
        return data
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"JSON parse error: {e}\nRaw: {clean[:300]}")


@app.post("/texte/check")
def texte_check(req: CheckRequest):
    """Verifică răspuns deschis la o întrebare despre text"""
    fb = groq(
        f"""Profesor de română BAC. Evaluează răspunsul la întrebarea: "{req.question}" (opera: {req.title} de {req.author}).
Răspuns model: {req.model_answer}
Răspuns elev: "{req.user_answer}"
Feedback în română (5-6 propoziții): puncte forte, ce lipsește, nota 1-10.""",
        temperature=0.2
    )
    return {"feedback": fb}


@app.post("/bac/generate")
def bac_generate(req: BACRequest):
    """Generează exercițiu BAC folosind poezii REALE din poezii.json"""

    SYS = "Ești profesor de română BAC. Răspunzi STRICT în JSON valid, fără markdown, fără text în afara JSON."

    poem = pick_poem(prefer_text=True)
    titlu  = poem["titlu"]
    autor  = poem["autor"]
    motiv  = poem.get("motiv", "")
    text   = poem.get("text", "").strip()

    # Dacă avem textul real, îl includem direct în prompt
    text_context = f"\nTEXTUL REAL AL POEZIEI (folosește EXACT aceste versuri, nu inventa altele):\n\"\"\"\n{text}\n\"\"\"\n" if text else f"\n(Nu avem textul — scrie versuri autentice cunoscute din această operă)\n"

    # ── figuri de stil ────────────────────────────────────────────────────────
    if req.type == "figuri":
        prompt = f"""Opera: „{titlu}" de {autor}. Motiv: {motiv}.
{text_context}
Generează exercițiu BAC Item 4 — figuri de stil.
{"Alege 6-10 versuri DIN TEXTUL DE MAI SUS (copiază exact, nu modifica)." if text else "Scrie un fragment autentic de 6-10 versuri din această poezie."}
Identifică 2-3 figuri de stil reale din fragment.

STRICT JSON:
{{"titlu":"{titlu}","autor":"{autor}","sursa":"{titlu} — {autor}","fragment":"versurile exacte","figuri":[{{"figura":"tipul figurii","exemplu":"citatul exact din fragment","structura":"cum e construită (ex: metasemia subst X și Y)","efect":"sugestia contextuală — ce exprimă, ce sentiment"}}]}}"""

    # ── motive literare ───────────────────────────────────────────────────────
    elif req.type == "motive":
        poem2 = pick_poem(prefer_text=True)
        while poem2["titlu"] == titlu:
            poem2 = pick_poem(prefer_text=True)
        text2 = poem2.get("text","").strip()
        text2_context = f"\nText real opera 2:\n\"\"\"\n{text2}\n\"\"\"" if text2 else ""

        prompt = f"""Opera 1: „{titlu}" de {autor}. Motiv: {motiv}.
{text_context}
Opera 2 pentru comparație: „{poem2['titlu']}" de {poem2['autor']}.{text2_context}

Generează exercițiu BAC Item 8 — motiv literar comparativ.
{"Alege 6-10 versuri DIN TEXTUL REAL AL OPEREI 1." if text else "Scrie fragment autentic din opera 1."}

STRICT JSON:
{{"titlu":"{titlu}","autor":"{autor}","sursa":"{titlu} — {autor}","fragment":"versurile exacte din opera 1","motiv":"{motiv.split('/')[0].strip()}","semnificatieInText":"cum se manifestă motivul (2-3 propoziții)","indiciu":"citatul concret din text","textComparatie":"{poem2['titlu']}","autorComparatie":"{poem2['autor']}","citatComparatie":"{"fragment din textul real al operei 2" if text2 else "fragment autentic din opera 2"}","interpretareComparatie":"cum apare același motiv acolo","raspunsModel":"răspuns model complet 150 cuvinte"}}"""

    # ── tipul uman ─────────────────────────────────────────────────────────────
    elif req.type == "tip_uman":
        prompt = f"""Opera: „{titlu}" de {autor}.
{text_context}
Generează exercițiu BAC Item 3 — tipul uman al naratorului/eului liric.
{"Folosește versuri DIN TEXTUL REAL de mai sus." if text else "Scrie fragment autentic 80-120 cuvinte."}

STRICT JSON:
{{"titlu":"{titlu}","autor":"{autor}","sursa":"{titlu} — {autor}","fragment":"fragment real din operă","tipUman":"tipul uman (tânărul entuziast / artistul debutant / intelectualul lucid / etc.)","citat1":"primul citat real din fragment","comentariu1":"de ce confirmă tipologia","citat2":"al doilea citat real","comentariu2":"comentariu"}}"""

    # ── portretul moral ───────────────────────────────────────────────────────
    elif req.type == "portret":
        prompt = f"""Opera: „{titlu}" de {autor}.
{text_context}
Generează exercițiu BAC Item 5 — portretul moral.
{"Folosește versuri DIN TEXTUL REAL de mai sus." if text else "Scrie fragment autentic 100-140 cuvinte."}

STRICT JSON:
{{"titlu":"{titlu}","autor":"{autor}","sursa":"{titlu} — {autor}","fragment":"fragment real","personaj":"eul liric / personajul","trasatura1":"trăsătură morală 1","exemplu1":"citat real din text","trasatura2":"trăsătură morală 2","exemplu2":"citat real din text","raspunsModel":"portret moral complet 6-7 rânduri"}}"""

    # ── starea de spirit ──────────────────────────────────────────────────────
    elif req.type == "stare_spirit":
        prompt = f"""Opera: „{titlu}" de {autor}.
{text_context}
Generează exercițiu BAC Item 6 — starea de spirit a eului liric.
{"Alege 6-12 versuri DIN TEXTUL REAL." if text else "Scrie fragment autentic de 6-12 versuri."}

STRICT JSON:
{{"titlu":"{titlu}","autor":"{autor}","sursa":"{titlu} — {autor}","fragment":"versurile reale","stare":"starea de spirit (dor/solitudine/alean/melancolie/exuberanță)","modalitate":"procedeul prin care se exprimă","raspunsModel":"interpretare completă 4-6 propoziții"}}"""

    # ── atitudinea personajelor ───────────────────────────────────────────────
    elif req.type == "atitudine":
        prompt = f"""Opera: „{titlu}" de {autor}.
{text_context}
Generează exercițiu BAC Item 7 — atitudinea personajelor.
{"Folosește text DIN FRAGMENTUL REAL de mai sus." if text else "Scrie fragment autentic de proză 130-170 cuvinte."}

STRICT JSON:
{{"titlu":"{titlu}","autor":"{autor}","sursa":"{titlu} — {autor}","fragment":"fragment real","protagonist":"personajul central sau eul liric","atitudine":"atitudinea (dezaprobare/invidie/admirație/ostilitate)","citat1":"citat real din fragment","comentariu1":"1-2 propoziții","citat2":"al doilea citat real","comentariu2":"1-2 propoziții","raspunsModel":"răspuns complet 6-7 rânduri"}}"""

    # ── sinonim contextual ────────────────────────────────────────────────────
    elif req.type == "sinonim":
        prompt = f"""Opera: „{titlu}" de {autor}.
{text_context}
Generează exercițiu BAC Item 1 — sinonim contextual.
{"Alege o frază DIN TEXTUL REAL." if text else "Alege o frază autentică din operă."}

STRICT JSON:
{{"titlu":"{titlu}","autor":"{autor}","sursa":"{titlu} — {autor}","fragment":"fraza reală 15-25 cuvinte","cuvant":"cuvântul țintă din frază","sinonime":["sin1","sin2","sin3","sin4","sin5"],"corect":"sinonimul cel mai potrivit contextual","raspunsModel":"enunț argumentativ model"}}"""

    # ── alt sens ──────────────────────────────────────────────────────────────
    elif req.type == "sens":
        prompt = f"""Opera: „{titlu}" de {autor}.
{text_context}
Generează exercițiu BAC Item 2 — alt sens al cuvântului.
{"Alege un fragment DIN TEXTUL REAL cu 3 cuvinte polisemantice." if text else "Alege fragment autentic 3-5 propoziții."}

STRICT JSON:
{{"titlu":"{titlu}","autor":"{autor}","sursa":"{titlu} — {autor}","fragment":"fragment real","cuvinte":["cuv1","cuv2","cuv3"],"sensInText":{{"cuv1":"sensul din text","cuv2":"sensul din text","cuv3":"sensul din text"}},"raspunsModel":{{"cuv1":"enunț cu alt sens","cuv2":"enunț cu alt sens","cuv3":"enunț cu alt sens"}}}}"""

    # ── valoarea punctuației ──────────────────────────────────────────────────
    elif req.type == "punctuatie":
        prompt = f"""Opera: „{titlu}" de {autor}.
{text_context}
Generează exercițiu BAC Item 9 — valoarea stilistică a punctuației.
{"Alege secvență DIN TEXTUL REAL cu semne de exclamare și/sau întrebare." if text else "Alege secvență autentică cu semne de punctuație cu valoare stilistică."}

STRICT JSON:
{{"titlu":"{titlu}","autor":"{autor}","sursa":"{titlu} — {autor}","secventa":"secvența reală cu semne de punctuație","semn1":"!","tip1":"exclamativ","valoare1":"ce exprimă","semn2":"?","tip2":"interogativ retoric","valoare2":"ce exprimă","raspunsModel":"răspuns în 2 enunțuri dezvoltate per semn"}}"""

    # ── eseu argumentativ ─────────────────────────────────────────────────────
    elif req.type == "personaj":
        prompt = f"""Generează exercițiu BAC Item 12 — eseu argumentativ.
Inspiră-te din tema operei „{titlu}" de {autor} (motiv: {motiv}).

STRICT JSON:
{{"asertiune":"aserțiune filozofică/morală 15-25 cuvinte","autor":"autorul aserțiunii","tema":"tema centrală","teza1":"prima teză","argument1":"argumentul","text1":"opera 1: titlu + autor","teza2":"a doua teză distinctă","argument2":"argumentul","text2":"opera 2: titlu + autor","raspunsModel":"plan eseu complet 200 cuvinte: introducere + 2 paragrafe + concluzie"}}"""

    else:
        raise HTTPException(status_code=400, detail=f"Tip necunoscut: {req.type}")

    raw = groq(prompt, system=SYS, temperature=0.3, max_tokens=1600)
    clean = re.sub(r"```json|```", "", raw).strip()
    match = re.search(r'\{.*\}', clean, re.DOTALL)
    if not match:
        raise HTTPException(status_code=500, detail="Model nu a returnat JSON valid")
    try:
        return json.loads(match.group())
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"JSON error: {e}")


@app.post("/bac/eval")
def bac_eval(req: BACEvalRequest):
    """Evaluează răspunsul elevului la exercițiu BAC"""
    if req.type == "figuri":
        model = "\n".join([f'{f["figura"]}: „{f["exemplu"]}" — {f["efect"]}' for f in req.content.get("figuri", [])])
    else:
        model = req.content.get("explicatieModel", "")

    raw = groq(
        f"""Examinator BAC română. Evaluează răspunsul conform baremului.
BAREM:\n{req.barem}
RĂSPUNS MODEL:\n{model}
RĂSPUNS ELEV:\n"{req.user_answer}"
Acordă punctaj specific pe fiecare criteriu din barem, explică ce a lipsit. Menționează punctajul total (ex: 4/5p). Română, 150 cuvinte max.""",
        temperature=0.2
    )

    # Extrage scorul
    score_match = re.search(r'(\d+)\s*/\s*(\d+)\s*p', raw)
    score = int(score_match.group(1)) if score_match else None

    return {"feedback": raw, "score": score}


@app.post("/gram/generate")
def gram_generate(req: GramRequest):
    """Generează exercițiu de gramatică"""
    import json

    PROMPTS = {
        "traducere": 'Propoziție rusă dificultate medie-avansată (15-20 cuv) cu construcții gramaticale complexe (participiu scurt, verb reflexiv, genitiv etc). STRICT JSON: {"exercitiu":"propoziția în rusă","raspunsModel":"traducerea corectă în română","explicatie":"2-3 note gramaticale despre construcțiile dificile"}',
        "perifraza": 'Propoziție română literară (15-25 cuv) din registru elevat. STRICT JSON: {"exercitiu":"propoziția originală","cerinta":"Reformulați propoziția menținând sensul, cu structură sintactică diferită","raspunsModel":"varianta reformulată corect","explicatie":"ce structuri gramaticale implică transformarea"}',
        "cuvant": 'Cuvânt românesc nivel B2-C1 (neologism sau cuvânt cu utilizare specifică). STRICT JSON: {"cuvant":"cuvântul","definitie":"definiția scurtă și clară","cerinta":"Creați o propoziție corectă gramatical care să ilustreze sensul cuvântului","exempluModel":"exemplu de propoziție corectă și elegantă"}',
        "comentariu": 'Temă de reflecție provocatoare (întrebare despre valori, societate, natură umană). STRICT JSON: {"tema":"întrebarea sau afirmația","cerinta":"Exprimați-vă opinia în 3-5 propoziții corecte gramatical în română","criterii":"corectitudine gramaticală, vocabular adecvat, coerență logică"}'
    }

    if req.task_type not in PROMPTS:
        raise HTTPException(status_code=400, detail="task_type invalid")

    raw = groq(PROMPTS[req.task_type], system="Răspunzi STRICT JSON fără markdown.", temperature=0.5)
    clean = re.sub(r"```json|```", "", raw).strip()
    match = re.search(r'\{.*\}', clean, re.DOTALL)
    if not match:
        raise HTTPException(status_code=500, detail="JSON invalid")

    import json
    result = json.loads(match.group())
    result["type"] = req.task_type
    return result


@app.post("/gram/check")
def gram_check(req: GramCheckRequest):
    """Analizează gramatical răspunsul elevului"""
    ctx_map = {
        "traducere": f'Tradus din rusă: "{req.task.get("exercitiu","")}" → Corect: "{req.task.get("raspunsModel","")}"',
        "perifraza": f'Perifraza lui: "{req.task.get("exercitiu","")}" → Model: "{req.task.get("raspunsModel","")}"',
        "cuvant": f'Propoziție cu cuvântul "{req.task.get("cuvant","")}" → Exemplu: "{req.task.get("exempluModel","")}"',
        "comentariu": f'Comentariu pe tema: "{req.task.get("tema","")}"',
    }
    ctx = ctx_map.get(req.task_type, "")

    fb = groq(
        f"""Profesor de română. Analizează gramatical răspunsul elevului.
Context: {ctx}
Răspuns elev: "{req.user_answer}"

Analizează:
1. Erori gramaticale specifice (morfologie, sintaxă, acord, ortografie) — citează exact
2. Ce a fost corect și bine formulat
3. Sugestii de vocabular mai precis/elevat
4. Nota generală 1-10 cu justificare scurtă
Răspuns în română, structurat, max 200 cuvinte.""",
        temperature=0.2
    )
    return {"feedback": fb}


@app.post("/translate")
def translate_word(req: TranslateRequest):
    """Traduce un cuvânt românesc"""
    lang_name = "rusă" if req.target_lang == "ru" else "engleză"
    tr = groq(
        f'Traduce cuvântul românesc "{req.word}" în {lang_name}. Răspunde DOAR cu traducerea (1-4 cuvinte), fără explicații.',
        temperature=0.1,
        max_tokens=20
    )
    return {"word": req.word, "translation": tr.strip()}


# ─── RUN ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    print("=" * 50)
    print("RomânăPro Backend")
    print(f"Model: {GROQ_MODEL}")
    print("Setează GROQ_API_KEY în environment!")
    print("http://localhost:8000")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=8000)
