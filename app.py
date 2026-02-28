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
from google import genai


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
            try: 
                data = json.load(f)
                # --- NEW: Ensure compatibility for existing files ---
                if "languages" not in data: data["languages"] = {}
                if "code" not in data: data["code"] = {}
                if "images" not in data: data["images"] = {}
                return data
            except json.JSONDecodeError: 
                return {"done_questions": [], "notes": {}, "code": {}, "languages": {}, "images": {}}
    return {"done_questions": [], "notes": {}, "code": {}, "languages": {}, "images": {}}

def save_progress(subject, progress):
    filepath = get_progress_filepath(subject)
    with open(filepath, 'w') as f: json.dump(progress, f, indent=4)

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
    
    sr_no_col = master_df.get('Sr. No.', pd.Series(master_df.index, name='Sr. No.')).astype(str)
    master_df['StableID'] = master_df['Chapter'].astype(str) + '_' + \
                             sr_no_col + '_' + \
                             master_df['Question'].str.slice(0, 30).str.replace(r'\W+', '', regex=True)
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
            local_model = "llama3.2:1b" if "1b" in model_name else "llama3.2:3b"
            if stream:
                for chunk in ollama.generate(model=local_model, prompt=prompt, stream=True):
                    yield chunk['response']
            else:
                yield ollama.generate(model=local_model, prompt=prompt)['response']
        except Exception as e:
            yield f"[Ollama Error] {e}. Is Ollama running?"


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
    st.session_state.flashcard_index = 0
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
        st.session_state.view = 'flashcard'; st.session_state.flashcard_index = 0; st.session_state.card_flipped = False; st.rerun()
    if st.button("📖 Smart PDF Reader", use_container_width=True):
        st.session_state.view = 'pdf_reader'
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
    
    if 'Category' in master_df.columns:
        selected_category = st.multiselect("Category:", master_df['Category'].unique(), default=master_df['Category'].unique())
    else:
        selected_category = master_df.index
    
    if 'Marks' in master_df.columns:
        selected_marks = st.multiselect("Marks:", sorted(master_df['Marks'].unique()), default=sorted(master_df['Marks'].unique()))
    else:
        selected_marks = master_df.index

    review_status = st.radio("Status:", ('All', 'Reviewed', 'Not Reviewed'), horizontal=True)
    st.divider()
    search_query = st.text_input("🔍 Search Questions & Notes")

if search_query:
    question_mask = master_df['Question'].str.contains(search_query, case=False, na=False)
    notes = st.session_state.progress.get('notes', {})
    matching_ids = {qid for qid, note in notes.items() if search_query.lower() in note.lower()}
    notes_mask = master_df['StableID'].isin(matching_ids)
    master_df = master_df[question_mask | notes_mask]

# --- Filtering Logic ---
filtered_df = master_df.copy()
if 'Category' in filtered_df.columns:
    filtered_df = filtered_df[filtered_df['Category'].isin(selected_category)]
if 'Marks' in filtered_df.columns:
    filtered_df = filtered_df[filtered_df['Marks'].isin(selected_marks)]
filtered_df = filtered_df[filtered_df['Chapter'].isin(selected_chapters)]

done_qids = set(st.session_state.progress['done_questions'])
if review_status == 'Reviewed': filtered_df = filtered_df[filtered_df['StableID'].isin(done_qids)]
elif review_status == 'Not Reviewed': filtered_df = filtered_df[~filtered_df['StableID'].isin(done_qids)]
filtered_df.reset_index(drop=True, inplace=True)

