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

    print(f"[texte] Caut text pentru: {req.title} de {req.author}")
    scribd_url = find_scribd_url(f"{req.title} {req.author}")
    source_text = ""

    if scribd_url:
        print(f"[texte] Scribd găsit: {scribd_url}")
        source_text = scrape_scribd(scribd_url)

    if not source_text:
        print("[texte] Scribd eșuat, folosesc snippet-uri din Google")
        source_text = get_text_fallback_snippets(req.title, req.author)

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
    {{"id":7,"tip":"deschis","intrebare":"Analizați tipul uman al personajului principal.","raspunsModel":"răspuns model complet nivel BAC 150 cuvinte"}},
    {{"id":8,"tip":"deschis","intrebare":"Identificați și comentați motivele principale cu argumente din text.","raspunsModel":"răspuns model complet"}},
    {{"id":9,"tip":"deschis","intrebare":"Prezentați tema operei și motivele literare principale.","raspunsModel":"răspuns model complet"}},
    {{"id":10,"tip":"deschis","intrebare":"Analizați relația dintre personajele principale și semnificația ei.","raspunsModel":"răspuns model complet"}}
  ]
}}
Completează cu conținut real și specific despre opera "{req.title}"."""

    raw = groq(prompt, system=system, max_tokens=2500)
    clean = re.sub(r"```json|```", "", raw).strip()
    match = re.search(r'\{.*\}', clean, re.DOTALL)
    if not match:
        raise HTTPException(status_code=500, detail="Model nu a returnat JSON valid")
    import json
    try:
        return json.loads(match.group())
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"JSON parse error: {e}")


class SummaryRequest(BaseModel):
    title: str
    author: str = ""
    summary: str  # textul introdus de utilizator


@app.post("/texte/from_summary")
def texte_from_summary(req: SummaryRequest):
    """Generează întrebări BAC pe baza rezumatului introdus manual de utilizator.
    NU inventează nimic — folosește STRICT textul primit."""
    import json

    word_count = len(req.summary.split())

    system = "Ești profesor de română BAC. Răspunzi STRICT în JSON valid, fără text în afara JSON-ului, fără markdown."

    prompt = f"""Ai primit un rezumat/text despre opera literară "{req.title}"{f' de {req.author}' if req.author else ''}.

REZUMATUL/TEXTUL ELEVULUI ({word_count} cuvinte):
\"\"\"
{req.summary}
\"\"\"

INSTRUCȚIUNI STRICTE:
1. Generează întrebări EXCLUSIV pe baza informațiilor din textul de mai sus.
2. NU inventa personaje, scene sau informații care nu apar în text.
3. Toate răspunsurile model trebuie să fie bazate pe textul primit.
4. Întrebările grila trebuie să aibă răspunsuri corecte care se regăsesc în text.
5. Întrebările deschise trebuie să ceară analiză a ceea ce e scris în text.
6. Rezumatul generat trebuie să fie o reformulare elegantă a textului primit (minim 200 cuvinte), NU să adauge informații noi.

