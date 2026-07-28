import streamlit as st
import pandas as pd
import os
import json
import math
import re
import io
from streamlit.components.v1 import html
from faster_whisper import WhisperModel
import ollama
import pdfplumber
import pyttsx3
import time
import hashlib
from datetime import date, timedelta
from google import genai
from reference_material import get_relevant_reference, format_sources, build_gap_check_prompt, build_reveal_answer_prompt, build_voice_feedback_prompt, TEXT_MODEL
import spaced_repetition as srs


# --- Page Configuration ---
st.set_page_config(page_title="Exam Prep Engine", page_icon="📚", layout="wide")

# --- Constants & Configuration ---
PAGE_SIZE = 15
DATA_DIR = "data"
PROGRESS_DIR = "progress"

# --- Setup: Create progress directory if it doesn't exist ---
if not os.path.exists(PROGRESS_DIR):
    os.makedirs(PROGRESS_DIR)

@st.cache_resource
def load_whisper():
    print("⬇️  Loading Whisper model...")
    with st.spinner("Loading AI Ear (Whisper Small)..."):
        # Changed from "base" to "small" for better accuracy
        return WhisperModel("small", device="cpu", compute_type="int8")

try:
    whisper_model = load_whisper()
except Exception as e:
    st.error(f"Error loading Whisper model: {e}")
    st.stop()

# --- Helper Functions ---
def defang_headings(text):
    """Demote markdown heading lines ('# Foo') to bold text so a note that happens to use
    headings doesn't render as a giant page-sized H1 in the compact flashcard/notes preview."""
    return re.sub(r'^#{1,6}\s*(.+)$', r'**\1**', text, flags=re.MULTILINE)

def get_subjects():
    if not os.path.exists(DATA_DIR): return []
    return sorted([d for d in os.listdir(DATA_DIR) if os.path.isdir(os.path.join(DATA_DIR, d))])

def get_progress_filepath(subject):
    filename = f"{subject}_progress.json"
    return os.path.join(PROGRESS_DIR, filename)

def load_progress(subject):
    filepath = get_progress_filepath(subject)
    if os.path.exists(filepath):
        with open(filepath, 'r') as f:
            try: return json.load(f)
            except json.JSONDecodeError: return {"done_questions": [], "notes": {}}
    return {"done_questions": [], "notes": {}}

def save_progress(subject, progress):
    filepath = get_progress_filepath(subject)
    with open(filepath, 'w') as f: json.dump(progress, f, indent=4)

def get_srs_entry(progress, qid):
    """The scheduling entry for a question, or a fresh one (due immediately) if never graded."""
    return progress.setdefault('srs', {}).get(qid, srs.new_entry())

def grade_srs(progress, qid, rating):
    """Grade a review (Again/Hard/Good/Easy) and store the resulting schedule in-place."""
    progress['srs'][qid] = srs.grade(get_srs_entry(progress, qid), rating)

def get_note_images(progress, note_key):
    """Images for a question, tolerating the old single-filename-string format."""
    val = progress.get("images", {}).get(note_key)
    if not val:
        return []
    return [val] if isinstance(val, str) else list(val)

STREAK_PATH = os.path.join(PROGRESS_DIR, "_streak.json")

def load_streak():
    if os.path.exists(STREAK_PATH):
        with open(STREAK_PATH, 'r') as f:
            try: return json.load(f)
            except json.JSONDecodeError: pass
    return {"last_review_date": None, "current_streak": 0, "longest_streak": 0}

def save_streak(streak):
    with open(STREAK_PATH, 'w') as f: json.dump(streak, f, indent=4)

def record_review_today(streak):
    """Advance the streak for a review happening right now. Any single rating counts - no
    minimum review count, so the daily bar stays low enough to never be its own excuse to skip."""
    today = date.today().isoformat()
    if streak["last_review_date"] == today:
        return streak
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    streak["current_streak"] = streak["current_streak"] + 1 if streak["last_review_date"] == yesterday else 1
    streak["last_review_date"] = today
    streak["longest_streak"] = max(streak["longest_streak"], streak["current_streak"])
    return streak

@st.cache_data
def load_data(subject):
    """Loads, merges, and prepares data for a specific subject with STABLE IDs."""
    subject_path = os.path.join(DATA_DIR, subject)
    all_questions = []
    try:
        for filename in sorted(os.listdir(subject_path)):
            if filename.endswith('.csv'):
                path = os.path.join(subject_path, filename)
                df = pd.read_csv(path)
                chapter_name = os.path.splitext(filename)[0].replace('_', ' ')
                df['Chapter'] = chapter_name
                all_questions.append(df)
    except FileNotFoundError:
        st.error(f"Could not find data for subject: {subject}")
        return pd.DataFrame()
    if not all_questions:
        st.warning(f"No CSV files found for '{subject}' in '{subject_path}'.")
        return pd.DataFrame()
    master_df = pd.concat(all_questions, ignore_index=True)
    master_df.columns = master_df.columns.str.strip()

    # 🔴 BULLETPROOF NaN FIX: Annihilate all forms of empty/null values in Streamlit
    for col in ['Category', 'Marks']:
        if col in master_df.columns:
            # 1. Convert everything to a string
            master_df[col] = master_df[col].astype(str).str.strip()
            
            # 2. Replace the literal string 'nan', 'NaN', and empty spaces with 'N/A'
            master_df[col] = master_df[col].replace(
                ['nan', 'NaN', 'None', '', '<NA>', 'null', 'NULL'], 'N/A'
            )
            
            # 3. Clean up the trailing decimals pandas adds to numbers
            if col == 'Marks':
                master_df[col] = master_df[col].replace(r'\.0$', '', regex=True)

    def make_hash(text):
        return hashlib.md5(str(text).encode()).hexdigest()[:8]

    master_df['StableID'] = master_df['Chapter'].astype(str) + '_' + master_df['Question'].apply(make_hash)
    
    # Filter out truly blank rows just in case
    master_df = master_df[master_df['Question'].str.strip() != ""]
    return master_df

