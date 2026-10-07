# 📚 Exam Prep Engine

A personal, locally-run exam preparation system that turns raw university question-bank PDFs into an AI-assisted study workflow: extract questions → attempt answers yourself → get AI grounded in your own reference PDFs to check gaps → review on a spaced-repetition schedule → export offline flashcards and printable study guides.

Built solo, iteratively, over one exam cycle (Semester exams, Aug 2026) across 19 subjects and **6,748 questions**. This document is a full technical + product snapshot of the codebase, intended as source material for later reports, presentations, or onboarding another AI assistant to the project.

---

## 1. Problem It Solves

University exam papers are drawn from large, messy PDF question banks (irregular tables, OCR'd scans, inconsistent headers). The traditional prep loop is:

1. Get PDF question bank from a WhatsApp group.
2. Manually retype/organize into something reviewable.
3. Write long-form notes per question from textbook PDFs.
4. Re-read notes before the exam (passive review — weak retention).

This project automates steps 1–2, restructures step 3 around **active recall** (attempt first, then AI-assisted gap-checking against your own reference material — not AI-generated answers copy-pasted in), and replaces step 4 with **spaced repetition** instead of re-reading.

---

## 2. Architecture Overview

```
┌─────────────────┐     ┌──────────────────┐     ┌───────────────────┐
│  University PDF  │────▶│  pdf_to_csv.py    │────▶│  data/<Subject>/   │
│ (question bank)  │     │  (pdfplumber +    │     │  Chapter_N_*.csv   │
└─────────────────┘     │   spatial regex)  │     └────────┬───────────┘
                         └──────────────────┘              │
                                                             ▼
┌────────────────────────────────────────────────────────────────────┐
│                              app.py (Streamlit)                     │
│  • Question List / Detail / Flashcard(SRS) / PDF Reader / Dashboard │
│  • Notes + code + images per question (progress/<Subject>.json)     │
│  • AI: gap-check, voice practice, reveal-answer — via ask_ai()      │
│  • Grounded in reference_material.py (chapter-matched source PDFs)  │
│  • Spaced repetition via spaced_repetition.py (SM-2-style)          │
└───────────────────┬───────────────────────────┬─────────────────────┘
                     │                           │
                     ▼                           ▼
        ┌─────────────────────┐      ┌─────────────────────────┐
        │ create_flashcards.py │      │   generate_pdf.py       │
        │  → qb_deploy/ static │      │  → WeasyPrint study      │
        │  HTML/JS/CSS site    │      │    guide PDF export       │
        └─────────────────────┘      └─────────────────────────┘
                     ▲
                     │
        ┌─────────────────────────┐
        │ archive_old_flashcards.py│  (freezes a timestamped
        │  → archives/             │   snapshot before re-export)
        └─────────────────────────┘
```

**Design principle threaded through the whole app:** questions are identified by a **StableID** (an MD5 hash of the question text), never by row number or DataFrame index — because the underlying CSVs get re-parsed/re-ordered and row numbers are not stable across sessions. See §7 for why this matters and where it bit us during development.

---

## 3. Tech Stack

| Layer | Tools |
|---|---|
| App framework | Streamlit 1.56 |
| Data | Pandas, CSV (per-subject, per-chapter files) |
| PDF question extraction | `pdfplumber` (spatial word coordinates, not just text streams) |
| Local LLM | `ollama` Python client — default model `gemma3:12b` (also `llama3.2:3b` / `1b` for speed) |
| Cloud LLM (opt-in) | `google-genai` 1.73.1 (Gemini), native PIL image support in prompt `contents` |
| Vision OCR | Local-first: `gemma3:12b` via Ollama; falls back to Gemini vision if local OCR fails/unavailable |
| Speech-to-text | `faster-whisper` ("small" model, CPU, int8, beam_size=1) |
| Text-to-speech | `pyttsx3` (offline TTS for PDF-reader "read aloud") |
| Static export | Vanilla HTML/CSS/JS (`index.html`, `style.css`, `script.js`) + `marked.js`, `KaTeX`, `highlight.js` via CDN |
| Printable export | WeasyPrint (HTML→PDF), Pygments (code highlighting), `python-markdown` (tables, fenced code) |
| Caching | `@st.cache_data` / `@st.cache_resource`, plus custom disk cache with mtime/size invalidation for OCR'd reference pages |

Full pinned dependency list: `requirements.txt`.

---

## 4. Repository Layout

```
qb/
├── app.py                    # Main Streamlit application (all views)
├── reference_material.py     # Reference-PDF matching, OCR, caching, relevance ranking, prompt builders
├── spaced_repetition.py      # SM-2-style scheduler (Again/Hard/Good/Easy)
├── pdf_to_csv.py             # University question-bank PDF → per-chapter CSVs
├── create_flashcards.py      # data/ + progress/ → static offline flashcard site (qb_deploy/)
├── generate_pdf.py           # qb_deploy/flashcard_data.json → printable WeasyPrint PDF
├── archive_old_flashcards.py # Freezes a timestamped snapshot of a subject's export to archives/
├── index.html / style.css / script.js   # Static flashcard site template (copied into qb_deploy/ on export)
├── requirements.txt
├── data/
│   └── <Subject>/
│       ├── Chapter_N_<Title>.csv     # Category, Sr.No., Question, Marks, CO, RBT Level
│       ├── images/                   # User-uploaded images, prefixed by StableID
│       └── reference/                # Source textbook/notes PDFs for AI grounding (per chapter)
│           └── .cache/               # OCR'd page text + metadata, keyed by chapter (gitignored)
├── progress/
│   ├── <Subject>_progress.json       # {"notes": {...}, "code": {...}, "images": {...}, "srs": {...}, "done_questions": [...]}
│   └── _streak.json                  # {"current_streak", "longest_streak", "last_review_date"}
├── qb_deploy/                        # Generated output (gitignored) — static site + PDFs
└── archives/                         # Timestamped frozen snapshots (gitignored inputs vary)
```

**19 subjects currently loaded**, 6,748 questions total (see §8 for the full per-subject breakdown). Of those, 6 are the active exam-cycle subjects: **Machine Learning, Cloud Computing, MAD, Artificial Intelligence, Distributed Systems, Compiler Design**; the rest are prior-semester subjects kept for reference/practice.

---

## 5. Core Features

### 5.1 Question Bank Ingestion (`pdf_to_csv.py`)
- Parses irregular university question-bank PDFs using `pdfplumber` word-level coordinates rather than relying on table structure (which OCR/export tools frequently mangle — headers split across lines, misaligned columns).
- Groups words into visual lines by y-coordinate (`group_lines`, tolerance-based).
- Extracts **Marks / CO (course outcome) / RBT (Revised Bloom's Taxonomy) level** using spatial position (`x0 > 350`, i.e. right-hand side of the page) combined with regex (`CO\d+`, 1–2 digit numbers) — never a fixed column header, since headers are unreliable across different PDF exports.
- Outputs one CSV per chapter into `data/<Subject>/`, columns: `Category, Sr. No., Question, Marks, CO, RBT Level`.

### 5.2 Study App (`app.py`) — Views

**Question List** — paginated, filterable by chapter and review status (`All / Reviewed / Not Reviewed`), per-chapter and per-filter progress bars. All filter dropdown values are passed through a `clean_ui_value` helper before hitting `st.multiselect`/`st.selectbox` (see §7.2 — raw Pandas `NaN` floats crash Streamlit's UI sort).

**Detail** — per-question workspace:
- Long-form Markdown notes editor (with tab-indent support, syntax-highlighted code block editor, multi-image upload).
- **AI Gap-Check**: you write your own answer attempt first; AI compares it against the chapter's reference material and flags what's missing/wrong — it does not hand you the answer. Grounded via `reference_material.get_relevant_reference()`, cites source PDF + page number.
- **Voice Practice**: record a spoken answer (`st.audio_input`) → transcribed locally via faster-whisper → AI feedback grounded the same way as gap-check → optionally save the transcript/feedback into notes → rate it directly on the SRS scale (voice practice counts as a review).
- **Reveal Full Reference Answer**: an explicit, separate expander (used *after* attempting) that returns a complete grounded answer with citations — kept behind a click so it's not the reflexive first move.
- Every AI response carries a **source citation line** (`unit1p1.pdf: p.3, p.4 · unit3_part2.pdf: p.7`) so you can find the original textbook passage quickly instead of trusting the AI blindly.

**Flashcards** — spaced-repetition review queue: shows a question, flips to reveal your own notes + images, then you self-rate `Again/Hard/Good/Easy`, which reschedules the card via `spaced_repetition.py`. Rating a card also updates the daily streak.

**PDF Reader** — upload a slide deck/notes PDF, page through it in-app, with offline text-to-speech read-aloud (`pyttsx3`) per page/slide.

**Dashboard** — daily streak (current + longest), total cards due across all subjects, per-subject notes-written / due-today breakdown. Built as a lightweight motivation mechanism (see §6).

### 5.3 Reference-Grounded AI (`reference_material.py`)
This is the core anti-hallucination layer, and the most iterated-on part of the codebase:

- **Chapter → PDF matching** (`find_reference_pdfs`): 4-tier fallback — exact name match → substring match → chapter/unit number extraction (regex on filenames like `unit1p1.pdf`) → token-overlap similarity (>0.6). Returns a **list**, since one chapter can be split across multiple reference PDFs.
- **Chapter name normalization**: handles camelCase filenames (`ServiceLevelAgreement` → `service level agreement`) via a lookahead/lookbehind regex split before lowercasing — this fixed a real retrieval miss where PDF text extraction glued `"SLA(ServiceLevelAgreement)"` into one token, causing near-zero keyword overlap with the query.
- **Page extraction with local-first OCR** (`get_reference_pages`): tries `pdfplumber.extract_text()` first; if a page is image-only/scanned (`_is_real_text` check), falls back to vision OCR — local `gemma3:12b` via Ollama first, Gemini vision second. Every OCR'd chapter is cached to disk (`.pages.json` + `.meta.json`, invalidated by source-file mtime/size) so re-runs are instant and a chapter is never silently re-OCR'd mid-session.
- **Relevance selection, not truncation** (`select_relevant_reference`): chunks reference pages and scores by singularized keyword overlap with the question + student answer (stopwords stripped), then packs the highest-scoring chunks up to a character budget — rather than naively taking the first N characters of a chapter, which was proven to drop the actually-relevant section when it appeared later in the document.
- **Prompt builders**: `build_gap_check_prompt`, `build_voice_feedback_prompt`, `build_reveal_answer_prompt` — all explicitly instructed to stay within the provided reference text, flag missing sub-parts of multi-part questions, and avoid both hallucination and reflexive nitpicking.

### 5.4 Spaced Repetition (`spaced_repetition.py`)
Simplified SM-2 variant, day-granularity (this is a daily study cadence app, not sub-day Anki-style requeuing):
- One entry per StableID: `{interval, ease, reps, due, last_reviewed}`.
- First exposure uses fixed learning steps (`Again→0d, Hard→1d, Good→1d, Easy→4d`) rather than the ease-multiplier formula, matching how Anki-style schedulers treat cards that haven't graduated yet.
- Subsequent reviews use the standard ease-adjusted interval growth, with ease floored at `MIN_EASE = 1.3`.

### 5.5 Static Site Export (`create_flashcards.py` → `qb_deploy/`)
Bundles a subject's questions + saved notes + code + images into `flashcard_data.json`, copies the vanilla HTML/CSS/JS template, and copies referenced images — producing a fully offline, deployable flashcard web app (mobile-responsive, KaTeX math rendering, syntax-highlighted code via highlight.js, swipe/keyboard navigation). Useful for reviewing on a phone without the Python app running.

### 5.6 Printable PDF Export (`generate_pdf.py`)
Takes the same `qb_deploy/flashcard_data.json` and renders a print-quality WeasyPrint PDF per subject (or per chapter): custom-font header/footer, syntax-highlighted code (Pygments, monokai), embedded images, proper Markdown table/list rendering. Good for a printed physical study guide.

### 5.7 Archival (`archive_old_flashcards.py`)
Freezes a timestamped snapshot of a subject's exported state into `archives/` before regenerating — so an old deployed flashcard set isn't silently overwritten.

---

## 6. Design Decisions & Rationale

These were explicit, considered tradeoffs made during development — worth keeping if writing up "design decisions" for a report:

- **Attempt-first, AI-checks-second** (not AI-drafts-notes): the stated concern was *"I might lose the understanding aspect if I don't write by myself."* The app is deliberately structured so gap-check/reveal only fire after (or explicitly opt into skipping) a self-written attempt — this is the generation effect from learning science, not a UI afterthought.
- **Ollama-local by default, Gemini opt-in**: cost and speed. Local `gemma3:12b` on an M-series Mac is the default provider; cloud is a conscious escalation, not the default path.
- **Grounding in the user's own reference PDFs, not open-world LLM knowledge**: prevents the AI from inventing plausible-but-wrong content, and produces page-number citations back to the actual textbook — trust is verifiable, not assumed.
- **StableID over row index everywhere**: CSVs get regenerated/reordered; identifying a question by content hash instead of position means notes/images/SRS state survive re-parsing.
- **Streamlit `on_change` callbacks for all note/code saves**: a sidebar subject-switch triggers a Streamlit rerun that would otherwise discard unsaved textarea content; callbacks guarantee the save happens before state is swapped.
- **Motivation via a visible streak + due-count dashboard**, not gamification bloat — kept intentionally minimal after evaluating the tradeoff between adding more features vs. just starting to study.

---

## 7. Notable Bugs Found & Fixed (useful for a "lessons learned" section)

| Bug | Root cause | Fix |
|---|---|---|
| Streamlit crashed sorting subject filter dropdowns | `NaN` floats from missing PDF data broke `<` comparisons in `st.multiselect` | `clean_ui_value` helper converts `NaN`/`None` → `"N/A"`, strips trailing `.0` |
| Infinite `st.rerun()` loop on image upload | Comparing `uploaded_file.name` directly to saved state name with no namespacing | Prefix uploaded filenames with `f"{note_key}_{uploaded_file.name}"` |
| `StreamlitAPIException: cannot be modified after widget instantiated` | Popping/reassigning a text_area's session_state key after the widget already rendered this run | Versioned widget keys (`note_{id}_v{n}`) that increment to force a real remount |
| "Add to notes" appeared to silently do nothing | Streamlit's `text_area` doesn't always visually refresh when the same key's underlying value changes | Same versioned-key fix as above |
| A note got silently wiped on save | `save_all_changes()` defaulted a missing widget key to `""` instead of falling back to the already-saved note | Fallback to `progress['notes'].get(note_key, "")`, not a bare `""` |
| Local Ollama answers got worse after selecting a bigger model | `ask_ai()` had a stale mapping silently downgrading `gemma3:12b` requests toward `llama3.2:3b` | Removed the silent downgrade path |
| Ollama truncated long prompts from the *start*, dropping the actual question | Ollama defaults `num_ctx` to 4096 regardless of what the model supports | Explicit `options={"num_ctx": 8192}` on every `ollama.generate()` call |
| AI said reference content "wasn't in the PDF" when it clearly was (SLA example) | PDF text extraction glued `"SLA(ServiceLevelAgreement)"` into one token; keyword scoring found near-zero overlap | camelCase-aware splitting in `normalize_chapter_name` before scoring |
| `unit1p1.pdf` never matched to "Chapter 1" | Filename shared no words with the chapter title | Added chapter/unit-number regex matching tier + multi-file return from `find_reference_pdfs` |
| Reference excerpt on flashcard flip was a 6,000-char wall of unrelated text | Naive head-of-document truncation, plus plural/singular mismatch (query "standards" vs. text "Standard") in keyword scoring | Chunk + relevance-score selection with singularization (`_score_tokens`), bounded to top-N chunks |
| `create_flashcards.py`: `AttributeError: 'list' object has no attribute 'strip'` | Multi-image support changed the images dict's value type but an old code path still assumed a single string | `get_images()` helper normalizes both the legacy single-string and new list formats |
| `generate_pdf.py`: exported PDFs had no images | Reader looked up the old singular `'image'` key; exporter had moved to plural `'images'` | Fallback lookup: `card_data.get('images', card_data.get('image', []))` |
| `StreamlitDuplicateElementKey` crash on a "most-asked first" sort | The data itself has genuine duplicate rows (identical question text reused across a chapter) that ended up adjacent after sorting, producing duplicate StableID-based widget keys | Composite row key: `f"{subject}_{start_idx+row_pos}_{stable_id}"` |
| Markdown tables didn't render in flashcards | A `.replace('\n', '<br>')` step ran before `st.markdown()`, destroying the newlines GFM tables need to parse | Removed the replace; pass Markdown through untouched |

---

## 8. Current Data Snapshot

*(as of this README — subject to change as more question banks are added)*

| Subject | Questions |
|---|---|
| Cryptography & Network Security | 799 |
| Software Engineering | 578 |
| Cloud Computing | 554 |
| Artificial Intelligence | 505 |
| Python for Data Science | 482 |
| Professional Ethics | 476 |
| Computer Network | 429 |
| Distributed Systems | 427 |
| MAD | 437 |
| System Software | 407 |
| Web Programming | 360 |
| Theory of Computation | 347 |
| Compiler Design | 269 |
| Machine Learning | 243 |
| Viva – ADA | 135 |
| Viva – PDS | 96 |
| Viva – SE | 105 |
| Viva – CN | 73 |
| Viva – CPDP | 26 |
| **Total** | **6,748** |

Active exam-cycle subjects (Aug 2026): Machine Learning, Cloud Computing, MAD, Artificial Intelligence, Distributed Systems, Compiler Design. Reference PDFs (for AI grounding) currently exist for all of those except Compiler Design.

---

## 9. Branches

- `main` — stable, in active study use. Has: reference grounding + citations, spaced repetition + streak + dashboard, voice practice tied into SRS, the Ollama context-window fix, concise reference excerpts. Flashcard review is gated on a note existing for a question (write-then-review model).
- `priority` — exam-triage work-in-progress (not yet merged): decouples flashcard review from note-writing (any question becomes reviewable immediately, prioritized by how many times it's historically been asked across past papers via a new `question_priority.py` module), plus list/dashboard surfacing of that "asked N×" frequency signal. Parked mid-exam-cycle in favor of actually studying; see the plan file for full scope if resuming.

---

## 10. Running It

```bash
pip install -r requirements.txt
streamlit run app.py
```

Ollama must be running locally with the desired model pulled (`ollama pull gemma3:12b`) for local AI features. Gemini is opt-in via an API key entered in the sidebar — nothing is sent to a cloud provider unless explicitly selected.

Utility scripts (run from repo root):
```bash
python pdf_to_csv.py <input.pdf> <Subject_Name>       # ingest a new question-bank PDF
python create_flashcards.py <Subject_Name>             # export static offline flashcard site
python generate_pdf.py <Subject_Name> [chapter|all]     # export printable study-guide PDF
python archive_old_flashcards.py <Subject_Name>         # snapshot before re-export
```