Generează JSON:
{{
  "rezumat": "reformulare elegantă și completă a textului primit, minim 200 cuvinte, fără informații adăugate",
  "scribd_url": "",
  "intrebari": [
    {{"id":1,"tip":"grila","intrebare":"întrebare despre personajele menționate în text","variante":["A) varianta corectă din text","B) variantă greșită","C) variantă greșită","D) variantă greșită"],"corect":"A"}},
    {{"id":2,"tip":"grila","intrebare":"întrebare despre evenimentele din text","variante":["A) ","B) ","C) ","D) "],"corect":"B"}},
    {{"id":3,"tip":"grila","intrebare":"întrebare despre tema/mesajul din text","variante":["A) ","B) ","C) ","D) "],"corect":"C"}},
    {{"id":4,"tip":"grila","intrebare":"întrebare despre relațiile dintre personaje menționate","variante":["A) ","B) ","C) ","D) "],"corect":"A"}},
    {{"id":5,"tip":"grila","intrebare":"întrebare dificilă despre detalii din text","variante":["A) ","B) ","C) ","D) "],"corect":"D"}},
    {{"id":6,"tip":"deschis","intrebare":"Analizați tipul uman al personajului principal pe baza informațiilor din text, menționând cel puțin două trăsături.","raspunsModel":"răspuns model bazat STRICT pe textul dat, 100-150 cuvinte"}},
    {{"id":7,"tip":"deschis","intrebare":"Identificați și comentați motivele literare principale prezente în text, cu exemple concrete.","raspunsModel":"răspuns model bazat pe textul dat"}},
    {{"id":8,"tip":"deschis","intrebare":"Prezentați conflictul principal și semnificația lui, conform textului.","raspunsModel":"răspuns model bazat pe textul dat"}},
    {{"id":9,"tip":"deschis","intrebare":"Comentați relația dintre personajele principale, bazându-vă pe informațiile din text.","raspunsModel":"răspuns model"}},
    {{"id":10,"tip":"deschis","intrebare":"Formulați un punct de vedere argumentat despre tema centrală a operei, raportându-vă la text.","raspunsModel":"răspuns model complet"}}
  ]
}}"""

    raw = groq(prompt, system=system, max_tokens=2500, temperature=0.2)
    clean = re.sub(r"```json|```", "", raw).strip()
    match = re.search(r'\{.*\}', clean, re.DOTALL)
    if not match:
        raise HTTPException(status_code=500, detail="Model nu a returnat JSON valid")
    try:
        return json.loads(match.group())
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"JSON error: {e}")





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
    """Generează exercițiu BAC: figuri | motive | personaj"""
    import json

    if req.type == "figuri":
        # Caută fragment real online
        results = serp_search("fragment literar figuri de stil metafora personificare poezie română", num=3)
        snippets = " ".join([r["snippet"] for r in results if r["snippet"]])
        prompt = f"""Generează un exercițiu BAC de figuri de stil. Context din surse online: {snippets[:500]}

STRICT JSON fără text în afară:
{{"fragment":"fragment literar original 4-8 versuri sau 4-5 propoziții cu 2-3 figuri de stil clare","sursa":"Titlu — Autor","figuri":[{{"figura":"metaforă/personificare/epitet/etc","exemplu":"citat exact din fragment","efect":"efectul artistic explicat pentru BAC"}}]}}"""

    elif req.type == "motive":
        results = serp_search("motiv literar luna codrul natura iubirea moartea poezie română", num=3)
        snippets = " ".join([r["snippet"] for r in results if r["snippet"]])
        prompt = f"""Generează exercițiu BAC despre motiv literar. Context: {snippets[:500]}

STRICT JSON:
{{"titlu":"titlul operei","autor":"autorul","fragment":"fragment/poezie 6-10 versuri","motiv":"numele motivului literar","explicatieModel":"comentariu complet nivel BAC 150 cuvinte: identificare, prezentare, semnificație, legătură cu tema"}}"""

    elif req.type == "personaj":
        results = serp_search("personaj principal tipologie literară Ion Moromeți Vitoria caracterizare BAC", num=3)
        snippets = " ".join([r["snippet"] for r in results if r["snippet"]])
        prompt = f"""Generează exercițiu BAC despre tipul personajului. Context: {snippets[:500]}

STRICT JSON:
{{"opera":"titlul operei","autor":"autorul","personaj":"numele personajului","tipologie":"tipul personajului (erou tragic, personaj realist, etc)","scena":"scenă reprezentativă 120-150 cuvinte cu dialog sau descriere","explicatieModel":"comentariu model complet nivel BAC 8 puncte: tipologie, 2 trăsături cu ilustrare, evoluție, relații, concluzie"}}"""
    else:
        raise HTTPException(status_code=400, detail="type trebuie să fie: figuri | motive | personaj")

    raw = groq(prompt, system="Răspunzi STRICT în JSON valid fără markdown.", temperature=0.4)
    clean = re.sub(r"```json|```", "", raw).strip()
    match = re.search(r'\{.*\}', clean, re.DOTALL)
    if not match:
        raise HTTPException(status_code=500, detail="Model nu a returnat JSON valid")

    import json
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
