import pandas as pd
import json
import os
import re
import sys
import shutil
from datetime import datetime

# --- CONFIGURATION ---
DATA_DIR = "data"
PROGRESS_DIR = "progress"
STATIC_FILES = ["index.html", "style.css", "script.js"] 
# --- END CONFIGURATION ---

if len(sys.argv) < 2:
    print("\n❌ Error: Missing subject name.")
    print("   Usage: python archive_old_flashcards.py <SubjectFolderName>")
    sys.exit(1) 

SUBJECT_TO_EXPORT = sys.argv[1]
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
ARCHIVE_DIR = f"archives/{SUBJECT_TO_EXPORT}_archive_{timestamp}"
ARCHIVE_IMG_DIR = os.path.join(ARCHIVE_DIR, "images")

print(f"\n📦 Starting ARCHIVE process for subject: '{SUBJECT_TO_EXPORT}'...")

# Create archive directories
os.makedirs(ARCHIVE_DIR, exist_ok=True)
os.makedirs(ARCHIVE_IMG_DIR, exist_ok=True)
print(f"✅ Created archive directory: '{ARCHIVE_DIR}'")

subject_path = os.path.join(DATA_DIR, SUBJECT_TO_EXPORT)
progress_file_path = os.path.join(PROGRESS_DIR, f"{SUBJECT_TO_EXPORT}_progress.json")
output_json_path = os.path.join(ARCHIVE_DIR, "flashcard_data.json")

# 🔴 OLD ID LOGIC (Keep this exactly like this so it finds your old notes)
def generate_stable_id(row):
    sr_no_col = str(row.get('Sr. No.', row.name))
    question_slice = re.sub(r'\W+', '', str(row['Question'])[:30])
    return f"{row['Chapter']}_{sr_no_col}_{question_slice}"

all_dfs = []
if not os.path.exists(subject_path):
    print(f"❌ Error: Subject folder '{subject_path}' not found.")
    sys.exit(1)

for filename in sorted(os.listdir(subject_path)):
    if filename.endswith('.csv'):
        path = os.path.join(subject_path, filename)
        df = pd.read_csv(path)
        chapter_name = os.path.splitext(filename)[0].replace('_', ' ')
        df['Chapter'] = chapter_name
        all_dfs.append(df)

if not all_dfs:
    print(f"❌ Error: No CSV files found.")
    sys.exit(1)
    
master_df = pd.concat(all_dfs, ignore_index=True)
master_df.columns = master_df.columns.str.strip()
master_df['StableID'] = master_df.apply(generate_stable_id, axis=1)

progress_data = {}
try:
    with open(progress_file_path, 'r') as f:
        progress_data = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    print(f"⚠️ Warning: Progress file not found. Nothing to archive.")
    sys.exit(1)

notes = progress_data.get("notes", {})
codes = progress_data.get("code", {})
images = progress_data.get("images", {})

exportable_qids = set(notes.keys()) | set(codes.keys()) | set(images.keys())
flashcard_df = master_df[master_df['StableID'].isin(exportable_qids)].copy()

flashcard_list = []
for _, row in flashcard_df.iterrows():
    stable_id = row['StableID']
    note_content = notes.get(stable_id, "").strip()
    code_content = codes.get(stable_id, "").strip()
    image_content = images.get(stable_id, "").strip()

    if note_content or code_content or image_content:
        flashcard_data = {
            "question": row['Question'],
            "note": notes.get(stable_id, ""),
            "code": codes.get(stable_id, ""),
            "image": images.get(stable_id, ""),
            "chapter": row['Chapter'],
            "marks": int(row.get('Marks', 0))
        }
        flashcard_list.append(flashcard_data)

        if image_content:
            source_image_path = os.path.join(subject_path, "images", image_content)
            if os.path.exists(source_image_path):
                shutil.copy(source_image_path, ARCHIVE_IMG_DIR)

with open(output_json_path, 'w') as f:
    json.dump(flashcard_list, f, indent=2)

for file_name in STATIC_FILES:
    if os.path.exists(file_name):
        shutil.copy(file_name, ARCHIVE_DIR)

print(f"\n✨ SUCCESS! ✨")
print(f"Your old flashcards are permanently archived at: {ARCHIVE_DIR}")
print("You can open index.html in that folder to view them anytime.")