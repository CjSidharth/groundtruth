import pandas as pd
import json
import os
import re
import sys
import shutil

# --- CONFIGURATION ---
DATA_DIR = "data"
PROGRESS_DIR = "progress"
DEPLOY_DIR = "qb_deploy" 
STATIC_FILES = ["index.html", "style.css", "script.js"] 
DEPLOY_IMG_DIR = os.path.join(DEPLOY_DIR, "images")
# --- END CONFIGURATION ---

if len(sys.argv) < 2:
    print("\n❌ Error: Missing subject name.")
    print("   Usage: python3 create_flashcards.py <SubjectFolderName>")
    sys.exit(1) 

SUBJECT_TO_EXPORT = sys.argv[1]
print(f"\n🚀 Starting export process for subject: '{SUBJECT_TO_EXPORT}'...")

if not os.path.exists(DEPLOY_DIR):
    os.makedirs(DEPLOY_DIR)
    print(f"✅ Created deployment directory: '{DEPLOY_DIR}'")

if not os.path.exists(DEPLOY_IMG_DIR):
    os.makedirs(DEPLOY_IMG_DIR)
    print(f"✅ Created deployment image directory: '{DEPLOY_IMG_DIR}'")

subject_path = os.path.join(DATA_DIR, SUBJECT_TO_EXPORT)
progress_file_path = os.path.join(PROGRESS_DIR, f"{SUBJECT_TO_EXPORT}_progress.json")
output_json_path = os.path.join(DEPLOY_DIR, "flashcard_data.json")

def generate_stable_id(row):
    sr_no_col = str(row.get('Sr. No.', row.name))
    question_slice = re.sub(r'\W+', '', str(row['Question'])[:30])
    return f"{row['Chapter']}_{sr_no_col}_{question_slice}"

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

progress_data = {}
try:
    with open(progress_file_path, 'r') as f:
        progress_data = json.load(f)
    print(f"✅ Successfully loaded progress data from '{progress_file_path}'.")
except (FileNotFoundError, json.JSONDecodeError):
    print(f"⚠️ Warning: Progress file not found or is empty at '{progress_file_path}'. Starting without any saved progress.")

# Get all parts of the progress data
notes = progress_data.get("notes", {})
codes = progress_data.get("code", {})
images = progress_data.get("images", {})

# We consider any question with a note, code, or image entry as potentially exportable.
exportable_qids = set(notes.keys()) | set(codes.keys()) | set(images.keys())
flashcard_df = master_df[master_df['StableID'].isin(exportable_qids)].copy()

flashcard_list = []
for _, row in flashcard_df.iterrows():
    stable_id = row['StableID']
    
    # --- THIS IS THE FIX ---
    # Check if there is any content (note, code, or image) to justify creating a flashcard.
    note_content = notes.get(stable_id, "").strip()
    code_content = codes.get(stable_id, "").strip()
    image_content = images.get(stable_id, "").strip()

    if note_content or code_content or image_content:
        # --- END FIX ---
        
        flashcard_data = {
            "question": row['Question'],
            "note": notes.get(stable_id, ""), # Use .get() to avoid errors if key is missing
            "code": codes.get(stable_id, ""),
            "image": images.get(stable_id, ""),
            "chapter": row['Chapter'],
            "marks": int(row.get('Marks', 0))
        }
        flashcard_list.append(flashcard_data)

        if image_content:
            source_image_path = os.path.join(subject_path, "images", image_content)
            if os.path.exists(source_image_path):
                shutil.copy(source_image_path, DEPLOY_IMG_DIR)
                print(f"🖼️  Copied image '{image_content}'")
            else:
                print(f"⚠️ Warning: Image file not found at '{source_image_path}'")

with open(output_json_path, 'w') as f:
    json.dump(flashcard_list, f, indent=2)
print(f"✅ Created '{output_json_path}' with {len(flashcard_list)} flashcards.")

for file_name in STATIC_FILES:
    if os.path.exists(file_name):
        shutil.copy(file_name, DEPLOY_DIR)
        print(f"✅ Copied '{file_name}' to '{DEPLOY_DIR}'")
    else:
        print(f"⚠️ Warning: Static file '{file_name}' not found in the root directory. It was not copied.")

print(f"\n✨ Success! ✨")
print(f"Your deployable static site is ready in the '{DEPLOY_DIR}' folder.")
