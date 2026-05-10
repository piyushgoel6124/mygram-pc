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
        print(f"Submitting {test_file} to local server API...")
        
        import requests
        import hashlib
        
        # Test Credentials (Matching your users.json)
        username = "piyush"
        password = "12345"
        device_id = "4c3f62d3727795b1"
        api_secret = "MyGram_Security_Key_2026" # From config.py
        
        # Generate signature exactly like the app
        sig_data = f"{username}{device_id}{api_secret}"
        sig = hashlib.md5(sig_data.encode()).hexdigest()
        
        url = "http://localhost:5030/upload"
        
        files = {'file': open(test_file, 'rb')}
        data = {
            'username': username,
            'password': password,
            'device_id': device_id,
            'sig': sig
        }
        
        try:
            print(f"Sending request to {url}...")
            response = requests.post(url, data=data, files=files)
            print(f"Status Code: {response.status_code}")
            print("Server Response:")
            print(response.text)
            
            if response.status_code == 200:
                print("\n[SUCCESS] Server accepted the Excel file!")
                res_data = response.json()
                req_id = res_data.get("request_id")
                
                print(f"\n--- Monitoring Progress (Request ID: {req_id}) ---")
                
                # Poll the stream for logs
                stream_url = f"http://localhost:5030/stream?id={req_id}&username={username}&device_id={device_id}&sig={sig}"
                
                import time
                finished = False
                while not finished:
                    try:
                        s_res = requests.get(stream_url, timeout=10)
                        lines = s_res.text.strip().split("\n")
                        for line in lines:
                            if "data:" in line:
                                log_msg = line.replace("data:", "").strip()
                                if log_msg == "__FINISHED__":
                                    finished = True
                                    print("\n[FINISH] Scraper signaled completion.")
                                    break
                                elif log_msg:
                                    print(f"Server Log: {log_msg}")
                    except Exception as stream_err:
                        # Stream might timeout during long scrolls, just retry
                        pass
                    
                    if not finished:
                        time.sleep(2)

                # Download the result
                print("\nDownloading result CSV...")
                dl_url = f"http://localhost:5030/download/{req_id}"
                dl_res = requests.get(dl_url)
                
                if dl_res.status_code == 200:
                    filename = f"bulk_result_{req_id[:8]}.csv"
                    with open(filename, "wb") as f:
                        f.write(dl_res.content)
                    print(f"[DONE] File saved as: {filename}")
                else:
                    print(f"[ERROR] Could not download file. Status: {dl_res.status_code}")

            else:
                print("\n[FAILED] Server rejected the request. Check logs for details.")
        except Exception as e:
            print(f"[ERROR] Could not connect to server: {e}")

if __name__ == "__main__":
    test_excel_direct()