# --- HELPER: Universal AI Caller (Safe Mode & Stable Model) ---
def ask_ai(prompt, provider, api_key=None, model_name="llama3.2:3b", stream=True):
    """
    Unified function to call either Local Ollama or Google Gemini.
    """
    if provider == "Google Gemini":
        if not api_key:
            # Removed Emoji to prevent ASCII error
            yield "[Error] Please enter a Google API Key in the Sidebar."
            return
        try:
            client = genai.Client(api_key=api_key)
            
            # --- FIX 1: USE STABLE MODEL ---
            # gemini-2.0-flash is restricted. gemini-1.5-flash is stable.
            target_model = 'gemma-3-27b-it' 
            
            if stream:
                response = client.models.generate_content_stream(
                    model=target_model,
                    contents=prompt
                )
                for chunk in response:
                    if chunk.text: yield chunk.text
            else:
                response = client.models.generate_content(
                    model=target_model,
                    contents=prompt
                )
                if response.text: yield response.text
                
        except Exception as e:
            # --- FIX 2: ASCII-SAFE ERROR HANDLING ---
            # We strip non-ascii characters from the error message just in case
            err_msg = str(e).encode('ascii', 'ignore').decode('ascii')
            
            if "429" in err_msg:
                yield "[Rate Limit] Google is slowing down requests. Switch to Local (Ollama) for 2 mins."
            else:
                yield f"[Gemini Error] {err_msg}"

    else: # Local Ollama
        try:
            # Ollama defaults num_ctx to 4096 regardless of what the model actually supports,
            # and silently truncates from the START of the prompt when it's exceeded - which
            # means the Reference Material (placed before the student's note in the grounded
            # prompts) is the first thing dropped for a long note, with no error at all.
            if stream:
                for chunk in ollama.generate(model=model_name, prompt=prompt, stream=True, options={"num_ctx": 8192}):
                    yield chunk['response']
            else:
                yield ollama.generate(model=model_name, prompt=prompt, options={"num_ctx": 8192})['response']
        except Exception as e:
            yield f"[Ollama Error] {e}. Is Ollama running?"

def is_ai_error(text):
    """True if ask_ai() yielded one of its own error strings (e.g. "[Ollama Error] ...")
    rather than real model output - ask_ai() never raises, so callers must check this
    themselves before treating a response as genuine feedback worth saving."""
    return bool(re.match(r'^\[[A-Za-z ]+\]', text or ""))


# --- HELPER: Dynamic Text Cleaner ---
def clean_text_dynamic(text, header_margin, footer_margin, specific_phrases):
    if not text: return ""
    lines = text.split('\n')
    cleaned_lines = []
    
    # Calculate total lines to estimate relative header/footer positions
    total_lines = len(lines)
    
    for i, line in enumerate(lines):
        # 1. Header Filter: Skip first N lines
        if i < header_margin:
            continue
            
        # 2. Footer Filter: Skip last N lines
        if i >= total_lines - footer_margin:
            continue
            
        # 3. Phrase Filter: Remove lines containing specific unwanted text
        # (Case insensitive check)
        if any(phrase.lower() in line.lower() for phrase in specific_phrases if phrase.strip()):
            continue
            
        # 4. Short Line Filter (Optional): Remove lines that are just page numbers (digits)
        if line.strip().isdigit() and len(line.strip()) < 4:
            continue
            
        cleaned_lines.append(line)
    
    return "\n".join(cleaned_lines)


# --- HELPER: Bionic Reading (Fixed Regex Logic) ---
def make_bionic(text):
    if not text: return ""
    
    # 1. Split text into parts: Words vs Non-Words (spaces, punctuation)
    # The capturing group () keeps the delimiters in the list
    tokens = re.split(r'([a-zA-Z0-9]+)', text)
    
    result = []
    for token in tokens:
        # 2. Only apply bolding to actual alphanumeric words
        if token.isalnum():
            # Bold the first 40%
            bold_len = math.ceil(len(token) * 0.4)
            if len(token) > 1:
                result.append(f"**{token[:bold_len]}**{token[bold_len:]}")
            else:
                result.append(f"**{token}**")
        else:
            # 3. Preserve spaces, tabs, and newlines exactly as they are
            result.append(token)
            
    # 4. Join them back together
    return "".join(result)



# --- HELPER: Offline Audio Generator ---
def generate_audio(text, filename="temp_slide_audio.mp3"):
    try:
        engine = pyttsx3.init()
        engine.setProperty('rate', 160) # Slower for technical content
        engine.save_to_file(text, filename)
        engine.runAndWait()
        return filename
    except Exception as e:
        return None



# --- Sidebar & State Management ---
st.sidebar.title("📚 Exam Prep Hub")
subjects = get_subjects()
if not subjects:
    st.error("No subject folders found in 'data' directory."); st.stop()

selected_subject = st.sidebar.selectbox("Choose a Subject to Study", options=subjects)

if 'current_subject' not in st.session_state or st.session_state.current_subject != selected_subject:
    st.session_state.current_subject = selected_subject
    st.session_state.progress = load_progress(selected_subject)
    st.session_state.view = 'list'
    st.session_state.current_question_index = None
    st.session_state.page_number = 0
    st.session_state.card_flipped = False
    st.session_state.audio_key_counter = 0 

master_df = load_data(selected_subject)
st.title(f"⚡ {selected_subject.replace('_', ' ')} Prep Engine")
if master_df.empty: st.stop()

