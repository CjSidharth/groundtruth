"""Reference-material grounding: fuzzy chapter->PDF matching, page-level text/OCR extraction
with disk caching, relevance-scored trimming, and gap-check prompt builders.

Reference PDFs live at data/<Subject>/reference/<any filename>.pdf, one per chapter.
Extracted/OCR'd text is cached per-page at data/<Subject>/reference/.cache/ so vision OCR
(needed only for handwritten/scanned pages) runs at most once per source file. OCR tries the
local Ollama vision model first (free) and only falls back to Gemini if that's unavailable.

Provenance (which PDF file + page number a piece of text came from) is tracked all the way
through extraction, caching, chunking, and relevance selection, so callers can cite exactly
where an AI response's grounding came from - see get_relevant_reference() and format_sources().
"""

import os
import re
import io
import json
import hashlib

import pdfplumber
import ollama
from google import genai

DATA_DIR = "data"

MIN_TEXT_CHARS = 40
MIN_TEXT_WORDS = 8
MAX_REFERENCE_CHARS = 6000
TEXT_MODEL = "gemma3:12b"
OLLAMA_VISION_MODEL = "gemma3:12b"
GEMINI_VISION_MODEL = "gemma-3-27b-it"
OCR_INSTRUCTION = (
    "Transcribe this handwritten/scanned page exactly, preserving structure. "
    "Output only the transcribed text, no commentary."
)


def normalize_chapter_name(name):
    """Lowercase a chapter/filename/text and strip 'chapter', digits, and punctuation, for both
    fuzzy chapter matching and relevance-scoring tokenization."""
    name = str(name)
    # Split camelCase-glued words BEFORE lowercasing - some source PDFs extract text with no
    # spaces between words that were tightly kerned (e.g. "SLA(ServiceLevelAgreement)"), which
    # silently tanks keyword-overlap scoring: "ServiceLevelAgreement" as one token matches none
    # of a query's "service"/"level"/"agreement" tokens, even though the content is exactly what
    # was asked about. This catches the common InitCap-style variant of that artifact.
    name = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', name)
    name = name.lower()
    name = re.sub(r'[^a-z\s]', ' ', name)          # digits/underscores/punctuation -> spaces first,
    name = re.sub(r'\bchapter\b', ' ', name)        # so "chapter" is isolated before this strips it
    return re.sub(r'\s+', ' ', name).strip()


def _token_overlap(a, b):
    """Jaccard similarity between the whitespace-tokenized words of two strings."""
    tokens_a, tokens_b = set(a.split()), set(b.split())
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def _first_number(text):
    """First run of digits in text, e.g. 'unit1p1' -> '1'. None if there isn't one."""
    m = re.search(r'\d+', str(text))
    return m.group(0) if m else None


def find_reference_pdfs(subject, chapter):
    """Locate the reference PDF(s) covering a chapter under data/<subject>/reference/.

    Tried in order, returning as soon as one tier finds anything:
    1. Exact normalized name match.
    2. Substring containment (collects ALL matches - handles a chapter split across
       several descriptively-named files).
    3. Chapter/unit number match, e.g. chapter "Chapter 1 ..." matching "unit1p1.pdf" and
       "unit1p2.pdf" (collects ALL matches sharing that number - handles multi-part files
       named like "unit<N>p<N>" that share no words with the chapter title at all).
    4. Best single match by word-overlap score (> 0.6).

    Returns a list of full paths, sorted, or [] if no reference folder/match exists.
    """
    ref_dir = os.path.join(DATA_DIR, subject, "reference")
    if not os.path.isdir(ref_dir):
        return []

    target = normalize_chapter_name(chapter)
    target_num = _first_number(chapter)

    candidates = []
    for filename in os.listdir(ref_dir):
        if not filename.lower().endswith(".pdf"):
            continue
        stem = os.path.splitext(filename)[0].replace('_', ' ')
        candidates.append((filename, normalize_chapter_name(stem), _first_number(stem)))

    exact = [f for f, norm, _ in candidates if norm == target]
    if exact:
        return [os.path.join(ref_dir, exact[0])]

    substring = [f for f, norm, _ in candidates if norm and (norm in target or target in norm)]
    if substring:
        return [os.path.join(ref_dir, f) for f in sorted(substring)]

    if target_num:
        by_number = [f for f, _, num in candidates if num == target_num]
        if by_number:
            return [os.path.join(ref_dir, f) for f in sorted(by_number)]

    best_filename, best_score = None, 0.0
    for filename, norm, _ in candidates:
        score = _token_overlap(target, norm)
        if score > best_score:
            best_filename, best_score = filename, score
    if best_score > 0.6:
        return [os.path.join(ref_dir, best_filename)]

    return []


