import os
import json
import hashlib
import uuid
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Depends, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Optional, List
import sqlite3
import groq
import shutil
from pathlib import Path
import aiofiles
import PyPDF2
from docx import Document

# ---------- Configuration ----------
SECRET_KEY = "your-super-secret-key-change-me"
GROQ_API_KEY = os.getenv("GROQ_API_KEY") or "your-groq-api-key"
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)
DB_PATH = "asifeda.db"
MAX_STORAGE_PER_USER = 100 * 1024 * 1024  # 100 MB

# ---------- Database Setup ----------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        full_name TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        storage_used INTEGER DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        filename TEXT NOT NULL,
        original_name TEXT NOT NULL,
        file_path TEXT NOT NULL,
        file_size INTEGER NOT NULL,
        uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        processed BOOLEAN DEFAULT 0,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS pyqs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        topic TEXT NOT NULL,
        question TEXT NOT NULL,
        options TEXT NOT NULL,
        correct_answer INTEGER NOT NULL,
        explanation TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        topic TEXT NOT NULL,
        predicted_questions TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()

init_db()

# ---------- FastAPI App ----------
app = FastAPI(title="ASIFEDA AI", version="5.0")

# For serving HTML (we'll embed frontend inside index.html)
templates = Jinja2Templates(directory="templates")
os.makedirs("templates", exist_ok=True)

