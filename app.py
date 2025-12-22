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
            try: return json.load(f)
            except json.JSONDecodeError: return {"done_questions": [], "notes": {}}
    return {"done_questions": [], "notes": {}}

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
                st.markdown("---"); st.subheader("Your Notes:")
                note = st.session_state.progress['notes'].get(question['StableID'], "*No notes.*")
                note_with_breaks = note.replace('\n', '<br>')
                st.markdown(note_with_breaks, unsafe_allow_html=True)
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
    if q_index is None or q_index >= len(filtered_df):
        st.warning("Selected question not available in current filter. Returning to list.")
        st.session_state.view = 'list'; st.rerun()
    question = filtered_df.iloc[q_index]
    note_key = question['StableID']
    text_area_key = f"note_{note_key}"
    
    # Navigation Header
    nav_cols = st.columns([1, 5, 1])
    if nav_cols[0].button("⬅️ Back"): st.session_state.view = 'list'; st.rerun()
    nav_cols[2].write(f"Q {q_index + 1} of {len(filtered_df)}")
    
    # Question Details
    st.subheader(f"Q: {question['Question']}")
    st.caption(f"Chapter: {question['Chapter']} | Sr.No: {question.get('Sr. No.', 'N/A')} | Marks: {question.get('Marks', 'N/A')}")
    st.divider()

    st.subheader("📝 Your Notes")

    # --- TAB INDENT SCRIPT ---
    def enable_tab_indent():
        js_code = """
        <script>
        const textareas = window.parent.document.querySelectorAll('textarea');
        textareas.forEach(textarea => {
            textarea.addEventListener('keydown', function(e) {
                if (e.key === 'Tab') {
                    e.preventDefault();
                    var start = this.selectionStart;
                    var end = this.selectionEnd;
                    this.value = this.value.substring(0, start) + "    " + this.value.substring(end);
                    this.selectionStart = this.selectionEnd = start + 4;
                }
            });
        });
        </script>
        """
        html(js_code, height=0)
    enable_tab_indent()
    
    # --- START: ADVANCED VOICE LOGIC ---
    if 'audio_key_counter' not in st.session_state: st.session_state.audio_key_counter = 0
    if 'temp_voice_data' not in st.session_state: st.session_state.temp_voice_data = None

    # Only show recorder if we aren't currently previewing a processed note
    if not st.session_state.temp_voice_data:
        
        # 1. Controls Row
        c1, c2, c3 = st.columns([0.5, 0.25, 0.25])
        with c1:
            audio_val = st.audio_input("🎤 Record Voice Note", key=f"audio_{note_key}_{st.session_state.audio_key_counter}")
        with c2:
            st.write("") # Spacing
            st.write("") 
            use_ai = st.toggle("✨ AI Analysis", value=True)
        with c3:
            st.write("") # Spacing
            # FIXED: Changed label_visibility to 'collapsed' (valid option)
            model_choice = st.selectbox("AI Model", ["3b (Smart)", "1b (Fast)"], label_visibility="collapsed", help="Select AI Model")
            selected_model = "llama3.2:3b" if "3b" in model_choice else "llama3.2:1b"

        # 2. Gatekeeper Logic (Transcribe vs Reset)
        if audio_val:
            st.info("Audio captured. Ready to process?")
            
            b1, b2 = st.columns(2)
            
            # Button to Start Processing
            if b1.button("⚡ Transcribe & Analyze", type="primary", use_container_width=True):
                
                # A. Transcribe (This part is fast, so a spinner is fine)
                with st.spinner("👂 Transcribing audio..."):
                    audio_bytes = audio_val.read()
                    audio_buffer = io.BytesIO(audio_bytes)
                    segments, info = whisper_model.transcribe(audio_buffer, beam_size=1)
                    raw_text = " ".join([segment.text for segment in segments]).strip()

                if raw_text:
                    ai_response = ""
                    
                    # B. AI Analysis (THIS IS WHERE WE STREAM)
                    if use_ai:
                        try:
                            prompt = f"""
                                You are an AI tutor specializing in viva (oral exam) preparation. Your goal is to critically evaluate a student's spoken answer to an exam question and provide actionable feedback.

                                Here is the exam question:
                                Question: "{question['Question']}"

                                Here is the student's raw transcript of their spoken answer:
                                Student's Transcript: "{raw_text}"

                                Please provide your response in the following Markdown format, directly addressing the student:

                                ### 🗣️ Your Refined Transcript
                                [Clean, grammatically corrected, well-structured (e.g., bullet points or concise paragraphs) version of the student's answer. Remove verbal filler like "um," "uh," repetitions, or irrelevant tangents. Aim for clarity and conciseness.]

                                ### 🧠 Viva Feedback & Improvement Points
                                Based on the Question and your Refined Transcript, here's how you can improve your oral explanation for a viva:
                                *   **Strengths:** What did you explain well or correctly? Mention specific concepts or correct terms used.
                                *   **Gaps/Missing Points:** What crucial concepts, keywords, definitions, or examples were missed, or not fully elaborated? Why are these important?
                                *   **Clarity & Structure:** Was your explanation easy to follow from start to finish? How could the flow, introduction, or conclusion be improved for a clear verbal delivery?
                                *   **Depth & Accuracy:** Did you go beyond surface-level definitions? Was all the information factually correct? Point out any inaccuracies.

                                ### ✅ Key Takeaways / Model Answer Snippet
                                Here are the most important points to include when confidently answering this question in a viva, presented concisely:
                                *   [Key point 1, concise explanation]
                                *   [Key point 2, concise explanation]
                                *   [Key point 3, concise explanation]
                                ... (Add more if necessary, covering the core elements)

                                Avoid any conversational introduction or conclusion outside of these specific Markdown sections.
                                """
                            
                            # Create a placeholder to stream text into
                            stream_box = st.empty()
                            full_streamed_text = ""
                            
                            # Stream the response
                            for chunk in ollama.generate(model=selected_model, prompt=prompt, stream=True):
                                content = chunk['response']
                                full_streamed_text += content
                                stream_box.markdown(full_streamed_text + "▌") # ▌ adds a typing cursor effect
                            
                            ai_response = full_streamed_text
                            stream_box.empty() # Clear the streaming box once done
                            
                        except Exception as e:
                            st.error(f"Ollama Error: {e}")
                    
                    # Store in TEMP state
                    st.session_state.temp_voice_data = {
                        "raw": raw_text,
                        "ai": ai_response
                    }
                    st.rerun()
            
            # Button to Reset
            if b2.button("🔄 Reset / Redo", type="secondary", use_container_width=True):
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
    st.subheader("🐍 Python Code (Optional)")
    current_code = st.session_state.progress.get("code", {}).get(note_key, "")
    new_code = st.text_area("Code:", value=current_code, height=250, label_visibility="collapsed", key=f"code_{note_key}")

    st.subheader("🖼️ Image (Optional)")
    current_image = st.session_state.progress.get("images", {}).get(note_key, "")
    if current_image:
        image_path = os.path.join(DATA_DIR, selected_subject, "images", current_image)
        if os.path.exists(image_path): st.image(image_path, width=400)
    uploaded_file = st.file_uploader("Upload an image:", type=["png", "jpg", "jpeg"], key=f"upload_{note_key}")

    def save_all_changes():
        st.session_state.progress['notes'][note_key] = new_note
        if new_code != current_code:
            if 'code' not in st.session_state.progress: st.session_state.progress['code'] = {}
            st.session_state.progress['code'][note_key] = new_code
        if uploaded_file is not None:
            image_dir = os.path.join(DATA_DIR, selected_subject, "images")
            if not os.path.exists(image_dir): os.makedirs(image_dir)
            with open(os.path.join(image_dir, uploaded_file.name), "wb") as f:
                f.write(uploaded_file.getbuffer())
            if 'images' not in st.session_state.progress: st.session_state.progress['images'] = {}
            st.session_state.progress['images'][note_key] = uploaded_file.name
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