def _is_real_text(text):
    """Heuristic: does this look like a genuine extracted text layer, or a blank/scanned page?"""
    text = text.strip()
    return len(text) >= MIN_TEXT_CHARS and len(text.split()) >= MIN_TEXT_WORDS


def _pil_to_png_bytes(pil_image):
    buf = io.BytesIO()
    pil_image.save(buf, format="PNG")
    return buf.getvalue()


def ocr_page_with_ollama(pil_image, model=OLLAMA_VISION_MODEL):
    """Transcribe a scanned/handwritten page locally via Ollama's vision-capable Gemma3.

    Raises if the model isn't pulled/running or returns nothing usable, so callers can fall
    back to Gemini rather than silently caching a bad transcription.
    """
    response = ollama.chat(
        model=model,
        messages=[{
            'role': 'user',
            'content': OCR_INSTRUCTION,
            'images': [_pil_to_png_bytes(pil_image)],
        }],
    )
    text = response['message']['content']
    if not text or not text.strip():
        raise ValueError("empty OCR response from Ollama")
    return text


def ocr_page_with_gemini(pil_image, api_key):
    """Transcribe a scanned/handwritten page image via Gemini vision (fallback path)."""
    if not api_key:
        return "[Scanned page - couldn't OCR locally, and no Gemini API key set as fallback]"
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=GEMINI_VISION_MODEL,
            contents=[OCR_INSTRUCTION, pil_image],
        )
        return response.text or ""
    except Exception as e:
        return f"[OCR failed: {e}]"


def ocr_page(pil_image, api_key):
    """OCR one scanned/handwritten page: local Ollama (gemma3:12b) first since it's free,
    falling back to Gemini vision only if the local model isn't available or errors."""
    try:
        return ocr_page_with_ollama(pil_image)
    except Exception:
        return ocr_page_with_gemini(pil_image, api_key)


def _extract_page_text_or_ocr(page, api_key):
    """Return the best-effort text for one PDF page: direct extraction, or OCR for scans."""
    text = page.extract_text() or ""
    if _is_real_text(text):
        return text
    try:
        pil_image = page.to_image(resolution=200).original
    except Exception:
        return text
    return ocr_page(pil_image, api_key)


def _cache_paths(subject, chapter):
    """Return (cache_dir, pages_cache_path, meta_cache_path) for a subject/chapter pair."""
    cache_dir = os.path.join(DATA_DIR, subject, "reference", ".cache")
    key = normalize_chapter_name(chapter).replace(' ', '_')
    if not key:
        key = hashlib.md5(str(chapter).encode()).hexdigest()[:8]
    return cache_dir, os.path.join(cache_dir, f"{key}.pages.json"), os.path.join(cache_dir, f"{key}.meta.json")


