import sys
from app_server import start_server
from session_manager import (
    get_all_sessions, login_and_save_session, 
    test_headless_session, fix_session_headed
)
from scraper_engine import scrape_reel, scrape_profile, scrape_since_reel

def print_menu():
    print("\n" + "="*40)
    print("   MyGram Instagram Scraper CLI   ")
    print("="*40)
    print("1. Login and Save Session (Headed)")
    print("2. Test Session (Headless)")
    print("3. Scrape Single Reel Data (Headless)")
    print("4. Bulk Scrape Profile Reels (Headless)")
    print("5. Scrape All Newer Reels (CLI Mode)")
    print("6. Start API Server (Flask + Tunnel)")
    print("7. Fix Session / Manual Interact (Headed)")
    print("0. Exit")
    print("="*40)

if __name__ == "__main__":
    # AUTO-START SERVER: If no arguments are provided, jump straight to option 6
    if len(sys.argv) == 1:
        print("[System] No arguments detected. Auto-starting API Server...")
        start_server()
    else:
        # Show menu if any argument (like -m, --menu, -h, etc.) is passed
        while True:
            print_menu()
            choice = input("\nSelect an option (0-7): ").strip()
            
            if choice == '1':
                login_and_save_session()
            elif choice == '2':
                test_headless_session()
            elif choice == '3':
                url = input("Enter Instagram Reel URL: ").strip()
                if url: scrape_reel(url)
            elif choice == '4':
                username = input("Enter Instagram Username: ").strip().replace('@', '')
                if username: scrape_profile(username)
            elif choice == '5':
                url = input("Enter the starting Reel URL: ").strip()
                if url: scrape_since_reel(url)
            elif choice == '6':
                start_server()
                break # app.run blocks, but if it returns, we exit
            elif choice == '7':
                fix_session_headed()
            elif choice == '0':
                sys.exit()
            else:
                print("Invalid choice.")
