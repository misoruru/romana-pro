"""
RomânăPro Backend — FastAPI
Запуск:
  pip install fastapi uvicorn groq serpapi playwright beautifulsoup4 requests
  playwright install chromium
  python backend.py
"""

import os, re, time, asyncio
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import requests
from bs4 import BeautifulSoup

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
    """Generează exercițiu BAC conform baremelor 2024 — toate tipurile"""
    import json

    SYS = "Ești profesor de română BAC. Răspunzi STRICT în JSON valid, fără markdown, fără text în afara JSON."

    # ── Item 1: Sinonim contextual (4p) ───────────────────────────────────────
    if req.type == "sinonim":
        prompt = """Generează un exercițiu BAC Item 1 — sinonim contextual.
Creează o frază literară în care un cuvânt are sens contextual specific.
STRICT JSON:
{"fragment":"fraza literară completă de 15-25 cuvinte","sursa":"Titlu — Autor (opera din care e inspirată)","cuvant":"cuvântul țintă din frază","sinonime":["sin1","sin2","sin3","sin4","sin5","sin6"],"corect":"sinonimul cel mai potrivit contextual","raspunsModel":"enunț argumentativ model 2-3 propoziții: de ce acel sinonim, referire la contextul frazei"}"""

    # ── Item 2: Alt sens al cuvântului (6p) ───────────────────────────────────
    elif req.type == "sens":
        prompt = """Generează un exercițiu BAC Item 2 — alt sens al cuvântului.
Alege un fragment literar scurt cu 3 cuvinte polisemantice interesante.
STRICT JSON:
{"fragment":"fragment literar 3-5 propoziții","sursa":"Titlu — Autor","cuvinte":["cuv1","cuv2","cuv3"],"sensInText":{"cuv1":"sensul din text","cuv2":"sensul din text","cuv3":"sensul din text"},"raspunsModel":{"cuv1":"enunț cu alt sens corect și elegant","cuv2":"enunț cu alt sens corect și elegant","cuv3":"enunț cu alt sens corect și elegant"}}"""

    # ── Item 3: Tipul uman (5p) ───────────────────────────────────────────────
    elif req.type == "tip_uman":
        results = serp_search("fragment proză română personaj narator tânăr artist intelectual", num=3)
        snippets = " ".join([r["snippet"] for r in results if r["snippet"]])[:400]
        prompt = f"""Generează exercițiu BAC Item 3 — tipul uman al naratorului. Context: {snippets}
STRICT JSON:
{{"fragment":"fragment proză la persoana I, 120-180 cuvinte, cu un narator cu personalitate distinctă (tânăr, artist, intelectual, rebel etc.)","sursa":"Titlu — Autor","tipUman":"tipul uman al naratorului (ex: tânărul entuziast, artistul debutant, intelectualul lucid)","citat1":"primul citat relevant din fragment","comentariu1":"comentariu 1-2 propoziții care confirmă tipologia","citat2":"al doilea citat relevant","comentariu2":"comentariu 1-2 propoziții"}}"""

    # ── Item 4: Figura de stil (5p) ───────────────────────────────────────────
    elif req.type == "figuri":
        results = serp_search("fragment literar figuri de stil metafora personificare epitet poezie română BAC", num=3)
        snippets = " ".join([r["snippet"] for r in results if r["snippet"]])[:400]
        prompt = f"""Generează exercițiu BAC Item 4 — comentarea unei figuri de stil. Context: {snippets}
STRICT JSON:
{{"fragment":"fragment literar 4-8 versuri sau 3-5 propoziții cu figuri de stil clare","sursa":"Titlu — Autor","figuri":[{{"figura":"metaforă/epitet/personificare/comparație/simbol","exemplu":"citatul exact din fragment","structura":"cum e construită figura (elementele ei)","efect":"sugestia contextuală — ce exprimă, ce sentimente transmite, ce idee accentuează"}}]}}"""

    # ── Item 5: Portretul moral (5p) ──────────────────────────────────────────
    elif req.type == "portret":
        results = serp_search("fragment proză română personaj trăsături morale caracter BAC", num=3)
        snippets = " ".join([r["snippet"] for r in results if r["snippet"]])[:400]
        prompt = f"""Generează exercițiu BAC Item 5 — portretul moral. Context: {snippets}
STRICT JSON:
{{"fragment":"fragment proză 120-160 cuvinte din care se pot deduce trăsături morale ale unui personaj","sursa":"Titlu — Autor","personaj":"numele personajului","trasatura1":"prima trăsătură morală (fermitate/sensibilitate/inteligență/orgoliu/luciditate etc.)","exemplu1":"exemplul din text care ilustrează trăsătura 1","trasatura2":"a doua trăsătură morală","exemplu2":"exemplul din text care ilustrează trăsătura 2","raspunsModel":"portret moral complet 6-7 rânduri: intro + 2 trăsături cu exemple + concluzie"}}"""

    # ── Item 6: Starea de spirit a eului liric (4p) ───────────────────────────
    elif req.type == "stare_spirit":
        results = serp_search("poezie română eu liric dor melancolie solitudine natura sentiment", num=3)
        snippets = " ".join([r["snippet"] for r in results if r["snippet"]])[:400]
        prompt = f"""Generează exercițiu BAC Item 6 — starea de spirit a eului liric. Context: {snippets}
STRICT JSON:
{{"fragment":"poezie sau fragment liric 6-12 versuri cu stare de spirit clară (dor, solitudine, alean, exuberanță, melancolie)","sursa":"Titlu — Autor","stare":"starea de spirit a eului liric","modalitate":"procedeul prin care se exprimă (imagini, figuri de stil, lexic, ritm)","raspunsModel":"interpretare completă 4-6 propoziții: numire stare + explicare + referire la text"}}"""

    # ── Item 7: Atitudinea personajelor (5p) ──────────────────────────────────
    elif req.type == "atitudine":
        results = serp_search("fragment proză română conflict personaje atitudine dezaprobare invidie admirație BAC", num=3)
        snippets = " ".join([r["snippet"] for r in results if r["snippet"]])[:400]
        prompt = f"""Generează exercițiu BAC Item 7 — atitudinea personajelor. Context: {snippets}
STRICT JSON:
{{"fragment":"fragment proză 140-180 cuvinte cu interacțiune clară între personaje unde se poate determina o atitudine (dezaprobare, invidie, admirație, ostilitate, ironie)","sursa":"Titlu — Autor","protagonist":"personajul central","atitudine":"atitudinea celorlalți față de protagonist (numită exact: dezaprobare/invidie/admirație etc.)","citat1":"primul citat care indică atitudinea","comentariu1":"comentariu 1-2 propoziții","citat2":"al doilea citat","comentariu2":"comentariu 1-2 propoziții","raspunsModel":"răspuns model complet 6-7 rânduri"}}"""

    # ── Item 8: Motiv literar comparativ (6p) ─────────────────────────────────
    elif req.type == "motive":
        results = serp_search("motiv literar verticalitate morală demnitate condiție umană poezie proză română", num=3)
        snippets = " ".join([r["snippet"] for r in results if r["snippet"]])[:400]
        prompt = f"""Generează exercițiu BAC Item 8 — motiv literar comparativ. Context: {snippets}
STRICT JSON:
{{"fragment":"fragment literar 80-120 cuvinte cu un motiv literar evident","sursa":"Titlu — Autor","motiv":"numele motivului literar (verticalitate morală/condiția artistului/natura/iubirea/moartea/timpul)","semnificatieInText":"cum se manifestă și ce semnifică motivul în textul dat (2-3 propoziții)","indiciu":"citatul/indiciul concret din text prin care se edifică motivul","textComparatie":"titlul operei pentru comparație","autorComparatie":"autorul operei pentru comparație","citatComparatie":"citat relevant din opera de comparație","interpretareComparatie":"cum apare același motiv în opera de comparație (2-3 propoziții)"}}"""

    # ── Item 9: Valoarea stilistică a punctuației (4p) ────────────────────────
    elif req.type == "punctuatie":
        prompt = """Generează exercițiu BAC Item 9 — valoarea stilistică a semnelor de punctuație.
STRICT JSON:
{"secventa":"o secvență de 2-3 propoziții cu semne de exclamare și/sau întrebare cu valoare stilistică clară","sursa":"Titlu — Autor","semn1":"primul semn de punctuație (exclamare/întrebare)","tip1":"tipul enunțului (exclamativ/interogativ)","valoare1":"valoarea stilistică — ce exprimă (certitudine/mândrie/indignare/nedumerire/implicare afectivă etc.)","semn2":"al doilea semn","tip2":"tipul enunțului","valoare2":"valoarea stilistică","raspunsModel":"răspuns model complet în 2 enunțuri dezvoltate per semn"}"""

    # ── Item 12: Eseu argumentativ (20p) ──────────────────────────────────────
    elif req.type == "personaj":
        prompt = """Generează exercițiu BAC Item 12 — eseu argumentativ.
STRICT JSON:
{"asertiune":"o aserțiune filozofică/morală provocatoare de 15-25 cuvinte despre valori umane, demnitate, destin, condiție umană (similar cu Aristotel, Eminescu, Blaga etc.)","autor":"autorul aserțiunii","tema":"tema generală a eseului","teza1":"prima teză posibilă","argument1":"argumentul pentru teza 1","text1":"opera literară română care ilustrează teza 1 (titlu + autor)","teza2":"a doua teză distinctă","argument2":"argumentul pentru teza 2","text2":"opera literară română care ilustrează teza 2 (titlu + autor)","raspunsModel":"plan detaliat de eseu: introducere (opinie clară) + 2 paragrafe teze+argumente+referințe literare + concluzie — 250 cuvinte"}"""

    else:
        raise HTTPException(status_code=400, detail=f"Tip necunoscut: {req.type}. Valide: sinonim|sens|tip_uman|figuri|portret|stare_spirit|atitudine|motive|punctuatie|personaj")

    raw = groq(prompt, system=SYS, temperature=0.4, max_tokens=1800)
    clean = re.sub(r"```json|```", "", raw).strip()
    match = re.search(r'\{.*\}', clean, re.DOTALL)
    if not match:
        raise HTTPException(status_code=500, detail="Model nu a returnat JSON valid")

    import json
    try:
        return json.loads(match.group())
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"JSON error: {e}\nRaw: {clean[:200]}")


