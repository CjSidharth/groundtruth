import sys
import os
import json
from fpdf import FPDF, XPos, YPos
from PIL import Image
import requests

# --- CONFIGURATION ---
DEPLOY_DIR = "qb_deploy"
# --- END CONFIGURATION ---

# --- FONT HANDLING (CHANGED) ---
# 1. Updated the font file name as requested
FONT_FILE = "RobotoMonoNerdFont-Regular.ttf"
# 2. Updated the internal font family name
FONT_NAME = "RobotoMonoNerd" 
UNICODE_FONT_LOADED = False

def setup_font(pdf):
    global UNICODE_FONT_LOADED
    if not os.path.exists(FONT_FILE):
        print(f"   - Font '{FONT_FILE}' not found. Downloading...")
        try:
            # 3. Updated the URL to a reliable source for Roboto Mono Nerd Font
            url = "https://github.com/ryanoasis/nerd-fonts/raw/master/patched-fonts/RobotoMono/Regular/RobotoMonoNerdFont-Regular.ttf"
            response = requests.get(url, stream=True)
            response.raise_for_status()
            with open(FONT_FILE, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            print("   - Font downloaded successfully.")
        except Exception as e:
            print(f"   - ❌  Error downloading font: {e}")
            print("   - Proceeding with standard font. Some characters may not render correctly.")
            return

    try:
        # 4. Added the new font with the new family name
        pdf.add_font(FONT_NAME, "", FONT_FILE)
        pdf.add_font(FONT_NAME, "B", FONT_FILE)
        pdf.add_font(FONT_NAME, "I", FONT_FILE)
        UNICODE_FONT_LOADED = True
        print(f"   - Unicode font '{FONT_NAME}' loaded successfully.")
    except Exception as e:
        print(f"   - ❌  Error adding font to PDF: {e}")
        print("   - Proceeding with standard font. Some characters may not render correctly.")

def get_font_name():
    return FONT_NAME if UNICODE_FONT_LOADED else "helvetica"
# --- END FONT HANDLING CHANGES ---

def add_image_safely(pdf, image_path):
    try:
        with Image.open(image_path) as img:
            img_width, img_height = img.size
        max_width = pdf.w - pdf.l_margin - pdf.r_margin
        max_height = pdf.h - pdf.t_margin - pdf.b_margin
        width_ratio = max_width / img_width
        height_ratio = max_height / img_height
        scale_ratio = min(width_ratio, height_ratio)
        final_width = img_width * scale_ratio
        final_height = img_height * scale_ratio
        remaining_space = pdf.h - pdf.get_y() - pdf.b_margin
        if final_height > remaining_space:
            pdf.add_page()
        pdf.image(image_path, w=final_width, h=final_height)
        pdf.ln(2)
    except Exception as e:
        print(f"   - ⚠️  Warning: Could not process image {os.path.basename(image_path)}. Error: {e}")

class PDF(FPDF):
    def header(self):
        self.set_font(get_font_name(), 'B', 12)
        self.cell(0, 10, f'{SUBJECT_NAME} - {CHAPTER_NAME}', border=0, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
        self.ln(10)

    def footer(self):
        self.set_y(-15)
        self.set_font(get_font_name(), 'I', 8)
        self.cell(0, 10, f'Page {self.page_no()}', border=0, new_x=XPos.RIGHT, new_y=YPos.TOP, align='C')

def create_pdf_direct(subject, chapter):
    print(f"🚀 Starting PDF generation for Subject: '{subject}'...")
    json_path = os.path.join(DEPLOY_DIR, "flashcard_data.json")
    image_dir = os.path.join(DEPLOY_DIR, "images")

    if chapter.lower() == 'all':
        output_filename = f"{subject}_ALL_CHAPTERS_notes.pdf"
        print("   - Mode: All Chapters")
    else:
        output_filename = f"{subject}_{chapter}_notes.pdf"
        print(f"   - Mode: Single Chapter ('{chapter}')")
    output_pdf_path = os.path.join(DEPLOY_DIR, output_filename)

    if not os.path.exists(json_path): sys.exit(f"❌ Error: '{json_path}' not found.")
    
    with open(json_path, 'r', encoding='utf-8') as f:
        all_flashcards = json.load(f)
    
    if chapter.lower() == 'all':
        filtered_flashcards = sorted(all_flashcards, key=lambda x: x['chapter'])
    else:
        filtered_flashcards = [c for c in all_flashcards if c['chapter'].lower() == chapter.lower()]

    if not filtered_flashcards: sys.exit(f"❌ Error: No flashcards found for the selection.")
    
    print(f"✅ Found {len(filtered_flashcards)} flashcards to process.")

    global SUBJECT_NAME
    SUBJECT_NAME = subject
    
    pdf = PDF()
    setup_font(pdf)
    pdf.set_auto_page_break(auto=True, margin=15)
    
    current_chapter = None

    for i, card_data in enumerate(filtered_flashcards):
        print(f"   - Processing question {i+1} of {len(filtered_flashcards)}...")
        
        global CHAPTER_NAME
        if card_data['chapter'] != current_chapter:
            current_chapter = card_data['chapter']
            CHAPTER_NAME = current_chapter
            pdf.add_page()
            pdf.set_font(get_font_name(), 'B', 24)
            pdf.multi_cell(0, 15, f"Chapter: {current_chapter}", align='C')

        pdf.add_page()
        
        pdf.set_font(get_font_name(), 'B', 14)
        pdf.multi_cell(0, 8, f"Q: {card_data['question']} ({card_data['marks']}m)")
        pdf.ln(5)

        if card_data['note']:
            pdf.set_font(get_font_name(), 'B', 12)
            pdf.cell(0, 10, "Notes:", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_font(get_font_name(), '', 11)
            note_text = card_data['note']
            if not UNICODE_FONT_LOADED:
                note_text = note_text.encode('latin-1', 'replace').decode('latin-1')
            pdf.multi_cell(0, 8, note_text)
            pdf.ln(5)

        if card_data['code']:
            pdf.set_font(get_font_name(), 'B', 12)
            pdf.cell(0, 10, "Code:", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            # --- CHANGE: Use our new monospaced font for code ---
            pdf.set_font(get_font_name(), '', 10)
            code_text = card_data['code']
            if not UNICODE_FONT_LOADED:
                 code_text = code_text.encode('latin-1', 'replace').decode('latin-1')
            pdf.multi_cell(0, 5, code_text)
            pdf.ln(5)

        image_list = card_data.get('image', [])
        if isinstance(image_list, str): image_list = [image_list]

        if image_list:
            pdf.set_font(get_font_name(), 'B', 12)
            pdf.cell(0, 10, "Outputs:" if len(image_list) > 1 else "Output:", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            for image_file in image_list:
                if image_file and image_file.strip():
                    image_path = os.path.join(image_dir, image_file)
                    if os.path.isfile(image_path):
                        add_image_safely(pdf, image_path)

    pdf.output(output_pdf_path)
    print(f"\n✨ Success! ✨")
    print(f"PDF has been saved to: {output_pdf_path}")

if __name__ == "__main__":
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        sys.exit("\n❌ Error: Invalid arguments.")
    
    subject_name = sys.argv[1]
    chapter_name = sys.argv[2] if len(sys.argv) == 3 else 'all'
    
    create_pdf_direct(subject_name, chapter_name)