# --- View Switching ---
if st.session_state.view == 'flashcard':
    st.header("🗂️ Flashcard Review Session")
    
    noted_qids = set(st.session_state.progress['notes'].keys())
    flashcard_deck = filtered_df[filtered_df['StableID'].isin(noted_qids)].reset_index(drop=True)
    
    if flashcard_deck.empty:
        st.warning("No notes found for questions matching filters. Add notes in the 'Question List' view.")
    else:
        deck_size = len(flashcard_deck)
        if st.session_state.flashcard_index >= deck_size: st.session_state.flashcard_index = 0
        question = flashcard_deck.iloc[st.session_state.flashcard_index]
        with st.container(height=500, border=True):
            st.subheader(f"Q: {question['Question']}")
            st.caption(f"Chapter: {question['Chapter']} | Marks: {question.get('Marks', 'N/A')}")
            if not st.session_state.card_flipped:
                st.markdown("<br>", unsafe_allow_html=True)
                if st.button("Show My Notes (Flip Card)", type="primary"): st.session_state.card_flipped = True; st.rerun()
            if st.session_state.card_flipped:
                st.markdown("---")
                st.subheader("Your Notes:")
                
                # 1. Show Text Notes
                note = st.session_state.progress['notes'].get(question['StableID'], "*No notes.*")
                note_with_breaks = note.replace('\n', '<br>')
                st.markdown(note_with_breaks, unsafe_allow_html=True)

                # 2. NEW: Show Code Snippet with Syntax Highlighting
                code_content = st.session_state.progress.get('code', {}).get(question['StableID'], "")
                if code_content and code_content.strip():
                    # Default to 'python' if language not saved (Backward Compatibility)
                    lang = st.session_state.progress.get('languages', {}).get(question['StableID'], "python")
                    st.caption(f"💻 Code ({lang}):")
                    st.code(code_content, language=lang)

                # 3. Show Image
                img_name = st.session_state.progress.get('images', {}).get(question['StableID'], "")
                if img_name:
                    img_path = os.path.join(DATA_DIR, selected_subject, "images", img_name)
                    if os.path.exists(img_path):
                        st.image(img_path)
        st.subheader(f"Card {st.session_state.flashcard_index + 1} of {deck_size}")
        nav_cols = st.columns(2)
        if nav_cols[0].button("◀ Prev", use_container_width=True, disabled=(st.session_state.flashcard_index == 0)):
            st.session_state.flashcard_index -= 1; st.session_state.card_flipped = False; st.rerun()
        if st.session_state.flashcard_index < deck_size - 1:
            if nav_cols[1].button("Next ▶", use_container_width=True):
                st.session_state.flashcard_index += 1; st.session_state.card_flipped = False; st.rerun()
        else:
            if nav_cols[1].button("Restart 🔄", use_container_width=True, type="primary"):
                st.session_state.flashcard_index = 0; st.session_state.card_flipped = False; st.rerun()