def get_reference_pages(subject, chapter, api_key):
    """Return cached/extracted reference pages for a chapter, or None if no reference PDF exists.

    Each page is {"source": <pdf filename>, "page": <1-indexed, per source file>, "text": ...},
    preserving provenance so callers can cite exactly which file/page a piece of text came from
    (per-file numbering, not global, since a chapter can span multiple source files - the user
    needs to know which specific file to open to page N).

    A chapter may resolve to multiple source files (see find_reference_pdfs). Vision OCR (for
    scanned pages) only runs once per source file; the combined result is cached to disk and
    reused until any source file's mtime/size changes, so cost doesn't scale with how many
    questions in that chapter get checked.
    """
    pdf_paths = find_reference_pdfs(subject, chapter)
    if not pdf_paths:
        return None

    meta = []
    for pdf_path in pdf_paths:
        stat = os.stat(pdf_path)
        meta.append({"source_pdf": os.path.basename(pdf_path), "mtime": stat.st_mtime, "size": stat.st_size})

    cache_dir, pages_cache_path, meta_cache_path = _cache_paths(subject, chapter)
    if os.path.exists(pages_cache_path) and os.path.exists(meta_cache_path):
        try:
            with open(meta_cache_path, 'r') as f:
                cached_meta = json.load(f)
            if cached_meta == meta:
                with open(pages_cache_path, 'r') as f:
                    return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    pages = []
    for pdf_path in pdf_paths:
        source_name = os.path.basename(pdf_path)
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                text = _extract_page_text_or_ocr(page, api_key)
                pages.append({"source": source_name, "page": i, "text": text})

    os.makedirs(cache_dir, exist_ok=True)
    with open(pages_cache_path, 'w') as f:
        json.dump(pages, f)
    with open(meta_cache_path, 'w') as f:
        json.dump(meta, f)

    return pages


def get_reference_text(subject, chapter, api_key):
    """Flat, untrimmed reference text for a chapter (no source tracking), or None if not found.

    Convenience wrapper around get_reference_pages() for callers that don't need provenance.
    Do NOT pass this into build_gap_check_prompt/build_voice_feedback_prompt/
    build_reveal_answer_prompt - they now expect pre-trimmed text from get_relevant_reference(),
    and this returns the full untrimmed chapter, which can blow a small model's context window.
    """
    pages = get_reference_pages(subject, chapter, api_key)
    if pages is None:
        return None
    return "\n\n".join(p["text"] for p in pages).strip()


_SECTION_SPLIT_RE = re.compile(r'\n(?=\d+(?:\.\d+)*\s+[A-Z])')


def _chunk_pages(pages):
    """Split each page's text into subsection-sized chunks, tagging every chunk with its
    source/page so relevance selection can report exactly which file/page it drew from.

    Two-step split per page, same as the old flat-text chunker did across the whole chapter:
    first on blank lines (a single page can contain more than one paragraph-separated piece),
    then on numbered headings like '1.8 Challenges Of Cloud' where present.
    """
    chunks = []
    for p in pages:
        for blank_split in re.split(r'\n\s*\n', p["text"]):
            for piece in _SECTION_SPLIT_RE.split(blank_split):
                piece = piece.strip()
                if piece:
                    chunks.append({"source": p["source"], "page": p["page"], "text": piece})
    return chunks


_STOPWORDS = {
    'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'for', 'from', 'has', 'have', 'in',
    'is', 'it', 'its', 'of', 'on', 'or', 'that', 'the', 'this', 'to', 'was', 'were', 'with',
    'what', 'when', 'where', 'which', 'who', 'why', 'how', 'do', 'does', 'did', 'you', 'your',
    # exam-question boilerplate that shows up constantly and carries no topical signal
    'explain', 'describe', 'write', 'short', 'note', 'notes', 'define', 'list', 'discuss',
    'briefly', 'about', 'give', 'state', 'compare', 'differentiate', 'between', 'suitable',
    'example', 'diagram', 'detail',
}


def _dedupe_sources(chunk_dicts):
    """Deduped, sorted list of {"source","page"} for a list of chunk (or page) dicts."""
    pairs = sorted({(c["source"], c["page"]) for c in chunk_dicts})
    return [{"source": s, "page": p} for s, p in pairs]


