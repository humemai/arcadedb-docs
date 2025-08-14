import html2text
import re
import sys

def clean_html_to_markdown(html_file, output_file):
    h = html2text.HTML2Text()
    h.ignore_links = True
    h.ignore_images = True
    h.ignore_emphasis = False
    h.body_width = 0
    h.ignore_tables = True
    h.ignore_anchors = True
    h.escape_all = False
    h.mark_code = False
    
    with open(html_file, 'r', encoding='utf-8') as f:
        html_content = f.read()
    
    markdown = h.handle(html_content)
    
    # Clean up the problematic syntax
    # Remove ::: blocks
    markdown = re.sub(r':::\s*{[^}]*}\s*\n', '', markdown)
    markdown = re.sub(r':::\s*\n', '', markdown)
    
    # Remove code block annotations
    markdown = re.sub(r'``` {\.rouge \.highlight}', '```', markdown)
    
    # Remove other markup artifacts
    markdown = re.sub(r'\[!\[edit\].*?\]\{\.image\}.*?\n', '', markdown)
    markdown = re.sub(r'\{[^}]*\}', '', markdown)  # Remove remaining {attributes}
    
    # Clean up multiple newlines
    markdown = re.sub(r'\n\s*\n\s*\n', '\n\n', markdown)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(markdown)
    
    print(f"Cleaned HTML converted to {output_file}")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python clean_html_to_md.py input.html output.md")
        sys.exit(1)
    
    clean_html_to_markdown(sys.argv[1], sys.argv[2])