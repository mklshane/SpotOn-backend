import os
import time
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from supabase import create_client, Client

# Load environment variables
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

if not all([SUPABASE_URL, SUPABASE_KEY]):
    raise ValueError("Missing Supabase credentials in .env file.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Base URL for the PDS directory
BASE_URL = "https://pds.org.ph/search-doctor"

def process_doctor(name, clinic_name, address):
    """Checks if doctor exists, updates/inserts them, and links to facility"""
    if not name:
        return
        
    name_clean = name.replace("Dr.", "").replace("Dra.", "").strip()
    
    # 1. Search for existing doctor using 'name'
    res = supabase.table("doctors").select("id").ilike("name", f"%{name_clean}%").execute()
    
    if res.data:
        doc_id = res.data[0]["id"]
        # Update existing doctor to be PDS certified
        supabase.table("doctors").update({"pds_certified": True, "source": "pds_directory"}).eq("id", doc_id).execute()
        print(f"  [Updated] {name} is now PDS Certified")
    else:
        # Insert new PDS doctor
        doc_data = {
            "name": name,
            "specialization": "general_dermatology",
            "pds_certified": True,
            "source": "pds_directory",
            "collected_by": "pds_scraper"
        }
        ins = supabase.table("doctors").insert(doc_data).execute()
        doc_id = ins.data[0]["id"] if ins.data else None
        print(f"  [Added] {name}")

    # 2. Add/Link Facility if available
    if doc_id and clinic_name:
        fac_res = supabase.table("facilities").select("id").ilike("name", f"%{clinic_name[:15]}%").execute()
        
        if fac_res.data:
            fac_id = fac_res.data[0]["id"]
        else:
            # Create basic facility placeholder with ALL required fields
            fac_data = {
                "name": clinic_name,
                "type": "dermatology_clinic",
                "address": address if address else "Unknown",
                "city": "Unknown", 
                "province": "Unknown", # Added to fix the NOT NULL constraint
                "latitude": 0.0,
                "longitude": 0.0,
                "collected_by": "pds_scraper"
            }
            f_ins = supabase.table("facilities").insert(fac_data).execute()
            fac_id = f_ins.data[0]["id"] if f_ins.data else None
            
        # Link them in doctor_facility
        if fac_id:
            try:
                supabase.table("doctor_facility").insert({
                    "doctor_id": doc_id,
                    "facility_id": fac_id,
                    "is_primary": True
                }).execute()
            except Exception:
                pass # Already linked

def scrape_pds_directory():
    print("==================================================")
    print("PDS DIRECTORY SCRAPER")
    print("==================================================")
    
    page = 1
    total_processed = 0
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

    while True:
        print(f"\nFetching Page {page}...")
        url = f"{BASE_URL}?search_query&paged={page}"
        
        try:
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code != 200:
                print(f"Failed to fetch page. Status: {response.status_code}")
                break
                
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Save the first page for debugging
            if page == 1:
                with open("pds_debug.html", "w", encoding="utf-8") as f:
                    f.write(soup.prettify())
            
            doctor_cards = soup.find_all(class_=lambda c: c and 'doctor' in c.lower())
            
            if not doctor_cards:
                print("No doctors found on this page. Either we reached the end, or the HTML structure requires custom CSS selectors.")
                break
                
            for card in doctor_cards:
                name_tag = card.find(['h2', 'h3', 'h4'])
                name = name_tag.text.strip() if name_tag else None
                
                # Filter out obvious UI elements that aren't doctors
                if not name or name.lower() in ["add subspecialties", "filter", "search", "submit"]:
                    continue
                
                clinic = "Private Clinic" # Fallback
                address = ""
                
                paragraphs = card.find_all('p')
                if len(paragraphs) > 0:
                    clinic = paragraphs[0].text.strip()
                if len(paragraphs) > 1:
                    address = paragraphs[1].text.strip()

                process_doctor(name, clinic, address)
                total_processed += 1
                
            page += 1
            time.sleep(2) 
            
        except Exception as e:
            print(f"Error scraping page {page}: {e}")
            break

    print("\n==================================================")
    print(f"COMPLETE. Processed {total_processed} doctors.")
    print("==================================================")

if __name__ == "__main__":
    scrape_pds_directory()