# Write the frontend HTML to a template file
with open("templates/index.html", "w", encoding="utf-8") as f:
    f.write("""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ASIFEDA AI – Ultimate Exam Platform</title>
  <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/axios/dist/axios.min.js"></script>
  <style>
    body { background: black; color: white; }
    .card { background: #111; border-radius: 1rem; padding: 1.5rem; margin-bottom: 1rem; border: 1px solid #2a2a2a; }
    .btn { background: white; color: black; padding: 0.5rem 1rem; border-radius: 2rem; font-weight: bold; }
    .btn-secondary { background: #1a1a1a; color: white; border: 1px solid #333; }
    .storage-bar { height: 6px; background: #222; border-radius: 3px; margin: 8px 0; }
    .storage-fill { height: 100%; background: #4ade80; border-radius: 3px; width: 0%; }
  </style>
</head>
<body>
<div id="loginScreen" class="min-h-screen flex items-center justify-center bg-black">
  <div class="bg-gray-900 p-8 rounded-2xl w-96 border border-gray-800">
    <h2 class="text-2xl font-bold mb-4">⚡ ASIFEDA AI</h2>
    <input type="email" id="email" placeholder="Email" class="w-full p-2 mb-2 bg-gray-800 rounded border border-gray-700 text-white">
    <input type="password" id="password" placeholder="Password" class="w-full p-2 mb-4 bg-gray-800 rounded border border-gray-700 text-white">
    <button onclick="login()" class="btn w-full">Login / Sign Up</button>
    <p class="text-xs text-gray-500 mt-4">First time? Account created automatically.</p>
  </div>
</div>
<div id="app" style="display:none">
  <div class="flex min-h-screen">
    <div class="w-64 bg-gray-900 border-r border-gray-800 p-4 fixed h-full overflow-auto">
      <div class="text-xl font-bold mb-6">⚡ ASIFEDA</div>
      <div onclick="showTab('dashboard')" class="nav-item p-2 mb-1 rounded cursor-pointer hover:bg-gray-800" data-tab="dashboard">📊 Dashboard</div>
      <div onclick="showTab('tutor')" class="nav-item p-2 mb-1 rounded cursor-pointer hover:bg-gray-800" data-tab="tutor">🤖 AI Tutor</div>
      <div onclick="showTab('quiz')" class="nav-item p-2 mb-1 rounded cursor-pointer hover:bg-gray-800" data-tab="quiz">✅ MCQ Quiz</div>
      <div onclick="showTab('notes')" class="nav-item p-2 mb-1 rounded cursor-pointer hover:bg-gray-800" data-tab="notes">📝 Notes</div>
      <div onclick="showTab('answer')" class="nav-item p-2 mb-1 rounded cursor-pointer hover:bg-gray-800" data-tab="answer">✍️ Answer Trainer</div>
      <div onclick="showTab('planner')" class="nav-item p-2 mb-1 rounded cursor-pointer hover:bg-gray-800" data-tab="planner">📅 Planner</div>
      <div onclick="showTab('pyq')" class="nav-item p-2 mb-1 rounded cursor-pointer hover:bg-gray-800" data-tab="pyq">📚 PYQ Bank</div>
      <div onclick="showTab('library')" class="nav-item p-2 mb-1 rounded cursor-pointer hover:bg-gray-800" data-tab="library">📁 Library (100MB)</div>
      <div onclick="showTab('predict')" class="nav-item p-2 mb-1 rounded cursor-pointer hover:bg-gray-800" data-tab="predict">🔮 Future Predictor</div>
      <div class="text-xs text-gray-600 mt-10 text-center">Built by Muhammad Asif</div>
    </div>
    <div class="ml-64 flex-1 p-6">
      <div class="flex justify-between mb-4">
        <h1 class="text-2xl font-bold">ASIFEDA Ultimate OS</h1>
        <button onclick="logout()" class="btn-secondary py-1 px-3 rounded-full text-sm">Logout</button>
      </div>
      <div id="dashboard" class="tab-pane">
        <div class="grid grid-cols-1 md:grid-cols-4 gap-4 mb-4">
          <div class="card"><div class="text-gray-400">Storage</div><div class="text-2xl font-bold" id="storageUsed">0 MB</div><div class="storage-bar"><div class="storage-fill" id="storageFill"></div></div></div>
          <div class="card"><div class="text-gray-400">PYQs Bank</div><div class="text-2xl font-bold" id="pyqCount">0</div></div>
          <div class="card"><div class="text-gray-400">Notes</div><div class="text-2xl font-bold" id="notesCount">0</div></div>
          <div class="card"><div class="text-gray-400">AI Requests</div><div class="text-2xl font-bold" id="aiCount">0</div></div>
        </div>
        <div class="card"><h3>⚡ Heavy‑Duty AI Engine</h3><p>500+ PYQs per topic | AI Future Predictor | Upload PDF/DOCX | 100MB per student | UPSC, SSC, NDA, JKSSB</p></div>
      </div>
      <div id="tutor" class="tab-pane hidden">
        <div class="card">
          <h3>🤖 AI Tutor (reads your files)</h3>
          <div id="chatMessages" class="h-96 overflow-y-auto mb-4 space-y-2 bg-black p-2 rounded border border-gray-800">
            <div class="message ai">Ask me anything about your exams. I can search your uploaded documents.</div>
          </div>
          <div class="flex gap-2">
            <textarea id="tutorInput" rows="1" class="flex-1 bg-gray-800 rounded p-2 border border-gray-700" placeholder="Type your question..."></textarea>
            <button onclick="sendMessage()" class="btn">Send</button>
          </div>
        </div>
      </div>
      <div id="quiz" class="tab-pane hidden"><div class="card"><h3>✅ MCQ Quiz</h3><input id="quizTopic" placeholder="Topic" class="w-full p-2 mb-2 bg-gray-800 rounded"><select id="quizExam" class="w-full p-2 mb-2 bg-gray-800 rounded"><option>UPSC</option><option>SSC</option><option>NDA</option><option>JKSSB</option></select><select id="quizCount" class="w-full p-2 mb-2 bg-gray-800 rounded"><option>5</option><option selected>10</option></select><button onclick="generateQuiz()" class="btn">Generate Quiz</button><div id="quizArea" class="mt-4"></div></div></div>
      <div id="notes" class="tab-pane hidden"><div class="card"><h3>📝 AI Notes</h3><input id="notesTopic" placeholder="Topic" class="w-full p-2 mb-2 bg-gray-800 rounded"><button onclick="generateNotes()" class="btn">Generate Notes</button><div id="notesOutput" class="mt-4"></div><button id="saveNoteBtn" onclick="saveNote()" style="display:none" class="btn-secondary mt-2">💾 Save Note</button></div><div id="savedNotesList"></div></div>
      <div id="answer" class="tab-pane hidden"><div class="card"><h3>✍️ Answer Trainer</h3><textarea id="answerQ" rows="3" class="w-full p-2 mb-2 bg-gray-800 rounded" placeholder="Paste question..."></textarea><button onclick="generateAnswer()" class="btn">Generate Model Answer</button><div id="answerOut" class="mt-4"></div></div></div>
      <div id="planner" class="tab-pane hidden"><div class="card"><h3>📅 Study Planner</h3><input id="targetExam" placeholder="Exam (e.g., UPSC 2025)" class="w-full p-2 mb-2 bg-gray-800 rounded"><input id="daysLeft" placeholder="Days left" value="90" class="w-full p-2 mb-2 bg-gray-800 rounded"><button onclick="generatePlan()" class="btn">Generate Plan</button><div id="planOut" class="mt-4"></div></div></div>
      <div id="pyq" class="tab-pane hidden"><div class="card"><h3>📚 PYQ Bank</h3><input id="searchPYQ" placeholder="Search by topic" class="w-full p-2 mb-2 bg-gray-800 rounded"><button onclick="searchPYQ()" class="btn">Search</button><button onclick="generatePYQBatch()" class="btn-secondary ml-2">🤖 AI Generate 500 PYQs</button><div id="pyqList" class="mt-4"></div></div></div>
      <div id="library" class="tab-pane hidden"><div class="card"><h3>📁 My Library (100MB)</h3><input type="file" id="fileUpload" multiple accept=".pdf,.docx,.txt" class="mb-2"><button onclick="uploadFiles()" class="btn">Upload</button><div id="fileList" class="mt-4"></div><div class="storage-bar mt-2"><div class="storage-fill" id="libStorageFill"></div></div></div></div>
      <div id="predict" class="tab-pane hidden"><div class="card"><h3>🔮 AI Future Paper Predictor</h3><input id="predictTopic" placeholder="Topic (e.g., Indian Polity)" class="w-full p-2 mb-2 bg-gray-800 rounded"><button onclick="predictFuture()" class="btn">Predict</button><div id="predictOut" class="mt-4"></div></div></div>
    </div>
  </div>
</div>
<script>
let token = null;

async function login() {
  const email = document.getElementById('email').value;
  const password = document.getElementById('password').value;
  const res = await axios.post('/api/auth/login', { email, password });
  if (res.data.access_token) {
    token = res.data.access_token;
    localStorage.setItem('token', token);
    document.getElementById('loginScreen').style.display = 'none';
    document.getElementById('app').style.display = 'block';
    loadDashboard();
    showTab('dashboard');
  } else alert('Error');
}

function logout() {
  localStorage.removeItem('token');
  location.reload();
}

if (localStorage.getItem('token')) {
  token = localStorage.getItem('token');
  document.getElementById('loginScreen').style.display = 'none';
  document.getElementById('app').style.display = 'block';
  loadDashboard();
}

function showTab(tab) {
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.add('hidden'));
  document.getElementById(tab).classList.remove('hidden');
  if (tab === 'pyq') searchPYQ();
  if (tab === 'library') loadFiles();
}

function loadDashboard() {
  axios.get('/api/users/stats', { headers: { Authorization: `Bearer ${token}` } }).then(res => {
    document.getElementById('storageUsed').innerText = (res.data.storage_used_mb || 0).toFixed(1) + ' MB';
    const percent = (res.data.storage_used / (100*1024*1024)) * 100;
    document.getElementById('storageFill').style.width = percent + '%';
    document.getElementById('pyqCount').innerText = res.data.pyq_count || 0;
    document.getElementById('notesCount').innerText = res.data.notes_count || 0;
  });
}

async function sendMessage() {
  const q = document.getElementById('tutorInput').value;
  if (!q) return;
  const chatDiv = document.getElementById('chatMessages');
  chatDiv.innerHTML += `<div class="message user text-right">${q}</div>`;
  document.getElementById('tutorInput').value = '';
  try {
    const res = await axios.post('/api/ai/chat', { message: q }, { headers: { Authorization: `Bearer ${token}` } });
    chatDiv.innerHTML += `<div class="message ai">${res.data.reply.replace(/\\n/g,'<br>')}</div>`;
  } catch(e) { chatDiv.innerHTML += `<div class="message ai">Error: ${e.response?.data?.detail || e.message}</div>`; }
  chatDiv.scrollTop = chatDiv.scrollHeight;
}

async function generateQuiz() {
  const topic = document.getElementById('quizTopic').value || 'General';
  const exam = document.getElementById('quizExam').value;
  const count = document.getElementById('quizCount').value;
  const area = document.getElementById('quizArea');
  area.innerHTML = '<div class="spinner"></div>';
  try {
    const res = await axios.post('/api/ai/generate-quiz', { topic, exam, count }, { headers: { Authorization: `Bearer ${token}` } });
    const questions = res.data.questions;
    let html = '';
    questions.forEach((q, idx) => {
      html += `<div class="border border-gray-700 p-3 mb-3 rounded"><strong>Q${idx+1}: ${q.q}</strong><br>`;
      q.opts.forEach((opt, i) => {
        html += `<button onclick="checkAnswer(${idx}, ${i}, ${q.ans}, this)" class="block w-full text-left p-2 m-1 bg-gray-800 rounded">${String.fromCharCode(65+i)}. ${opt}</button>`;
      });
      html += `<div id="exp-${idx}" class="text-sm mt-2"></div></div>`;
    });
    area.innerHTML = html;
    window.currentQuiz = questions;
  } catch(e) { area.innerHTML = 'Error generating quiz'; }
}
function checkAnswer(qIdx, selected, correct, btn) {
  const expDiv = document.getElementById(`exp-${qIdx}`);
  if (selected === correct) {
    expDiv.innerHTML = '✅ Correct! ' + window.currentQuiz[qIdx].exp;
    btn.style.borderColor = '#4ade80';
  } else {
    expDiv.innerHTML = `❌ Wrong. Correct: ${window.currentQuiz[qIdx].opts[correct]}. ${window.currentQuiz[qIdx].exp}`;
    btn.style.borderColor = '#f87171';
  }
}
async function generateNotes() { /* similar to previous */ }
async function generateAnswer() { /* similar */ }
async function generatePlan() { /* similar */ }
async function searchPYQ() {
  const topic = document.getElementById('searchPYQ').value;
  const res = await axios.get(`/api/pyq/search?topic=${topic}`, { headers: { Authorization: `Bearer ${token}` } });
  const pyqs = res.data;
  document.getElementById('pyqList').innerHTML = pyqs.map(p => `<div class="border border-gray-700 p-3 mb-2"><strong>${p.question}</strong><br>Options: ${JSON.parse(p.options).join(' | ')}<br><span class="text-green-400">Answer: ${JSON.parse(p.options)[p.correct_answer]}</span><br>${p.explanation}</div>`).join('');
}
async function generatePYQBatch() {
  const topic = document.getElementById('searchPYQ').value;
  if (!topic) return alert('Enter topic');
  document.getElementById('pyqList').innerHTML = 'Generating 500 PYQs... this may take a minute.';
  const res = await axios.post('/api/pyq/generate-batch', { topic, count: 500 }, { headers: { Authorization: `Bearer ${token}` } });
  alert(`Added ${res.data.added} PYQs`);
  searchPYQ();
}
async function uploadFiles() {
  const files = document.getElementById('fileUpload').files;
  const formData = new FormData();
  for (let f of files) formData.append('files', f);
  const res = await axios.post('/api/files/upload', formData, { headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'multipart/form-data' } });
  alert('Uploaded');
  loadFiles();
}
async function loadFiles() {
  const res = await axios.get('/api/files/list', { headers: { Authorization: `Bearer ${token}` } });
  const files = res.data;
  document.getElementById('fileList').innerHTML = files.map(f => `<div>📄 ${f.original_name} (${(f.file_size/1024).toFixed(1)} KB) <button onclick="deleteFile(${f.id})">🗑️</button></div>`).join('');
}
async function predictFuture() {
  const topic = document.getElementById('predictTopic').value;
  if (!topic) return alert('Enter topic');
  const res = await axios.post('/api/ai/predict-future', { topic }, { headers: { Authorization: `Bearer ${token}` } });
  document.getElementById('predictOut').innerHTML = `<div class="bg-gray-800 p-3 rounded"><strong>🔮 Predicted Questions for ${topic}</strong><ul>${res.data.predictions.map(p => `<li>${p}</li>`).join('')}</ul></div>`;
}
</script>
</body>
</html>""")