with st.sidebar:
    st.divider()
    st.header("⚙️ Controls & Filters")
    is_flashcard_mode = st.session_state.view == 'flashcard'
    view_cols = st.columns(2)
    if view_cols[0].button("📚 Question List", use_container_width=True, type="secondary" if is_flashcard_mode else "primary"):
        st.session_state.view = 'list'; st.rerun()
    if view_cols[1].button("🗂️ Flashcards", use_container_width=True, type="primary" if is_flashcard_mode else "secondary"):
        st.session_state.view = 'flashcard'; st.session_state.card_flipped = False; st.rerun()
    if st.button("📖 Smart PDF Reader", use_container_width=True):
        st.session_state.view = 'pdf_reader'
        st.rerun()
    if st.button("📊 Dashboard", use_container_width=True):
        st.session_state.view = 'dashboard'
        st.rerun()
    # In your Sidebar section:
    st.divider()
    st.markdown("### 🧠 AI Intelligence")
    ai_provider = st.radio(
        "Choose Brain:", 
        ["Local (Ollama)", "Google Gemini"], 
        help="Gemini is faster but requires an API Key. Ollama is private & offline."
    )
    gemini_key = ""
    
    if ai_provider == "Google Gemini":
        # 1. Try to get key from secrets.toml
        if "GEMINI_API_KEY" in st.secrets:
            gemini_key = st.secrets["GEMINI_API_KEY"]
            st.success("🔑 Key loaded from secrets!")
        # 2. Fallback to manual input if secret not found
        else:
            gemini_key = st.text_input("Enter Gemini API Key", type="password")
            if not gemini_key:
                st.warning("⚠️ key not found in .streamlit/secrets.toml")
    
    st.divider()
    chapters = sorted(master_df['Chapter'].unique())
    selected_chapters = st.multiselect("Chapter(s):", chapters, default=chapters)
    
    # 🔴 BULLETPROOF FIX: A helper function that forces everything to be a clean Python string
    def clean_ui_value(val):
        val_str = str(val).strip()
        if val_str.endswith('.0'): 
            return val_str[:-2]  # Turns "3.0" into "3"
        if val_str.lower() in ['nan', 'none', '']:
            return 'N/A'
        return val_str

    if 'Category' in master_df.columns:
        # Converts all unique values to strings, removes duplicates, and sorts them safely
        unique_categories = sorted(list(set(clean_ui_value(x) for x in master_df['Category'].unique())))
        selected_category = st.multiselect("Category:", unique_categories, default=unique_categories)
    else:
        selected_category = master_df.index
    
    if 'Marks' in master_df.columns:
        # Same process for Marks
        unique_marks = sorted(list(set(clean_ui_value(x) for x in master_df['Marks'].unique())))
        selected_marks = st.multiselect("Marks:", unique_marks, default=unique_marks)
    else:
        selected_marks = master_df.index

    review_status = st.radio("Status:", ('All', 'Reviewed', 'Not Reviewed'), horizontal=True)
    st.divider()
    search_query = st.text_input("🔍 Search Questions & Notes")
    st.divider()
    with st.expander("🧪 Testing utilities"):
        st.caption("Resets spaced-repetition scheduling for this subject so every noted question is due again. Does NOT touch your notes/code/images.")
        if st.checkbox("I understand, reset SRS progress", key="confirm_srs_reset"):
            if st.button("🗑️ Reset SRS Progress", use_container_width=True):
                st.session_state.progress['srs'] = {}
                save_progress(selected_subject, st.session_state.progress)
                st.toast("SRS progress reset - everything is due again.", icon="✅")
                st.rerun()

if search_query:
    question_mask = master_df['Question'].str.contains(search_query, case=False, na=False)
    notes = st.session_state.progress.get('notes', {})
    matching_ids = {qid for qid, note in notes.items() if search_query.lower() in note.lower()}
    notes_mask = master_df['StableID'].isin(matching_ids)
    master_df = master_df[question_mask | notes_mask]

# --- Filtering Logic ---
filtered_df = master_df.copy()

# 🔴 BULLETPROOF FIX: We apply the same string cleaning to the DataFrame before filtering
if 'Category' in filtered_df.columns:
    safe_cats = filtered_df['Category'].apply(clean_ui_value)
    filtered_df = filtered_df[safe_cats.isin(selected_category)]
    
if 'Marks' in filtered_df.columns:
    safe_marks = filtered_df['Marks'].apply(clean_ui_value)
    filtered_df = filtered_df[safe_marks.isin(selected_marks)]
    
filtered_df = filtered_df[filtered_df['Chapter'].isin(selected_chapters)]

done_qids = set(st.session_state.progress['done_questions'])
if review_status == 'Reviewed': filtered_df = filtered_df[filtered_df['StableID'].isin(done_qids)]
elif review_status == 'Not Reviewed': filtered_df = filtered_df[~filtered_df['StableID'].isin(done_qids)]
filtered_df.reset_index(drop=True, inplace=True)

# --- View Switching ---
if st.session_state.view == 'flashcard':
    st.header("🗂️ Spaced Repetition Review")

    noted_qids = {qid for qid, note in st.session_state.progress['notes'].items() if note and note.strip()}
    candidate_deck = filtered_df[filtered_df['StableID'].isin(noted_qids)].reset_index(drop=True)

    st.session_state.progress.setdefault('srs', {})
    srs_data = st.session_state.progress['srs']

    if candidate_deck.empty:
        st.warning("No notes found for questions matching filters. Add notes in the 'Question List' view.")
    else:
        due_mask = candidate_deck['StableID'].apply(lambda qid: srs.is_due(get_srs_entry(st.session_state.progress, qid)))
        due_deck = candidate_deck[due_mask].copy()

        if due_deck.empty:
            upcoming = [srs_data[qid]['due'] for qid in candidate_deck['StableID'] if qid in srs_data]
            next_due = min(upcoming) if upcoming else None
            msg = f"🎉 Nothing due right now out of {len(candidate_deck)} notes in scope."
            if next_due:
                msg += f" Next review unlocks {next_due}."
            st.success(msg)
        else:
            due_deck['_due'] = due_deck['StableID'].apply(lambda qid: get_srs_entry(st.session_state.progress, qid)['due'])
            due_deck = due_deck.sort_values('_due').reset_index(drop=True)
            question = due_deck.iloc[0]
            note_key = question['StableID']

            st.caption(f"📅 {len(due_deck)} due now  •  {len(candidate_deck)} total notes in this filter")

            with st.container(height=500, border=True):
                st.subheader(f"Q: {question['Question']}")
                st.caption(f"Chapter: {question['Chapter']} | Marks: {question.get('Marks', 'N/A')}")
                if not st.session_state.card_flipped:
                    st.markdown("<br>", unsafe_allow_html=True)
                    if st.button("Show My Notes (Flip Card)", type="primary", use_container_width=True):
                        st.session_state.card_flipped = True; st.rerun()
                else:
                    st.markdown("---"); st.subheader("Your Notes:")
                    note = st.session_state.progress['notes'].get(note_key, "*No notes.*")
                    # No newline->br conversion: that broke Markdown tables (they need real \n's
                    # between rows to be recognized at all). Streamlit's renderer supports GFM
                    # tables natively - just pass the text through.
                    st.markdown(defang_headings(note), unsafe_allow_html=True)
                    for img_name in get_note_images(st.session_state.progress, note_key):
                        image_path = os.path.join(DATA_DIR, selected_subject, "images", img_name)
                        if os.path.exists(image_path):
                            st.image(image_path, width=300)

            if st.session_state.card_flipped:
                st.caption("How well did you know this?")
                rate_cols = st.columns(4)
                for col, (emoji_label, rating) in zip(
                    rate_cols, [("😵 Again", "Again"), ("😬 Hard", "Hard"), ("🙂 Good", "Good"), ("😎 Easy", "Easy")]
                ):
                    if col.button(emoji_label, use_container_width=True, key=f"rate_{rating}_{note_key}"):
                        grade_srs(st.session_state.progress, note_key, rating)
                        save_progress(selected_subject, st.session_state.progress)
                        save_streak(record_review_today(load_streak()))
                        st.session_state.card_flipped = False
                        st.rerun()