elif st.session_state.view == 'detail':
    q_index = st.session_state.current_question_index
    if q_index is None: st.session_state.view = 'list'; st.rerun()
    question = filtered_df.iloc[q_index]
    note_key = question['StableID']
    text_area_key = f"note_{note_key}"
    
    # Navigation
    nav_cols = st.columns([1, 5, 1])
    if nav_cols[0].button("⬅️ Back"): st.session_state.view = 'list'; st.rerun()
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
                model_choice = st.selectbox("Model", ["3b", "1b"], label_visibility="collapsed")
                selected_model = "llama3.2:3b" if "3b" in model_choice else "llama3.2:1b"
            else:
                st.caption("Using Gemini")
                selected_model = "gemini"

        if audio_val:
            st.info("Audio captured.")
            b1, b2 = st.columns(2)
            
            if b1.button("⚡ Transcribe & Analyze", type="primary", use_container_width=True):
                with st.spinner("Processing..."):
                    # 1. Whisper Transcribe (Always Local)
                    audio_bytes = audio_val.read()
                    audio_buffer = io.BytesIO(audio_bytes)
                    segments, info = whisper_model.transcribe(audio_buffer, beam_size=1)
                    raw_text = " ".join([segment.text for segment in segments]).strip()

                if raw_text:
                    ai_response = ""
                    # 2. AI Analysis (Uses Universal Helper)
                    if use_ai:
                        prompt = f"""
                        You are a Viva Tutor.
                        Question: "{question['Question']}"
                        Student Answer: "{raw_text}"
                        
                        1. Clean up the transcript.
                        2. Give specific feedback on missing points.
                        """
                        
                        stream_box = st.empty()
                        full_streamed_text = ""
                        
                        # --- CALL THE HELPER ---
                        for chunk in ask_ai(prompt, ai_provider, gemini_key, selected_model):
                            full_streamed_text += chunk
                            stream_box.markdown(full_streamed_text + "▌")
                        
                        ai_response = full_streamed_text
                        stream_box.empty()
                    
                    st.session_state.temp_voice_data = {
                        "raw": raw_text,
                        "ai": ai_response
                    }
                    st.rerun()
            
            if b2.button("🔄 Reset", type="secondary", use_container_width=True):
                st.session_state.audio_key_counter += 1
                st.rerun()
                
    # --- PREVIEW / CONFIRMATION AREA ---
    if st.session_state.temp_voice_data:
        st.success("✅ Transcription Complete! Review before adding.")
        
        # Display the AI Generated Content
        if st.session_state.temp_voice_data["ai"]:
            st.markdown(st.session_state.temp_voice_data["ai"])
        else:
            st.markdown(f"**Transcript:** {st.session_state.temp_voice_data['raw']}")

        # Action Buttons
        btn_col1, btn_col2 = st.columns(2)
        
        if btn_col1.button("⬇️ Add to Notes", type="primary", use_container_width=True):
            current_text = st.session_state.get(text_area_key, st.session_state.progress['notes'].get(note_key, ""))
            
            # Format how it looks in the text box
            if st.session_state.temp_voice_data['ai']:
                new_entry = f"\n{st.session_state.temp_voice_data['ai']}\n"
            else:
                new_entry = f"\n- {st.session_state.temp_voice_data['raw']}\n"

            updated_note = (current_text + "\n" + new_entry) if current_text else new_entry
            
            # Save & Cleanup
            st.session_state.progress['notes'][note_key] = updated_note
            st.session_state[text_area_key] = updated_note
            st.session_state.temp_voice_data = None
            st.session_state.audio_key_counter += 1
            st.toast("Notes Appended!", icon="✅")
            st.rerun()

        if btn_col2.button("❌ Discard Results", type="secondary", use_container_width=True):
            st.session_state.temp_voice_data = None
            # We don't increment counter here so they can see the old audio, 
            # but usually you want to reset to record again:
            st.session_state.audio_key_counter += 1 
            st.toast("Discarded", icon="🗑️")
            st.rerun()
    # --- END VOICE ---

    # Notes Input
    current_note_value = st.session_state.get(text_area_key, st.session_state.progress['notes'].get(note_key, ""))
    new_note = st.text_area("Notes:", value=current_note_value, height=550, label_visibility="collapsed", placeholder="Add keywords...", key=text_area_key)

    if new_note != st.session_state.progress['notes'].get(note_key, ""):
         st.session_state.progress['notes'][note_key] = new_note

    st.divider()
    
    # --- NEW: Code Snippet Section with Language Selector ---
    c_head, c_lang = st.columns([0.7, 0.3])
    with c_head:
        st.subheader("💻 Code Snippet (Optional)")
    with c_lang:
        # Get saved language or default to python
        saved_lang = st.session_state.progress.get("languages", {}).get(note_key, "python")
        
        # Dropdown options
        lang_options = ["python", "html", "css", "javascript", "php", "sql", "java", "bash"]
        
        # Ensure saved_lang is in options (in case of manual edits)
        if saved_lang not in lang_options: lang_options.append(saved_lang)
        
        selected_lang = st.selectbox("Language", lang_options, index=lang_options.index(saved_lang), key=f"lang_{note_key}", label_visibility="collapsed")

    current_code = st.session_state.progress.get("code", {}).get(note_key, "")
    new_code = st.text_area("Code:", value=current_code, height=250, label_visibility="collapsed", key=f"code_{note_key}")

    st.subheader("🖼️ Image (Optional)")
    current_image = st.session_state.progress.get("images", {}).get(note_key, "")
    if current_image:
        image_path = os.path.join(DATA_DIR, selected_subject, "images", current_image)
        if os.path.exists(image_path): st.image(image_path, width=400)
    uploaded_file = st.file_uploader("Upload an image:", type=["png", "jpg", "jpeg"], key=f"upload_{note_key}")

    def save_all_changes():
        # Save Note
        st.session_state.progress['notes'][note_key] = new_note
        
        # Save Code
        if 'code' not in st.session_state.progress: st.session_state.progress['code'] = {}
        st.session_state.progress['code'][note_key] = new_code
        
        # NEW: Save Language
        if 'languages' not in st.session_state.progress: st.session_state.progress['languages'] = {}
        st.session_state.progress['languages'][note_key] = selected_lang

        # Save Image
        if uploaded_file is not None:
            image_dir = os.path.join(DATA_DIR, selected_subject, "images")
            if not os.path.exists(image_dir): os.makedirs(image_dir)
            with open(os.path.join(image_dir, uploaded_file.name), "wb") as f:
                f.write(uploaded_file.getbuffer())
            if 'images' not in st.session_state.progress: st.session_state.progress['images'] = {}
            st.session_state.progress['images'][note_key] = uploaded_file.name
        
        # Write to file
        save_progress(selected_subject, st.session_state.progress)
        st.toast("Saved!", icon="✅")
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
                        model_name = st.selectbox("Model", ["llama3.2:3b", "llama3.2:1b"], label_visibility="collapsed")
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