# ---------- API Endpoints ----------
security = HTTPBearer()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security), db=Depends(get_db)):
    token = credentials.credentials
    # Simple JWT verification (simplified – in production use python-jose)
    try:
        payload = json.loads(token)  # dummy
        user_id = payload.get("user_id")
        user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if user:
            return user
    except:
        pass
    raise HTTPException(401, "Invalid token")

class LoginData(BaseModel):
    email: str
    password: str

@app.post("/api/auth/login")
def login(data: LoginData, db=Depends(get_db)):
    email = data.email
    password = data.password
    user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not user:
        # Auto-register
        pwd_hash = hashlib.sha256(password.encode()).hexdigest()
        db.execute("INSERT INTO users (email, full_name, password_hash) VALUES (?, ?, ?)",
                   (email, email.split('@')[0], pwd_hash))
        db.commit()
        user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    else:
        if user["password_hash"] != hashlib.sha256(password.encode()).hexdigest():
            raise HTTPException(401, "Invalid password")
    # Create token (simplified)
    token = json.dumps({"user_id": user["id"], "exp": (datetime.utcnow() + timedelta(days=7)).isoformat()})
    return {"access_token": token, "token_type": "bearer"}

@app.get("/api/users/stats")
def stats(user=Depends(get_current_user), db=Depends(get_db)):
    storage = db.execute("SELECT SUM(file_size) as total FROM files WHERE user_id = ?", (user["id"],)).fetchone()["total"] or 0
    pyq_count = db.execute("SELECT COUNT(*) FROM pyqs WHERE user_id = ?", (user["id"],)).fetchone()[0]
    notes_count = db.execute("SELECT COUNT(*) FROM notes WHERE user_id = ?", (user["id"],)).fetchone()[0]
    return {"storage_used": storage, "storage_used_mb": storage/1024/1024, "pyq_count": pyq_count, "notes_count": notes_count}