@app.post("/bac/eval")
def bac_eval(req: BACEvalRequest):
    """Evaluează răspunsul elevului la exercițiu BAC — toate tipurile 2024"""

    # Construiește răspunsul model în funcție de tip
    c = req.content
    if req.type == "sinonim":
        model = f"Sinonimul corect: {c.get('corect','')}\n{c.get('raspunsModel','')}"
    elif req.type == "sens":
        rm = c.get("raspunsModel", {})
        model = "\n".join([f"{k}: {v}" for k,v in rm.items()]) if isinstance(rm, dict) else str(rm)
    elif req.type == "tip_uman":
        model = f"Tipul uman: {c.get('tipUman','')}\nCitat 1: {c.get('citat1','')} — {c.get('comentariu1','')}\nCitat 2: {c.get('citat2','')} — {c.get('comentariu2','')}"
    elif req.type == "figuri":
        model = "\n".join([f'{f.get("figura","")}: „{f.get("exemplu","")}"\nStructură: {f.get("structura","")}\nEfect: {f.get("efect","")}' for f in c.get("figuri", [])])
    elif req.type == "portret":
        model = f"Trăsătură 1: {c.get('trasatura1','')} — {c.get('exemplu1','')}\nTrăsătură 2: {c.get('trasatura2','')} — {c.get('exemplu2','')}\n{c.get('raspunsModel','')}"
    elif req.type == "stare_spirit":
        model = f"Starea: {c.get('stare','')}\nModalitate: {c.get('modalitate','')}\n{c.get('raspunsModel','')}"
    elif req.type == "atitudine":
        model = f"Atitudinea: {c.get('atitudine','')}\nCitat 1: {c.get('citat1','')} — {c.get('comentariu1','')}\nCitat 2: {c.get('citat2','')} — {c.get('comentariu2','')}\n{c.get('raspunsModel','')}"
    elif req.type == "motive":
        model = f"Motivul: {c.get('motiv','')}\nÎn text: {c.get('semnificatieInText','')}\nIndiciu: {c.get('indiciu','')}\nComparație: {c.get('textComparatie','')} de {c.get('autorComparatie','')}\n{c.get('interpretareComparatie','')}\nCitat: {c.get('citatComparatie','')}"
    elif req.type == "punctuatie":
        model = f"{c.get('semn1','')}: {c.get('tip1','')} — {c.get('valoare1','')}\n{c.get('semn2','')}: {c.get('tip2','')} — {c.get('valoare2','')}\n{c.get('raspunsModel','')}"
    elif req.type == "personaj":
        model = f"Aserțiune: {c.get('asertiune','')}\nTeză 1: {c.get('teza1','')} + {c.get('argument1','')} ({c.get('text1','')})\nTeză 2: {c.get('teza2','')} + {c.get('argument2','')} ({c.get('text2','')})\n{c.get('raspunsModel','')}"
    else:
        model = c.get("explicatieModel", c.get("raspunsModel", str(c)))

    # Determină punctajul maxim
    punctaje = {"sinonim":4,"sens":6,"tip_uman":5,"figuri":5,"portret":5,"stare_spirit":4,"atitudine":5,"motive":6,"punctuatie":4,"personaj":20}
    max_p = punctaje.get(req.type, 10)

    raw = groq(
        f"""Ești examinator oficial BAC română 2024. Evaluează PUNCT CU PUNCT conform baremului.

TIP EXERCIȚIU: {req.type} ({max_p} puncte maxim)

BAREM OFICIAL:
{req.barem}

RĂSPUNS MODEL COMPLET:
{model}

RĂSPUNS ELEV:
"{req.user_answer}"

Evaluare DETALIATĂ:
1. Parcurge fiecare criteriu din barem — a primit punctele sau nu? De ce?
2. Citează din răspunsul elevului ce a acoperit și ce nu.
3. CE LIPSEA EXACT pentru punctaj maxim — fii specific.
4. PUNCTAJ FINAL: X/{max_p}p cu calcul detaliat pe criterii.

Fii SEVER și PRECIS. Minimum 120 cuvinte.""",
        temperature=0.15,
        max_tokens=700
    )

    score_match = re.search(r'(\d+)\s*/\s*\d+\s*p', raw)
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
