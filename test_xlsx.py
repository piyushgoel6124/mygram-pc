import os
import sys

# Add current dir to path so we can import our modules
sys.path.append(os.getcwd())

from scraper_engine import scrape_multiple_urls

def test_excel_direct():
    print("--- MyGram Excel Test Tool ---")
    
    # Check if openpyxl is installed
    try:
        import openpyxl
        print("[OK] openpyxl is installed.")
    except ImportError:
        print("[ERROR] openpyxl is NOT installed. Run: pip install openpyxl")
        return

    # You can change this to a real path to test your actual file
    test_file = r"D:\Downloads\Book (84).xlsx" 
    
    if not os.path.exists(test_file):
        print(f"[!] {test_file} not found. Please create it or change the path in this script.")
        return

    print(f"Reading {test_file}...")
    content = []
    wb = openpyxl.load_workbook(test_file, data_only=True)
    for sheet in wb.worksheets:
        for row in sheet.iter_rows(values_only=True):
            for cell in row:
                if cell and isinstance(cell, str): content.append(cell)

    links = [line.strip() for line in content if isinstance(line, str) and "instagram.com" in line]
    print(f"Found {len(links)} links in Excel file.")
    
    if links:
        print("Starting direct scrape of first 2 links as a test...")
        results = scrape_multiple_urls(links[:2])
        print(f"Test Finished. Collected {len(results)} reels.")
        
        import json
        print("\n--- Scraped Data Sample ---")
        print(json.dumps(results, indent=4))

if __name__ == "__main__":
    test_excel_direct()