@app.post("/api/ai/chat")
async def chat(request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    body = await request.json()
    msg = body.get("message")
    # RAG: search user's files
    files = db.execute("SELECT file_path FROM files WHERE user_id = ?", (user["id"],)).fetchall()
    context = ""
    for f in files:
        if f["file_path"].endswith(".txt"):
            try:
                with open(f["file_path"], "r") as file:
                    text = file.read()
                    if msg.lower() in text.lower():
                        context += f"\n[From {f['file_path']}]: {text[:1500]}\n"
            except: pass
    client = groq.Groq(api_key=GROQ_API_KEY)
    sys_prompt = f"You are ASIFEDA AI exam tutor. Use this context if relevant: {context}"
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": msg}],
        max_tokens=1500
    )
    reply = response.choices[0].message.content
    return {"reply": reply}

@app.post("/api/ai/generate-quiz")
async def generate_quiz(request: Request, user=Depends(get_current_user)):
    body = await request.json()
    topic = body.get("topic")
    exam = body.get("exam")
    count = body.get("count")
    client = groq.Groq(api_key=GROQ_API_KEY)
    prompt = f"Generate {count} MCQs on '{topic}' for {exam} exam. Return JSON array: [{{'q':'question','opts':['a','b','c','d'],'ans':0,'exp':'explanation'}}]"
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7
    )
    import json
    try:
        questions = json.loads(response.choices[0].message.content.replace("```json","").replace("```",""))
    except:
        questions = []
    return {"questions": questions}