elif st.session_state.view == 'detail':
    q_index = st.session_state.current_question_index
    if q_index is None: st.session_state.view = 'list'; st.rerun()
    question = filtered_df.iloc[q_index]
    note_key = question['StableID']
    # Versioned key (same trick as audio_key_counter below): Streamlit's text_area can keep
    # showing its old on-screen text even after the underlying session_state value is cleared/
    # updated server-side - popping/reassigning the SAME key isn't a reliable visual refresh.
    # Changing the key itself forces a real remount, which does reliably refresh what's shown.
    notes_version_key = f"notes_version_{note_key}"
    st.session_state.setdefault(notes_version_key, 0)
    text_area_key = f"note_{note_key}_v{st.session_state[notes_version_key]}"

    # 🔴 CRITICAL FIX: Callback to guarantee saves before UI changes
    def auto_save_text(widget_key, dict_name):
        st.session_state.progress[dict_name][note_key] = st.session_state[widget_key]
        save_progress(st.session_state.current_subject, st.session_state.progress)

    def save_all_changes():
        # 1. Grab text directly from session state to avoid NameErrors
        # Falls back to the already-saved value, not "" - the text_area's widget key can be
        # momentarily absent from session_state (e.g. right after a programmatic pop() used to
        # force-refresh the widget), and defaulting to "" here would silently wipe a real note.
        latest_note = st.session_state.get(text_area_key, st.session_state.progress['notes'].get(note_key, ""))
        latest_code = st.session_state.get(f"code_{note_key}", st.session_state.progress.get('code', {}).get(note_key, ""))
        
        # 2. Save Notes
        st.session_state.progress['notes'][note_key] = latest_note
        
        # 3. Save Code
        st.session_state.progress.setdefault("code", {})
        st.session_state.progress['code'][note_key] = latest_code
        
        # (Images are already auto-saved when uploaded, so we don't need them here!)

        # 4. Write to disk
        save_progress(selected_subject, st.session_state.progress)
        st.toast("Saved!", icon="✅")
    
    # Navigation
    nav_cols = st.columns([1, 5, 1])
    if nav_cols[0].button("⬅️ Back"):
        save_all_changes()   # <-- Much cleaner and saves EVERYTHING
        st.session_state.view = 'list'
        st.rerun()
    nav_cols[2].write(f"Q {q_index + 1}")
    
    st.subheader(f"Q: {question['Question']}")
    st.divider()

    st.subheader("📝 Your Notes")

    # --- TAB INDENT SCRIPT ---
    def enable_tab_indent():
        js_code = """<script>...</script>""" # (Keep your existing script string)
        html(js_code, height=0)
    enable_tab_indent()
    
    # --- VOICE LOGIC (FIXED TO USE ask_ai) ---
    if 'audio_key_counter' not in st.session_state: st.session_state.audio_key_counter = 0
    if 'temp_voice_data' not in st.session_state: st.session_state.temp_voice_data = None

    if not st.session_state.temp_voice_data:
        c1, c2, c3 = st.columns([0.5, 0.25, 0.25])
        with c1:
            audio_val = st.audio_input("🎤 Record", key=f"audio_{note_key}_{st.session_state.audio_key_counter}")
        with c2:
            st.write("") 
            st.write("") 
            use_ai = st.toggle("✨ Analyze", value=True)
        with c3:
            st.write("") 
            # Only show local model choice if using Ollama
            if ai_provider == "Local (Ollama)":
                # Default to the best model here (first in the list): this is a review step
                # after you've already recorded and waited for transcription, not a fast loop -
                # quality matters more than shaving a few seconds, same reasoning as gap-check.
                model_options = {f"{TEXT_MODEL} (best)": TEXT_MODEL, "3b (fast)": "llama3.2:3b", "1b (fastest)": "llama3.2:1b"}
                model_choice = st.selectbox("Model", list(model_options.keys()), label_visibility="collapsed")
                selected_model = model_options[model_choice]
            else:
                st.caption("Using Gemini")
                selected_model = "gemini"

        if audio_val:
            st.info("Audio captured.")
            b1, b2 = st.columns(2)

            if b1.button("⚡ Transcribe & Analyze", type="primary", use_container_width=True):
                raw_text = None
                with st.spinner("Transcribing..."):
                    try:
                        # Whisper Transcribe (Always Local)
                        audio_bytes = audio_val.read()
                        audio_buffer = io.BytesIO(audio_bytes)
                        segments, info = whisper_model.transcribe(audio_buffer, beam_size=1)
                        raw_text = " ".join([segment.text for segment in segments]).strip()
                    except Exception as e:
                        st.error(f"Transcription failed: {e}")

                if raw_text is not None and not raw_text:
                    st.warning("No speech detected - try recording again.")
                elif raw_text:
                    ai_response, ai_failed, sources = "", False, []
                    if use_ai:
                        # Ground feedback in the chapter's reference material when available,
                        # same as the "Check Against Reference" gap-check feature - falls back
                        # to a generic ungrounded prompt for chapters with no reference PDF yet.
                        ref_text = None
                        try:
                            ref_text, sources = get_relevant_reference(selected_subject, question['Chapter'], question['Question'], gemini_key)
                        except Exception:
                            ref_text, sources = None, []

                        if ref_text and ref_text.strip():
                            prompt = build_voice_feedback_prompt(question['Question'], raw_text, ref_text)
                        else:
                            sources = []
                            prompt = f"""
                            You are a Viva Tutor.
                            Question: "{question['Question']}"
                            Student Answer: "{raw_text}"

                            1. Clean up the transcript.
                            2. Give specific feedback on missing points.
                            """

                        stream_box = st.empty()
                        full_streamed_text = ""
                        for chunk in ask_ai(prompt, ai_provider, gemini_key, selected_model):
                            full_streamed_text += chunk
                            stream_box.markdown(full_streamed_text + "▌")

                        ai_response = full_streamed_text
                        ai_failed = is_ai_error(ai_response)
                        stream_box.empty()

                    st.session_state.temp_voice_data = {
                        "raw": raw_text,
                        "ai": ai_response,
                        "ai_failed": ai_failed,
                        "sources": sources,
                    }
                    st.rerun()

            if b2.button("🔄 Reset", type="secondary", use_container_width=True):
                st.session_state.audio_key_counter += 1
                st.rerun()

    # --- PREVIEW / CONFIRMATION AREA ---
    if st.session_state.temp_voice_data:
        voice_data = st.session_state.temp_voice_data
        st.success("✅ Transcription Complete! Review before adding.")

        if voice_data["ai_failed"]:
            st.error(voice_data["ai"])
            st.caption(f"Transcript: {voice_data['raw']}")
        elif voice_data["ai"]:
            st.markdown(voice_data["ai"])
        else:
            st.markdown(f"**Transcript:** {voice_data['raw']}")

        if voice_data.get("sources"):
            st.caption(f"📄 Sources: {format_sources(voice_data['sources'])}")

        def _voice_addition():
            # Never save a failed-AI-call error string into notes as if it were feedback -
            # fall back to the raw transcript in that case, same as when AI wasn't used at all.
            if voice_data["ai"] and not voice_data["ai_failed"]:
                addition = f"\n{voice_data['ai']}\n"
            else:
                addition = f"\n- {voice_data['raw']}\n"
            if voice_data.get("sources"):
                addition += f"\n*Source: {format_sources(voice_data['sources'])}*\n"
            return addition

        def _save_voice_note():
            current_text = st.session_state.get(text_area_key, st.session_state.progress['notes'].get(note_key, ""))
            addition = _voice_addition()
            updated_note = (current_text + "\n" + addition) if current_text else addition
            st.session_state.progress['notes'][note_key] = updated_note
            st.session_state[notes_version_key] += 1
            save_progress(selected_subject, st.session_state.progress)

        st.caption("How well did you know this?")
        rate_cols = st.columns(4)
        for col, (emoji_label, rating) in zip(
            rate_cols, [("😵 Again", "Again"), ("😬 Hard", "Hard"), ("🙂 Good", "Good"), ("😎 Easy", "Easy")]
        ):
            if col.button(emoji_label, use_container_width=True, key=f"voice_rate_{rating}_{note_key}"):
                _save_voice_note()
                grade_srs(st.session_state.progress, note_key, rating)
                save_progress(selected_subject, st.session_state.progress)
                save_streak(record_review_today(load_streak()))
                st.session_state.temp_voice_data = None
                st.session_state.audio_key_counter += 1
                st.toast(f"Saved and rated {rating}!", icon="✅")
                st.rerun()

        btn_col1, btn_col2 = st.columns(2)
        if btn_col1.button("⬇️ Add to Notes (no rating)", use_container_width=True):
            _save_voice_note()
            st.session_state.temp_voice_data = None
            st.session_state.audio_key_counter += 1
            st.toast("Notes Appended!", icon="✅")
            st.rerun()

        if btn_col2.button("❌ Discard Results", type="secondary", use_container_width=True):
            st.session_state.temp_voice_data = None
            st.session_state.audio_key_counter += 1
            st.toast("Discarded", icon="🗑️")
            st.rerun()
    # --- END VOICE ---

    # Notes Input
    current_note_value = st.session_state.get(text_area_key, st.session_state.progress['notes'].get(note_key, ""))
    
    new_note = st.text_area(
        "Notes:", 
        value=current_note_value, 
        height=550, 
        label_visibility="collapsed", 
        placeholder="Add keywords...", 
        key=text_area_key,
        on_change=auto_save_text,             # <-- Added callback
        args=(text_area_key, "notes")         # <-- Passed arguments
    )

    # --- REFERENCE GAP-CHECK ---
    # Default to Ollama here regardless of the sidebar's ai_provider toggle: this can run
    # over hundreds of questions, so routine checks should be free/local. Gemini is only
    # ever forced (inside get_relevant_reference) for OCR'ing scanned/handwritten reference pages.
    st.divider()
    if 'temp_gap_check' not in st.session_state: st.session_state.temp_gap_check = None

    st.subheader("🔍 Check Against Reference")
    if not new_note.strip():
        st.caption("Write an attempt first, then check it against your reference material.")
    elif not st.session_state.temp_gap_check:
        if st.button("Check My Answer vs Reference"):
            with st.spinner("Loading reference & checking..."):
                try:
                    ref_text, sources = get_relevant_reference(selected_subject, question['Chapter'], question['Question'], gemini_key)
                except Exception as e:
                    ref_text, sources = None, []
                    st.error(f"Couldn't read reference material: {e}")

            if ref_text is None:
                st.info(f"No reference material found for this chapter. Add a PDF under "
                         f"data/{selected_subject}/reference/ to enable this.")
            elif not ref_text.strip():
                st.warning("Reference material found but no readable/OCR'd text was extracted.")
            else:
                prompt = build_gap_check_prompt(question['Question'], new_note, ref_text)
                stream_box = st.empty()
                full_result = ""
                for chunk in ask_ai(prompt, "Local (Ollama)", gemini_key, TEXT_MODEL):
                    full_result += chunk
                    stream_box.markdown(full_result + "▌")
                stream_box.empty()
                st.session_state.temp_gap_check = {"result": full_result, "sources": sources}
                st.rerun()
    else:
        st.markdown(st.session_state.temp_gap_check["result"])
        if st.session_state.temp_gap_check.get("sources"):
            st.caption(f"📄 Sources: {format_sources(st.session_state.temp_gap_check['sources'])}")
        gc1, gc2 = st.columns(2)
        if gc1.button("⬇️ Save to Notes", key="gapcheck_save", use_container_width=True):
            gap_note = f"\n**Gap-check:**\n{st.session_state.temp_gap_check['result']}\n"
            if st.session_state.temp_gap_check.get("sources"):
                gap_note += f"\n*Source: {format_sources(st.session_state.temp_gap_check['sources'])}*\n"
            updated_note = (new_note + "\n" + gap_note) if new_note else gap_note
            st.session_state.progress['notes'][note_key] = updated_note
            st.session_state[notes_version_key] += 1
            st.session_state.progress.setdefault("reference_checked", {})
            st.session_state.progress["reference_checked"][note_key] = True
            save_progress(selected_subject, st.session_state.progress)
            st.session_state.temp_gap_check = None
            st.toast("Gap-check saved to notes!", icon="✅")
            st.rerun()
        if gc2.button("❌ Dismiss", key="gapcheck_dismiss", use_container_width=True):
            st.session_state.temp_gap_check = None
            st.rerun()

    if 'temp_reveal_answer' not in st.session_state: st.session_state.temp_reveal_answer = None

    with st.expander("📖 Reveal Full Reference Answer (use after attempting)"):
        if not st.session_state.temp_reveal_answer:
            if st.button("Show Reference Answer", key="reveal_ref_answer"):
                with st.spinner("Loading reference..."):
                    try:
                        ref_text, sources = get_relevant_reference(selected_subject, question['Chapter'], question['Question'], gemini_key)
                    except Exception as e:
                        ref_text, sources = None, []
                        st.error(f"Couldn't read reference material: {e}")

                if ref_text is None:
                    st.info("No reference material found for this chapter.")
                elif not ref_text.strip():
                    st.warning("Reference material found but no readable/OCR'd text was extracted.")
                else:
                    prompt = build_reveal_answer_prompt(question['Question'], ref_text)
                    stream_box = st.empty()
                    full_result = ""
                    for chunk in ask_ai(prompt, "Local (Ollama)", gemini_key, TEXT_MODEL):
                        full_result += chunk
                        stream_box.markdown(full_result + "▌")
                    stream_box.empty()
                    st.session_state.temp_reveal_answer = {"result": full_result, "sources": sources}
                    st.rerun()
        else:
            st.markdown(st.session_state.temp_reveal_answer["result"])
            if st.session_state.temp_reveal_answer.get("sources"):
                st.caption(f"📄 Sources: {format_sources(st.session_state.temp_reveal_answer['sources'])}")
            rv1, rv2 = st.columns(2)
            if rv1.button("⬇️ Add to Notes", key="reveal_save", use_container_width=True):
                addition = f"\n**Reference Answer:**\n{st.session_state.temp_reveal_answer['result']}\n"
                if st.session_state.temp_reveal_answer.get("sources"):
                    addition += f"\n*Source: {format_sources(st.session_state.temp_reveal_answer['sources'])}*\n"
                updated_note = (new_note + "\n" + addition) if new_note else addition
                st.session_state.progress['notes'][note_key] = updated_note
                st.session_state[notes_version_key] += 1
                st.session_state.progress.setdefault("reference_checked", {})
                st.session_state.progress["reference_checked"][note_key] = True
                save_progress(selected_subject, st.session_state.progress)
                st.session_state.temp_reveal_answer = None
                st.toast("Added to notes!", icon="✅")
                st.rerun()
            if rv2.button("❌ Dismiss", key="reveal_dismiss", use_container_width=True):
                st.session_state.temp_reveal_answer = None
                st.rerun()
    # --- END REFERENCE GAP-CHECK ---

    st.divider()
    st.subheader("🐍 Python Code (Optional)")
    st.session_state.progress.setdefault("code", {})
    current_code = st.session_state.progress["code"].get(note_key, "")
    
    code_area_key = f"code_{note_key}"
    new_code = st.text_area(
        "Code:", 
        value=current_code, 
        height=250, 
        label_visibility="collapsed", 
        key=code_area_key,
        on_change=auto_save_text,             # <-- Added callback
        args=(code_area_key, "code")          # <-- Passed arguments
    )


    st.subheader("🖼️ Images (Optional)")
    st.session_state.progress.setdefault("images", {})
    current_images = get_note_images(st.session_state.progress, note_key)
    if current_images:
        img_cols = st.columns(min(len(current_images), 4))
        for i, img_name in enumerate(current_images):
            image_path = os.path.join(DATA_DIR, selected_subject, "images", img_name)
            with img_cols[i % len(img_cols)]:
                if os.path.exists(image_path):
                    st.image(image_path, width=200)
                if st.button("🗑️ Remove", key=f"rm_img_{note_key}_{i}"):
                    remaining = [n for j, n in enumerate(current_images) if j != i]
                    st.session_state.progress["images"][note_key] = remaining
                    save_progress(selected_subject, st.session_state.progress)
                    st.rerun()
    uploaded_file = st.file_uploader("Add an image:", type=["png", "jpg", "jpeg"], key=f"upload_{note_key}")

    # 🔴 CRITICAL FIX: Prevent infinite rerun loop
    if uploaded_file is not None:
        safe_name = f"{note_key}_{uploaded_file.name}"

        if safe_name not in current_images:
            image_dir = os.path.join(DATA_DIR, selected_subject, "images")
            os.makedirs(image_dir, exist_ok=True)

            with open(os.path.join(image_dir, safe_name), "wb") as f:
                f.write(uploaded_file.getbuffer())

            st.session_state.progress["images"][note_key] = current_images + [safe_name]
            save_progress(selected_subject, st.session_state.progress)
            st.toast("Image saved!", icon="✅")
            st.rerun()

    

    st.divider()
    # 1. Save Button (Extra convenience)
    if st.button("Save All", type="primary"):
        save_all_changes()
        st.rerun()
    
    st.divider()
    # 2. Navigation Buttons (With Fix applied)
    nav_cols_bottom = st.columns(2)
    
    # FIX: We wrapped the conditions in bool(...) to convert numpy.bool to python bool
    is_first = bool(q_index == 0)
    is_last = bool(q_index >= len(filtered_df) - 1)

    if nav_cols_bottom[0].button("◀ Prev Q", use_container_width=True, disabled=is_first):
        save_all_changes()
        st.session_state.current_question_index -= 1
        st.rerun()
    if nav_cols_bottom[1].button("Next Q ▶", use_container_width=True, disabled=is_last):
        save_all_changes()
        st.session_state.current_question_index += 1
        st.rerun()