def select_relevant_reference(pages, query, max_chars=MAX_REFERENCE_CHARS):
    """Pick the reference chunks most relevant to `query`, preserving document order, within a
    character budget. Returns (text, sources) - sources is a deduped list of {"source","page"}
    for exactly the chunks that made it into `text`, so a citation is always accurate to what
    the model actually saw (never guessed/self-reported by the model).

    A chapter can run well past what a small local model's context window can hold, and a whole
    chapter usually covers many subsections a given question has nothing to do with. Blindly
    cutting off whatever falls after `max_chars` risks silently dropping the exact section the
    question is actually about (this happened in practice: a chapter's "Challenges" subsection
    started at character 12,309, past a flat 6,000-char head-truncation). Instead, rank chunks by
    keyword overlap with the question and keep the most relevant ones, in original order.

    Stopwords/exam boilerplate ("what", "is", "explain", "write a short note on", ...) are
    stripped from the query before scoring - otherwise they dominate the overlap score (they
    appear in nearly every chunk) and drown out the one or two actual topic words that identify
    the right section. This happened in practice: "What is machine imaging?" matched a chunk on
    "is"/"what" alone while the chunk that actually said "Cloud Machine Imaging" scored lower and
    got cut, because "machine" is common but "imaging" alone wasn't enough to outweigh it.
    """
    full_text = "\n\n".join(p["text"] for p in pages).strip()
    chunks = _chunk_pages(pages)

    if len(full_text) <= max_chars:
        return full_text, _dedupe_sources(pages)

    query_tokens = set(normalize_chapter_name(query).split()) - _STOPWORDS
    if not query_tokens:
        query_tokens = set(normalize_chapter_name(query).split())

    def score(chunk):
        return len(query_tokens & set(normalize_chapter_name(chunk["text"]).split()))

    ranked = sorted(range(len(chunks)), key=lambda i: -score(chunks[i]))

    selected, total = set(), 0
    for i in ranked:
        chunk_len = len(chunks[i]["text"])
        if total and total + chunk_len > max_chars:
            continue
        selected.add(i)
        total += chunk_len
        if total >= max_chars:
            break

    ordered = [chunks[i] for i in sorted(selected)]
    text = "\n\n".join(c["text"] for c in ordered) + "\n...[reference trimmed to question-relevant sections]"
    return text, _dedupe_sources(ordered)


def get_relevant_reference(subject, chapter, query, api_key, max_chars=MAX_REFERENCE_CHARS):
    """One-call convenience: find + extract/OCR + cache a chapter's reference material, then
    trim it to what's relevant to `query`. Returns (text, sources) - (None, []) if no reference
    PDF matches this chapter at all. This is the main entry point app.py should use; `text` is
    already trimmed and ready to pass straight into the build_*_prompt functions."""
    pages = get_reference_pages(subject, chapter, api_key)
    if pages is None:
        return None, []
    return select_relevant_reference(pages, query, max_chars)


def format_sources(sources):
    """Human-readable citation string, e.g. 'unit1p1.pdf: p.3, p.4 · unit3_part2.pdf: p.7',
    grouped by file (alphabetical) with pages sorted/deduped ascending. "" for an empty list."""
    if not sources:
        return ""
    by_file = {}
    for s in sources:
        by_file.setdefault(s["source"], set()).add(s["page"])
    parts = []
    for fname in sorted(by_file):
        pages = sorted(by_file[fname])
        parts.append(f"{fname}: p." + ", p.".join(str(p) for p in pages))
    return " · ".join(parts)


