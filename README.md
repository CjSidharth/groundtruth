# 📚 Exam Prep Engine

An AI-assisted exam-prep toolkit. Turn messy university question-bank PDFs into a study workflow: **attempt answers yourself → get AI feedback grounded in your own reference PDFs → review on a spaced-repetition schedule → export offline flashcards and printable study guides.**

| Component | What it does |
|---|---|
| `app.py` | Streamlit study app: browse questions, write notes/code, attach images, record voice answers, spaced-repetition review |
| `pdf_to_csv.py` | Extracts questions from irregular/OCR'd university PDFs into CSV |
| `create_flashcards.py` | Builds an offline, deployable HTML/JS/CSS flashcard site (`qb_deploy/`) |
| `generate_pdf.py` | Exports your notes as a printable study-guide PDF (WeasyPrint) |
| `archive_old_flashcards.py` | Snapshots a subject's flashcards before re-exporting |

> Want the full design write-up? See [docs/PROJECT_OVERVIEW.md](docs/PROJECT_OVERVIEW.md).

---

## ✅ Prerequisites

| Requirement | Notes |
|---|---|
| **Python 3.11+** | Required by the pinned `numpy` / `pandas` versions |
| **Git** | To clone the repo |
| **[Ollama](https://ollama.com/download)** *(optional)* | For private, offline AI. Skip it if you'll use Gemini |
| **Gemini API key** *(optional)* | Free key from [Google AI Studio](https://aistudio.google.com/apikey). Skip it if you'll use Ollama |
| **Pango** *(only for PDF export)* | WeasyPrint needs it — see [platform notes](#-platform-notes) |

You need **at least one** of Ollama or a Gemini key for the AI features. Browsing, notes, and flashcards work without either.

---

## 🚀 Installation

### 1. Clone the repo

```bash
git clone https://github.com/CjSidharth/qb.git
cd qb
```

### 2. Create a virtual environment and install dependencies

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Windows (PowerShell)**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. Set up an AI provider

**Option A — Local (Ollama, private & offline)**

```bash
ollama pull gemma3:12b      # best quality, ~8 GB, used for answers and scanned-page OCR
ollama pull llama3.2:3b     # optional: faster, lighter
```

Make sure Ollama is running (the desktop app, or `ollama serve`) before you start the study app.

**Option B — Google Gemini (faster, needs internet)**

Create `.streamlit/secrets.toml`:

```toml
GEMINI_API_KEY = "your-key-here"
```

Or skip the file and paste the key into the app's sidebar. `.streamlit/` is git-ignored, so your key won't be committed.

### 4. Run the app

```bash
streamlit run app.py
```

Streamlit opens at <http://localhost:8501>. The first voice recording downloads the Whisper `small` model (~500 MB) once.

---

## 📂 Adding your own subjects

The app reads whatever is under `data/`:

```
data/
└── My_Subject/
    ├── Chapter_1_INTRODUCTION.csv     # question bank (see columns below)
    ├── images/                        # images used by questions
    └── reference/                     # optional: one textbook/notes PDF per chapter
        └── Ch-1_Introduction.pdf
```

CSV columns: `Category, Sr. No., Question, Marks, CO, RBT Level`

**Generate the CSVs from a question-bank PDF:**

```bash
python pdf_to_csv.py path/to/question_bank.pdf data/My_Subject
```

**Reference PDFs** (optional) are fuzzy-matched to chapters by filename. When present, AI feedback is grounded in them and cites the source file and page. Scanned or handwritten pages are OCR'd once, then cached.

Your notes and progress are saved automatically to `progress/<Subject>_progress.json`.

---

## 🛠️ Utility scripts

Run from the repo root, with the virtualenv active:

```bash
python create_flashcards.py <Subject_Name>          # static offline flashcard site → qb_deploy/
python generate_pdf.py <Subject_Name> [chapter|all] # printable study-guide PDF   → qb_deploy/
python archive_old_flashcards.py <Subject_Name>     # timestamped backup → archives/
```

`<Subject_Name>` is the folder name under `data/`, e.g. `Machine_Learning`.

To use the flashcards on your phone, host the contents of `qb_deploy/` on any static host (GitHub Pages, Netlify, …). No server or build step needed.

---

## 🖥️ Platform notes

**macOS**

```bash
brew install pango          # only needed for generate_pdf.py
```

**Linux (Debian/Ubuntu)**

```bash
sudo apt install libpango-1.0-0 libpangoft2-1.0-0 espeak-ng
```

`espeak-ng` is used for text-to-speech.

**Windows**

Install the [GTK3 runtime](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#windows) if you want PDF export. Everything else works out of the box.

---

## 🩹 Troubleshooting

| Problem | Fix |
|---|---|
| `[Ollama Error] ... Is Ollama running?` | Start Ollama, then check `ollama list` shows the model you selected |
| `[Rate Limit] Google is slowing down requests` | Wait a couple of minutes or switch the sidebar to **Local (Ollama)** |
| `pip install` fails on `numpy`/`pandas` | Your Python is older than 3.11. Check with `python --version` |
| `generate_pdf.py` fails on `cairo`/`pango` | Install Pango — see [platform notes](#-platform-notes) |
| No sound from text-to-speech | Linux: install `espeak-ng`. Otherwise check the system output device |
| Microphone recording doesn't appear | Allow microphone access for the browser tab |

---

## 🔒 Privacy

Nothing is sent to a cloud provider unless you pick **Google Gemini** in the sidebar. With **Local (Ollama)** all questions, notes, audio, and reference PDFs stay on your machine.

---

## 🤝 Contributing

Before changing code, read [CLAUDE.md](CLAUDE.md). It lists a few rules that exist to prevent data-loss bugs: stable question IDs, NaN handling in Streamlit filters, `on_change` auto-save, and image-upload naming.
