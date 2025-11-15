import sys
import os
import json
from fpdf import FPDF, XPos, YPos
from PIL import Image
import requests

# --- NEW: Imports for Syntax Highlighting ---
from pygments import highlight
from pygments.lexers import get_lexer_by_name, TextLexer
from pygments.formatters import HtmlFormatter
from pygments.util import ClassNotFound
# --- END NEW ---

# --- CONFIGURATION ---
DEPLOY_DIR = "qb_deploy"
# --- END CONFIGURATION ---

# --- FONT HANDLING ---
FONT_FILE = "RobotoMonoNerdFont-Regular.ttf"
FONT_NAME = "RobotoMonoNerd" 
UNICODE_FONT_LOADED = False

def setup_font(pdf):
    global UNICODE_FONT_LOADED
    if not os.path.exists(FONT_FILE):
        print(f"    - Font '{FONT_FILE}' not found. Downloading...")
        try:
            url = "https://github.com/ryanoasis/nerd-fonts/raw/master/patched-fonts/RobotoMono/Regular/RobotoMonoNerdFont-Regular.ttf"
            response = requests.get(url, stream=True)
            response.raise_for_status()
            with open(FONT_FILE, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            print("    - Font downloaded successfully.")
        except Exception as e:
            print(f"    - ❌  Error downloading font: {e}")
            print("    - Proceeding with standard font. Some characters may not render correctly.")
            return

    try:
        pdf.add_font(FONT_NAME, "", FONT_FILE)
        pdf.add_font(FONT_NAME, "B", FONT_FILE)
        pdf.add_font(FONT_NAME, "I", FONT_FILE)
        UNICODE_FONT_LOADED = True
        print(f"    - Unicode font '{FONT_NAME}' loaded successfully.")
    except Exception as e:
        print(f"    - ❌  Error adding font to PDF: {e}")
        print("    - Proceeding with standard font. Some characters may not render correctly.")

def get_font_name():
    return FONT_NAME if UNICODE_FONT_LOADED else "helvetica"
# --- END FONT HANDLING ---


# --- MODIFIED: Improved Image Resizing ---
def add_image_safely(pdf, image_path):
    """
    Adds an image to the PDF, scaling it to fit the available width and
    remaining height on the page. Adds a new page if space is insufficient.
    """
    try:
        with Image.open(image_path) as img:
            img_width, img_height = img.size
            
            # 1. Define max width and check remaining height
            max_width = pdf.w - pdf.l_margin - pdf.r_margin
            remaining_space = pdf.h - pdf.get_y() - pdf.b_margin
            
            # 2. If not enough space for a reasonably sized image, add a new page
            #    (e.g., if less than 30mm is left)
            MIN_IMAGE_HEIGHT_REMAINING = 30 
            if remaining_space < MIN_IMAGE_HEIGHT_REMAINING:
                pdf.add_page()
                # After adding a page, the remaining space is the total printable height
                remaining_space = pdf.h - pdf.t_margin - pdf.b_margin

            # 3. Set max_height to the *actual* available space
            max_height = remaining_space
            
            # 4. Calculate scaling ratio based on *both* width and height
            width_ratio = max_width / img_width
            height_ratio = max_height / img_height
            
            # Use the smaller ratio to fit, and ensure we don't scale *up*
            scale_ratio = min(width_ratio, height_ratio, 1.0) 
            
            final_width = img_width * scale_ratio
            final_height = img_height * scale_ratio

            # 5. Add the correctly scaled image
            pdf.image(image_path, w=final_width, h=final_height)
            pdf.ln(2)
            
    except Exception as e:
        print(f"    - ⚠️  Warning: Could not process image {os.path.basename(image_path)}. Error: {e}")
# --- END MODIFIED ---


class PDF(FPDF):
    def header(self):
        self.set_font(get_font_name(), 'B', 12)
        # Use a global CHAPTER_NAME that gets updated during processing
        global CHAPTER_NAME
        title = f'{SUBJECT_NAME} - {CHAPTER_NAME}' if CHAPTER_NAME else SUBJECT_NAME
        self.cell(0, 10, title, border=0, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
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
        print("    - Mode: All Chapters")
    else:
        output_filename = f"{subject}_{chapter}_notes.pdf"
        print(f"    - Mode: Single Chapter ('{chapter}')")
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
    
    # --- NEW: Initialize CHAPTER_NAME as None ---
    global CHAPTER_NAME
    CHAPTER_NAME = None 
    
    pdf = PDF()
    setup_font(pdf)
    pdf.set_auto_page_break(auto=True, margin=15)
    
    # --- NEW: Setup the HTML formatter for pygments ---
    # This formatter uses inline styles (noclasses=True) which fpdf2 understands
    html_formatter = HtmlFormatter(full=False, noclasses=True, style='default')
    
    current_chapter = None

    for i, card_data in enumerate(filtered_flashcards):
        print(f"    - Processing question {i+1} of {len(filtered_flashcards)}...")
        
        if card_data['chapter'] != current_chapter:
            current_chapter = card_data['chapter']
            CHAPTER_NAME = current_chapter # Update global for header
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
            
            # --- MODIFIED: Added markdown=True ---
            pdf.multi_cell(0, 8, note_text, markdown=True)
            pdf.ln(5)

        if card_data['code']:
            pdf.set_font(get_font_name(), 'B', 12)
            pdf.cell(0, 10, "Code:", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            
            pdf.set_font(get_font_name(), '', 10) # Set base font
            
            code_text = card_data['code']
            
            # --- FIX 1: Strip invisible characters that cause crashes ---
            code_text = code_text.replace('\u200b', '')
            
            if not UNICODE_FONT_LOADED:
                    code_text = code_text.encode('latin-1', 'replace').decode('latin-1')
            
            try:
                lexer = get_lexer_by_name("python") 
            except ClassNotFound:
                lexer = TextLexer()

            highlighted_code = highlight(code_text, lexer, html_formatter)
            highlighted_code_with_br = highlighted_code.replace('\n', '<br>')

            # --- FIX 2: Force HTML parser to use our Unicode font ---
            # This wraps the code in a div that explicitly sets the font,
            # overriding the 'courier' default.
            font_family = get_font_name()
            html_to_render = f'<div style="font-family: \'{font_family}\';">{highlighted_code_with_br}</div>'
            
            # Render the HTML
            pdf.write_html(html_to_render)
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
                        # Use the new, improved image function
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
