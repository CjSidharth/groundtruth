import sys
import os
import json
import requests
from pathlib import Path

# --- Imports for WeasyPrint, Pygments, and Markdown ---
from weasyprint import HTML, CSS
from pygments import highlight
from pygments.lexers import get_lexer_by_name, TextLexer
from pygments.formatters import HtmlFormatter
from pygments.util import ClassNotFound
import markdown
# --- END Imports ---

# --- CONFIGURATION ---
DEPLOY_DIR = "qb_deploy"
# --- END CONFIGURATION ---

# --- FONT HANDLING ---
FONT_FILE = "RobotoMonoNerdFont-Regular.ttf"
FONT_NAME = "RobotoMonoNerd"

def check_and_download_font():
    """Checks if the font file exists and downloads it if not."""
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
            return True
        except Exception as e:
            print(f"    - ❌  Error downloading font: {e}")
            print("    - Proceeding without custom font. Some characters may not render correctly.")
            return False
    return True
# --- END FONT HANDLING ---

def create_pdf_direct(subject, chapter):
    print(f"🚀 Starting PDF generation for Subject: '{subject}'...")
    json_path = os.path.join(DEPLOY_DIR, "flashcard_data.json")
    image_dir = Path(DEPLOY_DIR, "images").resolve()

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

    # Filter Flashcards
    if chapter.lower() == 'all':
        # Sort by chapter so they appear in order
        filtered_flashcards = sorted(all_flashcards, key=lambda x: x['chapter'])
    else:
        filtered_flashcards = [c for c in all_flashcards if c['chapter'].lower() == chapter.lower()]

    if not filtered_flashcards: sys.exit(f"❌ Error: No flashcards found for the selection.")

    print(f"✅ Found {len(filtered_flashcards)} flashcards to process.")

    font_available = check_and_download_font()

    # --- STYLE CONFIGURATION ---
    # Using 'friendly' or 'colorful' usually looks better on white PDF paper than 'monokai'
    # but 'monokai' is good if you want a dark code block background.
    html_formatter = HtmlFormatter(style='monokai', cssclass='highlight')
    pygments_css = html_formatter.get_style_defs('.highlight')

    main_css = f"""
        @font-face {{
            font-family: '{FONT_NAME}';
            src: url('{Path(FONT_FILE).resolve().as_uri()}');
        }}

        html {{
            font-family: {'"{}"'.format(FONT_NAME) if font_available else 'monospace'};
            font-size: 11pt;
            line-height: 1.5;
            color: #333;
        }}
        
        body {{ margin: 0; }}

        @page {{
            size: A4;
            margin: 2cm;

            @top-center {{
                content: "{subject}";
                font-style: italic;
                font-size: 9pt;
                color: #666;
            }}
            
            @bottom-right {{
                content: "Page " counter(page);
                font-size: 9pt;
                color: #666;
            }}
        }}

        .chapter-title {{
            font-size: 24pt;
            text-align: center;
            color: #333;
            page-break-before: always;
            margin-bottom: 2cm;
            border-bottom: 2px solid #333;
            padding-bottom: 10px;
        }}

        .card {{
            page-break-before: always;
            page-break-inside: avoid;
        }}
        
        .question {{
            font-size: 14pt;
            font-weight: bold;
            color: #000;
            margin-bottom: 0.5cm;
            background-color: #f0f0f0;
            padding: 10px;
            border-left: 5px solid #333;
        }}

        .section-title {{
            font-size: 12pt;
            font-weight: bold;
            color: #444;
            border-bottom: 1px solid #ccc;
            padding-bottom: 2px;
            margin-top: 1cm;
            margin-bottom: 0.5cm;
        }}

        img {{
            max-width: 100%;
            height: auto;
            border: 1px solid #ddd;
            display: block;
            margin-top: 0.5cm;
        }}
        
        .note-content h1, .note-content h2, .note-content h3 {{
            margin-top: 1.5em;
            margin-bottom: 0.5em;
            line-height: 1.2;
        }}
        
        .note-content ul {{ padding-left: 1.5em; margin-bottom: 1em; }}
        .note-content ol {{ padding-left: 1.5em; }}
        .note-content li::marker {{ content: "• "; font-size: 1.2em; color: #555; }}
        .note-content ol li::marker {{ content: normal; }}

        .note-content code {{
            background-color: #eee;
            padding: 2px 5px;
            border-radius: 3px;
            font-size: 0.9em;
            color: #333;
        }}
        
        /* Inject Pygments CSS here */
        {pygments_css}
        
        /* Style the container created by Pygments */
        .highlight {{
            background-color: #272822; /* Monokai background */
            border-radius: 5px;
            padding: 1em;
            margin-bottom: 1em;
            border: 1px solid #444;
        }}
        
        .highlight pre {{
            margin: 0;
            font-size: 9.5pt;
            white-space: pre-wrap !important;
            word-wrap: break-word !important;
            font-family: '{FONT_NAME}', monospace;
            line-height: 1.4;
        }}
    """

    html_parts = []
    current_chapter = None

    for i, card_data in enumerate(filtered_flashcards):
        print(f"    - Processing question {i+1} of {len(filtered_flashcards)}...")

        if card_data['chapter'] != current_chapter:
            current_chapter = card_data['chapter']
            html_parts.append(f'<h1 class="chapter-title">Chapter: {current_chapter}</h1>')

        html_parts.append('<div class="card">')
        html_parts.append(f"<div class='question'>Q: {card_data['question']} ({card_data['marks']}m)</div>")

        # --- NOTES SECTION ---
        if card_data.get('note'):
            html_parts.append("<div class='section-title'>Notes:</div>")
            note_html = markdown.markdown(card_data['note'], extensions=['fenced_code', 'tables'])
            html_parts.append(f"<div class='note-content'>{note_html}</div>")

        # --- CODE SECTION (UPDATED FOR LANGUAGES) ---
        if card_data.get('code') and card_data['code'].strip():
            # 1. Get the language from JSON, default to 'python'
            lang = card_data.get('language', 'python')
            
            # 2. Display the language name nicely
            html_parts.append(f"<div class='section-title'>Code ({lang}):</div>")
            
            code_text = card_data['code'].replace('\u200b', '') # Remove zero-width spaces
            
            try:
                # 3. Dynamic Lexer Selection
                lexer = get_lexer_by_name(lang, stripall=True)
            except ClassNotFound:
                print(f"      ⚠️ Warning: Lexer for '{lang}' not found. Using text.")
                lexer = TextLexer()
            
            # 4. Generate HTML
            highlighted_code = highlight(code_text, lexer, html_formatter)
            html_parts.append(highlighted_code)

        # --- IMAGE SECTION ---
        image_val = card_data.get('image', [])
        
        # Normalize to list
        if isinstance(image_val, str):
            image_list = [image_val] if image_val.strip() else []
        elif isinstance(image_val, list):
            image_list = image_val
        else:
            image_list = []
        
        # Filter valid images
        valid_images = []
        for image_file in image_list:
            if image_file and image_file.strip():
                image_path = Path(image_dir, image_file)
                if image_path.is_file():
                    valid_images.append(image_path)
        
        # Render valid images
        if valid_images:
            title = "Outputs:" if len(valid_images) > 1 else "Output:"
            html_parts.append(f"<div class='section-title'>{title}</div>")
            for image_path in valid_images:
                # Use as_uri() for proper file:// path handling in WeasyPrint
                html_parts.append(f'<img src="{image_path.as_uri()}" alt="{image_path.name}">')
        
        html_parts.append('</div>') # End Card

    final_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>{subject} - {chapter}</title>
    </head>
    <body>
        {''.join(html_parts)}
    </body>
    </html>
    """

    print("\n🚀 Rendering PDF with WeasyPrint...")
    html = HTML(string=final_html, base_url=__file__)
    css = CSS(string=main_css)
    html.write_pdf(output_pdf_path, stylesheets=[css])

    print(f"\n✨ Success! ✨")
    print(f"PDF has been saved to: {output_pdf_path}")

if __name__ == "__main__":
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        sys.exit("\n❌ Error: Invalid arguments.\nUsage: python generate_pdf.py <subject> [chapter|all]")

    subject_name = sys.argv[1]
    chapter_name = sys.argv[2] if len(sys.argv) == 3 else 'all'

    create_pdf_direct(subject_name, chapter_name)
