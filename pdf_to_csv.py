import sys
import os
import re
import csv
import pdfplumber

# Regex definitions
ROW_RE = re.compile(r'^(\d{1,3})\.?$') # Handles "1" and "1." 
CHAPTER_RE = re.compile(r'^CHAPTER\s*:\s*(\d+)', re.IGNORECASE)

def group_lines(words, tol=4):
    """Group words into visual lines based on their 'top' (y) coordinate."""
    words = sorted(words, key=lambda w: (round(w['top'] / tol), w['x0']))
    lines, cur, cur_top = [], [], None
    for w in words:
        if cur_top is None or abs(w['top'] - cur_top) > tol:
            if cur:
                lines.append(cur)
            cur, cur_top = [w], w['top']
        else:
            cur.append(w)
    if cur:
        lines.append(cur)
    return lines

def clean_question(text):
    text = re.sub(r'\(\s*([A-Za-z]{3,9})\s*-\s*(\d{4})\s*\)', r'(\1-\2)', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def parse_pdf(pdf_path):
    rows = []
    chapter_num = None
    chapter_title = None
    category = "Short Questions" # Default fallback
    awaiting_title = False   
    current_row = None       

    def flush_row():
        nonlocal current_row
        if current_row and current_row['question_parts']:
            question = clean_question(" ".join(current_row['question_parts']))
            if question:
                rows.append({
                    "chapter_num": chapter_num,
                    "chapter_title": chapter_title,
                    "category": category,
                    "sr_no": current_row['sr_no'],
                    "question": question,
                    "marks": current_row['marks'],
                    "co": current_row['co'],
                    "rbt": current_row['rbt'],
                })
        current_row = None

    def process_words_for_metadata(word_list):
        """Extracts Marks, CO, and RBT from the right side of the page."""
        meta_words = [w for w in word_list if w['x0'] > 350]
        q_words = [w for w in word_list if w['x0'] <= 350]
        
        marks_val, co_val, rbt_val = None, None, None
        
        for w in sorted(meta_words, key=lambda x: x['x0']):
            tok = w['text'].strip()
            if not marks_val and re.match(r'^\d{1,2}$', tok):
                marks_val = tok
            elif not co_val and re.match(r'^CO\d+$', tok, re.IGNORECASE):
                co_val = tok.upper()
            elif not rbt_val and re.match(r'^[A-Z]{2}$', tok, re.IGNORECASE):
                rbt_val = tok.upper()
            elif re.match(r'^\*?L\d+-?L?\d*$', tok, re.IGNORECASE):
                pass # Ignore L levels
            else:
                q_words.append(w) # Wasn't metadata, put it back in question
                
        return sorted(q_words, key=lambda x: x['x0']), marks_val, co_val, rbt_val

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            H = page.height
            words = page.extract_words()
            words = [w for w in words if 60 < w['top'] < H - 60]
            
            for line in group_lines(words):
                line = sorted(line, key=lambda w: w['x0'])
                text = " ".join(w['text'] for w in line)
                text_upper = text.strip().upper()

                # 1. Check for Chapter Titles
                if awaiting_title:
                    chapter_title = text.strip()
                    awaiting_title = False
                    continue

                m = CHAPTER_RE.match(text)
                if m:
                    flush_row()
                    chapter_num = m.group(1)
                    awaiting_title = True
                    category = "Short Questions" 
                    continue

                # 2. Check for Category Changes (Bulletproof)
                if "SHORT QUESTION" in text_upper or "DESCRIPTIVE QUESTION" in text_upper:
                    flush_row()
                    category = "Short Questions" if "SHORT" in text_upper else "Descriptive Questions"
                    continue
                
                # Ignore Table Headers completely
                if text.strip() in ("QUESTIONS",) or re.match(r'^(No\.|Sr\.)', text.strip()):
                    continue  

                # 3. Process Questions
                first_word = line[0]
                m_row = ROW_RE.match(first_word['text'])

                if m_row:
                    flush_row()
                    sr_no = m_row.group(1) # Strips the dot if present (e.g. "1." -> "1")
                    rest = line[1:]
                    
                    q_words, m_val, c_val, r_val = process_words_for_metadata(rest)
                    
                    current_row = {
                        "sr_no": sr_no,
                        "question_parts": [w['text'] for w in q_words],
                        "marks": m_val or "",
                        "co": c_val or "",
                        "rbt": r_val or "",
                    }
                elif current_row is not None:
                    # Continuation of a question
                    q_words, m_val, c_val, r_val = process_words_for_metadata(line)
                    current_row['question_parts'].extend([w['text'] for w in q_words])
                    
                    if m_val and not current_row['marks']: current_row['marks'] = m_val
                    if c_val and not current_row['co']: current_row['co'] = c_val
                    if r_val and not current_row['rbt']: current_row['rbt'] = r_val
                    
        flush_row()
    return rows

def sanitize_filename(title, num):
    title = re.sub(r'[^A-Za-z0-9 ]', '', title or '').strip()
    title = re.sub(r'\s+', '_', title)[:60]
    return f"Chapter_{num}_{title}" if title else f"Chapter_{num}"

def write_csvs(rows, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    by_chapter = {}
    for r in rows:
        by_chapter.setdefault(r['chapter_num'], []).append(r)

    written = []
    for chap_num, chap_rows in sorted(by_chapter.items(), key=lambda kv: int(kv[0])):
        title = chap_rows[0]['chapter_title']
        fname = sanitize_filename(title, chap_num) + ".csv"
        fpath = os.path.join(out_dir, fname)
        with open(fpath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["Category", "Sr. No.", "Question", "Marks", "CO", "RBT Level"])
            for r in chap_rows:
                writer.writerow([r['category'], r['sr_no'], r['question'],
                                  r['marks'], r['co'], r['rbt']])
        written.append((fpath, len(chap_rows), title))
    return written

if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("Usage: python3 pdf_to_csv.py <input.pdf> <output_dir>")
    pdf_path, out_dir = sys.argv[1], sys.argv[2]
    rows = parse_pdf(pdf_path)
    print(f"Parsed {len(rows)} questions total.")
    written = write_csvs(rows, out_dir)
    for fpath, n, title in written:
        print(f"  {fpath}  ({n} questions) - {title}")