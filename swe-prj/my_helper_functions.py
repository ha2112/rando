import re

# --- 2. Cleaning Function ---
def clean_presentation_text(text):
    """Cleans text extracted from presentations."""
    
    # Remove presentation headers/footers (Vietnamese and English)
    text = re.sub(r"TRƯỜNG CÔNG NGHỆ THÔNG TIN VÀ TRUYỀN THÔNG", "", text)
    text = re.sub(r"School of Information and Communication Technology", "", text)
    
    # Remove dates like '3/12/2025'
    text = re.sub(r"\d{1,2}/\d{1,2}/\d{4}", "", text)
    
    # Remove page/slide numbers (often appear alone or with context like 'Link Layer: 6-XX')
    text = re.sub(r"Link Layer: \d+-\d+", "", text) # Remove specific "Link Layer: X-Y" references
    text = re.sub(r"^\d+\s*$", "", text, flags=re.MULTILINE) # Remove lines containing only numbers (slide numbers)
    text = re.sub(r"\n\d+\n", "\n", text) # Remove slide numbers appearing between lines
    text = re.sub(r"\s+\d+\s*$", "", text, flags=re.MULTILINE) # Remove trailing numbers on lines
    
    # Replace common unicode symbols used for bullets/arrows with Markdown list item
    text = re.sub(r"[\uf0a7\uf06c\uf0e8\uf0e0\uf0df❖]", "*", text) # Replace with standard Markdown bullet
    
    # Normalize bullet points preceded by '*' or '•'
    text = re.sub(r"\n[•*]\s*", "\n* ", text) 
    
    # Remove excessive newlines
    text = re.sub(r"\n{3,}", "\n\n", text) # Replace 3 or more newlines with just two
    
    # Remove leading/trailing whitespace from each line
    text = "\n".join([line.strip() for line in text.splitlines()])
    
    # Remove initial quote if present
    if text.startswith("'"):
        text = text[1:]
    if text.endswith("'"):
        text = text[:-1]
        
    # Remove lines that became empty after cleaning
    text = "\n".join([line for line in text.splitlines() if line.strip()])
    
    return text