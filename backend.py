"""
RomânăPro Backend — FastAPI + SQLite (для Render.com)
"""

import os, re, sqlite3
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Optional
import requests
from bs4 import BeautifulSoup
from jose import jwt, JWTError
from passlib.context import CryptContext

# ─── CONFIG ───────────────────────────────────────────────────────────────────
GROQ_API_KEY      = os.getenv("GROQ_API_KEY", "")
SERP_API_KEY      = os.getenv("SERP_API_KEY", "")
GROQ_MODEL        = "llama-3.3-70b-versatile"
GROQ_URL          = "https://api.groq.com/openai/v1/chat/completions"
SECRET_KEY        = os.getenv("SECRET_KEY", "romana-pro-super-secret-change-me")
ALGORITHM         = "HS256"
TOKEN_EXPIRE_DAYS = 30
# На Render файлы хранятся в /tmp (ephemeral) или в persistent disk
# Для бесплатного плана используем /tmp — данные сбрасываются при рестарте
# Для сохранения данных подключи Render Disk ($7/мес) и смени путь на /data/romana.db
DB_PATH = os.getenv("DB_PATH", "/tmp/romana.db")

pwd_context   = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)

app = FastAPI(title="RomânăPro API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── DATABASE ─────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email    TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS vocab (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            word        TEXT NOT NULL,
            translation TEXT NOT NULL,
            stage       TEXT DEFAULT 'învăț',
            level       INTEGER DEFAULT 0,
            next_review INTEGER DEFAULT 0,
            ok_count    INTEGER DEFAULT 0,
            UNIQUE(user_id, word)
        );
        CREATE TABLE IF NOT EXISTS barem (
            user_id  INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            figuri   TEXT,
            motive   TEXT,
            personaj TEXT
        );
    """)
    conn.commit()
    conn.close()
    print(f"[db] SQLite ready at {DB_PATH}")

init_db()

# ─── AUTH HELPERS ─────────────────────────────────────────────────────────────
def hash_password(p: str) -> str:
    return pwd_context.hash(p)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def create_token(user_id: int, username: str) -> str:
    exp = datetime.utcnow() + timedelta(days=TOKEN_EXPIRE_DAYS)
    return jwt.encode({"sub": str(user_id), "username": username, "exp": exp}, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(creds: HTTPAuthorizationCredentials = Depends(bearer_scheme)):
    if not creds:
        raise HTTPException(status_code=401, detail="Не авторизован")
    try:
        p = jwt.decode(creds.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        return {"id": int(p["sub"]), "username": p["username"]}
    except JWTError:
        raise HTTPException(status_code=401, detail="Токен недействителен")

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
    target_lang: str

class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str

class LoginRequest(BaseModel):
    email: str
    password: str

class VocabCard(BaseModel):
    word: str
    translation: str
    stage: str = "învăț"
    level: int = 0
    next_review: int = 0
    ok_count: int = 0

class VocabSyncRequest(BaseModel):
    cards: list[VocabCard]

class BaremSaveRequest(BaseModel):
    figuri: str
    motive: str
    personaj: str


# ─── ENDPOINTS ────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "model": GROQ_MODEL}


# ─── AUTH ─────────────────────────────────────────────────────────────────────

@app.post("/auth/register")
def register(req: RegisterRequest):
    if len(req.username.strip()) < 3:
        raise HTTPException(400, "Имя пользователя минимум 3 символа")
    if len(req.password) < 6:
        raise HTTPException(400, "Пароль минимум 6 символов")
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, email, password_hash) VALUES (?,?,?)",
            (req.username.strip(), req.email.strip().lower(), hash_password(req.password))
        )
        conn.commit()
        uid = conn.execute("SELECT id FROM users WHERE email=?", (req.email.strip().lower(),)).fetchone()["id"]
        return {"token": create_token(uid, req.username.strip()), "username": req.username.strip(), "user_id": uid}
    except sqlite3.IntegrityError as e:
        if "username" in str(e):
            raise HTTPException(400, "Это имя уже занято")
        raise HTTPException(400, "Этот email уже зарегистрирован")
    finally:
        conn.close()


@app.post("/auth/login")
def login(req: LoginRequest):
    conn = get_db()
    try:
        u = conn.execute("SELECT * FROM users WHERE email=?", (req.email.strip().lower(),)).fetchone()
        if not u or not verify_password(req.password, u["password_hash"]):
            raise HTTPException(401, "Неверный email или пароль")
        return {"token": create_token(u["id"], u["username"]), "username": u["username"], "user_id": u["id"]}
    finally:
        conn.close()


@app.get("/auth/me")
def me(user=Depends(get_current_user)):
    return {"user_id": user["id"], "username": user["username"]}


# ─── VOCAB ────────────────────────────────────────────────────────────────────

@app.get("/vocab")
def vocab_get(user=Depends(get_current_user)):
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM vocab WHERE user_id=? ORDER BY rowid", (user["id"],)).fetchall()
        return {"cards": [{"id": r["id"], "word": r["word"], "translation": r["translation"],
                           "stage": r["stage"], "level": r["level"],
                           "nextReview": r["next_review"], "ok": r["ok_count"]} for r in rows]}
    finally:
        conn.close()


@app.post("/vocab/sync")
def vocab_sync(req: VocabSyncRequest, user=Depends(get_current_user)):
    conn = get_db()
    try:
        conn.execute("DELETE FROM vocab WHERE user_id=?", (user["id"],))
        for c in req.cards:
            conn.execute(
                "INSERT OR REPLACE INTO vocab (user_id,word,translation,stage,level,next_review,ok_count) VALUES (?,?,?,?,?,?,?)",
                (user["id"], c.word, c.translation, c.stage, c.level, c.next_review, c.ok_count)
            )
        conn.commit()
        return {"ok": True, "count": len(req.cards)}
    finally:
        conn.close()


# ─── BAREM ────────────────────────────────────────────────────────────────────

@app.get("/barem/user")
def barem_get(user=Depends(get_current_user)):
    conn = get_db()
    try:
        r = conn.execute("SELECT * FROM barem WHERE user_id=?", (user["id"],)).fetchone()
        return {"figuri": r["figuri"], "motive": r["motive"], "personaj": r["personaj"]} if r else None
    finally:
        conn.close()


@app.post("/barem/user")
def barem_save(req: BaremSaveRequest, user=Depends(get_current_user)):
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO barem (user_id,figuri,motive,personaj) VALUES (?,?,?,?)",
            (user["id"], req.figuri, req.motive, req.personaj)
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


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
    port = int(os.getenv("PORT", 8000))
    print(f"RomânăPro Backend — port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
