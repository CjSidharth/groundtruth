import streamlit as st
import pandas as pd
import os
import json
import math
import re

# --- Page Configuration ---
st.set_page_config(page_title="Exam Prep Engine", page_icon="📚", layout="wide")

# --- Constants & Configuration ---
PAGE_SIZE = 15
DATA_DIR = "data"
PROGRESS_DIR = "progress"

# --- Setup: Create progress directory if it doesn't exist ---
if not os.path.exists(PROGRESS_DIR):
    os.makedirs(PROGRESS_DIR)

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
    
    # --- STABLE ID LOGIC ---
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
    selected_category = st.multiselect("Category:", master_df['Category'].unique(), default=master_df['Category'].unique())
    selected_marks = st.multiselect("Marks:", sorted(master_df['Marks'].unique()), default=sorted(master_df['Marks'].unique()))
    review_status = st.radio("Status:", ('All', 'Reviewed', 'Not Reviewed'), horizontal=True)

# --- Filtering Logic (Now uses StableID) ---
filtered_df = master_df[master_df['Chapter'].isin(selected_chapters) & master_df['Category'].isin(selected_category) & master_df['Marks'].isin(selected_marks)].copy()
done_qids = set(st.session_state.progress['done_questions'])
if review_status == 'Reviewed': filtered_df = filtered_df[filtered_df['StableID'].isin(done_qids)]
elif review_status == 'Not Reviewed': filtered_df = filtered_df[~filtered_df['StableID'].isin(done_qids)]
filtered_df.reset_index(drop=True, inplace=True)

# --- View Switching ---
if st.session_state.view == 'flashcard':
    st.header("🗂️ Flashcard Review Session")
    
    # --- FIX: Use StableID to find notes ---
    noted_qids = set(st.session_state.progress['notes'].keys())
    flashcard_deck = filtered_df[filtered_df['StableID'].isin(noted_qids)].reset_index(drop=True)
    # --- END FIX ---
    
    if flashcard_deck.empty:
        st.warning("No notes found for questions matching filters. Add notes in the 'Question List' view.")
    else:
        deck_size = len(flashcard_deck)
        if st.session_state.flashcard_index >= deck_size: st.session_state.flashcard_index = 0
        question = flashcard_deck.iloc[st.session_state.flashcard_index]
        with st.container(height=500, border=True):
            st.subheader(f"Q: {question['Question']}")
            st.caption(f"Chapter: {question['Chapter']} | Marks: {question['Marks']}")
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

    # --- FIX: Use StableID for notes key ---
    note_key = question['StableID']
    # --- END FIX ---
    
    current_note = st.session_state.progress['notes'].get(note_key, "")
    new_note = st.text_area("Notes:", value=current_note, height=250, key=f"note_{selected_subject}_{note_key}",
                            label_visibility="collapsed", placeholder="Add keywords...")
    def save_note_if_changed():
        if new_note != current_note:
            st.session_state.progress['notes'][note_key] = new_note
            save_progress(selected_subject, st.session_state.progress)
            st.toast("Note saved!", icon="✅")
    nav_cols = st.columns([1, 5, 1])
    if nav_cols[0].button("⬅️ Back"): save_note_if_changed(); st.session_state.view = 'list'; st.rerun()
    nav_cols[2].write(f"Q {q_index + 1} of {len(filtered_df)}")
    st.subheader(f"Q: {question['Question']}")
    st.caption(f"Chapter: {question['Chapter']} | Sr.No: {question.get('Sr. No.', 'N/A')} | Marks: {question['Marks']}")
    st.divider()
    st.subheader("📝 Your Notes")
    if st.button("Save Note", type="primary"): save_note_if_changed(); st.rerun()
    st.divider()
    nav_cols_bottom = st.columns(2)
    if nav_cols_bottom[0].button("◀ Prev Q", use_container_width=True, disabled=(q_index == 0)):
        save_note_if_changed(); st.session_state.current_question_index -= 1; st.rerun()
    if nav_cols_bottom[1].button("Next Q ▶", use_container_width=True, disabled=(q_index >= len(filtered_df) - 1)):
        save_note_if_changed(); st.session_state.current_question_index += 1; st.rerun()

else: # List View
    st.subheader("📊 Chapter Progress")
    progress_cols = st.columns(len(chapters))
    for i, chapter in enumerate(chapters):
        # --- FIX: Use StableID for progress calculation ---
        chapter_qids = set(master_df[master_df['Chapter'] == chapter]['StableID'])
        # --- END FIX ---
        total = len(chapter_qids); done_count = len(chapter_qids.intersection(done_qids))
        with progress_cols[i]: st.metric(label=chapter, value=f"{done_count}/{total}"); st.progress(done_count / total if total > 0 else 0)
    st.divider()
    st.header(f"📖 Question Bank ({len(filtered_df)} questions found)")
    max_page = max(0, math.ceil(len(filtered_df) / PAGE_SIZE) - 1)
    if st.session_state.page_number > max_page: st.session_state.page_number = 0
    start_idx = st.session_state.page_number * PAGE_SIZE; end_idx = start_idx + PAGE_SIZE
    paginated_df = filtered_df.iloc[start_idx:end_idx]
    for _, row in paginated_df.iterrows():
        # --- FIX: Use StableID for done status and keys ---
        stable_id = row['StableID']
        is_done = stable_id in done_qids
        # --- END FIX ---
        col1, col2 = st.columns([0.1, 0.9])
        with col1:
            new_is_done = st.checkbox(f"Mark {stable_id}", value=is_done,
                                      key=f"done_{selected_subject}_{stable_id}", label_visibility="collapsed",
                                      help="Mark as reviewed")
        with col2:
            sr_no_display = f"{row.get('Sr. No.', '')}. " if pd.notna(row.get('Sr. No.')) else ""
            if is_done: st.markdown(f"<span style='opacity: 0.5; text-decoration: line-through;'>{sr_no_display}{row['Question']}</span>", unsafe_allow_html=True)
            else: st.markdown(f"**{sr_no_display}{row['Question']}**")
            st.caption(f"Chapter: {row['Chapter']} | Marks: {row['Marks']}")
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