elif st.session_state.view == 'pdf_reader':
    # --- Top Bar: Minimal Navigation & Settings ---
    c_head, c_set = st.columns([0.8, 0.2])
    with c_head:
        st.subheader("📖 Intelligent Slide Studio")
    with c_set:
        show_settings = st.toggle("⚙️ Settings", value=False)

    uploaded_pdf = st.file_uploader("Upload PDF (Slides/Notes)", type="pdf", label_visibility="collapsed")
    
    if uploaded_pdf:
        # Save & Open
        temp_path = os.path.join(DATA_DIR, "temp_reading_material.pdf")
        with open(temp_path, "wb") as f: f.write(uploaded_pdf.getbuffer())
        
        # --- Cleaning Settings ---
        if show_settings:
            with st.container(border=True):
                st.caption("🧹 Text Cleaning Filter")
                sc1, sc2, sc3 = st.columns(3)
                header_lines = sc1.number_input("Skip Top Lines", 0, 10, 0)
                footer_lines = sc2.number_input("Skip Bottom Lines", 0, 10, 0)
                ignore_phrases = sc3.text_input("Ignore Phrases (comma sep)", "").split(",")
                ignore_list = [p.strip() for p in ignore_phrases if p.strip()]
        else:
            header_lines, footer_lines, ignore_list = 0, 0, []

        with pdfplumber.open(temp_path) as pdf:
            total_pages = len(pdf.pages)
            if 'pdf_page' not in st.session_state: st.session_state.pdf_page = 0
            
            # --- Smart Navigation Bar ---
            nav1, nav2, nav3 = st.columns([1, 4, 1])
            if nav1.button("◀ Prev", use_container_width=True): 
                st.session_state.pdf_page = max(0, st.session_state.pdf_page - 1); st.rerun()
            
            nav2.progress((st.session_state.pdf_page + 1) / total_pages)
            nav2.caption(f"Slide {st.session_state.pdf_page + 1} of {total_pages}")
            
            if nav3.button("Next ▶", use_container_width=True): 
                st.session_state.pdf_page = min(total_pages - 1, st.session_state.pdf_page + 1); st.rerun()

            # --- THE SPLIT VIEW LAYOUT ---
            col_visual, col_tools = st.columns([1, 1]) 
            current_page_obj = pdf.pages[st.session_state.pdf_page]
            
            # --- LEFT: VISUALS ---
            with col_visual:
                st.markdown("##### 🖼️ Visual Slide")
                try:
                    page_image = current_page_obj.to_image(resolution=200).original
                    st.image(page_image, use_container_width=True)
                except Exception as e:
                    st.warning("Install 'pypdfium2' to see images.")

            # --- RIGHT: INTELLIGENCE ---
            with col_tools:
                raw_text = current_page_obj.extract_text() or ""
                clean_text = clean_text_dynamic(raw_text, header_lines, footer_lines, ignore_list)
                
                st.markdown("##### 🧠 Knowledge Engine")
                t1, t2, t3, t4 = st.tabs(["👁️ Bionic", "🤖 AI Tutor", "⚡ Speed", "🎧 Audio"])
                
                with t1: # Bionic Reading
                    with st.container(height=400):
                        st.markdown(make_bionic(clean_text), unsafe_allow_html=True)
                
                with t2: # AI Tutor (FIXED TO USE ask_ai)
                    st.caption(f"Brain: {ai_provider}")
                    # Only show local model selector if using Ollama
                    if ai_provider == "Local (Ollama)":
                        model_name = st.selectbox("Model", ["llama3.2:3b", "llama3.2:1b", TEXT_MODEL], label_visibility="collapsed")
                    else:
                        model_name = "gemini-2.0-flash" # Placeholder for logic

                    if st.button("🧠 Explain Slide", use_container_width=True):
                        with st.spinner("Analyzing..."):
                            prompt = f"""
                            Explain this slide content simply.
                            If diagrams are implied, describe the concept.
                            Slide Text: "{clean_text}"
                            """
                            stream_box = st.empty()
                            full_res = ""
                            
                            # --- USE THE HELPER FUNCTION HERE ---
                            for chunk in ask_ai(prompt, ai_provider, gemini_key, model_name):
                                full_res += chunk
                                stream_box.markdown(full_res + "▌")
                            stream_box.markdown(full_res)

                with t3: # RSVP Speed Reader (PAUSABLE)
                    # Initialize session state for this specific reader if not present
                    if 'rsvp_pos' not in st.session_state: st.session_state.rsvp_pos = 0
                    if 'rsvp_active' not in st.session_state: st.session_state.rsvp_active = False

                    speed = st.slider("WPM", 200, 600, 300)
                    # Split logic that keeps it simple for RSVP
                    words = clean_text.split()
                    
                    # Controls Row
                    r1, r2, r3 = st.columns(3)
                    
                    if r1.button("▶ Start / Resume", key="rsvp_start"):
                        st.session_state.rsvp_active = True
                        
                    if r2.button("⏹ Stop / Pause", key="rsvp_stop"):
                        st.session_state.rsvp_active = False
                        
                    if r3.button("🔄 Reset", key="rsvp_reset"):
                        st.session_state.rsvp_pos = 0
                        st.session_state.rsvp_active = False
                        st.rerun()

                    # The Reader Window
                    reader_placeholder = st.empty()
                    progress_text = st.empty()

                    if st.session_state.rsvp_active:
                        # Loop starting from the saved position
                        for i in range(st.session_state.rsvp_pos, len(words)):
                            # Check if we should stop (requires external interruption usually, 
                            # but helps if logic changes)
                            if not st.session_state.rsvp_active: break
                            
                            word = words[i]
                            st.session_state.rsvp_pos = i # Save position
                            
                            # Update UI
                            reader_placeholder.markdown(
                                f"<div style='height:150px; display:flex; align-items:center; justify-content:center; background-color:#222; border-radius:10px;'>"
                                f"<h1 style='font-size:60px; margin:0; color:white;'>{word}</h1></div>", 
                                unsafe_allow_html=True
                            )
                            progress_text.caption(f"Word {i+1} of {len(words)}")
                            
                            # Speed math
                            time.sleep(60/speed)
                        
                        # If finished
                        if st.session_state.rsvp_pos >= len(words) - 1:
                            st.session_state.rsvp_active = False
                            st.session_state.rsvp_pos = 0
                            reader_placeholder.success("✅ Reading Complete!")
                    else:
                        # Static View (When Paused)
                        current_word = words[st.session_state.rsvp_pos] if words and st.session_state.rsvp_pos < len(words) else "Ready"
                        reader_placeholder.markdown(
                                f"<div style='height:150px; display:flex; align-items:center; justify-content:center; background-color:#333; border-radius:10px;'>"
                                f"<h1 style='font-size:60px; margin:0; color:gray;'>{current_word}</h1></div>", 
                                unsafe_allow_html=True
                            )
                        progress_text.caption(f"Paused at: {st.session_state.rsvp_pos} / {len(words)}")

                with t4: # Audio
                    if st.button("▶ Read Aloud", use_container_width=True):
                        f_path = generate_audio(clean_text)
                        if f_path: st.audio(f_path)