def build_gap_check_prompt(question, student_answer, reference_text):
    """Build the gap-check prompt: surfaces what's missing/wrong, never hands over the full answer.

    reference_text should already be trimmed to the relevant excerpt (see get_relevant_reference) -
    this no longer trims internally."""
    return f"""You are an exam-prep gap-check assistant. Do NOT provide the full correct answer.
Question: "{question}"
Reference Material (chapter excerpt): \"\"\"{reference_text}\"\"\"
Student's Own Attempt: \"\"\"{student_answer}\"\"\"

Compare the student's attempt against the reference material ONLY. Do not add facts from your own
general knowledge that aren't in the reference material above. List, IN THIS ORDER:
1. If the question has multiple parts (e.g. "X and Y", "compare A and B", "list types and give an
   example"), check EACH part separately and state plainly which parts the student's attempt did
   not address at all. This is the most important check - do it first, before smaller wording gaps.
2. Key points/concepts from the reference that are genuinely ABSENT from the student's attempt
   within the parts it did attempt. Two rules for this step:
   a) A point the student expressed in their own words, even loosely or incompletely, is NOT
      missing - do not flag paraphrasing, reordering, or omitted exact phrasing as a gap. Only
      flag a point if the underlying idea or fact itself isn't there at all.
   b) Only flag points that are actually relevant to what the question asks. The reference excerpt
      may contain other topics (definitions, side-sections, unrelated subsections) that happen to
      appear near the relevant content but aren't part of what this specific question is asking
      for - do not require the student to cover those.
3. Any factual inaccuracies in the student's attempt - meaning it states something that is actually
   WRONG per the reference, not just phrased with a different word for the same idea (e.g. "overcome"
   vs "address" describing the same solution is NOT an inaccuracy). If there is no genuine
   inaccuracy, write "None" - do not manufacture one to fill this section.
4. Topics the QUESTION asks about that the reference material doesn't discuss at all (this is about
   gaps in the reference material itself, not about how thoroughly the student elaborated on
   something that IS covered - that belongs in step 2, not here). If everything the question asks
   is covered by the reference, write "N/A".
If the student's attempt already covers a section well, say so briefly instead of forcing minor
nitpicks - a short "well covered" is more useful than manufactured gaps.
Do not restate the full answer. Be concise, bullet-pointed."""


def build_voice_feedback_prompt(question, transcript, reference_text):
    """Build the voice-answer feedback prompt: clean up a raw speech transcript, then apply the
    same reference-grounded gap-check rules as build_gap_check_prompt. Spoken answers are messier
    than typed notes (filler words, false starts, mid-sentence corrections), so step 1 asks the
    model to present a coherent version of what was actually said before comparing it.

    reference_text should already be trimmed to the relevant excerpt (see get_relevant_reference) -
    this no longer trims internally."""
    return f"""You are an exam-prep viva/oral-answer assistant. Do NOT provide the full correct answer.
Question: "{question}"
Reference Material (chapter excerpt): \"\"\"{reference_text}\"\"\"
Student's Raw Spoken Transcript: \"\"\"{transcript}\"\"\"

1. First, briefly restate what the student actually said as a coherent statement, stripping filler
   words, false starts, and mid-sentence self-corrections - do not add anything they didn't say.
2. Then compare that cleaned-up statement against the reference material ONLY. Do not add facts from
   your own general knowledge that aren't in the reference material above. List, IN THIS ORDER:
   a. If the question has multiple parts (e.g. "X and Y", "compare A and B"), check EACH part
      separately and state plainly which parts the student did not address at all. This is the most
      important check - do it first, before smaller wording gaps.
   b. Key points/concepts from the reference that are genuinely ABSENT from what the student said,
      within the parts they did attempt. A point expressed loosely or in different words is NOT
      missing - only flag it if the underlying idea or fact isn't there at all. Only flag points
      actually relevant to what the question asks, not unrelated topics that happen to be nearby in
      the reference material.
   c. Any factual inaccuracies - meaning something actually WRONG per the reference, not just a
      different word for the same idea. If there is no genuine inaccuracy, write "None".
If the student's answer already covers a section well, say so briefly instead of forcing minor
nitpicks. Do not restate the full answer. Be concise, bullet-pointed."""


def build_reveal_answer_prompt(question, reference_text):
    """Build the prompt for the secondary 'reveal full reference answer' action.

    reference_text should already be trimmed to the relevant excerpt (see get_relevant_reference) -
    this no longer trims internally."""
    return f"""Using ONLY the reference material below, write a clear, complete answer to the question.
Do not add facts from your own general knowledge that aren't in the reference material - if part of
THE QUESTION AS ASKED isn't covered by the reference material, say so explicitly instead of inventing
an answer for it. Do NOT bring up or caveat about other cloud/CS topics that happen to be nearby in
the source material but that the question doesn't actually ask about (e.g. if the question only asks
for a definition and features, don't comment on service models or deployment models just because
they're discussed elsewhere in the same chapter) - stay strictly scoped to what was asked.
Question: "{question}"
Reference Material: \"\"\"{reference_text}\"\"\""""
