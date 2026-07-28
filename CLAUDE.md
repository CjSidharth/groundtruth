# 📚 Exam Prep Engine - Project Guidelines for Claude

## 🎯 Project Overview

This project is an AI-powered Exam Preparation suite. It consists of:

1. A Streamlit web application (`app.py`) for students to review questions, add notes, and use AI (Whisper/Gemini/Ollama) to analyze audio answers.
2. A PDF-to-CSV parser (`pdf_to_csv.py`) that extracts questions from highly irregular university PDFs.
3. A static site generator (`create_flashcards.py`) that builds an offline, deployable HTML/JS/CSS flashcard app.
4. A PDF generator (`generate_pdf.py`) that uses WeasyPrint to export student notes into printable study guides.

## 🛠️ Tech Stack

- **Frontend/App:** Python, Streamlit, Pandas
- **AI & Audio:** faster-whisper, ollama, google-genai, pyttsx3
- **Data Processing:** pdfplumber, regex, hashlib
- **Exporting:** WeasyPrint, Pygments, Markdown, raw HTML/CSS/JS

## 🚨 CRITICAL RULES (Do NOT Break These)

When editing code in this repository, you MUST adhere to the following rules, which were implemented to fix severe edge-cases and data-loss bugs:

### 1. The StableID System

- **Rule:** Never use row numbers or DataFrame indices to identify questions.
- **Why:** CSV rows change. We use an MD5 hash of the question text to generate a `StableID`.
- **Implementation:** Both `app.py` and `create_flashcards.py` must use: `hashlib.md5(str(text).encode()).hexdigest()[:8]`

### 2. Streamlit & Pandas `NaN` Handling

- **Rule:** Never pass raw Pandas columns directly into Streamlit UI components (like `st.multiselect`).
- **Why:** Missing data in PDFs results in `NaN` floats, which crashes Streamlit's `<` sorting operator.
- **Implementation:** Always use a pure python string-cleaning helper (e.g., `clean_ui_value`) to convert `NaN`/`None` to `"N/A"` and strip `.0` from floats before rendering UI filters.

### 3. Streamlit Auto-Save & Callbacks

- **Rule:** Text Areas for notes and code MUST use the `on_change` callback to save data to the `st.session_state.progress` dictionary.
- **Why:** If a user clicks the sidebar to change subjects, Streamlit reruns and destroys unsaved text. `on_change` guarantees saves happen before state swaps.

### 4. Image Upload Infinite Loops

- **Rule:** Uploaded images must be renamed with a prefix (e.g., `f"{note_key}_{uploaded_file.name}"`).
- **Why:** Comparing `uploaded_file.name` to the saved state name without a prefix causes an infinite `st.rerun()` loop that crashes the app.

### 5. PDF Parsing Flexibility

- **Rule:** Do not rely on strictly formatted table headers in `pdf_to_csv.py`.
- **Why:** OCR splits headers across lines randomly. Use spatial coordinates (`x0 > 350`) and regex (`CO\d+`, `\d{1,2}`) to extract Marks, CO, and RBT from the right side of the page.

## 🧹 Coding Style & Formatting

- Write clean, modular Python. Extract large helper functions into separate files if necessary.
- Provide PEP-257 docstrings for new functions.
- If modifying HTML/JS/CSS for the flashcard deployer, keep it vanilla (no React/frameworks) and prioritize mobile responsiveness.

## 📂 Directory Structure

- `/data/`: Contains subject folders, which contain `.csv` question banks and an `/images/` subfolder.
- `/progress/`: Contains JSON files storing student notes, code, and image links.
- `/qb_deploy/`: The output folder for the generated Flashcard website and WeasyPrint PDFs.
- `/archives/`: Contains frozen, timestamped backups of old flashcard states.