elif st.session_state.view == 'dashboard':
    st.header("📊 Dashboard")

    streak = load_streak()
    streak_cols = st.columns(2)
    streak_cols[0].metric("🔥 Current Streak", f"{streak['current_streak']} day{'s' if streak['current_streak'] != 1 else ''}")
    streak_cols[1].metric("🏆 Best Streak", f"{streak['longest_streak']} day{'s' if streak['longest_streak'] != 1 else ''}")

    st.divider()

    total_due = 0
    rows = []
    for subj in get_subjects():
        subj_df = load_data(subj)
        subj_progress = load_progress(subj)
        noted_qids = {qid for qid, note in subj_progress.get('notes', {}).items() if note and note.strip()}
        due_count = sum(1 for qid in noted_qids if srs.is_due(get_srs_entry(subj_progress, qid)))
        total_due += due_count
        rows.append({
            "Subject": subj.replace('_', ' '),
            "Notes": f"{len(noted_qids)}/{len(subj_df)}",
            "Due Today": due_count,
        })

    st.metric("📅 Total Due Across All Subjects", total_due)
    st.caption("Clear this to zero today - that's the whole job.")

    st.divider()
    st.subheader("Per-Subject Breakdown")
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

else: # List View
    st.subheader("Chapter Progress (for current filter)")
    done_qids = set(st.session_state.progress.get('done_questions', []))

    if len(chapters) > 0:
        progress_cols = st.columns(len(selected_chapters))
        for i, chapter in enumerate(selected_chapters):
            chapter_questions_in_filter = filtered_df[filtered_df['Chapter'] == chapter]
            total_in_chapter_and_filter = len(chapter_questions_in_filter)
            chapter_qids_in_filter = set(chapter_questions_in_filter['StableID'])
            done_in_chapter_and_filter = len(chapter_qids_in_filter.intersection(done_qids))

            with progress_cols[i]:
                if total_in_chapter_and_filter > 0:
                    st.metric(label=chapter, value=f"{done_in_chapter_and_filter}/{total_in_chapter_and_filter}")
                    st.progress(done_in_chapter_and_filter / total_in_chapter_and_filter)
                else:
                    st.metric(label=chapter, value="0/0")
                    st.progress(0)

    st.subheader("Filtered Progress Dashboard")
    total_questions_filtered = len(filtered_df)
    filtered_qids = set(filtered_df['StableID'])
    done_count_filtered = len(filtered_qids.intersection(done_qids))
    remaining_count = total_questions_filtered - done_count_filtered
    progress_percent = done_count_filtered / total_questions_filtered if total_questions_filtered > 0 else 0

    p_cols = st.columns(3)
    p_cols[0].metric("Matching Questions", f"{total_questions_filtered}")
    p_cols[1].metric("Reviewed in this Set", f"{done_count_filtered} ({int(progress_percent * 100)}%)")
    p_cols[2].metric("Remaining in this Set", f"{remaining_count}")
    st.progress(progress_percent)

    st.divider()
    
    st.header(f"Question Bank ({len(filtered_df)} questions found)")
    max_page = max(0, math.ceil(len(filtered_df) / PAGE_SIZE) - 1)
    if st.session_state.page_number > max_page: st.session_state.page_number = 0
    start_idx = st.session_state.page_number * PAGE_SIZE; end_idx = start_idx + PAGE_SIZE
    paginated_df = filtered_df.iloc[start_idx:end_idx]
    for _, row in paginated_df.iterrows():
        stable_id = row['StableID']
        is_done = stable_id in done_qids
        col1, col2 = st.columns([0.1, 0.9])
        with col1:
            new_is_done = st.checkbox(f"Mark {stable_id}", value=is_done,
                                      key=f"done_{selected_subject}_{stable_id}", label_visibility="collapsed",
                                      help="Mark as reviewed")
        with col2:
            sr_no_display = f"{row.get('Sr. No.', '')}. " if pd.notna(row.get('Sr. No.')) else ""
            if is_done: st.markdown(f"<span style='opacity: 0.5; text-decoration: line-through;'>{sr_no_display}{row['Question']}</span>", unsafe_allow_html=True)
            else: st.markdown(f"**{sr_no_display}{row['Question']}**")
            st.caption(f"Chapter: {row['Chapter']} | Marks: {row.get('Marks', 'N/A')}")
            if st.button("Add/Edit Notes", key=f"review_{selected_subject}_{stable_id}", type="secondary"):
                st.session_state.view = 'detail'; st.session_state.current_question_index = filtered_df.index[filtered_df['StableID'] == stable_id][0]; st.rerun()
        if new_is_done != is_done:
            if new_is_done: st.session_state.progress['done_questions'].append(stable_id)
            else: st.session_state.progress['done_questions'].remove(stable_id)
            save_progress(selected_subject, st.session_state.progress); st.rerun()
    st.divider()
    if len(filtered_df) > PAGE_SIZE:
        page_nav_cols = st.columns([1, 2, 1])
        if page_nav_cols[0].button("◀ Prev Page", disabled=(st.session_state.page_number == 0), use_container_width=True): st.session_state.page_number -= 1; st.rerun()
        page_nav_cols[1].markdown(f"<div style='text-align: center; margin-top: 0.5em;'>Page {st.session_state.page_number + 1} of {max_page + 1}</div>", unsafe_allow_html=True)
        if page_nav_cols[2].button("Next Page ▶", disabled=(st.session_state.page_number >= max_page), use_container_width=True): st.session_state.page_number += 1; st.rerun()
