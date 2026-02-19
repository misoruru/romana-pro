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


# ─── BAZA DE DATE POEZII ──────────────────────────────────────────────────────
def _load_poems():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "poezii.json")
    if os.path.exists(path):
        try:
            data = json.load(open(path, "r", encoding="utf-8"))
            ok = [p for p in data if str(p.get("text","")).strip()]
            print(f"[poezii] {len(ok)} poezii cu text incarcate din {len(data)} total")
            return ok
        except Exception as e:
            print(f"[poezii] eroare la incarcare: {e}")
    print("[poezii] ATENTIE: poezii.json nu a fost gasit in", os.path.dirname(os.path.abspath(__file__)))
    return []

_POEMS = _load_poems()

def _pick():
    """Alege o poezie random din baza de date"""
    if _POEMS:
        return random.choice(_POEMS)
    # fallback daca lipseste fisierul
    return {"titlu":"Plumb","autor":"George Bacovia","text":"Dormeau adanc sicriele de plumb."}

def _pick_different(exclude_titlu):
    """Alege o poezie random diferita de cea data (pentru comparatii)"""
    pool = [p for p in _POEMS if p.get("titlu") != exclude_titlu]
    if pool:
        return random.choice(pool)
    return _pick()

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
    """Genereaza exercitiu BAC din baza de date reala de poezii"""

    SYS = "Esti profesor de romana BAC. Raspunzi STRICT in JSON valid, fara markdown, fara text in afara JSON-ului."
    TASK = req.type

    # ── Alege poezia principala ──────────────────────────────────────────────
    poem  = _pick()
    titlu = poem["titlu"]
    autor = poem["autor"]
    text  = poem["text"].strip()

    # Taiem textul la max 3000 caractere ca sa nu depasim contextul
    text_trunc = text[:3000]
    # Textul complet al poeziei pentru frontend (/ inlocuit cu newline)
    poem_full_text = text.replace("/", "\n")

    # ── figuri de stil ────────────────────────────────────────────────────────
    if TASK == "figuri":
        prompt = f'''Ai primit urmatoarea poezie REALA:

TITLU: {titlu}
AUTOR: {autor}
TEXT:
"""
{text_trunc}
"""

Sarcina: genereaza un exercitiu BAC Item 4 (figuri de stil).
1. Alege un fragment de 4-10 versuri DIN TEXTUL DE MAI SUS (copiaza exact, nu modifica niciun cuvant).
2. Identifica 2-3 figuri de stil reale din acel fragment.
3. Pentru fiecare figura explica: cum e construita si ce sugereaza in context.
IMPORTANT: Nu modifica si nu rescrie textul poeziei in niciun fel.

STRICT JSON:
{{"titlu":"{titlu}","autor":"{autor}","sursa":"{titlu} — {autor}","fragment":"versurile exacte copiate din text","figuri":[{{"figura":"tipul (metafora/epitet/comparatie/personificare/enumeratie)","exemplu":"citatul exact din fragment","structura":"cum e construita (ex: metasemia substantivelor X si Y)","efect":"ce sugereaza / ce sentiment exprima in context"}}]}}'''

    # ── motive literare ───────────────────────────────────────────────────────
    elif TASK == "motive":
        poem2 = _pick_different(titlu)
        titlu2 = poem2["titlu"]
        autor2 = poem2["autor"]
        text2  = poem2["text"].strip()[:1500]

        prompt = f'''Ai primit doua poezii REALE pentru un exercitiu de comparatie a motivului literar.

POEZIA 1:
TITLU: {titlu}
AUTOR: {autor}
TEXT:
"""
{text_trunc}
"""

POEZIA 2 (pentru comparatie):
TITLU: {titlu2}
AUTOR: {autor2}
TEXT:
"""
{text2}
"""

Sarcina: genereaza exercitiu BAC Item 8 — motiv literar comparativ.
1. Identifica un motiv literar prezent in AMBELE poezii (un singur cuvant sau sintagma scurta).
2. Alege un fragment relevant (4-8 versuri exacte, nemodificate) din fiecare poezie.
3. Explica cum se manifesta motivul in fiecare opera.
IMPORTANT: Nu modifica textul poeziilor in niciun fel. Copiaza versurile exact cum sunt.

STRICT JSON:
{{"titlu":"{titlu}","autor":"{autor}","sursa":"{titlu} — {autor}","fragment":"versurile exacte din poezia 1","motiv":"numele motivului identificat","semnificatieInText":"cum se manifesta motivul in poezia 1 (2-3 propozitii)","indiciu":"citatul concret din text care sustine motivul","textComparatie":"{titlu2}","autorComparatie":"{autor2}","citatComparatie":"versurile exacte din poezia 2","interpretareComparatie":"cum apare acelasi motiv in poezia 2 (2-3 propozitii)","raspunsModel":"raspuns model complet nivel BAC 150 cuvinte: identificare motiv + analiza in ambele texte + concluzie"}}'''

    # ── tipul uman ─────────────────────────────────────────────────────────────
    elif TASK == "tip_uman":
        prompt = f'''Ai primit urmatoarea poezie REALA:

TITLU: {titlu}
AUTOR: {autor}
TEXT:
"""
{text_trunc}
"""

Sarcina: genereaza exercitiu BAC Item 3 — tipul uman al eului liric / naratorului.
1. Determina tipul uman al vocii poetice pe baza textului.
2. Alege 2 citate exacte din text care confirma tipologia.
IMPORTANT: Nu modifica si nu rescrie textul poeziei in niciun fel.

STRICT JSON:
{{"titlu":"{titlu}","autor":"{autor}","sursa":"{titlu} — {autor}","fragment":"un fragment relevant de 4-8 versuri copiat exact","tipUman":"tipul uman (ex: intelectualul contemplativ / poetul revoltat / omul singuratic / etc.)","citat1":"primul citat exact din text","comentariu1":"de ce confirma tipologia (1-2 propozitii)","citat2":"al doilea citat exact din text","comentariu2":"comentariu (1-2 propozitii)"}}'''

    # ── portretul moral ───────────────────────────────────────────────────────
    elif TASK == "portret":
        prompt = f'''Ai primit urmatoarea poezie REALA:

TITLU: {titlu}
AUTOR: {autor}
TEXT:
"""
{text_trunc}
"""

Sarcina: genereaza exercitiu BAC Item 5 — portretul moral al eului liric.
Alege 2 trasaturi morale clare din text si sustine-le cu citate exacte.
IMPORTANT: Nu modifica si nu rescrie textul poeziei in niciun fel.

STRICT JSON:
{{"titlu":"{titlu}","autor":"{autor}","sursa":"{titlu} — {autor}","fragment":"fragment relevant de 4-8 versuri copiat exact","personaj":"eul liric / personajul","trasatura1":"prima trasatura morala","exemplu1":"citatul exact din text","trasatura2":"a doua trasatura morala","exemplu2":"citatul exact din text","raspunsModel":"portret moral complet 6-7 randuri nivel BAC"}}'''

    # ── starea de spirit ──────────────────────────────────────────────────────
    elif TASK == "stare_spirit":
        prompt = f'''Ai primit urmatoarea poezie REALA:

TITLU: {titlu}
AUTOR: {autor}
TEXT:
"""
{text_trunc}
"""

Sarcina: genereaza exercitiu BAC Item 6 — starea de spirit a eului liric.
Identifica starea dominanta si procedeele prin care e exprimata.
IMPORTANT: Nu modifica si nu rescrie textul poeziei in niciun fel.

STRICT JSON:
{{"titlu":"{titlu}","autor":"{autor}","sursa":"{titlu} — {autor}","fragment":"6-10 versuri exacte din text care exprima cel mai bine starea","stare":"starea de spirit (dor / solitudine / melancolie / angoasa / exuberanta / revolta)","modalitate":"procedeele artistice prin care se exprima (figuri, imagini, lexic, ritm)","raspunsModel":"interpretare completa 4-6 propozitii nivel BAC"}}'''

    # ── atitudinea ────────────────────────────────────────────────────────────
    elif TASK == "atitudine":
        prompt = f'''Ai primit urmatoarea poezie REALA:

TITLU: {titlu}
AUTOR: {autor}
TEXT:
"""
{text_trunc}
"""

Sarcina: genereaza exercitiu BAC Item 7 — atitudinea eului liric / personajului.
Identifica atitudinea fata de un element (natura, iubire, moarte, societate etc.) cu 2 citate exacte.
IMPORTANT: Nu modifica si nu rescrie textul poeziei in niciun fel.

STRICT JSON:
{{"titlu":"{titlu}","autor":"{autor}","sursa":"{titlu} — {autor}","fragment":"fragment relevant de 4-8 versuri copiat exact","protagonist":"eul liric","atitudine":"atitudinea (veneratie / revolta / melancolie / resemnare / admiratie / deznadejde)","citat1":"primul citat exact din text","comentariu1":"ce sugereaza (1-2 propozitii)","citat2":"al doilea citat exact din text","comentariu2":"1-2 propozitii","raspunsModel":"raspuns complet 6-7 randuri nivel BAC"}}'''

    # ── sinonim contextual ────────────────────────────────────────────────────
    elif TASK == "sinonim":
        prompt = f'''Ai primit urmatoarea poezie REALA:

TITLU: {titlu}
AUTOR: {autor}
TEXT:
"""
{text_trunc}
"""

Sarcina: genereaza exercitiu BAC Item 1 — sinonim contextual.
Alege un cuvant din text care are sens contextual specific (nu sens uzual).
Propune 5 sinonime posibile si indica care e cel mai potrivit in context.
IMPORTANT: Nu modifica si nu rescrie textul poeziei in niciun fel.

STRICT JSON:
{{"titlu":"{titlu}","autor":"{autor}","sursa":"{titlu} — {autor}","fragment":"fraza/versul exact din text care contine cuvantul","cuvant":"cuvantul ales","sinonime":["sin1","sin2","sin3","sin4","sin5"],"corect":"sinonimul cel mai potrivit contextual","raspunsModel":"enunt argumentativ model: de ce acel sinonim si nu altele"}}'''

    # ── alt sens ─────────────────────────────────────────────────────────────
    elif TASK == "sens":
        prompt = f'''Ai primit urmatoarea poezie REALA:

TITLU: {titlu}
AUTOR: {autor}
TEXT:
"""
{text_trunc}
"""

Sarcina: genereaza exercitiu BAC Item 2 — alt sens al cuvantului.
Alege 3 cuvinte polisemantice din text. Pentru fiecare: explica sensul din text si construieste un enunt cu un alt sens al aceluiasi cuvant.
IMPORTANT: Nu modifica si nu rescrie textul poeziei in niciun fel.

STRICT JSON:
{{"titlu":"{titlu}","autor":"{autor}","sursa":"{titlu} — {autor}","fragment":"un fragment de 3-5 versuri copiat exact","cuvinte":["cuv1","cuv2","cuv3"],"sensInText":{{"cuv1":"sensul cuvantului in poem","cuv2":"sensul in poem","cuv3":"sensul in poem"}},"raspunsModel":{{"cuv1":"enunt cu alt sens corect","cuv2":"enunt cu alt sens corect","cuv3":"enunt cu alt sens corect"}}}}'''

    # ── valoarea punctuatiei ──────────────────────────────────────────────────
    elif TASK == "punctuatie":
        prompt = f'''Ai primit urmatoarea poezie REALA:

TITLU: {titlu}
AUTOR: {autor}
TEXT:
"""
{text_trunc}
"""

Sarcina: genereaza exercitiu BAC Item 9 — valoarea stilistica a punctuatiei.
Alege din text o secventa care contine semne de punctuatie cu valoare expresiva (!, ?, —, ...).
Explica valoarea stilistica a fiecarui semn.
IMPORTANT: Nu modifica si nu rescrie textul poeziei in niciun fel.

STRICT JSON:
{{"titlu":"{titlu}","autor":"{autor}","sursa":"{titlu} — {autor}","secventa":"secventa exacta din text cu semnele de punctuatie","semn1":"semnul (! sau ? sau — sau ...)","tip1":"tipul enuntului sau functia","valoare1":"ce exprima in context (certitudine/indignare/ironie/suspans/pauza meditativa)","semn2":"al doilea semn daca exista","tip2":"tipul","valoare2":"ce exprima","raspunsModel":"2 enunturi dezvoltate per semn, nivel BAC"}}'''

    # ── eseu argumentativ ─────────────────────────────────────────────────────
    elif TASK == "personaj":
        prompt = f'''Genereaza un exercitiu BAC Item 12 — eseu argumentativ.
Creeaza o asertiune filozofica/morala inspirata din tema poeziei „{titlu}" de {autor}.
Asertiuna trebuie sa fie generala (nu despre aceasta poezie) si sa permita argumentare cu 2 opere literare.

STRICT JSON:
{{"asertiune":"asertiunea filozofica/morala 15-25 cuvinte","autor":"autorul asertinutii (filosof/scriitor cunoscut)","tema":"tema centrala a eseului","teza1":"prima teza (o idee de aparat)","argument1":"argumentul pentru teza 1","text1":"opera literara 1 — titlu + autor","teza2":"a doua teza distincta","argument2":"argumentul pentru teza 2","text2":"opera literara 2 — titlu + autor","raspunsModel":"plan complet eseu 200 cuvinte: introducere (explica asertiunea) + teza1+arg+exemplu + teza2+arg+exemplu + concluzie"}}'''

    else:
        raise HTTPException(status_code=400, detail=f"Tip necunoscut: {TASK}")

    raw   = groq(prompt, system=SYS, temperature=0.3, max_tokens=1600)
    clean = re.sub(r"```json|```", "", raw).strip()
    match = re.search(r'\{.*\}', clean, re.DOTALL)
    if not match:
        raise HTTPException(status_code=500, detail="Model nu a returnat JSON valid")
    try:
        result = json.loads(match.group())
        # Adaugam textul complet al poeziei (cu / inlocuit cu newline)
        result["poem_full_text"] = poem_full_text
        result["poem_titlu"] = titlu
        result["poem_autor"] = autor
        return result
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