@app.post("/api/ai/predict-future")
async def predict_future(request: Request, user=Depends(get_current_user)):
    body = await request.json()
    topic = body.get("topic")
    client = groq.Groq(api_key=GROQ_API_KEY)
    prompt = f"Predict 10 most likely exam questions on '{topic}' for UPSC 2025. Return JSON array of strings."
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}]
    )
    import json
    preds = json.loads(response.choices[0].message.content.replace("```json","").replace("```",""))
    return {"predictions": preds}

@app.post("/api/pyq/generate-batch")
async def generate_pyq_batch(request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    body = await request.json()
    topic = body.get("topic")
    count = body.get("count", 500)
    client = groq.Groq(api_key=GROQ_API_KEY)
    all_pyqs = []
    for _ in range(0, count, 20):
        batch = min(20, count - len(all_pyqs))
        prompt = f"Generate {batch} previous year style questions on '{topic}' for UPSC. Return JSON array: [{{'question':'...','options':['a','b','c','d'],'correct':0,'explanation':'...'}}]"
        resp = client.chat.completions.create(model="llama-3.3-70b-versatile", messages=[{"role":"user","content":prompt}])
        data = json.loads(resp.choices[0].message.content.replace("```json","").replace("```",""))
        for item in data:
            db.execute("INSERT INTO pyqs (user_id, topic, question, options, correct_answer, explanation) VALUES (?,?,?,?,?,?)",
                       (user["id"], topic, item["question"], json.dumps(item["options"]), item["correct"], item.get("explanation","")))
            db.commit()
            all_pyqs.append(item)
    return {"added": len(all_pyqs)}

@app.get("/api/pyq/search")
def search_pyq(topic: str, user=Depends(get_current_user), db=Depends(get_db)):
    rows = db.execute("SELECT * FROM pyqs WHERE user_id = ? AND topic LIKE ?", (user["id"], f"%{topic}%")).fetchall()
    return [{"question": r["question"], "options": r["options"], "correct_answer": r["correct_answer"], "explanation": r["explanation"]} for r in rows]

@app.post("/api/files/upload")
async def upload_files(files: List[UploadFile] = File(...), user=Depends(get_current_user), db=Depends(get_db)):
    # Check quota
    used = db.execute("SELECT SUM(file_size) as total FROM files WHERE user_id = ?", (user["id"],)).fetchone()["total"] or 0
    for f in files:
        if used + f.size > MAX_STORAGE_PER_USER:
            raise HTTPException(400, "Storage quota exceeded (100MB)")
        file_path = UPLOAD_DIR / f"{uuid.uuid4()}_{f.filename}"
        async with aiofiles.open(file_path, "wb") as out:
            content = await f.read()
            await out.write(content)
        db.execute("INSERT INTO files (user_id, filename, original_name, file_path, file_size) VALUES (?,?,?,?,?)",
                   (user["id"], file_path.name, f.filename, str(file_path), len(content)))
        db.commit()
        used += len(content)
    return {"message": "Uploaded"}

@app.get("/api/files/list")
def list_files(user=Depends(get_current_user), db=Depends(get_db)):
    rows = db.execute("SELECT id, original_name, file_size FROM files WHERE user_id = ?", (user["id"],)).fetchall()
    return [{"id": r["id"], "original_name": r["original_name"], "file_size": r["file_size"]} for r in rows]

@app.get("/")
async def root():
    with open("templates/index.html", "r") as f:
        return HTMLResponse(content=f.read())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
