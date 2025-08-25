import pandas as pd
import json
import os
import re
import sys
import shutil

# --- CONFIGURATION ---
DATA_DIR = "data"
PROGRESS_DIR = "progress"
DEPLOY_DIR = "qb_deploy" # The new output folder
STATIC_FILES = ["index.html", "style.css", "script.js"] # Files to copy
# --- END CONFIGURATION ---

# --- 1. Get Subject Name from Command-Line Argument ---
if len(sys.argv) < 2:
    print("\n❌ Error: Missing subject name.")
    print("   Usage: python3 create_flashcards.py <SubjectFolderName>")
    print("   Example: python3 create_flashcards.py Software_Engineering")
    sys.exit(1) # Exit the script with an error code

SUBJECT_TO_EXPORT = sys.argv[1]
print(f"\n🚀 Starting export process for subject: '{SUBJECT_TO_EXPORT}'...")

# --- 2. Create the Clean Deployment Directory ---
if not os.path.exists(DEPLOY_DIR):
    os.makedirs(DEPLOY_DIR)
    print(f"✅ Created deployment directory: '{DEPLOY_DIR}'")

# --- File and Folder Paths ---
subject_path = os.path.join(DATA_DIR, SUBJECT_TO_EXPORT)
progress_file_path = os.path.join(PROGRESS_DIR, f"{SUBJECT_TO_EXPORT}_progress.json")
output_json_path = os.path.join(DEPLOY_DIR, "flashcard_data.json")


def generate_stable_id(row):
    """
    Helper function to create the stable ID.
    MUST EXACTLY MATCH THE LOGIC IN app.py
    """
    sr_no_col = str(row.get('Sr. No.', row.name))
    question_slice = re.sub(r'\W+', '', str(row['Question'])[:30])
    return f"{row['Chapter']}_{sr_no_col}_{question_slice}"


# --- 3. Load all questions from CSVs ---
all_dfs = []
if not os.path.exists(subject_path):
    print(f"❌ Error: Subject folder '{subject_path}' not found. Please check the subject name.")
    sys.exit(1)

for filename in sorted(os.listdir(subject_path)):
    if filename.endswith('.csv'):
        path = os.path.join(subject_path, filename)
        df = pd.read_csv(path)
        chapter_name = os.path.splitext(filename)[0].replace('_', ' ')
        df['Chapter'] = chapter_name
        all_dfs.append(df)

if not all_dfs:
    print(f"❌ Error: No CSV files found in '{subject_path}'.")
    sys.exit(1)
    
master_df = pd.concat(all_dfs, ignore_index=True)
master_df.columns = master_df.columns.str.strip()
master_df['StableID'] = master_df.apply(generate_stable_id, axis=1)
print(f"✅ Loaded and processed {len(master_df)} total questions.")


# --- 4. Load the notes from the progress file ---
try:
    with open(progress_file_path, 'r') as f:
        progress_data = json.load(f)
    notes = progress_data.get("notes", {})
    if not notes:
        print(f"⚠️ Warning: No notes found in '{progress_file_path}'. The flashcard file will be empty.")
    else:
        print(f"✅ Successfully loaded {len(notes)} notes.")
except FileNotFoundError:
    print(f"❌ Error: Progress file not found at '{progress_file_path}'.")
    sys.exit(1)


# --- 5. Create the flashcard data ---
noted_qids = set(notes.keys())
flashcard_df = master_df[master_df['StableID'].isin(noted_qids)].copy()

flashcard_list = []
for _, row in flashcard_df.iterrows():
    stable_id = row['StableID']
    if stable_id in notes and notes[stable_id].strip() != "":
        flashcard_list.append({
            "question": row['Question'],
            "note": notes[stable_id],
            "chapter": row['Chapter'],
            "marks": int(row.get('Marks', 0))
        })

# --- 6. Save the flashcard_data.json to the deploy folder ---
with open(output_json_path, 'w') as f:
    json.dump(flashcard_list, f, indent=2)
print(f"✅ Created '{output_json_path}' with {len(flashcard_list)} flashcards.")


# --- 7. Copy the static website files to the deploy folder ---
for file_name in STATIC_FILES:
    if os.path.exists(file_name):
        shutil.copy(file_name, DEPLOY_DIR)
        print(f"✅ Copied '{file_name}' to '{DEPLOY_DIR}'")
    else:
        print(f"⚠️ Warning: Static file '{file_name}' not found in the root directory. It was not copied.")

print(f"\n✨ Success! ✨")
print(f"Your deployable static site is ready in the '{DEPLOY_DIR}' folder.")
print(f"You can now zip this folder, deploy it, or open its 'index.html' file.")
